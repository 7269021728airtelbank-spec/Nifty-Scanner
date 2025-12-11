# ====================================================================
# QCS V9.2 LIVE SCANNER (OI Primary Signal, QCS Informational)
# FINAL VERSION with G1/R2/G9/R10 Strategies
# NIFTY Scanner | GitHub Actions & JSONBin Compatible
# QCS SCORE IS NOW INFORMATIONAL - DOES NOT BLOCK TRADES!
# ====================================================================

# ----------------- 1. IMPORTS & CONFIGURATION -----------------
import os
import requests
import json
import time
import math
from datetime import datetime, timedelta
import numpy as np 
import re
from bs4 import BeautifulSoup

# --- GitHub Secrets (Used instead of local variables) ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") 
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
JSON_API_KEY = os.environ.get("JSON_API_KEY") 
JSON_BIN_ID = os.environ.get("JSON_BIN_ID")
JSON_BIN_URL = f"https://api.jsonbin.io/v3/b/{JSON_BIN_ID}" 

# --- CONFIGURATION (Adapted for Serverless) ---
START_TIME = "09:10"
END_TIME = "15:30"
CALIBRATION_TIME = "09:30"

# --- V9.2 CORE THRESHOLDS & WEIGHTS ---
HEAVY_OI_THRESHOLD = 150000 
MASSIVE_OI_THRESHOLD = 400000 # Unused in this version but kept for future
MIN_VOLATILITY_PTS = 15   
MAX_RISK_REWARD = 0.8     

# --- V9.2 MACRO SAFETY CHECKS ---
MACRO_FLOW_THRESHOLD = 3000.0   # Cr.
VIX_EXTREME_THRESHOLD = 22.0    

# --- DYNAMIC THRESHOLD BASELINE ---
BASE_ATM_OI_THRESHOLD = 100000 
BASE_TRAP_COUNT = 3            
MAX_HISTORY = 20               
VOLATILITY_LOOKBACK = 5        
MAX_OI_DISTANCE = 500          # +/- range around spot for OI checks

# --- QCS WEIGHTS (Sum must be 1.0) ---
CONFIG_QCS_WEIGHTS = {
    "VIX_LEVEL": 0.25,
    "SPOT_VOLATILITY": 0.20,
    "TIME_MULTIPLIER": 0.15,
    "OI_THRESHOLD_MET": 0.25, 
    "RISK_REWARD": 0.15,
}

# --- QCS ACTION THRESHOLDS (Used only for Lot Size/PTSL) ---
QCS_NO_TRADE_THRESHOLD = 55  # Used for LOT/PTSL only
QCS_GIANT_TRADE_MIN = 75     # Used for LOT/PTSL only
LOT_SIZE_MODERATE = 1
LOT_SIZE_GIANT = 3

# --- NON-LINEAR MISMATCH FILTER ---
MISMATCH_VIX_MAX_BLOCK = 12.0
MISMATCH_SPOT_VOL_MIN_BLOCK = 25.0

# --- PTSL RANGES ---
PTSL_QCS_SLOW_MIN = 80
TSL_DISTANCE_SLOW = 0.02    # 2.0%
TSL_DISTANCE_MEDIUM = 0.015 # 1.5%
TSL_DISTANCE_FAST = 0.01    # 1.0%

# Time Multiplier Window 
TIME_WINDOW_MORNING_START = "09:15"
TIME_WINDOW_MORNING_END = "10:00"
STRENGTH_MULTIPLIER_MORNING = 1.5 

# NSE Headers
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Accept-Encoding": "gzip, deflate",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/option-chain"
}

# Global State (Initialized)
session = requests.Session()
# Variables that will be managed by JSONBin.io
oi_state = {}
last_trade_entry = {} 
spot_history = [] 
historical_metrics = {'tioi_list': [], 'avg_doi_list': []}
todays_calibration = {'calibrated': False, 'ATM_OI': BASE_ATM_OI_THRESHOLD, 'TRAP_COUNT': BASE_TRAP_COUNT, 'logged_today': False}
calibration_snapshots = []
FULL_TRADE_BLOCKED = False 
CURRENT_TRADE_STATUS = {
    "in_trade": False,
    "entry_price": 0.0,
    "lots": 0,
    "ptsl_distance": 0.0,
    "trade_id": None,
    "strike": 0, 
    "option": "",
    "tsl_level": 0.0
}


