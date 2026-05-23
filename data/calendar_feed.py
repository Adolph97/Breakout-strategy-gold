import requests
from bs4 import BeautifulSoup
import pandas as pd
from datetime import datetime, timedelta
import re
import logging
import json
import time

# Forex Factory calendar URL
FF_CALENDAR_URL = "https://www.forexfactory.com/calendar?day=thisweek"

def scrape_forex_factory_calendar():
    """
    Scrape Forex Factory calendar for high-impact USD events
    Returns list of upcoming high-impact events
    """
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }

        response = requests.get(FF_CALENDAR_URL, headers=headers, timeout=30)
        response.raise_for_status()

        soup = BeautifulSoup(response.content, 'html.parser')

        # Find the calendar table
        calendar_table = soup.find('table', {'class': 'calendar__table'})
        if not calendar_table:
            logging.warning("Could not find calendar table on Forex Factory")
            return []

        events = []
        rows = calendar_table.find_all('tr', {'class': re.compile('calendar__row')})

        for row in rows:
            try:
                # Get time
                time_cell = row.find('td', {'class': 'calendar__time'})
                time_str = time_cell.get_text(strip=True) if time_cell else ""

                # Get currency
                currency_cell = row.find('td', {'class': 'calendar__currency'})
                currency = currency_cell.get_text(strip=True) if currency_cell else ""

                # Get impact
                impact_cell = row.find('td', {'class': 'calendar__impact'})
                impact = impact_cell.get_text(strip=True) if impact_cell else ""

                # Get event name
                event_cell = row.find('td', {'class': 'calendar__event'})
                event_name = event_cell.get_text(strip=True) if event_cell else ""

                # Skip if not USD or not high impact
                if currency != 'USD' or impact != 'High':
                    continue

                # Parse date/time (simplified - in reality you'd need to handle the date grouping)
                # For now, we'll assume events are today or this week
                event_datetime = datetime.now()  # Placeholder - would need proper date parsing

                events.append({
                    'datetime': event_datetime,
                    'event': event_name,
                    'impact': impact,
                    'currency': currency,
                    'time_str': time_str
                })

            except Exception as e:
                logging.error(f"Error parsing calendar row: {e}")
                continue

        logging.info(f"Scraped {len(events)} high-impact USD events from Forex Factory")
        return events

    except requests.exceptions.RequestException as e:
        logging.error(f"Error fetching Forex Factory calendar: {e}")
        return []
    except Exception as e:
        logging.error(f"Error scraping Forex Factory calendar: {e}")
        return []

def get_high_impact_news_events():
    """
    Get filtered list of high-impact USD events for trading decisions
    Returns dict with news status for scoring
    """
    events = scrape_forex_factory_calendar()

    # Filter for events within news block window
    now = datetime.now()
    high_impact_soon = False
    upcoming_events = []

    for event in events:
        # In a real implementation, you'd calculate time difference properly
        # For now, we'll use a simplified check
        event_time = event['datetime']
        time_diff = abs((event_time - now).total_seconds() / 60)  # minutes

        if time_diff <= config.NEWS_BLOCK_MINUTES:
            high_impact_soon = True

        upcoming_events.append({
            'event': event['event'],
            'time': event['time_str'],
            'minutes_until': int(time_diff) if 'event_time' in locals() else 0
        })

    return {
        'high_impact_soon': high_impact_soon,
        'upcoming_events': upcoming_events,
        'timestamp': now.isoformat()
    }

def save_events_to_db(db_conn, events_data):
    """
    Save scraped events to SQLite database
    """
    if not events_data:
        return False

    try:
        cursor = db_conn.cursor()
        # Clear old events for this week
        cursor.execute('DELETE FROM calendar_events WHERE week_start = ?',
                      [datetime.now().strftime('%Y-%W')])

        for event in events_data.get('upcoming_events', []):
            cursor.execute('''
                INSERT INTO calendar_events
                (event_name, event_time, impact, currency, week_start, timestamp)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                event['event'],
                event['time'],
                'High',
                'USD',
                datetime.now().strftime('%Y-%W'),
                datetime.now().isoformat()
            ))

        db_conn.commit()
        return True
    except Exception as e:
        logging.error(f"Error saving calendar events to DB: {e}")
        return False

def get_latest_events_from_db(db_conn):
    """
    Retrieve latest calendar events from database
    """
    try:
        cursor = db_conn.cursor()
        cursor.execute('''
            SELECT * FROM calendar_events
            WHERE week_start = ?
            ORDER BY timestamp DESC
        ''', [datetime.now().strftime('%Y-%W')])

        rows = cursor.fetchall()
        if rows:
            columns = [description[0] for description in cursor.description]
            events = [dict(zip(columns, row)) for row in rows]
            return {
                'high_impact_soon': len(events) > 0,  # Simplified
                'upcoming_events': events,
                'timestamp': datetime.now().isoformat()
            }
        return {'high_impact_soon': False, 'upcoming_events': [], 'timestamp': datetime.now().isoformat()}
    except Exception as e:
        logging.error(f"Error retrieving calendar events from DB: {e}")
        return {'high_impact_soon': False, 'upcoming_events': [], 'timestamp': datetime.now().isoformat()}

if __name__ == "__main__":
    # Test the calendar feed
    logging.basicConfig(level=logging.INFO)

    print("Scraping Forex Factory calendar...")
    events_data = get_high_impact_news_events()
    print(f"High impact news soon: {events_data['high_impact_soon']}")
    print(f"Upcoming events: {len(events_data['upcoming_events'])}")
    for event in events_data['upcoming_events'][:3]:  # Show first 3
        print(f"  - {event['event']} at {event['time']}")