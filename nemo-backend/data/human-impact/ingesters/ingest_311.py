"""
311 Service Requests Data Ingestion

Fetches recent 311 complaints from NYC Open Data Socrata API
for hazard detection (noise, scaffolding, downed trees, etc.)

Hot/Cold Hybrid Dataset: 311 Service Requests from 2010 to Present
Source: https://data.cityofnewyork.us/Social-Services/311-Service-Requests-from-2010-to-Present/erm2-nwe9
"""

import os
import logging
from datetime import datetime, timedelta
from typing import Optional, List

import pandas as pd
import requests

from shared.db import get_connection

logger = logging.getLogger(__name__)

DEFAULT_ENDPOINT = "https://data.cityofnewyork.us/resource/erm2-nwe9.json"

# Complaint types relevant to pedestrian safety
SAFETY_COMPLAINT_TYPES = [
    'Noise',
    'Noise - Commercial',
    'Noise - Residential', 
    'Noise - Street/Sidewalk',
    'Noise - Vehicle',
    'Blocked Driveway',
    'Illegal Parking',
    'Street Condition',
    'Sidewalk Condition',
    'Street Light Condition',
    'Traffic Signal Condition',
    'Scaffold Safety',
    'Construction',
    'Building/Use',
    'Air Quality',
    'Hazardous Materials',
    'Sewer',
    'Water System',
    'Damaged Tree',
    'Overgrown Tree/Branches',
    'New Tree Request',
    'Dead/Dying Tree',
]


def fetch_311_data(
    endpoint: Optional[str] = None,
    app_token: Optional[str] = None,
    hours_back: int = 72,
    complaint_types: Optional[List[str]] = None,
    limit: int = 10000
) -> pd.DataFrame:
    """
    Fetch recent 311 complaints from Socrata API.
    
    Args:
        endpoint: Socrata JSON endpoint URL
        app_token: NYC Open Data app token
        hours_back: How many hours of data to fetch
        complaint_types: List of complaint types to filter (None = all safety types)
        limit: Maximum records to fetch
        
    Returns:
        DataFrame with 311 complaint data
        
    Raises:
        RuntimeError: If HTTP request fails
    """
    endpoint = endpoint or os.environ.get('SOCRATA_311_ENDPOINT', DEFAULT_ENDPOINT)
    app_token = app_token or os.environ.get('SOCRATA_APP_TOKEN')
    
    headers = {}
    if app_token:
        headers['X-App-Token'] = app_token
    
    # Time filter
    since = (datetime.now() - timedelta(hours=hours_back)).strftime('%Y-%m-%dT%H:%M:%S')
    
    # Build complaint type filter
    types = complaint_types or SAFETY_COMPLAINT_TYPES
    type_filter = ' OR '.join([f"complaint_type='{t}'" for t in types])
    
    params = {
        '$limit': limit,
        '$where': f"created_date >= '{since}' AND ({type_filter})",
        '$order': 'created_date DESC'
    }
    
    logger.info(f"Fetching 311 data since {since}")
    
    try:
        response = requests.get(
            endpoint,
            headers=headers,
            params=params,
            timeout=30
        )
        response.raise_for_status()
        
        data = response.json()
        df = pd.DataFrame(data)
        logger.info(f"Fetched {len(df)} 311 complaint records")
        return df
        
    except requests.HTTPError as e:
        logger.error(f"HTTP error fetching 311 data: {e.response.status_code}")
        raise RuntimeError(f"311 API HTTP error: {e.response.status_code}") from e
    except requests.RequestException as e:
        logger.error(f"Request error fetching 311 data: {e}")
        raise RuntimeError(f"311 API request error: {e}") from e


def clean_311_data(df: pd.DataFrame) -> pd.DataFrame:
    """
    Clean and prepare 311 data for storage.
    
    Args:
        df: Raw DataFrame from Socrata
        
    Returns:
        Cleaned DataFrame
    """
    if df.empty:
        return df
    
    initial_count = len(df)
    
    # Standardize column names
    df.columns = df.columns.str.lower().str.strip()
    
    # Convert dates
    if 'created_date' in df.columns:
        df['created_date'] = pd.to_datetime(df['created_date'], errors='coerce')
    if 'closed_date' in df.columns:
        df['closed_date'] = pd.to_datetime(df['closed_date'], errors='coerce')
    
    # Extract coordinates from location field if lat/lon not present
    if 'latitude' not in df.columns and 'location' in df.columns:
        # Location is often a dict with latitude/longitude
        df['latitude'] = df['location'].apply(
            lambda x: float(x.get('latitude')) if isinstance(x, dict) and x.get('latitude') else None
        )
        df['longitude'] = df['location'].apply(
            lambda x: float(x.get('longitude')) if isinstance(x, dict) and x.get('longitude') else None
        )
    
    # Convert lat/lon to numeric
    if 'latitude' in df.columns:
        df['latitude'] = pd.to_numeric(df['latitude'], errors='coerce')
    if 'longitude' in df.columns:
        df['longitude'] = pd.to_numeric(df['longitude'], errors='coerce')
    
    # Drop rows without coordinates
    df = df.dropna(subset=['latitude', 'longitude'])
    
    # Filter valid NYC coordinates
    df = df[
        (df['latitude'] >= 40.4) & (df['latitude'] <= 41.0) &
        (df['longitude'] >= -74.3) & (df['longitude'] <= -73.6)
    ]
    
    cleaned_count = len(df)
    logger.info(f"Cleaned 311 data: {initial_count} -> {cleaned_count} records")
    
    return df


