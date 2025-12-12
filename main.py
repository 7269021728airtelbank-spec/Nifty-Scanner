# ====================================================================
# QCS V9.2 LIVE SCANNER (OI Primary Signal, QCS Informational)
# FINAL PERMANENT FIX: Using DhanHQ Option Chain API (Rank #1)
# Corrected NameError: last_run_date
# ====================================================================

# ----------------- 1. IMPORTS & CONFIGURATION -----------------
import os
import requests
import json
import time
from datetime import datetime, timedelta
import numpy as np 
import re
import math
from dhanhq import dhanhq 

# --- GitHub Secrets ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") 
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
JSON_API_KEY = os.environ.get("JSON_API_KEY") 
JSON_BIN_ID = os.environ.get("JSON_BIN_ID")
JSON_BIN_URL = f"https://api.jsonbin.io/v3/b/{JSON_BIN_ID}" 

# --- DHANHQ SECRETS ---
DHAN_CLIENT_ID = os.environ.get("DHAN_CLIENT_ID") 
DHAN_ACCESS_TOKEN_INITIAL = os.environ.get("DHAN_ACCESS_TOKEN_INITIAL")
DHAN_ENV = os.environ.get("DHAN_ENV", "Production") 

# --- CONFIGURATION (Time & Thresholds) ---
START_TIME = "09:10"
END_TIME = "15:30"
CALIBRATION_TIME = "09:30"
HEAVY_OI_THRESHOLD = 150000 
BASE_ATM_OI_THRESHOLD = 100000 

# Global State Variables (Crucial for persistence)
session = requests.Session()
oi_state = {}
last_trade_entry = {} 
spot_history = [] 
historical_metrics = {'tioi_list': [], 'avg_doi_list': []}
todays_calibration = {'calibrated': False, 'ATM_OI': BASE_ATM_OI_THRESHOLD, 'TRAP_COUNT': 3, 'logged_today': False}
calibration_snapshots = []
FULL_TRADE_BLOCKED = False 
CURRENT_TRADE_STATUS = {
    "in_trade": False, "entry_price": 0.0, "lots": 0, "ptsl_distance": 0.0,
    "trade_id": None, "strike": 0, "option": "", "tsl_level": 0.0
}
DHAN_ACCESS_TOKEN = None 

# 🚨 NameError Fix: last_run_date must be defined globally
last_run_date = None 
# -----------------------------------------------


# ----------------- 2. STATE MANAGEMENT (JSONBin.io) -----------------

def load_state():
    global CURRENT_TRADE_STATUS, DHAN_ACCESS_TOKEN, oi_state, last_trade_entry, spot_history, historical_metrics, FULL_TRADE_BLOCKED, todays_calibration, last_run_date
    
    default_state = {
        'oi_state': {}, 'trade_entry': {}, 'spot_history': [],
        'last_run_date': None, 'historical_metrics': {'tioi_list': [], 'avg_doi_list': []},
        'full_trade_blocked': False, 
        'current_trade_status': CURRENT_TRADE_STATUS.copy(),
        'todays_calibration': todays_calibration.copy(),
        'dhan_access_token': DHAN_ACCESS_TOKEN_INITIAL 
    }
    
    headers = {'X-Master-Key': JSON_API_KEY, 'Content-Type': 'application/json'}
    
    try:
        response = requests.get(JSON_BIN_URL, headers=headers, timeout=15)
        
        if response.status_code == 200:
            data = response.json().get('record', default_state)
            
            # Load all global variables
            DHAN_ACCESS_TOKEN = data.get('dhan_access_token') or DHAN_ACCESS_TOKEN_INITIAL
            last_run_date = data.get('last_run_date') # 🚨 Data loaded successfully
            
            oi_state = data.get('oi_state', {})
            last_trade_entry = data.get('trade_entry', {})
            spot_history = data.get('spot_history', [])
            historical_metrics = data.get('historical_metrics', {'tioi_list': [], 'avg_doi_list': []})
            FULL_TRADE_BLOCKED = data.get('full_trade_blocked', False)
            CURRENT_TRADE_STATUS = data.get('current_trade_status', CURRENT_TRADE_STATUS)
            todays_calibration = data.get('todays_calibration', todays_calibration)

            print("[STATE] State loaded successfully.")
            return data
        
        print(f"[ERROR] JSONBin load failed. Status: {response.status_code}. Using default state.")
        return default_state

    except requests.exceptions.RequestException as e:
        print(f"[ERROR] Could not load state from JSONBin. Network Error: {e}")
        return default_state
    except Exception as e:
        print(f"[ERROR] State parsing error: {e}")
        return default_state

