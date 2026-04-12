"""
Restaurant Inspection Data Ingestion (DOHMH NYC)

Fetches restaurant inspection results from NYC Open Data Socrata API,
cleans the data per design requirements, and upserts into PostGIS.

Cold Dataset: DOHMH New York City Restaurant Inspection Results
Source: https://data.cityofnewyork.us/Health/DOHMH-New-York-City-Restaurant-Inspection-Results/43nn-pn8j
"""

import os
import logging
from typing import Optional
from io import StringIO

import pandas as pd
import requests
from shapely.geometry import Point

from shared.db import get_connection

logger = logging.getLogger(__name__)

# Socrata endpoint for restaurant inspections
DEFAULT_ENDPOINT = "https://data.cityofnewyork.us/resource/43nn-pn8j.csv"


def fetch_restaurant_data(
    endpoint: Optional[str] = None,
    app_token: Optional[str] = None,
    limit: int = 50000
) -> pd.DataFrame:
    """
    Fetch restaurant inspection data from Socrata API.
    
    Args:
        endpoint: Socrata CSV endpoint URL
        app_token: NYC Open Data app token for higher rate limits
        limit: Maximum number of records to fetch
        
    Returns:
        DataFrame with raw restaurant inspection data
        
    Raises:
        RuntimeError: If HTTP request fails
    """
    endpoint = endpoint or os.environ.get('SOCRATA_RESTAURANT_ENDPOINT', DEFAULT_ENDPOINT)
    app_token = app_token or os.environ.get('SOCRATA_APP_TOKEN')
    
    headers = {}
    if app_token:
        headers['X-App-Token'] = app_token
    
    params = {
        '$limit': limit,
        '$order': 'inspection_date DESC'
    }
    
    logger.info(f"Fetching restaurant data from {endpoint}")
    
    try:
        response = requests.get(
            endpoint,
            headers=headers,
            params=params,
            timeout=60,
            stream=True
        )
        response.raise_for_status()
        
        # Read CSV from response
        df = pd.read_csv(StringIO(response.text))
        logger.info(f"Fetched {len(df)} raw restaurant records")
        return df
        
    except requests.HTTPError as e:
        logger.error(f"HTTP error fetching restaurant data: {e.response.status_code}")
        raise RuntimeError(f"Restaurant API HTTP error: {e.response.status_code}") from e
    except requests.RequestException as e:
        logger.error(f"Request error fetching restaurant data: {e}")
        raise RuntimeError(f"Restaurant API request error: {e}") from e


def clean_restaurant_data(df: pd.DataFrame) -> pd.DataFrame:
    """
    Clean restaurant inspection data per design requirements.
    
    Cleaning rules (Property 21):
    - Drop rows where action == "No violations were recorded"
    - Drop rows where dba is null
    
    Args:
        df: Raw DataFrame from Socrata
        
    Returns:
        Cleaned DataFrame ready for upsert
    """
    initial_count = len(df)
    
    # Standardize column names to lowercase
    df.columns = df.columns.str.lower().str.strip()
    
    # Drop rows with "No violations were recorded"
    if 'action' in df.columns:
        df = df[df['action'] != 'No violations were recorded']
    
    # Drop rows where dba (business name) is null
    if 'dba' in df.columns:
        df = df[df['dba'].notna()]
        df = df[df['dba'].str.strip() != '']
    
    # Convert inspection_date to datetime
    if 'inspection_date' in df.columns:
        df['inspection_date'] = pd.to_datetime(df['inspection_date'], errors='coerce')
    
    # Convert grade_date to datetime
    if 'grade_date' in df.columns:
        df['grade_date'] = pd.to_datetime(df['grade_date'], errors='coerce')
    
    # Convert score to integer
    if 'score' in df.columns:
        df['score'] = pd.to_numeric(df['score'], errors='coerce').astype('Int64')
    
    # Clean grade field (should be single character)
    if 'grade' in df.columns:
        df['grade'] = df['grade'].str.strip().str.upper().str[:1]
    
    # Create unique inspection_id if not present
    if 'inspection_id' not in df.columns:
        # Combine camis + inspection_date + violation_code for uniqueness
        df['inspection_id'] = (
            df['camis'].astype(str) + '_' + 
            df['inspection_date'].astype(str) + '_' +
            df.get('violation_code', pd.Series([''] * len(df))).fillna('').astype(str)
        )
    
    cleaned_count = len(df)
    logger.info(f"Cleaned restaurant data: {initial_count} -> {cleaned_count} records")
    
    return df


