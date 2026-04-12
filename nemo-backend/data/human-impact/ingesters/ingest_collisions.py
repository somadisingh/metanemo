"""
Motor Vehicle Collision Data Ingestion (NYPD / Vision Zero)

Fetches collision data from NYC Open Data Socrata API,
filters to last 3 years, and upserts into PostGIS.

Cold Dataset: NYPD Motor Vehicle Collisions - Crashes
Source: https://data.cityofnewyork.us/Public-Safety/Motor-Vehicle-Collisions-Crashes/h9gi-nx95
"""

import os
import logging
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
import requests

from shared.db import get_connection

logger = logging.getLogger(__name__)

# Socrata endpoint for collision data
DEFAULT_ENDPOINT = "https://data.cityofnewyork.us/resource/h9gi-nx95.json"


def fetch_collision_data(
    endpoint: Optional[str] = None,
    app_token: Optional[str] = None,
    limit: int = 100000
) -> pd.DataFrame:
    """
    Fetch collision data from Socrata API.
    
    Args:
        endpoint: Socrata JSON endpoint URL
        app_token: NYC Open Data app token for higher rate limits
        limit: Maximum number of records to fetch
        
    Returns:
        DataFrame with raw collision data
        
    Raises:
        RuntimeError: If HTTP request fails (Property 27)
    """
    endpoint = endpoint or os.environ.get('SOCRATA_COLLISION_ENDPOINT', DEFAULT_ENDPOINT)
    app_token = app_token or os.environ.get('SOCRATA_APP_TOKEN')
    
    headers = {}
    if app_token:
        headers['X-App-Token'] = app_token
    
    # Filter to last 3 years in the API query for efficiency
    three_years_ago = (datetime.now() - timedelta(days=3*365)).strftime('%Y-%m-%d')
    
    params = {
        '$limit': limit,
        '$where': f"crash_date >= '{three_years_ago}'",
        '$order': 'crash_date DESC'
    }
    
    logger.info(f"Fetching collision data from {endpoint}")
    
    try:
        response = requests.get(
            endpoint,
            headers=headers,
            params=params,
            timeout=60
        )
        response.raise_for_status()
        
        data = response.json()
        df = pd.DataFrame(data)
        logger.info(f"Fetched {len(df)} raw collision records")
        return df
        
    except requests.HTTPError as e:
        logger.error(f"HTTP error fetching collision data: {e.response.status_code}")
        raise RuntimeError(f"Collision API HTTP error: {e.response.status_code}") from e
    except requests.RequestException as e:
        logger.error(f"Request error fetching collision data: {e}")
        raise RuntimeError(f"Collision API request error: {e}") from e


def filter_collision_data(df: pd.DataFrame) -> pd.DataFrame:
    """
    Filter collision data per design requirements.
    
    Filtering rules (Property 25):
    - Remove rows where crash_date is more than 3 years before current date
    
    Args:
        df: Raw DataFrame from Socrata
        
    Returns:
        Filtered DataFrame ready for upsert
    """
    initial_count = len(df)
    
    # Standardize column names
    df.columns = df.columns.str.lower().str.strip()
    
    # Convert crash_date to datetime
    if 'crash_date' in df.columns:
        df['crash_date'] = pd.to_datetime(df['crash_date'], errors='coerce')
        
        # Filter to last 3 years (Property 25)
        three_years_ago = datetime.now() - timedelta(days=3*365)
        df = df[df['crash_date'] >= three_years_ago]
    
    # Convert crash_time to time
    if 'crash_time' in df.columns:
        df['crash_time'] = pd.to_datetime(df['crash_time'], format='%H:%M', errors='coerce').dt.time
    
    # Convert numeric fields
    numeric_cols = [
        'number_of_persons_injured', 'number_of_persons_killed',
        'number_of_pedestrians_injured', 'number_of_pedestrians_killed',
        'number_of_cyclist_injured', 'number_of_cyclist_killed',
        'number_of_motorist_injured', 'number_of_motorist_killed'
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0).astype(int)
    
    # Create collision_id if not present
    if 'collision_id' not in df.columns:
        df['collision_id'] = df.index.astype(str)
    
    # Convert latitude/longitude
    if 'latitude' in df.columns:
        df['latitude'] = pd.to_numeric(df['latitude'], errors='coerce')
    if 'longitude' in df.columns:
        df['longitude'] = pd.to_numeric(df['longitude'], errors='coerce')
    
    # Drop rows without valid coordinates
    df = df.dropna(subset=['latitude', 'longitude'])
    
    # Filter out obviously invalid coordinates (outside NYC bounds)
    df = df[
        (df['latitude'] >= 40.4) & (df['latitude'] <= 41.0) &
        (df['longitude'] >= -74.3) & (df['longitude'] <= -73.6)
    ]
    
    filtered_count = len(df)
    logger.info(f"Filtered collision data: {initial_count} -> {filtered_count} records")
    
    return df