def upsert_311_complaints(df: pd.DataFrame) -> int:
    """
    Upsert 311 complaints into PostGIS.
    
    Args:
        df: Cleaned DataFrame with 311 data
        
    Returns:
        Number of records upserted
    """
    if df.empty:
        logger.warning("No 311 records to upsert")
        return 0
    
    upsert_sql = """
        INSERT INTO complaints_311 (
            unique_key, created_date, closed_date, agency, agency_name,
            complaint_type, descriptor, location_type, incident_zip,
            incident_address, street_name, cross_street_1, cross_street_2,
            borough, status, resolution_description, geom
        ) VALUES (
            %(unique_key)s, %(created_date)s, %(closed_date)s, %(agency)s, %(agency_name)s,
            %(complaint_type)s, %(descriptor)s, %(location_type)s, %(incident_zip)s,
            %(incident_address)s, %(street_name)s, %(cross_street_1)s, %(cross_street_2)s,
            %(borough)s, %(status)s, %(resolution_description)s,
            ST_SetSRID(ST_MakePoint(%(longitude)s, %(latitude)s), 4326)
        )
        ON CONFLICT (unique_key) DO UPDATE SET
            status = EXCLUDED.status,
            closed_date = EXCLUDED.closed_date,
            resolution_description = EXCLUDED.resolution_description
    """
    
    records = []
    for _, row in df.iterrows():
        record = {
            'unique_key': str(row.get('unique_key', '')),
            'created_date': row.get('created_date') if pd.notna(row.get('created_date')) else None,
            'closed_date': row.get('closed_date') if pd.notna(row.get('closed_date')) else None,
            'agency': str(row.get('agency', ''))[:50] if pd.notna(row.get('agency')) else None,
            'agency_name': str(row.get('agency_name', '')) if pd.notna(row.get('agency_name')) else None,
            'complaint_type': str(row.get('complaint_type', '')) if pd.notna(row.get('complaint_type')) else None,
            'descriptor': str(row.get('descriptor', '')) if pd.notna(row.get('descriptor')) else None,
            'location_type': str(row.get('location_type', '')) if pd.notna(row.get('location_type')) else None,
            'incident_zip': str(row.get('incident_zip', ''))[:10] if pd.notna(row.get('incident_zip')) else None,
            'incident_address': str(row.get('incident_address', '')) if pd.notna(row.get('incident_address')) else None,
            'street_name': str(row.get('street_name', '')) if pd.notna(row.get('street_name')) else None,
            'cross_street_1': str(row.get('cross_street_1', '')) if pd.notna(row.get('cross_street_1')) else None,
            'cross_street_2': str(row.get('cross_street_2', '')) if pd.notna(row.get('cross_street_2')) else None,
            'borough': str(row.get('borough', '')) if pd.notna(row.get('borough')) else None,
            'status': str(row.get('status', '')) if pd.notna(row.get('status')) else None,
            'resolution_description': str(row.get('resolution_description', '')) if pd.notna(row.get('resolution_description')) else None,
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
                logger.info(f"Upserted {len(records)} 311 complaint records")
                return len(records)
                
    except Exception as e:
        logger.error(f"Database error upserting 311 complaints: {e}")
        raise RuntimeError(f"311 upsert failed: {e}") from e


def cleanup_old_complaints(days: int = 7) -> int:
    """
    Remove 311 complaints older than specified days.
    
    Args:
        days: Delete complaints older than this many days
        
    Returns:
        Number of records deleted
    """
    delete_sql = """
        DELETE FROM complaints_311
        WHERE created_date < NOW() - INTERVAL '%s days'
    """
    
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(delete_sql, (days,))
                deleted = cur.rowcount
                logger.info(f"Deleted {deleted} old 311 complaints (>{days} days)")
                return deleted
    except Exception as e:
        logger.error(f"Error cleaning up old complaints: {e}")
        return 0


def run_311_ingestion(hours_back: int = 72) -> dict:
    """
    Run the complete 311 ingestion pipeline.
    
    Args:
        hours_back: How many hours of data to fetch
        
    Returns:
        Dict with ingestion statistics
    """
    logger.info("Starting 311 ingestion pipeline")
    
    try:
        # Fetch
        raw_df = fetch_311_data(hours_back=hours_back)
        
        # Clean
        clean_df = clean_311_data(raw_df)
        
        # Upsert
        upserted = upsert_311_complaints(clean_df)
        
        # Cleanup old data
        deleted = cleanup_old_complaints(days=7)
        
        result = {
            'success': True,
            'raw_count': len(raw_df),
            'clean_count': len(clean_df),
            'upserted_count': upserted,
            'deleted_count': deleted
        }
        logger.info(f"311 ingestion complete: {result}")
        return result
        
    except Exception as e:
        logger.error(f"311 ingestion failed: {e}")
        return {
            'success': False,
            'error': str(e)
        }


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    run_311_ingestion()
