# ====================================================================
# QCS V9.2 LIVE SCANNER (OI Primary Signal, QCS Informational)
# FINAL PERMANENT FIX: Using DhanHQ Option Chain API (Rank #1)
# Corrected NameError: last_run_date | ADDED: 3-Min Execution Loop
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
last_run_date = None 

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
            DHAN_ACCESS_TOKEN = data.get('dhan_access_token') or DHAN_ACCESS_TOKEN_INITIAL
            last_run_date = data.get('last_run_date')
            oi_state = data.get('oi_state', {})
            last_trade_entry = data.get('trade_entry', {})
            spot_history = data.get('spot_history', [])
            historical_metrics = data.get('historical_metrics', {'tioi_list': [], 'avg_doi_list': []})
            FULL_TRADE_BLOCKED = data.get('full_trade_blocked', False)
            CURRENT_TRADE_STATUS = data.get('current_trade_status', CURRENT_TRADE_STATUS)
            todays_calibration = data.get('todays_calibration', todays_calibration)
            print("[STATE] State loaded successfully.")
            return data
    except Exception as e:
        print(f"[ERROR] State load error: {e}")
        return default_state

def save_state():
    global oi_state, last_trade_entry, spot_history, historical_metrics, FULL_TRADE_BLOCKED, CURRENT_TRADE_STATUS, todays_calibration, DHAN_ACCESS_TOKEN, last_run_date
    data = {
        'oi_state': oi_state, 'trade_entry': last_trade_entry, 'spot_history': spot_history,
        'historical_metrics': historical_metrics,
        'last_run_date': last_run_date,
        'full_trade_blocked': FULL_TRADE_BLOCKED,
        'current_trade_status': CURRENT_TRADE_STATUS,
        'todays_calibration': todays_calibration,
        'dhan_access_token': DHAN_ACCESS_TOKEN 
    }
    headers = {'X-Master-Key': JSON_API_KEY, 'Content-Type': 'application/json'}
    try:
        print("[STATE] Saving state...")
        requests.put(JSON_BIN_URL, headers=headers, json=data, timeout=15)
        print("[STATE] Saved.")
    except Exception as e: print(f"Save Error: {e}")

# ----------------- 3. DHANHQ UTILITIES -----------------

def get_dhan_session():
    global DHAN_ACCESS_TOKEN
    if not DHAN_ACCESS_TOKEN: return None
    try:
        return dhanhq(client_id=DHAN_CLIENT_ID, access_token=DHAN_ACCESS_TOKEN)
    except: return None

def pre_calculate_market_data(raw_records):
    total_oi_ce = sum(r['CE_openInterest'] for r in raw_records)
    total_oi_pe = sum(r['PE_openInterest'] for r in raw_records)
    total_coi_ce = sum(r['CE_changeinOpenInterest'] for r in raw_records)
    total_coi_pe = sum(r['PE_changeinOpenInterest'] for r in raw_records)
    return {
        'total_oi_ce': total_oi_ce, 'total_oi_pe': total_oi_pe,
        'total_coi_ce': total_coi_ce, 'total_coi_pe': total_coi_pe,
        'tioi': total_oi_ce + total_oi_pe, 'tcoi': total_coi_ce + total_coi_pe,
        'raw_strikes': raw_records
    }

def round_price(price, base=50):
    return int(base * round(float(price)/base))

def fetch_dhan_oc_data(index_name):
    dhan_session = get_dhan_session()
    if not dhan_session: return None
    try:
        response = dhan_session.get_option_chain(exchange_segment='NSE_FO', security_id='200', instrument_type='Index')
        if response and response.get('status') == 'success' and response.get('data'):
            oc_data = response['data']
            raw_records = []
            nearest_strikes = oc_data.get('optionChainResponse', [])
            if not nearest_strikes: return None
            underlying = nearest_strikes[0].get('underlyingValue')
            for r in nearest_strikes:
                raw_records.append({
                    'strikePrice': r.get('strikePrice'),
                    'CE_openInterest': r.get('CE', {}).get('openInterest', 0),
                    'CE_changeinOpenInterest': r.get('CE', {}).get('changeinOpenInterest', 0),
                    'PE_openInterest': r.get('PE', {}).get('openInterest', 0),
                    'PE_changeinOpenInterest': r.get('PE', {}).get('changeinOpenInterest', 0),
                })
            market_data = pre_calculate_market_data(raw_records)
            return {
                "vix": 15.5, "underlying_value": underlying, "market_data": market_data, 
                "atm_strike": round_price(underlying, 50), "raw_records": raw_records
            }
    except: return None