def upsert_restaurants(df: pd.DataFrame) -> int:
    """
    Upsert restaurant records into PostGIS.
    
    Uses ON CONFLICT (inspection_id) DO UPDATE for idempotency (Property 22).
    Transaction is rolled back on any error (Property 24).
    
    Args:
        df: Cleaned DataFrame with restaurant data
        
    Returns:
        Number of records upserted
        
    Raises:
        RuntimeError: If database operation fails (includes input params)
    """
    if df.empty:
        logger.warning("No restaurant records to upsert")
        return 0
    
    upsert_sql = """
        INSERT INTO restaurants (
            inspection_id, camis, dba, boro, building, street, zipcode, phone,
            cuisine_description, inspection_date, action, violation_code,
            violation_description, critical_flag, score, grade, grade_date,
            inspection_type, geom, updated_at
        ) VALUES (
            %(inspection_id)s, %(camis)s, %(dba)s, %(boro)s, %(building)s,
            %(street)s, %(zipcode)s, %(phone)s, %(cuisine_description)s,
            %(inspection_date)s, %(action)s, %(violation_code)s,
            %(violation_description)s, %(critical_flag)s, %(score)s, %(grade)s,
            %(grade_date)s, %(inspection_type)s,
            CASE 
                WHEN %(longitude)s IS NOT NULL AND %(latitude)s IS NOT NULL 
                THEN ST_SetSRID(ST_MakePoint(%(longitude)s, %(latitude)s), 4326)
                ELSE NULL 
            END,
            NOW()
        )
        ON CONFLICT (inspection_id) DO UPDATE SET
            dba = EXCLUDED.dba,
            score = EXCLUDED.score,
            grade = EXCLUDED.grade,
            grade_date = EXCLUDED.grade_date,
            action = EXCLUDED.action,
            violation_description = EXCLUDED.violation_description,
            critical_flag = EXCLUDED.critical_flag,
            geom = EXCLUDED.geom,
            updated_at = NOW()
    """
    
    # Prepare records for insertion
    records = []
    for _, row in df.iterrows():
        record = {
            'inspection_id': str(row.get('inspection_id', '')),
            'camis': str(row.get('camis', '')) if pd.notna(row.get('camis')) else None,
            'dba': str(row.get('dba', '')),
            'boro': str(row.get('boro', '')) if pd.notna(row.get('boro')) else None,
            'building': str(row.get('building', '')) if pd.notna(row.get('building')) else None,
            'street': str(row.get('street', '')) if pd.notna(row.get('street')) else None,
            'zipcode': str(row.get('zipcode', ''))[:10] if pd.notna(row.get('zipcode')) else None,
            'phone': str(row.get('phone', '')) if pd.notna(row.get('phone')) else None,
            'cuisine_description': str(row.get('cuisine_description', '')) if pd.notna(row.get('cuisine_description')) else None,
            'inspection_date': row.get('inspection_date') if pd.notna(row.get('inspection_date')) else None,
            'action': str(row.get('action', '')) if pd.notna(row.get('action')) else None,
            'violation_code': str(row.get('violation_code', '')) if pd.notna(row.get('violation_code')) else None,
            'violation_description': str(row.get('violation_description', '')) if pd.notna(row.get('violation_description')) else None,
            'critical_flag': str(row.get('critical_flag', '')) if pd.notna(row.get('critical_flag')) else None,
            'score': int(row.get('score')) if pd.notna(row.get('score')) else None,
            'grade': str(row.get('grade', ''))[:1] if pd.notna(row.get('grade')) else None,
            'grade_date': row.get('grade_date') if pd.notna(row.get('grade_date')) else None,
            'inspection_type': str(row.get('inspection_type', '')) if pd.notna(row.get('inspection_type')) else None,
            'latitude': float(row.get('latitude')) if pd.notna(row.get('latitude')) else None,
            'longitude': float(row.get('longitude')) if pd.notna(row.get('longitude')) else None,
        }
        records.append(record)
    
    try:
        with get_connection(autocommit=False) as conn:
            with conn.cursor() as cur:
                for record in records:
                    cur.execute(upsert_sql, record)
                
                conn.commit()
                logger.info(f"Upserted {len(records)} restaurant records")
                return len(records)
                
    except Exception as e:
        logger.error(f"Database error upserting restaurants: {e}")
        raise RuntimeError(
            f"Restaurant upsert failed: {e}. "
            f"Input params: {len(records)} records"
        ) from e


def run_restaurant_ingestion() -> dict:
    """
    Run the complete restaurant ingestion pipeline.
    
    Returns:
        Dict with ingestion statistics
    """
    logger.info("Starting restaurant ingestion pipeline")
    
    try:
        # Fetch
        raw_df = fetch_restaurant_data()
        
        # Clean
        clean_df = clean_restaurant_data(raw_df)
        
        # Upsert
        upserted = upsert_restaurants(clean_df)
        
        result = {
            'success': True,
            'raw_count': len(raw_df),
            'clean_count': len(clean_df),
            'upserted_count': upserted
        }
        logger.info(f"Restaurant ingestion complete: {result}")
        return result
        
    except Exception as e:
        logger.error(f"Restaurant ingestion failed: {e}")
        return {
            'success': False,
            'error': str(e)
        }


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    run_restaurant_ingestion()