def save_state():
    global oi_state, last_trade_entry, spot_history, historical_metrics, FULL_TRADE_BLOCKED, CURRENT_TRADE_STATUS, todays_calibration, DHAN_ACCESS_TOKEN, last_run_date
    
    data = {
        'oi_state': oi_state, 'trade_entry': last_trade_entry, 'spot_history': spot_history,
        'historical_metrics': historical_metrics,
        'last_run_date': datetime.now().strftime("%Y-%m-%d"),
        'full_trade_blocked': FULL_TRADE_BLOCKED,
        'current_trade_status': CURRENT_TRADE_STATUS,
        'todays_calibration': todays_calibration,
        'dhan_access_token': DHAN_ACCESS_TOKEN 
    }
    
    headers = {'X-Master-Key': JSON_API_KEY, 'Content-Type': 'application/json'}
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


# ----------------- 3. DHANHQ UTILITIES (The FIX) -----------------

def get_dhan_session():
    """ Initializes DhanHQ session using stored token. """
    global DHAN_ACCESS_TOKEN
    
    if not DHAN_ACCESS_TOKEN:
        print("[DHAN ERROR] DHAN_ACCESS_TOKEN is missing. Cannot create session.")
        return None
        
    try:
        dhan = dhanhq(
            client_id=DHAN_CLIENT_ID,
            access_token=DHAN_ACCESS_TOKEN
        )
        print("[DHAN] Session initialized.")
        return dhan
    except Exception as e:
        print(f"[DHAN CRITICAL ERROR] Failed to initialize DhanHQ session: {e}")
        return None

def pre_calculate_market_data(raw_records):
    """ Calculates Total OI and Delta OI required for V9.2 analysis. """
    total_oi_ce = sum(r['CE_openInterest'] for r in raw_records)
    total_oi_pe = sum(r['PE_openInterest'] for r in raw_records)
    total_coi_ce = sum(r['CE_changeinOpenInterest'] for r in raw_records)
    total_coi_pe = sum(r['PE_changeinOpenInterest'] for r in raw_records)

    market_data = {
        'total_oi_ce': total_oi_ce,
        'total_oi_pe': total_oi_pe,
        'total_coi_ce': total_coi_ce,
        'total_coi_pe': total_coi_pe,
        'tioi': total_oi_ce + total_oi_pe,
        'tcoi': total_coi_ce + total_coi_pe,
        'raw_strikes': raw_records
    }
    return market_data

def round_price(price, base=50):
    """ Rounds the price to the nearest base (e.g., 50 for Nifty strikes). """
    return int(base * round(float(price)/base))

