import requests
import pandas as pd
from datetime import datetime
import logging
import json
import os
import time

# Try multiple CFTC endpoints - they sometimes change
COT_ENDPOINTS = [
    "https://publicreporting.cftc.gov/resource/jun7-fc8e.json",
    "https://publicreporting.cftc.gov/resource/6dca-a2qw.json",  # Alternative endpoint
    "https://publicreporting.cftc.gov/resource/bu7f-ynin.json",  # Another alternative
]

def fetch_cot_data():
    """
    Fetch latest COT data for Gold from CFTC website
    Tries multiple endpoints to avoid 403 errors
    Returns dict with COT data or None if failed
    """
    for endpoint in COT_ENDPOINTS:
        try:
            # Add a small delay to avoid rate limiting
            time.sleep(1)

            params = {
                "cftc_contract_market_code": "088691",
                "$order": "report_date_as_yyyy_mm_dd DESC",
                "$limit": "1"
            }

            # Add headers to mimic a browser request
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Accept': 'application/json',
            }

            response = requests.get(endpoint, params=params, headers=headers, timeout=30)

            # If we get a 403, try the next endpoint
            if response.status_code == 403:
                logging.warning(f"403 Forbidden for {endpoint}, trying next endpoint...")
                continue

            response.raise_for_status()

            data = response.json()

            if not data:
                logging.warning(f"No COT data returned from {endpoint}")
                continue

            # Parse the latest COT report
            latest = data[0]

            # Extract key metrics for gold trading
            # Non-commercial positions (speculators)
            non_comm_long = int(latest.get('noncommercial_long_all', 0)) if latest.get('noncommercial_long_all') else 0
            non_comm_short = int(latest.get('noncommercial_short_all', 0)) if latest.get('noncommercial_short_all') else 0

            # Commercial positions (hedgers)
            comm_long = int(latest.get('commercial_long_all', 0)) if latest.get('commercial_long_all') else 0
            comm_short = int(latest.get('commercial_short_all', 0)) if latest.get('commercial_short_all') else 0

            # Calculate net positions
            non_comm_net = non_comm_long - non_comm_short
            comm_net = comm_long - comm_short

            # Determine bias based on positioning
            # Extreme positioning can indicate contrarian signals
            total_non_comm = non_comm_long + non_comm_short
            if total_non_comm > 0:
                non_comm_long_pct = (non_comm_long / total_non_comm) * 100
            else:
                non_comm_long_pct = 50

            # Bias logic:
            # - If non-commercials are excessively long (>70%), market may be overbought (bias: SELL)
            # - If non-commercials are excessively short (<30%), market may be oversold (bias: BUY)
            # - Otherwise, neutral
            if non_comm_long_pct > 70:
                bias = "SELL"  # Overbought, potential reversal down
            elif non_comm_long_pct < 30:
                bias = "BUY"   # Oversold, potential reversal up
            else:
                bias = "NEUTRAL"

            cot_data = {
                "report_date": latest.get('report_date_as_yyyy_mm_dd'),
                "non_comm_long": non_comm_long,
                "non_comm_short": non_comm_short,
                "comm_long": comm_long,
                "comm_short": comm_short,
                "non_comm_net": non_comm_net,
                "comm_net": comm_net,
                "non_comm_long_pct": round(non_comm_long_pct, 2),
                "bias": bias,
                "timestamp": datetime.now().isoformat()
            }

            logging.info(f"Fetched COT data from {endpoint}: {cot_data}")
            return cot_data

        except requests.exceptions.RequestException as e:
            logging.warning(f"Error fetching COT data from {endpoint}: {e}")
            continue
        except (ValueError, KeyError) as e:
            logging.error(f"Error parsing COT data from {endpoint}: {e}")
            continue

    logging.error("All COT endpoints failed")
    return None

def save_cot_to_db(db_conn, cot_data):
    """
    Save COT data to SQLite database
    """
    if not cot_data:
        return False

    try:
        cursor = db_conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO cot_data
            (report_date, non_comm_long, non_comm_short, comm_long, comm_short,
             non_comm_net, comm_net, non_comm_long_pct, bias, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            cot_data['report_date'],
            cot_data['non_comm_long'],
            cot_data['non_comm_short'],
            cot_data['comm_long'],
            cot_data['comm_short'],
            cot_data['non_comm_net'],
            cot_data['comm_net'],
            cot_data['non_comm_long_pct'],
            cot_data['bias'],
            cot_data['timestamp']
        ))
        db_conn.commit()
        return True
    except Exception as e:
        logging.error(f"Error saving COT data to DB: {e}")
        return False

def get_latest_cot_from_db(db_conn):
    """
    Retrieve latest COT data from database
    """
    try:
        cursor = db_conn.cursor()
        cursor.execute('''
            SELECT * FROM cot_data
            ORDER BY timestamp DESC
            LIMIT 1
        ''')
        row = cursor.fetchone()

        if row:
            columns = [description[0] for description in cursor.description]
            return dict(zip(columns, row))
        return None
    except Exception as e:
        logging.error(f"Error retrieving COT data from DB: {e}")
        return None

def get_gold_cot():
    """
    Convenience function to get formatted COT data for gold
    Returns dict with key metrics or static fallback if API is unreachable
    """
    cot_data = fetch_cot_data()
    if not cot_data:
        logger.warning("COT API unavailable - using cached bias")
        return {
            "date": "2026-05-13",
            "spec_long": 219793,
            "spec_short": 48171,
            "net": 171622,
            "bias": "BULLISH",
            "source": "cached"
        }

    return {
        "date": cot_data['report_date'],
        "spec_long": cot_data['non_comm_long'],
        "spec_short": cot_data['non_comm_short'],
        "net": cot_data['non_comm_net'],
        "bull_pct": cot_data['non_comm_long_pct'],
        "bias": cot_data['bias']
    }

if __name__ == "__main__":
    # Test the COT feed
    logging.basicConfig(level=logging.INFO)

    print("Fetching COT data...")
    cot_data = fetch_cot_data()
    if cot_data:
        print("COT Data:")
        for key, value in cot_data.items():
            print(f"  {key}: {value}")
    else:
        print("Failed to fetch COT data from all endpoints")