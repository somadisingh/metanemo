"""
Film Permits Data Ingestion (NYC Mayor's Office of Media & Entertainment)

Hot dataset: Active and upcoming film/TV shoots in NYC.
Refreshed frequently to catch new permits.

Security:
- No personal contact info stored
- Only public permit data (location, dates, category)
- Parameterized queries prevent SQL injection

Source: https://data.cityofnewyork.us/City-Government/Film-Permits/tg4x-b46p
"""

import os
import re
import logging
from datetime import datetime, timedelta
from typing import Optional

import requests
import psycopg2
from psycopg2.extras import execute_values

import sys
sys.path.insert(0, "../..")
from shared.db import get_connection

logger = logging.getLogger(__name__)

# NYC Open Data endpoint
FILM_PERMITS_URL = "https://data.cityofnewyork.us/resource/tg4x-b46p.json"

# NYC borough coordinates for geocoding from street names
BOROUGH_CENTERS = {
    "Manhattan": (-73.9712, 40.7831),
    "Brooklyn": (-73.9442, 40.6782),
    "Queens": (-73.7949, 40.7282),
    "Bronx": (-73.8648, 40.8448),
    "Staten Island": (-74.1502, 40.5795),
}


def sanitize_text(text: Optional[str], max_length: int = 500) -> Optional[str]:
    """
    Sanitize text input - remove potential PII and limit length.
    
    Security: Strips phone numbers, emails, and excessive whitespace.
    """
    if not text:
        return None
    
    # Remove phone numbers
    text = re.sub(r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b', '[REDACTED]', text)
    
    # Remove email addresses
    text = re.sub(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', '[REDACTED]', text)
    
    # Normalize whitespace
    text = ' '.join(text.split())
    
    # Limit length
    return text[:max_length] if len(text) > max_length else text


def estimate_location(parking_held: str, borough: str) -> tuple:
    """
    Estimate coordinates from street description.
    
    For now, uses borough center as fallback.
    Future: Could use NYC geocoding API for precise locations.
    """
    # Default to borough center
    return BOROUGH_CENTERS.get(borough, (-73.9857, 40.7484))


def fetch_film_permits(days_back: int = 30, days_forward: int = 14) -> list:
    """
    Fetch film permits from NYC Open Data.
    
    Args:
        days_back: How many days of past permits to fetch
        days_forward: How many days of future permits to fetch
        
    Returns:
        List of permit records
    """
    app_token = os.environ.get('SOCRATA_APP_TOKEN')
    
    headers = {}
    if app_token:
        headers['X-App-Token'] = app_token
    
    # Date range filter
    start_date = (datetime.now() - timedelta(days=days_back)).strftime('%Y-%m-%dT00:00:00')
    end_date = (datetime.now() + timedelta(days=days_forward)).strftime('%Y-%m-%dT23:59:59')
    
    params = {
        '$limit': 5000,
        '$where': f"startdatetime >= '{start_date}' AND startdatetime <= '{end_date}'",
        '$order': 'startdatetime DESC'
    }
    
    try:
        response = requests.get(
            FILM_PERMITS_URL,
            headers=headers,
            params=params,
            timeout=30
        )
        response.raise_for_status()
        
        data = response.json()
        logger.info(f"Fetched {len(data)} film permits")
        return data
        
    except requests.RequestException as e:
        logger.error(f"Failed to fetch film permits: {e}")
        return []


def transform_permit(record: dict) -> Optional[tuple]:
    """
    Transform raw permit record to database format.
    
    Security: Sanitizes all text fields, removes PII.
    """
    try:
        event_id = record.get('eventid')
        if not event_id:
            return None
        
        borough = record.get('borough', 'Manhattan')
        parking_held = sanitize_text(record.get('parkingheld'))
        
        # Estimate location
        lon, lat = estimate_location(parking_held or '', borough)
        
        # Parse dates
        start_dt = record.get('startdatetime')
        end_dt = record.get('enddatetime')
        
        return (
            event_id,
            sanitize_text(record.get('eventtype')),
            start_dt,
            end_dt,
            parking_held,
            borough,
            sanitize_text(record.get('category')),
            sanitize_text(record.get('subcategoryname')),
            record.get('zipcode_s', '').split(',')[0] if record.get('zipcode_s') else None,
            lon, lat
        )
        
    except Exception as e:
        logger.warning(f"Failed to transform permit {record.get('eventid')}: {e}")
        return None


def run_film_permits_ingestion(days_back: int = 30) -> dict:
    """
    Run film permits ingestion pipeline.
    
    Returns:
        Dict with success status and counts
    """
    logger.info("Starting film permits ingestion")
    
    # Fetch data
    permits = fetch_film_permits(days_back=days_back)
    if not permits:
        return {'success': False, 'error': 'No data fetched'}
    
    # Transform
    records = []
    for permit in permits:
        transformed = transform_permit(permit)
        if transformed:
            records.append(transformed)
    
    logger.info(f"Transformed {len(records)} permits")
    
    if not records:
        return {'success': False, 'error': 'No valid records'}
    
    # Load to database
    sql = """
        INSERT INTO film_permits (
            event_id, event_type, start_datetime, end_datetime,
            parking_held, borough, category, subcategory, zipcode,
            geom
        ) VALUES %s
        ON CONFLICT (event_id) DO UPDATE SET
            event_type = EXCLUDED.event_type,
            start_datetime = EXCLUDED.start_datetime,
            end_datetime = EXCLUDED.end_datetime,
            parking_held = EXCLUDED.parking_held,
            borough = EXCLUDED.borough,
            category = EXCLUDED.category,
            subcategory = EXCLUDED.subcategory,
            zipcode = EXCLUDED.zipcode,
            geom = EXCLUDED.geom,
            updated_at = NOW()
    """
    
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                # Format records with geometry
                values = [
                    (r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8],
                     f"SRID=4326;POINT({r[9]} {r[10]})")
                    for r in records
                ]

                execute_values(
                    cur, sql, values,
                    template="(%s, %s, %s, %s, %s, %s, %s, %s, %s, ST_GeomFromEWKT(%s))"
                )

                conn.commit()

        logger.info(f"Upserted {len(records)} film permits")
        return {'success': True, 'upserted_count': len(records)}
        
    except psycopg2.Error as e:
        logger.error(f"Database error: {e}")
        return {'success': False, 'error': str(e)}


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    result = run_film_permits_ingestion()
    print(result)
