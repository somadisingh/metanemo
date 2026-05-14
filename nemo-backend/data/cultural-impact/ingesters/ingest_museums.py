"""
Museums & Cultural Venues Data Ingestion

Cold dataset: Museums, galleries, theaters, cultural centers.
Refreshed weekly.

Security:
- Phone numbers stripped (privacy)
- Only public venue info stored
- Parameterized queries prevent SQL injection

Source: https://data.cityofnewyork.us/resource/fn6f-htvy.json
"""

import os
import re
import logging
from typing import Optional

import requests
import psycopg2

import sys
sys.path.insert(0, "../..")
from shared.db import get_connection

logger = logging.getLogger(__name__)

# NYC Open Data endpoint
MUSEUMS_URL = "https://data.cityofnewyork.us/resource/fn6f-htvy.json"


def sanitize_text(text: Optional[str], max_length: int = 500) -> Optional[str]:
    """Sanitize text - remove PII, limit length."""
    if not text:
        return None
    
    # Remove phone numbers for privacy
    text = re.sub(r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b', '', text)
    text = re.sub(r'\(\d{3}\)\s*\d{3}[-.]?\d{4}', '', text)
    
    # Remove emails
    text = re.sub(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', '', text)
    
    # Normalize whitespace
    text = ' '.join(text.split())
    
    return text[:max_length] if len(text) > max_length else text


def sanitize_url(url: Optional[str]) -> Optional[str]:
    """Validate and sanitize URL."""
    if not url:
        return None
    
    url = url.strip()
    
    # Basic URL validation
    if not url.startswith(('http://', 'https://')):
        if url.startswith('www.'):
            url = 'http://' + url
        else:
            return None
    
    # Remove tracking parameters for privacy
    if '?' in url:
        url = url.split('?')[0]
    
    return url[:500] if len(url) > 500 else url


def fetch_museums() -> list:
    """Fetch museums from NYC Open Data."""
    app_token = os.environ.get('SOCRATA_APP_TOKEN')
    
    headers = {}
    if app_token:
        headers['X-App-Token'] = app_token
    
    params = {
        '$limit': 5000
    }
    
    try:
        response = requests.get(
            MUSEUMS_URL,
            headers=headers,
            params=params,
            timeout=30
        )
        response.raise_for_status()
        
        data = response.json()
        logger.info(f"Fetched {len(data)} museums/venues")
        return data
        
    except requests.RequestException as e:
        logger.error(f"Failed to fetch museums: {e}")
        return []


def extract_coordinates(record: dict) -> Optional[tuple]:
    """Extract coordinates from record."""
    geom = record.get('the_geom')
    if geom and geom.get('type') == 'Point':
        coords = geom.get('coordinates', [])
        if len(coords) >= 2:
            return (coords[0], coords[1])
    return None


def infer_borough(zipcode: str) -> Optional[str]:
    """Infer borough from zipcode."""
    if not zipcode:
        return None
    
    zip_prefix = zipcode[:3] if len(zipcode) >= 3 else zipcode
    
    # NYC zipcode ranges by borough
    if zip_prefix in ['100', '101', '102']:
        return 'Manhattan'
    elif zip_prefix in ['112', '113', '114']:
        return 'Brooklyn'
    elif zip_prefix in ['110', '111', '113', '114', '116']:
        return 'Queens'
    elif zip_prefix in ['104']:
        return 'Bronx'
    elif zip_prefix in ['103']:
        return 'Staten Island'
    
    return None


def run_museums_ingestion() -> dict:
    """Run museums ingestion pipeline."""
    logger.info("Starting museums ingestion")
    
    # Fetch data
    museums = fetch_museums()
    if not museums:
        return {'success': False, 'error': 'No data fetched'}
    
    # Transform and load
    inserted = 0
    
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                for record in museums:
                    name = sanitize_text(record.get('name'))
                    if not name:
                        continue

                    coords = extract_coordinates(record)
                    if not coords:
                        continue

                    zipcode = record.get('zip', '').replace('.0', '')
                    borough = infer_borough(zipcode)

                    # Note: Phone number intentionally NOT stored for privacy
                    website = sanitize_url(record.get('url'))

                    address = sanitize_text(record.get('adress1', '') + ' ' + record.get('address2', ''))
                    city = sanitize_text(record.get('city'))

                    try:
                        cur.execute("""
                            INSERT INTO cultural_venues (name, venue_type, address, city, zipcode, borough, website, geom)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326))
                            ON CONFLICT DO NOTHING
                        """, (
                            name,
                            'Museum',
                            address,
                            city,
                            zipcode,
                            borough,
                            website,
                            coords[0], coords[1]
                        ))
                        inserted += 1
                    except Exception as e:
                        logger.warning(f"Failed to insert {name}: {e}")
                        continue

                conn.commit()

        logger.info(f"Inserted {inserted} museums")
        return {'success': True, 'inserted_count': inserted}
        
    except psycopg2.Error as e:
        logger.error(f"Database error: {e}")
        return {'success': False, 'error': str(e)}


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    result = run_museums_ingestion()
    print(result)
