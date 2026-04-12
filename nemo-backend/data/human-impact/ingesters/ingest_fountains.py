#!/usr/bin/env python3
"""
Ingest NYC Drinking Fountains from NYC Parks Open Data.
Source: https://data.cityofnewyork.us/Recreation/NYC-Parks-Drinking-Fountains/622h-mkfu
"""

import requests
from shared.db import get_connection

FOUNTAINS_URL = "https://data.cityofnewyork.us/resource/622h-mkfu.json"

def fetch_fountains():
    """Fetch drinking fountain data from NYC Open Data."""
    params = {'$limit': 5000}
    response = requests.get(FOUNTAINS_URL, params=params, timeout=30)
    response.raise_for_status()
    return response.json()

def transform_fountain(row):
    """Transform raw fountain data to DB format."""
    try:
        lat = float(row.get('latitude', 0) or 0)
        lon = float(row.get('longitude', 0) or 0)
        
        if lat == 0 or lon == 0:
            return None
            
        return {
            'park_name': row.get('park_name', row.get('name', 'Unknown')),
            'location': row.get('location', ''),
            'status': row.get('status', 'Active'),
            'latitude': lat,
            'longitude': lon
        }
    except (ValueError, TypeError):
        return None

def ingest():
    """Main ingestion function."""
    print("Fetching drinking fountains...")
    data = fetch_fountains()
    print(f"Fetched {len(data)} raw records")
    
    records = []
    for row in data:
        transformed = transform_fountain(row)
        if transformed:
            records.append(transformed)
    
    print(f"Transformed {len(records)} valid records")
    
    if not records:
        print("No valid records to insert")
        return
    
    with get_connection() as conn:
        cur = conn.cursor()
        
        cur.execute("TRUNCATE TABLE drinking_fountains")
        
        sql = """
            INSERT INTO drinking_fountains (park_name, location, status, geom)
            VALUES (%s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326))
        """
        
        batch = [(r['park_name'], r['location'], r['status'],
                  r['longitude'], r['latitude']) for r in records]
        
        cur.executemany(sql, batch)
        conn.commit()
        
        cur.execute("SELECT COUNT(*) FROM drinking_fountains")
        count = cur.fetchone()[0]
        print(f"Inserted {count} drinking fountains")
        
        cur.close()

if __name__ == "__main__":
    ingest()