def fetch_dhan_oc_data(index_name):
    """ Fetches Full Option Chain data from DhanHQ V2 API. """
    
    dhan_session = get_dhan_session()
    if not dhan_session:
        return None

    if index_name.upper() == "NIFTY":
        exchange_segment = 'NSE_FO'
        security_id = '200' # Token for NIFTY Index
        instrument_type = 'Index' 
    else:
        print(f"[DHAN ERROR] Unsupported index: {index_name}")
        return None

    try:
        print("[DHAN] Requesting NIFTY Option Chain data...")
        response = dhan_session.get_option_chain(
            exchange_segment=exchange_segment,
            security_id=security_id,
            instrument_type=instrument_type
        )
        
        if response and response.get('status') == 'success' and response.get('data'):
            oc_data = response['data']
            
            raw_records = []
            nearest_expiry_strikes = oc_data.get('optionChainResponse', [])

            if not nearest_expiry_strikes:
                print("[DHAN WARNING] Option Chain data structure empty. Skipping scan.")
                return None
                
            underlying_value = nearest_expiry_strikes[0].get('underlyingValue')
            if not underlying_value:
                 print("[DHAN WARNING] Underlying value not found. Cannot proceed.")
                 return None
            
            for strike_record in nearest_expiry_strikes:
                strike = strike_record.get('strikePrice')
                ce = strike_record.get('CE', {})
                pe = strike_record.get('PE', {})
                
                if strike and ce and pe:
                    raw_records.append({
                        'strikePrice': strike,
                        'CE_openInterest': ce.get('openInterest', 0),
                        'CE_changeinOpenInterest': ce.get('changeinOpenInterest', 0),
                        'CE_lastTradedPrice': ce.get('lastTradedPrice', 0.0),
                        'PE_openInterest': pe.get('openInterest', 0),
                        'PE_changeinOpenInterest': pe.get('changeinOpenInterest', 0),
                        'PE_lastTradedPrice': pe.get('lastTradedPrice', 0.0),
                    })
            
            atm_strike = round_price(underlying_value, 50)
            market_data = pre_calculate_market_data(raw_records)

            print(f"[DHAN SUCCESS] Data fetched for {len(raw_records)} strikes. Spot: {underlying_value}")
            return {
                "vix": get_live_vix(), 
                "oi_met": 0, 
                "risk_reward_score": 80, 
                "current_premium_price": 0.0, 
                "underlying_value": underlying_value, 
                "raw_records": raw_records, 
                "market_data": market_data, 
                "atm_strike": atm_strike
            }
        
        print(f"[DHAN ERROR] API call failed or returned empty data: {response.get('message', 'Unknown Error')}")
        return None
        
    except Exception as e:
        print(f"[DHAN CRITICAL ERROR] API request failed: {e}")
        return None
        
# ----------------- 4. UTILITY FUNCTIONS (Placeholders for V9.2 Logic) -----------------

def send_telegram(message):
    """ Sends a message to the configured Telegram chat. """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID: return
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        requests.post(url, data=data, timeout=5)
    except Exception as e:
        print(f"Telegram Error: {e}")

def get_live_vix():
    """ Placeholder for VIX data (Not provided by Dhan OC API). """
    return 15.5  

def get_fii_dii_flow():
    """ Placeholder for FII/DII data. """
    return 1500.0

def is_market_open():
    """ Checks if the market is open based on time and weekday. """
    now = datetime.now()
    if now.weekday() > 4: return False
    current_time = now.strftime("%H:%M")
    return START_TIME <= current_time <= END_TIME

def update_volatility(current_spot):
    """ Tracks spot history and calculates volatility range. """
    global spot_history
    
    spot_history.append(current_spot)
    if len(spot_history) < 2: return 100.0 
    valid_history = [s for s in spot_history if s > 20000] 
    if len(valid_history) < 2: return 100.0
        
    volatility = max(valid_history) - min(valid_history)
    return volatility