# ----------------- 4. UTILITY & ANALYSIS -----------------

def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN: return
    try: requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", 
                      data={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}, timeout=5)
    except: pass

def is_market_open():
    now = datetime.now()
    if now.weekday() > 4: return False
    return START_TIME <= now.strftime("%H:%M") <= END_TIME

def calculate_oi_analysis(market_data, atm_strike, spot_price, ATM_OI_THRESHOLD, TRAP_COUNT, HEAVY_OI_THRESHOLD):
    doi_ce, doi_pe = 0, 0
    for record in market_data['raw_strikes']:
        if abs(record['strikePrice'] - atm_strike) <= 150: 
            doi_ce += record['CE_changeinOpenInterest']
            doi_pe += record['PE_changeinOpenInterest']
    
    direction, signal_type = None, None
    if doi_ce > doi_pe * 1.5 and doi_ce > ATM_OI_THRESHOLD:
        direction, signal_type = "BEARISH (R2/G9)", "SELL_CALL"
    elif doi_pe > doi_ce * 1.5 and doi_pe > ATM_OI_THRESHOLD:
        direction, signal_type = "BULLISH (G1/R10)", "BUY_CALL"
    
    return {"direction": direction, "signal_type": signal_type, "doi_ce": doi_ce, "doi_pe": doi_pe}

def analyze_market(index):
    data = fetch_dhan_oc_data(index) 
    if not data: return
    
    oi_signal = calculate_oi_analysis(
        data['market_data'], data['atm_strike'], data['underlying_value'],
        todays_calibration['ATM_OI'], todays_calibration['TRAP_COUNT'], HEAVY_OI_THRESHOLD
    )
    
    if oi_signal["direction"]:
        msg = f"🔍 <b>V9.2 Signal: {oi_signal['direction']}</b>\nSpot: {data['underlying_value']}\nCE DOI: {oi_signal['doi_ce']}\nPE DOI: {oi_signal['doi_pe']}"
        send_telegram(msg)
    save_state()

# ----------------- 5. MAIN LOOP STARTUP (The Fix) -----------------

def main_serverless():
    global last_run_date
    load_state() 
    
    # 🚨 LOOP: Run 6 times (Total 18 Mins) to ensure 3-min accuracy
    for i in range(6):
        print(f"\n>>> Cycle {i+1} of 6 Start at {datetime.now().strftime('%H:%M:%S')} <<<")
        now_date = datetime.now().strftime("%Y-%m-%d")

        # 1. Daily Reset
        if last_run_date != now_date:
            global oi_state, spot_history, historical_metrics, FULL_TRADE_BLOCKED, CURRENT_TRADE_STATUS, todays_calibration
            oi_state, spot_history, historical_metrics = {}, [], {'tioi_list': [], 'avg_doi_list': []}
            FULL_TRADE_BLOCKED = False
            CURRENT_TRADE_STATUS = {"in_trade": False, "entry_price": 0.0, "lots": 0, "ptsl_distance": 0.0}
            todays_calibration = {'calibrated': False, 'ATM_OI': BASE_ATM_OI_THRESHOLD, 'TRAP_COUNT': 3, 'logged_today': False}
            last_run_date = now_date
            save_state()
            send_telegram("🚀 <b>Bot Reset! (Loop Mode Active)</b>\nMonitoring NIFTY every 3 mins.")

        # 2. Market Execution
        if is_market_open():
            analyze_market("NIFTY")
        else:
            print("Market closed. Stopping loop.")
            break

        if i < 5: 
            print("Waiting 3 minutes for next cycle...")
            time.sleep(180) # Satiq 3 Minutes Gap

if __name__ == "__main__":
    main_serverless()
