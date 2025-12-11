# ====================================================================
# QCS V9.2 Market Scanner - Serverless Deployment on GitHub Actions
# NIFTY Scanner | Entry Threshold: 0.55 | Telegram Test ON
# ====================================================================

# ----------------- 1. IMPORTS & CONFIGURATION -----------------
import os
import requests
import json 
from datetime import datetime
import numpy as np
from bs4 import BeautifulSoup
import time 

# --- Keys will be loaded from GitHub Actions Secrets ---
JSON_API_KEY = os.environ.get("JSON_API_KEY") 
JSON_BIN_ID = os.environ.get("JSON_BIN_ID")
JSON_BIN_URL = f"https://api.jsonbin.io/v3/b/{JSON_BIN_ID}" 

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") 
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# --- Global Variables for State Management ---
oi_state = {}
last_trade_entry = {}
spot_history = []
historical_metrics = {'tioi_list': [], 'avg_doi_list': []}
FULL_TRADE_BLOCKED = False
CURRENT_TRADE_STATUS = {'traded': False, 'entry_price': 0.0, 'oi_side': None}

# ----------------- 2. STATE MANAGEMENT (JSONBin.io HTTP) -----------------

def load_state():
    """Loads bot state from JSONBin.io (HTTP GET)"""
    
    default_state = {
        'oi_state': {}, 'trade_entry': {}, 'spot_history': [],
        'last_run_date': None, 'historical_metrics': {'tioi_list': [], 'avg_doi_list': []},
        'full_trade_blocked': False, 'current_trade_status': {'traded': False, 'entry_price': 0.0, 'oi_side': None} 
    }
    
    headers = {
        'X-Master-Key': JSON_API_KEY,
        'Content-Type': 'application/json'
    }
    
    try:
        print("[STATE] Attempting to load state from JSONBin...")
        response = requests.get(JSON_BIN_URL, headers=headers, timeout=15)
        
        if response.status_code == 200:
            data = response.json()
            if 'record' in data and data['record']:
                 print("[STATE] State loaded successfully.")
                 return data['record']
        
        print(f"[ERROR] JSONBin load failed or Bin empty. Status: {response.status_code}. Using default state.")
        return default_state

    except requests.exceptions.RequestException as e:
        print(f"[ERROR] Could not load state from JSONBin. Network Error: {e}")
        return default_state
    except Exception as e:
        print(f"[ERROR] State parsing error: {e}")
        return default_state

def save_state():
    """Saves bot state to JSONBin.io (HTTP PUT)"""
    global oi_state, last_trade_entry, spot_history, historical_metrics, FULL_TRADE_BLOCKED, CURRENT_TRADE_STATUS
    
    data = {
        'oi_state': oi_state, 'trade_entry': last_trade_entry, 'spot_history': spot_history,
        'historical_metrics': historical_metrics,
        'last_run_date': datetime.now().strftime("%Y-%m-%d"),
        'full_trade_blocked': FULL_TRADE_BLOCKED,
        'current_trade_status': CURRENT_TRADE_STATUS
    }
    
    headers = {
        'X-Master-Key': JSON_API_KEY,
        'Content-Type': 'application/json'
    }
    
    try:
        print("[STATE] Attempting to save state to JSONBin...")
        response = requests.put(JSON_BIN_URL, headers=headers, json=data, timeout=15)
        
        if response.status_code in [200, 204]:
            print("[STATE] State saved successfully.")
        else:
            print(f"[ERROR] JSONBin save failed. Status: {response.status_code}, Response: {response.text}")
            
    except requests.exceptions.RequestException as e:
        print(f"[ERROR] Save Network Error: {e}")
    except Exception as e:
        print(f"General Save Error: {e}")

# ----------------- 3. UTILITY FUNCTIONS -----------------

