#!/usr/bin/env python3
"""
Ingest NYC Subway Entrances from MTA Open Data.
Source: https://data.ny.gov/Transportation/MTA-Subway-Entrances/i9wp-a4ja
"""

import requests
from shared.db import get_connection

SUBWAY_ENTRANCES_URL = "https://data.ny.gov/resource/i9wp-a4ja.json"

def fetch_subway_entrances():
    """Fetch subway entrance data from NY Open Data."""
    params = {'$limit': 5000}
    response = requests.get(SUBWAY_ENTRANCES_URL, params=params, timeout=30)
    response.raise_for_status()
    return response.json()

def transform_entrance(row):
    """Transform raw entrance data to DB format."""
    try:
        lat = float(row.get('entrance_latitude', 0) or 0)
        lon = float(row.get('entrance_longitude', 0) or 0)
        
        if lat == 0 or lon == 0:
            return None
            
        return {
            'station_name': row.get('stop_name', row.get('constituent_station_name', 'Unknown')),
            'line': row.get('daytime_routes', row.get('line', '')),
            'entrance_type': row.get('entrance_type', 'Unknown'),
            'ada_accessible': row.get('entrance_type', '').lower() == 'elevator',
            'latitude': lat,
            'longitude': lon
        }
    except (ValueError, TypeError, KeyError):
        return None

def ingest():
    """Main ingestion function."""
    print("Fetching subway entrances...")
    data = fetch_subway_entrances()
    print(f"Fetched {len(data)} raw records")
    
    records = []
    for row in data:
        transformed = transform_entrance(row)
        if transformed:
            records.append(transformed)
    
    print(f"Transformed {len(records)} valid records")
    
    if not records:
        print("No valid records to insert")
        return
    
    with get_connection() as conn:
        cur = conn.cursor()
        
        # Clear existing data
        cur.execute("TRUNCATE TABLE subway_entrances")
        
        # Insert new data
        sql = """
            INSERT INTO subway_entrances (station_name, line, entrance_type, ada, geom)
            VALUES (%s, %s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326))
        """
        
        batch = [(r['station_name'], r['line'], r['entrance_type'], r['ada_accessible'], 
                  r['longitude'], r['latitude']) for r in records]
        
        cur.executemany(sql, batch)
        conn.commit()
        
        cur.execute("SELECT COUNT(*) FROM subway_entrances")
        count = cur.fetchone()[0]
        print(f"Inserted {count} subway entrances")
        
        cur.close()

if __name__ == "__main__":
    ingest()