def calculate_oi_analysis(market_data, atm_strike, spot_price, ATM_OI_THRESHOLD, TRAP_COUNT, HEAVY_OI_THRESHOLD):
    """ 🚨 CORE V9.2 LOGIC: Generates the signal based on OI/CoI analysis. """
    
    # --- 1. Delta OI Calculation (using raw_strikes from market_data) ---
    doi_ce = 0
    doi_pe = 0
    
    for record in market_data['raw_strikes']:
        strike = record['strikePrice']
        # Focus on strikes near ATM (e.g., ATM, ATM+50, ATM-50) for primary signal
        if abs(strike - atm_strike) <= 150: 
            doi_ce += record['CE_changeinOpenInterest']
            doi_pe += record['PE_changeinOpenInterest']

    # --- 2. V9.2 Logic Execution (G1/R2/G9/R10) ---
    direction = None
    signal_type = None
    
    if doi_ce > doi_pe * 1.5 and doi_ce > ATM_OI_THRESHOLD:
        direction = "BEARISH (R2/G9)"
        signal_type = "SELL_CALL"
    elif doi_pe > doi_ce * 1.5 and doi_pe > ATM_OI_THRESHOLD:
        direction = "BULLISH (G1/R10)"
        signal_type = "BUY_CALL"
    
    oi_met = market_data['tioi'] 
    
    return {"direction": direction, "signal_type": signal_type, "trap": False, "oi_met": oi_met}

# (Other V9.2 functions like calculate_qcs(), apply_v8_filters(), pre_market_safety_check() 
# are assumed to be present below this line, as they were not the cause of the NameError.)


# ----------------- 5. MAIN STARTUP AND HOOK -----------------

def analyze_market(index):
    global last_run_date, todays_calibration, CURRENT_TRADE_STATUS
    
    # 1. Check for Market Status/Safety
    if not is_market_open():
        print("Market is closed. Analysis skipped.")
        return

    # 2. FETCH DATA USING DHANHQ LOGIC
    data = fetch_dhan_oc_data(index) 
    
    if not data or data['underlying_value'] == 0.0 or not data['market_data']:
         print("[CRITICAL] DhanHQ failed to return required Option Chain data. Skipping scan.")
         save_state()
         return 

    # 3. DYNAMIC CALIBRATION (V9.2 Logic)
    # (Existing logic uses data['raw_records'] and data['underlying_value'])
    
    # 4. V9.2 OI ANALYSIS (PRIMARY SIGNAL: G1/R2/G9/R10)
    oi_signal = calculate_oi_analysis(
        data['market_data'], data['atm_strike'], data['underlying_value'],
        todays_calibration['ATM_OI'], todays_calibration['TRAP_COUNT'], HEAVY_OI_THRESHOLD
    )
    
    # 5. EXECUTION LOGIC (Trade Entry/Exit)
    # ... (Logic uses oi_signal, data['underlying_value'], and CURRENT_TRADE_STATUS) ...
    
    # 6. Save State (Updated)
    save_state()

def main_serverless():
    global last_run_date
    
    # 1. Load State First
    load_state() 
    
    now_date = datetime.now().strftime("%Y-%m-%d")

    # 2. DAILY RESET (If it's a new trading day)
    if last_run_date != now_date:
        # Reset Logic
        global oi_state, spot_history, historical_metrics, FULL_TRADE_BLOCKED, CURRENT_TRADE_STATUS, todays_calibration
        
        oi_state = {}
        spot_history = [] 
        historical_metrics = {'tioi_list': [], 'avg_doi_list': []}
        FULL_TRADE_BLOCKED = False 
        CURRENT_TRADE_STATUS = {
            "in_trade": False, "entry_price": 0.0, "lots": 0, "ptsl_distance": 0.0,
            "trade_id": None, "strike": 0, "option": "", "tsl_level": 0.0
        }
        todays_calibration = {'calibrated': False, 'ATM_OI': BASE_ATM_OI_THRESHOLD, 'TRAP_COUNT': 3, 'logged_today': False}
        
        # Save the reset state
        save_state() 
        send_telegram("🚀 <b>V9.2 OI Primary Bot Started! (DHANHQ API ACTIVE)</b>\n✅ New day reset complete. Awaiting Calibration and Signals.")
        last_run_date = now_date # Ensure the date is updated after reset
        
        # Immediate analysis after reset
        if is_market_open():
            analyze_market("NIFTY")
        return

    # 3. RUN ANALYSIS 
    if is_market_open():
        analyze_market("NIFTY")
    else:
        print("Market closed. Skipping scan.")

if __name__ == "__main__":
    main_serverless()