def send_telegram_alert(message):
    """Sends a message to the Telegram bot."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        'chat_id': TELEGRAM_CHAT_ID,
        'text': message,
        'parse_mode': 'Markdown'
    }
    try:
        response = requests.post(url, data=payload, timeout=10)
        if response.status_code == 200:
            print(f"[TELEGRAM] Alert sent successfully: {message[:50]}...")
        else:
            print(f"[ERROR] Failed to send Telegram alert. Status: {response.status_code}, Response: {response.text}")
    except requests.exceptions.RequestException as e:
        print(f"[ERROR] Telegram Network Error: {e}")

def is_market_open():
    """Checks if the current time is within market hours (9:15 AM to 3:30 PM IST, Mon-Fri)."""
    now_ist = datetime.now() 
    
    if not (0 <= now_ist.weekday() <= 4):
        print("[CHECK] Market closed: Weekend.")
        return False

    start_time = now_ist.replace(hour=9, minute=15, second=0, microsecond=0)
    end_time = now_ist.replace(hour=15, minute=30, second=0, microsecond=0)

    if start_time <= now_ist <= end_time:
        return True
    else:
        print("[CHECK] Market closed: Out of hours.")
        return False

# ----------------- 4. NSE DATA FETCHING -----------------

def get_nse_data(symbol="NIFTY"):
    """Fetches real-time price and OI data from NSE India."""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    
    # --- HERE 1: NSE URL (NIFTY Symbol Added) ---
    # NIFTY के लिए URL सेट किया गया
    url = "https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY" 
    
    try:
        session = requests.Session()
        session.get("https://www.nseindia.com", headers=headers, timeout=10)
        
        response = session.get(url, headers=headers, timeout=10)
        response.raise_for_status() 
        
        data = response.json()
        
        current_price = data['records']['underlyingValue']
        oi_data = data['filtered']['data']
        
        if not current_price or not oi_data:
            print("[ERROR] NSE data extraction failed. Price or OI data missing.")
            return None, None
            
        return current_price, oi_data
        
    except requests.exceptions.RequestException as e:
        print(f"[ERROR] Failed to fetch NSE data. Network/HTTP Error: {e}")
        return None, None
    except Exception as e:
        print(f"[ERROR] NSE Data Processing Error: {e}")
        return None, None

# ----------------- 5. QCS V9.2 LOGIC IMPLEMENTATION -----------------

WEIGHTS = {
    "VIX_LEVEL": 0.35,
    "SPOT_VOLATILITY": 0.30,
    "TIME_MULTIPLIER": 0.35 
}

def calculate_qcs_metrics(oi_data, current_price):
    """Calculates TIOI (Total OI Index) and DOIC (OI Change) metrics based on V9.2 logic."""
    
    total_call_oi = sum(item['CE']['openInterest'] for item in oi_data if 'CE' in item)
    total_put_oi = sum(item['PE']['openInterest'] for item in oi_data if 'PE' in item)
    
    if total_call_oi + total_put_oi == 0:
        return 0, 0
        
    tioi = (total_call_oi - total_put_oi) / (total_call_oi + total_put_oi)
    
    total_call_coi = sum(item['CE']['changeInOpenInterest'] for item in oi_data if 'CE' in item)
    total_put_coi = sum(item['PE']['changeInOpenInterest'] for item in oi_data if 'PE' in item)

    if total_call_coi + total_put_coi == 0:
        doic = 0
    else:
        doic = (total_call_coi - total_put_coi) / (total_call_coi + total_put_coi)
    
    return tioi, doic

def apply_momentum_filter(spot_history):
    """Applies a simple 10-period momentum filter."""
    if len(spot_history) < 10:
        return 0
    
    momentum = (spot_history[-1] - spot_history[-10]) / spot_history[-10]
    return momentum

def calculate_final_qcs(tioi, doic, spot_history):
    """Combines all V9.2 factors to produce the final QCS score."""
    global historical_metrics
    
    momentum = apply_momentum_filter(spot_history)
    
    qcs_score = (0.5 * tioi) + (0.3 * doic) + (0.2 * momentum)
    
    historical_metrics['tioi_list'].append(tioi)
    
    return qcs_score

# ----------------- 6. TRADE EXECUTION LOGIC -----------------

def handle_trade_logic(qcs_score, current_price):
    """Handles entry and exit logic based on the final QCS score."""
    global CURRENT_TRADE_STATUS, FULL_TRADE_BLOCKED
    
    # --- HERE 2: ENTRY THRESHOLDS (Set to 0.55) ---
    ENTRY_THRESHOLD = 0.55  
    EXIT_THRESHOLD = 0.10   

    if FULL_TRADE_BLOCKED:
        send_telegram_alert("⚠️ **ALERT:** Full Trade Blocked. No new trades will be initiated today.")
        return
        
    if not CURRENT_TRADE_STATUS['traded']:
        
        # BUY Signal (Strong Bullish)
        if qcs_score > ENTRY_THRESHOLD:
            # --- HERE 3: YOUR TRADE EXECUTION (BUY) ---
            trade_side = "BUY (CALL)"
            CURRENT_TRADE_STATUS['traded'] = True
            CURRENT_TRADE_STATUS['entry_price'] = current_price
            CURRENT_TRADE_STATUS['oi_side'] = trade_side
            
            alert_msg = f"🟢 **QCS BUY SIGNAL!**\nScore: {qcs_score:.4f} > {ENTRY_THRESHOLD}\nEntry Price: {current_price:.2f}\nSide: {trade_side}"
            send_telegram_alert(alert_msg)
            print(f"[TRADE] Entered {trade_side} at {current_price:.2f}")

        # SELL Signal (Strong Bearish)
        elif qcs_score < -ENTRY_THRESHOLD:
            # --- HERE 4: YOUR TRADE EXECUTION (SELL) ---
            trade_side = "SELL (PUT)"
            CURRENT_TRADE_STATUS['traded'] = True
            CURRENT_TRADE_STATUS['entry_price'] = current_price
            CURRENT_TRADE_STATUS['oi_side'] = trade_side
            
            alert_msg = f"🔴 **QCS SELL SIGNAL!**\nScore: {qcs_score:.4f} < {-ENTRY_THRESHOLD}\nEntry Price: {current_price:.2f}\nSide: {trade_side}"
            send_telegram_alert(alert_msg)
            print(f"[TRADE] Entered {trade_side} at {current_price:.2f}")

    else:
        # Exit Logic
        if abs(qcs_score) < EXIT_THRESHOLD:
            exit_price = current_price
            
            pnl_points = exit_price - CURRENT_TRADE_STATUS['entry_price']
            if CURRENT_TRADE_STATUS['oi_side'] == "SELL (PUT)":
                 pnl_points = -pnl_points 

            pnl_percent = (pnl_points / CURRENT_TRADE_STATUS['entry_price']) * 100
            
            exit_msg = f"🟡 **TRADE EXIT!** (Neutral QCS)\nExit Price: {exit_price:.2f}\nEntry: {CURRENT_TRADE_STATUS['entry_price']:.2f}\nP&L: {pnl_points:.2f} points ({pnl_percent:.2f}%)"
            send_telegram_alert(exit_msg)
            
            CURRENT_TRADE_STATUS['traded'] = False
            CURRENT_TRADE_STATUS['entry_price'] = 0.0
            CURRENT_TRADE_STATUS['oi_side'] = None
            print(f"[TRADE] Exited trade.")
            
# ----------------- 7. MAIN EXECUTION -----------------

def main():
    """Main scanner loop executed by GitHub Action."""
    global oi_state, last_trade_entry, spot_history, historical_metrics, FULL_TRADE_BLOCKED, CURRENT_TRADE_STATUS

    # 1. Market Check
    if not is_market_open():
        return

    # --- TELEGRAM TEST LINE (Confirming Connectivity) ---
    send_telegram_alert("✅ QCS Bot Test Message: Deployment Successful!")
    # --------------------------------------------------

    # 2. Load State
    state = load_state()
    oi_state = state['oi_state']
    last_trade_entry = state['trade_entry']
    spot_history = state['spot_history']
    historical_metrics = state['historical_metrics']
    FULL_TRADE_BLOCKED = state['full_trade_blocked']
    CURRENT_TRADE_STATUS = state['current_trade_status']

    # 3. Fetch Data
    # --- HERE 5: NSE Symbol (Set to NIFTY) ---
    symbol_to_scan = "NIFTY" 
    current_price, oi_data = get_nse_data(symbol=symbol_to_scan)

    if not current_price:
        print("[FAIL] Cannot proceed without current price data.")
        return

    # 4. Update History
    spot_history.append(current_price)
    if len(spot_history) > 20:
        spot_history.pop(0)

    # 5. Calculate Metrics
    tioi, doic = calculate_qcs_metrics(oi_data, current_price)
    qcs_score = calculate_final_qcs(tioi, doic, spot_history)
    
    print(f"[QCS] TIOI: {tioi:.4f}, DOIC: {doic:.4f}, Final QCS: {qcs_score:.4f}")

    # 6. Execute Trade Logic
    handle_trade_logic(qcs_score, current_price)

    # 7. Save State
    save_state()

# Execute main function
if __name__ == "__main__":
    main()
