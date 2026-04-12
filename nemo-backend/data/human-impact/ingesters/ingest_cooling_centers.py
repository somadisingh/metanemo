#!/usr/bin/env python3
"""
Ingest NYC Cooling Centers from NYC Open Data.
Source: https://data.cityofnewyork.us/Health/Cooling-Center/x89p-9dd2
"""

import requests
from shared.db import get_connection

COOLING_CENTERS_URL = "https://data.cityofnewyork.us/resource/x89p-9dd2.json"

def fetch_cooling_centers():
    """Fetch cooling center data from NYC Open Data."""
    params = {'$limit': 1000}
    response = requests.get(COOLING_CENTERS_URL, params=params, timeout=30)
    response.raise_for_status()
    return response.json()

def transform_center(row):
    """Transform raw cooling center data to DB format."""
    try:
        lat = float(row.get('latitude', 0) or 0)
        lon = float(row.get('longitude', 0) or 0)
        
        if lat == 0 or lon == 0:
            return None
            
        return {
            'name': row.get('name', row.get('facility_name', 'Unknown')),
            'address': row.get('address', ''),
            'borough': row.get('borough', ''),
            'hours': row.get('hours', row.get('extended_hours', '')),
            'ada_accessible': row.get('ada_accessible', 'No').lower() in ('yes', 'true', '1'),
            'latitude': lat,
            'longitude': lon
        }
    except (ValueError, TypeError):
        return None

def ingest():
    """Main ingestion function."""
    print("Fetching cooling centers...")
    data = fetch_cooling_centers()
    print(f"Fetched {len(data)} raw records")
    
    records = []
    for row in data:
        transformed = transform_center(row)
        if transformed:
            records.append(transformed)
    
    print(f"Transformed {len(records)} valid records")
    
    if not records:
        print("No valid records to insert")
        return
    
    with get_connection() as conn:
        cur = conn.cursor()
        
        cur.execute("TRUNCATE TABLE cooling_centers")
        
        sql = """
            INSERT INTO cooling_centers (name, address, borough, hours, ada_accessible, geom)
            VALUES (%s, %s, %s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326))
        """
        
        batch = [(r['name'], r['address'], r['borough'], r['hours'], r['ada_accessible'],
                  r['longitude'], r['latitude']) for r in records]
        
        cur.executemany(sql, batch)
        conn.commit()
        
        cur.execute("SELECT COUNT(*) FROM cooling_centers")
        count = cur.fetchone()[0]
        print(f"Inserted {count} cooling centers")
        
        cur.close()

if __name__ == "__main__":
    ingest()