def upsert_collisions(df: pd.DataFrame) -> int:
    """
    Upsert collision records into PostGIS.
    
    Uses ON CONFLICT (collision_id) DO UPDATE for idempotency (Property 26).
    Transaction is rolled back on any error (Property 28).
    
    Args:
        df: Filtered DataFrame with collision data
        
    Returns:
        Number of records upserted
        
    Raises:
        RuntimeError: If database operation fails
    """
    if df.empty:
        logger.warning("No collision records to upsert")
        return 0
    
    upsert_sql = """
        INSERT INTO collisions (
            collision_id, crash_date, crash_time, borough, zip_code,
            on_street_name, cross_street_name, off_street_name,
            number_of_persons_injured, number_of_persons_killed,
            number_of_pedestrians_injured, number_of_pedestrians_killed,
            number_of_cyclist_injured, number_of_cyclist_killed,
            number_of_motorist_injured, number_of_motorist_killed,
            contributing_factor_vehicle_1, contributing_factor_vehicle_2,
            vehicle_type_code_1, vehicle_type_code_2,
            geom, updated_at
        ) VALUES (
            %(collision_id)s, %(crash_date)s, %(crash_time)s, %(borough)s, %(zip_code)s,
            %(on_street_name)s, %(cross_street_name)s, %(off_street_name)s,
            %(number_of_persons_injured)s, %(number_of_persons_killed)s,
            %(number_of_pedestrians_injured)s, %(number_of_pedestrians_killed)s,
            %(number_of_cyclist_injured)s, %(number_of_cyclist_killed)s,
            %(number_of_motorist_injured)s, %(number_of_motorist_killed)s,
            %(contributing_factor_vehicle_1)s, %(contributing_factor_vehicle_2)s,
            %(vehicle_type_code_1)s, %(vehicle_type_code_2)s,
            ST_SetSRID(ST_MakePoint(%(longitude)s, %(latitude)s), 4326),
            NOW()
        )
        ON CONFLICT (collision_id) DO UPDATE SET
            number_of_persons_injured = EXCLUDED.number_of_persons_injured,
            number_of_persons_killed = EXCLUDED.number_of_persons_killed,
            number_of_pedestrians_injured = EXCLUDED.number_of_pedestrians_injured,
            number_of_pedestrians_killed = EXCLUDED.number_of_pedestrians_killed,
            geom = EXCLUDED.geom,
            updated_at = NOW()
    """
    
    # Prepare records
    records = []
    for _, row in df.iterrows():
        record = {
            'collision_id': str(row.get('collision_id', '')),
            'crash_date': row.get('crash_date') if pd.notna(row.get('crash_date')) else None,
            'crash_time': row.get('crash_time') if pd.notna(row.get('crash_time')) else None,
            'borough': str(row.get('borough', '')) if pd.notna(row.get('borough')) else None,
            'zip_code': str(row.get('zip_code', ''))[:10] if pd.notna(row.get('zip_code')) else None,
            'on_street_name': str(row.get('on_street_name', '')) if pd.notna(row.get('on_street_name')) else None,
            'cross_street_name': str(row.get('cross_street_name', '')) if pd.notna(row.get('cross_street_name')) else None,
            'off_street_name': str(row.get('off_street_name', '')) if pd.notna(row.get('off_street_name')) else None,
            'number_of_persons_injured': int(row.get('number_of_persons_injured', 0)),
            'number_of_persons_killed': int(row.get('number_of_persons_killed', 0)),
            'number_of_pedestrians_injured': int(row.get('number_of_pedestrians_injured', 0)),
            'number_of_pedestrians_killed': int(row.get('number_of_pedestrians_killed', 0)),
            'number_of_cyclist_injured': int(row.get('number_of_cyclist_injured', 0)),
            'number_of_cyclist_killed': int(row.get('number_of_cyclist_killed', 0)),
            'number_of_motorist_injured': int(row.get('number_of_motorist_injured', 0)),
            'number_of_motorist_killed': int(row.get('number_of_motorist_killed', 0)),
            'contributing_factor_vehicle_1': str(row.get('contributing_factor_vehicle_1', '')) if pd.notna(row.get('contributing_factor_vehicle_1')) else None,
            'contributing_factor_vehicle_2': str(row.get('contributing_factor_vehicle_2', '')) if pd.notna(row.get('contributing_factor_vehicle_2')) else None,
            'vehicle_type_code_1': str(row.get('vehicle_type_code_1', '')) if pd.notna(row.get('vehicle_type_code_1')) else None,
            'vehicle_type_code_2': str(row.get('vehicle_type_code_2', '')) if pd.notna(row.get('vehicle_type_code_2')) else None,
            'latitude': float(row.get('latitude')),
            'longitude': float(row.get('longitude')),
        }
        records.append(record)
    
    try:
        with get_connection(autocommit=False) as conn:
            with conn.cursor() as cur:
                for record in records:
                    cur.execute(upsert_sql, record)
                
                conn.commit()
                logger.info(f"Upserted {len(records)} collision records")
                return len(records)
                
    except Exception as e:
        logger.error(f"Database error upserting collisions: {e}")
        raise RuntimeError(
            f"Collision upsert failed: {e}. "
            f"Input params: {len(records)} records"
        ) from e


def run_collision_ingestion() -> dict:
    """
    Run the complete collision ingestion pipeline.
    
    Returns:
        Dict with ingestion statistics
    """
    logger.info("Starting collision ingestion pipeline")
    
    try:
        # Fetch
        raw_df = fetch_collision_data()
        
        # Filter
        filtered_df = filter_collision_data(raw_df)
        
        # Upsert
        upserted = upsert_collisions(filtered_df)
        
        result = {
            'success': True,
            'raw_count': len(raw_df),
            'filtered_count': len(filtered_df),
            'upserted_count': upserted
        }
        logger.info(f"Collision ingestion complete: {result}")
        return result
        
    except Exception as e:
        logger.error(f"Collision ingestion failed: {e}")
        return {
            'success': False,
            'error': str(e)
        }


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    run_collision_ingestion()