# ----------------- 2. STATE MANAGEMENT (JSONBin.io HTTP - Adapted) -----------------

def load_state():
    """Loads bot state from JSONBin.io (HTTP GET)"""
    global CURRENT_TRADE_STATUS
    
    default_state = {
        'oi_state': {}, 'trade_entry': {}, 'spot_history': [],
        'last_run_date': None, 'historical_metrics': {'tioi_list': [], 'avg_doi_list': []},
        'full_trade_blocked': False, 
        'current_trade_status': CURRENT_TRADE_STATUS.copy(),
        'todays_calibration': todays_calibration.copy()
    }
    
    headers = {'X-Master-Key': JSON_API_KEY, 'Content-Type': 'application/json'}
    
    try:
        print("[STATE] Attempting to load state from JSONBin...")
        response = requests.get(JSON_BIN_URL, headers=headers, timeout=15)
        
        if response.status_code == 200:
            data = response.json().get('record', default_state)
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
    """Saves bot state to JSONBin.io (HTTP PUT)"""
    global oi_state, last_trade_entry, spot_history, historical_metrics, FULL_TRADE_BLOCKED, CURRENT_TRADE_STATUS, todays_calibration
    
    data = {
        'oi_state': oi_state, 'trade_entry': last_trade_entry, 'spot_history': spot_history,
        'historical_metrics': historical_metrics,
        'last_run_date': datetime.now().strftime("%Y-%m-%d"),
        'full_trade_blocked': FULL_TRADE_BLOCKED,
        'current_trade_status': CURRENT_TRADE_STATUS,
        'todays_calibration': todays_calibration
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

# ----------------- 3. UTILITY FUNCTIONS -----------------

def send_telegram(message):
    """Sends HTML formatted message to Telegram (Renamed from send_telegram_alert)"""
    if "YOUR_BOT_TOKEN" in TELEGRAM_BOT_TOKEN or not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("\n[CONSOLE - TG NOT SET]:")
        print(message)
        return
        
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        requests.post(url, data=data, timeout=5)
    except Exception as e:
        print(f"Telegram Error: {e}")

def get_live_vix():
    """Fetches LIVE India VIX from NSE"""
    # NOTE: Simplified VIX fetching for serverless environment
    try:
        vix_url = "https://www.nseindia.com/api/quote-derivative?symbol=INDIA%20VIX&identifier=NC_NIFTY_INDIA_VIX"
        r = session.get(vix_url, headers=HEADERS, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if 'stocks' in data and len(data['stocks']) > 0:
                vix_value = float(data['stocks'][0]['lastPrice'])
                return round(vix_value, 2)
    except:
        pass
    
    # Fallback: Index page scrape
    try:
        r = session.get("https://www.nseindia.com", headers=HEADERS, timeout=10)
        soup = BeautifulSoup(r.text, 'html.parser')
        vix_text = soup.find(text=re.compile(r'India VIX.*?d+'))
        if vix_text:
            match = re.search(r'(d+.?d*)', vix_text)
            if match:
                return float(match.group(1))
    except:
        pass
    
    return 15.5  # Ultimate fallback value

def get_fii_dii_flow():
    """Fetches previous day FII/DII net flow (Absolute Total) - Placeholder for Serverless"""
    return 1500.0  # Ultimate fallback value (in Cr.)

def get_nse_data(index_name):
    """Fetches Option Chain from NSE with Session & Cookies"""
    url = f"https://www.nseindia.com/api/option-chain-indices?symbol={index_name}"
    home_url = "https://www.nseindia.com"
    
    try:
        r_home = session.get(home_url, headers=HEADERS, timeout=10)
        r_data = session.get(url, headers=HEADERS, timeout=10, cookies=r_home.cookies)
        
        if r_data.status_code == 200:
            return r_data.json()
        else:
            print(f"NSE Connection Error: Status {r_data.status_code}")
            return None
    except Exception as e:
        print(f"Fetch Error: {e}")
        return None

def get_expiry_dates(data):
    if not data or 'records' not in data: return []
    return sorted(data['records']['expiryDates'])

def get_nearest_expiry(expiry_list):
    today = datetime.now().date()
    for exp in expiry_list:
        try:
            exp_date = datetime.strptime(exp, "%d-%b-%Y").date()
            if exp_date >= today:
                return exp
        except: continue
    return expiry_list[0] if expiry_list else None
    
def round_price(price, base=50):
    return int(base * round(float(price)/base))

def is_market_open():
    now = datetime.now()
    if now.weekday() > 4: return False
    current_time = now.strftime("%H:%M")
    return START_TIME <= current_time <= END_TIME

def update_volatility(current_spot):
    """Calculates market volatility based on High-Low range of last X scans."""
    global spot_history
    
    spot_history.append(current_spot)
    if len(spot_history) > VOLATILITY_LOOKBACK: 
        spot_history.pop(0)
    
    if len(spot_history) < 2: return 100.0 
    valid_history = [s for s in spot_history if s > 20000] 
    if len(valid_history) < 2: return 100.0
        
    volatility = max(valid_history) - min(valid_history)
    return volatility

def calculate_qcs(vix, spot_volatility_pts, oi_met, risk_reward_score, current_time_str):
    """Calculates the Quantum Confidence Score (QCS) based on weighted factors."""
    vix_scaled = min(100, max(0, 100 * (30.0 - vix) / 18.0)) 
    spot_vol_scaled = min(100, max(0, 100 * (spot_volatility_pts - 10.0) / 20.0))
    
    time_scaled = 50 
    if TIME_WINDOW_MORNING_START <= current_time_str <= TIME_WINDOW_MORNING_END:
        time_scaled = 100 
    
    oi_scaled = oi_met * 100
    
    qcs = (
        CONFIG_QCS_WEIGHTS["VIX_LEVEL"] * vix_scaled +
        CONFIG_QCS_WEIGHTS["SPOT_VOLATILITY"] * spot_vol_scaled +
        CONFIG_QCS_WEIGHTS["TIME_MULTIPLIER"] * time_scaled +
        CONFIG_QCS_WEIGHTS["OI_THRESHOLD_MET"] * oi_scaled +
        CONFIG_QCS_WEIGHTS["RISK_REWARD"] * risk_reward_score
    )
    return round(min(100.0, qcs), 2)

def get_ptsl_distance(qcs_score):
    """Determines Trailing Stop Loss (TSL) distance based on QCS volatility."""
    if qcs_score >= PTSL_QCS_SLOW_MIN:
        return TSL_DISTANCE_SLOW 
    elif qcs_score > QCS_NO_TRADE_THRESHOLD: 
        return TSL_DISTANCE_MEDIUM 
    else:
        return TSL_DISTANCE_FAST 

def apply_v8_filters(vix, spot_volatility_pts, qcs_score):
    """
    MODIFIED: QCS is now informational. Filters only return True/False based on Macro/VIX Mismatch, 
    but NOT based on QCS_NO_TRADE_THRESHOLD. QCS is used only for lot sizing.
    """
    
    # 1. Macro Block Check (VIX/Vol Mismatch) - This is a safety filter and REMAINS.
    if vix < MISMATCH_VIX_MAX_BLOCK and spot_volatility_pts > MISMATCH_SPOT_VOL_MIN_BLOCK:
        print(f"[FILTER] MISMATCH_BLOCK: Low VIX ({vix}) vs High Spot Vol ({spot_volatility_pts})")
        return False, "MISMATCH_BLOCK", 0 
        
    # 2. QCS NO_TRADE Check (Removed/Modified)
    # This logic is intentionally removed to keep QCS informational.
        
    # 3. Lot Sizing (QCS is still used here)
    if qcs_score >= QCS_GIANT_TRADE_MIN:
        return True, "GIANT", LOT_SIZE_GIANT
    else:
        # If QCS is below GIANT threshold, it returns MODERATE lot size, but still allows trade (True)
        return True, "MODERATE", LOT_SIZE_MODERATE


def calculate_adaptive_thresholds(raw_records, spot_price, current_time_str):
    """Calculates TIOI and ATM Avg dOI from opening data for the day's thresholds."""
    global historical_metrics, todays_calibration, calibration_snapshots
    
    current_total_oi = 0
    current_snapshot = []
    LOT_SIZE = 50 
    
    for item in raw_records:
        ce = item.get('CE', {})
        pe = item.get('PE', {})
        ce_oi = ce.get('openInterest', 0) * LOT_SIZE 
        pe_oi = pe.get('openInterest', 0) * LOT_SIZE
        
        current_snapshot.append({
            'strike': item['strikePrice'],
            'ce_oi': ce_oi,
            'pe_oi': pe_oi,
        })
        current_total_oi += ce_oi + pe_oi
    
    # Skip calibration logic if not in the time window
    if current_time_str <= CALIBRATION_TIME and not todays_calibration['calibrated']:
         print(f"[CALIB] Calibrating snapshot at {current_time_str}")
         calibration_snapshots.append(current_snapshot)
         return todays_calibration['ATM_OI'], todays_calibration['TRAP_COUNT']
    
    if current_time_str > CALIBRATION_TIME and not todays_calibration['calibrated']:
        print("--- RUNNING DYNAMIC CALIBRATION (V9.2) ---")
        
        today_tioi = current_total_oi 
        today_avg_doi = 0
        
        if len(calibration_snapshots) >= 2:
            atm_strike = round_price(spot_price, 50)
            first_snap_map = {item['strike']: item for item in calibration_snapshots[0]}
            latest_snap = calibration_snapshots[-1]
            
            total_change = 0
            count = 0
            
            for item in latest_snap:
                strike = item['strike']
                if strike in first_snap_map and abs(strike - atm_strike) <= 300: 
                    ce_change = abs(item['ce_oi'] - first_snap_map[strike]['ce_oi'])
                    pe_change = abs(item['pe_oi'] - first_snap_map[strike]['pe_oi'])
                    total_change += (ce_change + pe_change)
                    count += 1
            
            today_avg_doi = total_change / count if count > 0 else 0
        
        if not todays_calibration.get('logged_today'):
            historical_metrics['tioi_list'].append(today_tioi)
            historical_metrics['avg_doi_list'].append(today_avg_doi)
            historical_metrics['tioi_list'] = historical_metrics['tioi_list'][-MAX_HISTORY:]
            historical_metrics['avg_doi_list'] = historical_metrics['avg_doi_list'][-MAX_HISTORY:]
            todays_calibration['logged_today'] = True
            
        hist_tioi_list = np.array(historical_metrics['tioi_list'])
        hist_avg_doi_list = np.array(historical_metrics['avg_doi_list'])

        new_trap_count = BASE_TRAP_COUNT
        if len(hist_tioi_list) >= 5: 
            p70_tioi = np.percentile(hist_tioi_list, 70)
            p90_tioi = np.percentile(hist_tioi_list, 90)
            
            if today_tioi > p90_tioi:
                new_trap_count = BASE_TRAP_COUNT + 2 
            elif today_tioi > p70_tioi:
                new_trap_count = BASE_TRAP_COUNT + 1 
            
            todays_calibration['TRAP_COUNT'] = new_trap_count
        
        new_atm_oi = BASE_ATM_OI_THRESHOLD
        if len(hist_avg_doi_list) >= 5 and today_avg_doi > 0:
            p30_avg_doi = np.percentile(hist_avg_doi_list, 30)
            p70_avg_doi = np.percentile(hist_avg_doi_list, 70)
            
            factor = 1.0
            if today_avg_doi < p30_avg_doi:
                factor = 0.7
            elif today_avg_doi > p70_avg_doi:
                factor = 1.3
            
            new_atm_oi = int(BASE_ATM_OI_THRESHOLD * factor)
            todays_calibration['ATM_OI'] = new_atm_oi
        
        todays_calibration['calibrated'] = True
        
        send_telegram(f"""
<b>🤖 DYNAMIC THRESHOLDS CALIBRATED (V9.2 LIVE)</b>
<b>Time:</b> {current_time_str}
<b>Market Mode:</b> {'Heavy/Noisy' if todays_calibration['TRAP_COUNT'] > BASE_TRAP_COUNT else 'Normal/Calm'}
---
<b>⛔ TRAP_COUNT:</b> {BASE_TRAP_COUNT} ➡️ <b>{todays_calibration['TRAP_COUNT']}</b> (TIOI Based)
<b>🌊 ATM_OI_THRESHOLD:</b> {BASE_ATM_OI_THRESHOLD/1000:.0f}k ➡️ <b>{todays_calibration['ATM_OI']/1000:.0f}k</b> (Avg dOI Based)
<b>📊 LIVE VIX:</b> {get_live_vix():.2f}
        """)
        
    return todays_calibration['ATM_OI'], todays_calibration['TRAP_COUNT']

def get_option_premium(raw_records, strike, option_type):
    """Fetches the last traded price (premium) for a specific strike and option type."""
    for item in raw_records:
        if item['strikePrice'] == strike:
            option_data = item.get(option_type)
            if option_data and 'lastPrice' in option_data:
                return option_data['lastPrice']
    return 0.0

def fetch_live_data():
    """ Fetches NSE data, calculates spot volatility, and prepares market_data for analysis. """
    global oi_state 
    
    nse_data = get_nse_data("NIFTY")
    if not nse_data or 'records' not in nse_data:
        return None

    spot = nse_data['records']['underlyingValue']
    expiry_list = get_expiry_dates(nse_data)
    curr_expiry = get_nearest_expiry(expiry_list)
    raw_records = [x for x in nse_data['records']['data'] if x['expiryDate'] == curr_expiry]
    
    spot_volatility_pts = update_volatility(spot) 
    live_vix = get_live_vix()  
    risk_reward_score = 80
    current_premium = 0.0 
    
    market_data = {}
    current_oi_snapshot = {} 

    for item in raw_records:
        strike = item['strikePrice']
        ce_oi = item.get('CE', {}).get('openInterest', 0)
        pe_oi = item.get('PE', {}).get('openInterest', 0)
        
        prev_data = oi_state.get(str(strike), {'ce_oi': ce_oi, 'pe_oi': pe_oi})
        ce_prev_oi = prev_data.get('ce_oi', ce_oi)
        pe_prev_oi = prev_data.get('pe_oi', pe_oi)

        market_data[strike] = {
            "ce_oi": ce_oi, "ce_prev_oi": ce_prev_oi,
            "pe_oi": pe_oi, "pe_prev_oi": pe_prev_oi,
        }
        
        current_oi_snapshot[str(strike)] = {'ce_oi': ce_oi, 'pe_oi': pe_oi}

    oi_state = current_oi_snapshot

    return {
        "vix": live_vix,
        "spot_volatility_pts": spot_volatility_pts, 
        "oi_met": 0, 
        "risk_reward_score": risk_reward_score, 
        "current_premium_price": current_premium, 
        "underlying_value": spot, 
        "raw_records": raw_records,
        "market_data": market_data,
        "atm_strike": round_price(spot, 50)
    }

def calculate_oi_analysis(market_data, atm_strike, spot_price, ATM_OI_THRESHOLD, TRAP_COUNT, HEAVY_OI_THRESHOLD):
    """
    Performs V9.2 OI analysis using dynamically adjusted thresholds, 
    incorporating V6.5 strategies (G1, R2, G9/R10).
    """
    
    strikes = sorted(market_data.keys())
    relevant_strikes = []
    atm_band_strikes = []
    trap_range_strikes = []
    LOT_SIZE = 50 

    for strike in strikes:
        distance = abs(strike - spot_price)
        
        # Check nearby strikes for signal
        if distance <= MAX_OI_DISTANCE: relevant_strikes.append(strike)
        # Check strikes closest to ATM for ATM flow logic (within +/- 100 points)
        if abs(strike - atm_strike) <= 100: atm_band_strikes.append(strike)
        # Check nearby strikes for trap logic (within +/- 500 points)
        if distance <= MAX_OI_DISTANCE: trap_range_strikes.append(strike)

    doi_map = {} 
    
    for strike in relevant_strikes:
        data = market_data.get(strike, {})
        # Change in OI is calculated here (current OI - previous OI) * LOT_SIZE
        ce_doi = (data.get("ce_oi", 0) - data.get("ce_prev_oi", 0)) * LOT_SIZE
        pe_doi = (data.get("pe_oi", 0) - data.get("pe_prev_oi", 0)) * LOT_SIZE

        doi_map[strike] = {"ce_doi": ce_doi, "pe_doi": pe_doi}

    # 1. Strategy 17 – Trap (Double Blast) Check
    total_ce_heavy = 0
    total_pe_heavy = 0

    for strike in trap_range_strikes:
        d = doi_map.get(strike)
        if not d: continue
        
        # NOTE: Using the HEAVY_OI_THRESHOLD (150k) for trap count
        if d["ce_doi"] >= HEAVY_OI_THRESHOLD: total_ce_heavy += 1
        if d["pe_doi"] >= HEAVY_OI_THRESHOLD: total_pe_heavy += 1

    is_trap = (total_ce_heavy >= TRAP_COUNT and total_pe_heavy >= TRAP_COUNT)
    if is_trap:
        print(f"[TRAP ALERT] Double Blast: CE={total_ce_heavy}, PE={total_pe_heavy}, TRAP_COUNT={TRAP_COUNT}")
        return {"direction": "TRAP", "trap": True, "oi_met": 0}

    # 2. V6.5 Core Directional Logic (G1, R2) - PRIMARY SIGNAL GENERATION
    final_direction = None         
    final_signal_type = None       
    final_suggested_option = None  
    final_suggested_strike = None
    oi_met_flag = 0 
    
    best_heavy_score = 0
    best_heavy_record = None
    
    # Check for G1 (Heavy Put Writing) and R2 (Heavy Call Writing)
    for strike in relevant_strikes:
        d = doi_map.get(strike)
        if not d: continue

        ce_doi = d["ce_doi"]
        pe_doi = d["pe_doi"]
        
        # G1: Heavy Put Writing (Bullish) - Prioritized
        if pe_doi >= HEAVY_OI_THRESHOLD: 
            direction = "BULLISH"
            signal_type = "G1"
            
        # R2: Heavy Call Writing (Bearish)
        elif ce_doi >= HEAVY_OI_THRESHOLD:
            direction = "BEARISH"
            signal_type = "R2"
            
        else: continue # Skip if not G1 or R2

        suggested_option = "CE" if direction == "BULLISH" else "PE"

        # Score calculation to find the strongest/closest signal
        distance_score = max(1, MAX_OI_DISTANCE - abs(strike - spot_price)) # Closer strikes get higher distance score
        magnitude_score = max(ce_doi, pe_doi)
        score = magnitude_score * distance_score

        if score > best_heavy_score:
            best_heavy_score = score
            best_heavy_record = {
                "strike": strike, "direction": direction, "suggested_option": suggested_option,
                "strength": "HEAVY-OI", "signal_type": signal_type, "ce_doi": ce_doi, "pe_doi": pe_doi
            }

    if best_heavy_record is not None:
        final_direction = best_heavy_record["direction"]
        # Use the signal type identified (G1 or R2)
        final_signal_type = best_heavy_record["signal_type"] 
        final_suggested_option = best_heavy_record["suggested_option"]
        final_suggested_strike = best_heavy_record["strike"]
        oi_met_flag = 1 

    # 3. ATM Flow Strategies (G9, R10) - Secondary Signal (only if G1/R2 not triggered)
    if final_direction is None:
        atm_ce_doi_total = 0
        atm_pe_doi_total = 0

        for strike in atm_band_strikes:
            d = doi_map.get(strike)
            if not d: continue
            atm_ce_doi_total += d["ce_doi"]
            atm_pe_doi_total += d["pe_doi"]
        
        # G9: ATM Put Writing (BULLISH)
        if atm_pe_doi_total > ATM_OI_THRESHOLD and atm_pe_doi_total > atm_ce_doi_total:
            final_direction = "BULLISH"
            final_signal_type = "G9"
            final_suggested_option = "CE"
            final_suggested_strike = atm_strike
            oi_met_flag = 1 
            
        # R10: ATM Call Writing (BEARISH)
        elif atm_ce_doi_total > ATM_OI_THRESHOLD and atm_ce_doi_total > atm_pe_doi_total:
            final_direction = "BEARISH"
            final_signal_type = "R10"
            final_suggested_option = "PE"
            final_suggested_strike = atm_strike
            oi_met_flag = 1 

    return {
        "direction": final_direction,
        "signal_type": final_signal_type, # G1, R2, G9, or R10
        "suggested_option": final_suggested_option,
        "strike": final_suggested_strike,
        "trap": False,
        "oi_met": oi_met_flag
    }

def pre_market_safety_check():
    """V9.2 LIVE FII/DII + VIX Check (Macro Safety Block)"""
    fii_dii_net = get_fii_dii_flow()
    pre_market_vix = get_live_vix()
    
    print(f"LIVE FII/DII Flow: {fii_dii_net:.0f} Cr | VIX: {pre_market_vix}")
    
    if fii_dii_net > MACRO_FLOW_THRESHOLD:
        send_telegram(f"🚨 <b>MACRO BLOCK ACTIVE!</b> Extreme FII/DII Net Flow ({fii_dii_net:.0f} Cr) detected (Threshold: {MACRO_FLOW_THRESHOLD:.0f} Cr). Trading Halted.")
        return True
        
    if pre_market_vix > VIX_EXTREME_THRESHOLD:
        send_telegram(f"🚨 <b>MACRO BLOCK ACTIVE!</b> Extreme VIX ({pre_market_vix:.1f}) detected (Threshold: {VIX_EXTREME_THRESHOLD:.1f}). Trading Halted.")
        return True
        
    return False

def analyze_market(index):
    """Main analysis function integrating OI logic, Dynamic Calibration, QCS, and trade execution."""
    global last_trade_entry, spot_history, historical_metrics, todays_calibration, FULL_TRADE_BLOCKED, CURRENT_TRADE_STATUS, oi_state
    
    print(f"\n--- Scanning {index} at {datetime.now().strftime('%H:%M:%S')} ---")
    current_time_str = datetime.now().strftime("%H:%M")
    
    # 1. LOAD STATE
    full_state = load_state()
    last_trade_entry = full_state['trade_entry']
    spot_history = full_state.get('spot_history', [])
    historical_metrics = full_state['historical_metrics']
    FULL_TRADE_BLOCKED = full_state['full_trade_blocked']
    CURRENT_TRADE_STATUS.update(full_state.get('current_trade_status', CURRENT_TRADE_STATUS.copy()))
    oi_state = full_state.get('oi_state', {})
    # Load calibration state for current run
    todays_calibration.update(full_state.get('todays_calibration', todays_calibration.copy()))
    
    data = fetch_live_data() 
    
    if not data or data['underlying_value'] == 0.0:
         print("Critical NSE fetch failure. Skipping scan.")
         save_state()
         return 

    if FULL_TRADE_BLOCKED:
        print("MACRO Safety Block active. Skipping trade analysis.")
        save_state()
        return

    # 2. DYNAMIC CALIBRATION
    ATM_OI_THRESHOLD, TRAP_COUNT = calculate_adaptive_thresholds(data.get('raw_records', []), data['underlying_value'], current_time_str)
    
    # 3. V9.2 OI ANALYSIS (PRIMARY SIGNAL: G1/R2/G9/R10)
    oi_signal = calculate_oi_analysis(
        data['market_data'], data['atm_strike'], data['underlying_value'],
        ATM_OI_THRESHOLD, TRAP_COUNT, HEAVY_OI_THRESHOLD
    )
    
    if oi_signal.get("trap", False):
        save_state()
        return 

    # 4. QCS CALCULATION (INFORMATIONAL SCORE)
    data["oi_met"] = oi_signal["oi_met"] 
    qcs_score = calculate_qcs(
        data["vix"], data["spot_volatility_pts"], data["oi_met"], 
        data["risk_reward_score"], current_time_str
    )
    
    # 5. FILTERS & ACTION
    # NOTE: trade_allowed is True unless VIX/Vol Mismatch or Macro Block is active.
    # QCS is only used for Lot Size.
    trade_allowed, trade_type_or_reason, lots = apply_v8_filters(
        data["vix"], data["spot_volatility_pts"], qcs_score
    )

    print(f"QCS: {qcs_score:.2f} | VIX: {data['vix']:.2f} | Vol: {data['spot_volatility_pts']:.2f} | {trade_type_or_reason} | Signal: {oi_signal['direction']} ({oi_signal['signal_type']})")

    
    # --- FINAL TRADE EXECUTION LOGIC ---
    final_direction = oi_signal['direction']
    
    if final_direction in ("BULLISH", "BEARISH") and trade_allowed:
        
        # Check for same trade repetition
        if (CURRENT_TRADE_STATUS["in_trade"] and 
            CURRENT_TRADE_STATUS["option"] == oi_signal["suggested_option"]): # Simplified check
            print("Already in same trade. Skipping alert.")
            save_state()
            return

        # GET LIVE PREMIUM PRICE
        live_premium = get_option_premium(
            data['raw_records'], 
            oi_signal['strike'], 
            oi_signal['suggested_option']
        )
        
        ptsl_dist = get_ptsl_distance(qcs_score)
        
        # NOTE: Telegram message shows QCS but signal is based on OI analysis (G1/R2/G9/R10)
        msg = f"""
<b>🟢🔴 QUANTUM ALERT (OI Primary, QCS Informative)</b>
<b>Signal:</b> <b>{final_direction}</b> ({oi_signal['signal_type']})
<b>QCS:</b> 📢 <b>{qcs_score:.2f}</b> | <b>VIX:</b> {data['vix']:.2f}
<b>Action:</b> Buy <b>{oi_signal['strike']} {oi_signal['suggested_option']}</b> ({lots} Lot)
<b>Entry Price:</b> ₹<b>{live_premium:.2f}</b> 🎯
<b>PTSL Distance:</b> 📉 {ptsl_dist*100:.1f}% (QCS Based)
<b>Spot:</b> {data['underlying_value']:.2f}
        """
        send_telegram(msg)
        
        # Update current trade status with live premium
        CURRENT_TRADE_STATUS.update({
            "in_trade": True,
            "entry_price": live_premium,
            "lots": lots,
            "ptsl_distance": ptsl_dist,
            "strike": oi_signal["strike"],
            "option": oi_signal["suggested_option"]
        })
        
    elif CURRENT_TRADE_STATUS["in_trade"]:
        # Exit logic goes here (TSL/Exit)
        # NOTE: Placeholder for TSL logic
        pass
        
    elif final_direction not in ("BULLISH", "BEARISH"):
        print("No valid OI signal generated.")

    # 6. Save State (Updated)
    save_state()


# ----------------- 7. MAIN STARTUP AND HOOK (REPLACING WHILE LOOP) -----------------

def main_serverless():
    global FULL_TRADE_BLOCKED, todays_calibration
    
    print("--- NIFTY OI BOT V9.2 LIVE DATA STARTED (OI PRIMARY) ---")
    
    # Load state for date check
    full_state = load_state()
    FULL_TRADE_BLOCKED = full_state.get('full_trade_blocked', False)
    # Load calibration state for date check
    todays_calibration.update(full_state.get('todays_calibration', todays_calibration.copy()))
    last_run_date = full_state.get('last_run_date')
    
    now_date = datetime.now().strftime("%Y-%m-%d")

    # 1. DAILY RESET (If it's a new trading day)
    if last_run_date != now_date:
        print(f"[RESET] New trading day ({now_date}) detected. Resetting runtime state.")
        # Reset runtime variables
        FULL_TRADE_BLOCKED = False
        todays_calibration = {'calibrated': False, 'ATM_OI': BASE_ATM_OI_THRESHOLD, 'TRAP_COUNT': BASE_TRAP_COUNT, 'logged_today': False}
        
        # Reset trade status
        reset_trade_status = CURRENT_TRADE_STATUS.copy()
        reset_trade_status["in_trade"] = False 
        
        # Save the initial reset state
        full_state.update({
            'full_trade_blocked': False,
            'current_trade_status': reset_trade_status,
            'todays_calibration': todays_calibration,
            'oi_state': {} # Clear OI state for the new day
        })
        save_state() 
        send_telegram("🚀 <b>V9.2 OI Primary Bot Started!</b>\n✅ New day reset complete. Awaiting Calibration and Signals.")

        # Perform pre-market macro check
        FULL_TRADE_BLOCKED = pre_market_safety_check()
        full_state['full_trade_blocked'] = FULL_TRADE_BLOCKED
        save_state() # Save after macro check

    # 2. RUN ANALYSIS IF MARKET IS OPEN
    if is_market_open():
        analyze_market("NIFTY")
    else:
        now = datetime.now().strftime("%H:%M")
        if now == "15:35": 
             save_state()
             print("Market Closed. Final state saved.")
        else:
             print(f"Market Closed. Time: {now}. Skipping analysis.")

# Execute main function for GitHub Actions
if __name__ == "__main__":
    main_serverless()
