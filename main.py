# ================= CONFIGURATION & API KEYS (Reading from GitHub Secrets) =================
import os
from datetime import datetime
import requests
import json 
# ... आपके अन्य import (numpy, bs4, etc.) यहाँ होने चाहिए ...

# Keys will be loaded from GitHub Actions Secrets (Environment Variables)
# हम इन्हें सीधे os.environ से पढ़ते हैं
JSON_API_KEY = os.environ.get("JSON_API_KEY") 
JSON_BIN_ID = os.environ.get("JSON_BIN_ID")
JSON_BIN_URL = f"https://api.jsonbin.io/v3/b/{JSON_BIN_ID}" 

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") 
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


# ================= STATE MANAGEMENT FUNCTIONS (JSONBin.io HTTP) =================

def load_state():
    """Loads bot state from JSONBin.io (HTTP GET)"""
    global CURRENT_TRADE_STATUS
    
    # यदि Fetching विफल होती है तो Default State का उपयोग होता है
    default_state = {
        'oi_state': {}, 'trade_entry': {}, 'spot_history': [],
        'last_run_date': None, 
        'historical_metrics': {'tioi_list': [], 'avg_doi_list': []},
        'full_trade_blocked': False,
        # सुनिश्चित करें कि CURRENT_TRADE_STATUS डिफ़ॉल्ट रूप से सही हो
        'current_trade_status': {'traded': False, 'entry_price': 0.0, 'oi_side': None} 
    }
    
    headers = {
        # JSONBin के लिए Master Key की आवश्यकता है
        'X-Master-Key': JSON_API_KEY,
        'Content-Type': 'application/json'
    }
    
    try:
        print("[STATE] Attempting to load state from JSONBin...")
        response = requests.get(JSON_BIN_URL, headers=headers, timeout=15)
        
        if response.status_code == 200:
            data = response.json()
            # JSONBin 'record' key में डेटा भेजता है
            if 'record' in data and data['record']:
                 print("[STATE] State loaded successfully.")
                 return data['record']
        
        # यदि 200 नहीं है या Bin खाली है (Status: 404, 403, 200 खाली)
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
        'oi_state': oi_state, 
        'trade_entry': last_trade_entry, 
        'spot_history': spot_history,
        'historical_metrics': historical_metrics,
        'last_run_date': datetime.now().strftime("%Y-%m-%d"),
        'full_trade_blocked': FULL_TRADE_BLOCKED,
        'current_trade_status': CURRENT_TRADE_STATUS
    }
    
    headers = {
        # JSONBin के लिए Master Key की आवश्यकता है
        'X-Master-Key': JSON_API_KEY,
        'Content-Type': 'application/json'
    }
    
    try:
        print("[STATE] Attempting to save state to JSONBin...")
        # PUT रिक्वेस्ट पूरे Bin कंटेंट को ओवरराइट कर देता है
        response = requests.put(JSON_BIN_URL, headers=headers, json=data, timeout=15)
        
        if response.status_code in [200, 204]:
            print("[STATE] State saved successfully.")
        else:
            print(f"[ERROR] JSONBin save failed. Status: {response.status_code}, Response: {response.text}")
            
    except requests.exceptions.RequestException as e:
        print(f"[ERROR] Save Network Error: {e}")
    except Exception as e:
        print(f"General Save Error: {e}")

# ... आपके QCS V9.2 के बाकी सभी फ़ंक्शंस (get_nse_data, is_market_open, calculate_metrics, send_telegram_alert, main_scanner_loop) यहाँ से शुरू होते हैं ...
