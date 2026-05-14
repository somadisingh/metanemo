"""
Landmarks Data Ingestion (NYC Landmarks Preservation Commission)

Cold dataset: Historic buildings and sites with GeoJSON boundaries.
Refreshed weekly - landmarks don't change often.

Security:
- Public historical data only
- No personal information
- Parameterized queries prevent SQL injection

Source: https://data.cityofnewyork.us/resource/buis-pvji.json
"""

import os
import logging
from typing import Optional
import json

import requests
import psycopg2
from psycopg2.extras import execute_values

import sys
sys.path.insert(0, "../..")
from shared.db import get_connection

logger = logging.getLogger(__name__)

# NYC Open Data endpoint for Individual Landmarks
LANDMARKS_URL = "https://data.cityofnewyork.us/resource/buis-pvji.json"


def sanitize_text(text: Optional[str], max_length: int = 2000) -> Optional[str]:
    """Sanitize text - limit length, normalize whitespace."""
    if not text:
        return None
    text = ' '.join(text.split())
    return text[:max_length] if len(text) > max_length else text


def fetch_landmarks() -> list:
    """
    Fetch landmarks from NYC Open Data.
    
    Returns:
        List of landmark records with GeoJSON geometries
    """
    app_token = os.environ.get('SOCRATA_APP_TOKEN')
    
    headers = {}
    if app_token:
        headers['X-App-Token'] = app_token
    
    params = {
        '$limit': 10000,
        '$select': 'objectid,lpc_name,address,borough,block,lot,bbl,the_geom'
    }
    
    try:
        response = requests.get(
            LANDMARKS_URL,
            headers=headers,
            params=params,
            timeout=60
        )
        response.raise_for_status()
        
        data = response.json()
        logger.info(f"Fetched {len(data)} landmarks")
        return data
        
    except requests.RequestException as e:
        logger.error(f"Failed to fetch landmarks: {e}")
        return []


def geojson_to_wkt(geojson: dict) -> Optional[str]:
    """Convert GeoJSON geometry to WKT format."""
    if not geojson:
        return None
    
    geom_type = geojson.get('type')
    coords = geojson.get('coordinates')
    
    if not geom_type or not coords:
        return None
    
    try:
        if geom_type == 'MultiPolygon':
            # Convert MultiPolygon coordinates to WKT
            polygons = []
            for polygon in coords:
                rings = []
                for ring in polygon:
                    points = ', '.join([f"{p[0]} {p[1]}" for p in ring])
                    rings.append(f"({points})")
                polygons.append(f"({', '.join(rings)})")
            return f"SRID=4326;MULTIPOLYGON({', '.join(polygons)})"
        
        elif geom_type == 'Polygon':
            rings = []
            for ring in coords:
                points = ', '.join([f"{p[0]} {p[1]}" for p in ring])
                rings.append(f"({points})")
            return f"SRID=4326;POLYGON({', '.join(rings)})"
        
        elif geom_type == 'Point':
            return f"SRID=4326;POINT({coords[0]} {coords[1]})"
        
    except Exception as e:
        logger.warning(f"Failed to convert geometry: {e}")
    
    return None


def calculate_centroid(geojson: dict) -> Optional[tuple]:
    """Calculate centroid of a geometry for distance queries."""
    if not geojson:
        return None
    
    coords = geojson.get('coordinates', [])
    geom_type = geojson.get('type')
    
    try:
        all_points = []
        
        if geom_type == 'MultiPolygon':
            for polygon in coords:
                for ring in polygon:
                    all_points.extend(ring)
        elif geom_type == 'Polygon':
            for ring in coords:
                all_points.extend(ring)
        elif geom_type == 'Point':
            return (coords[0], coords[1])
        
        if all_points:
            avg_lon = sum(p[0] for p in all_points) / len(all_points)
            avg_lat = sum(p[1] for p in all_points) / len(all_points)
            return (avg_lon, avg_lat)
            
    except Exception as e:
        logger.warning(f"Failed to calculate centroid: {e}")
    
    return None


def transform_landmark(record: dict) -> Optional[tuple]:
    """Transform raw landmark record to database format."""
    try:
        object_id = record.get('objectid')
        lpc_name = sanitize_text(record.get('lpc_name'))
        
        if not object_id or not lpc_name:
            return None
        
        geojson = record.get('the_geom')
        wkt = geojson_to_wkt(geojson)
        centroid = calculate_centroid(geojson)
        
        return (
            object_id,
            lpc_name,
            sanitize_text(record.get('address')),
            record.get('borough'),
            record.get('block'),
            record.get('lot'),
            record.get('bbl'),
            wkt,
            centroid[0] if centroid else None,
            centroid[1] if centroid else None
        )
        
    except Exception as e:
        logger.warning(f"Failed to transform landmark {record.get('objectid')}: {e}")
        return None


def run_landmarks_ingestion() -> dict:
    """
    Run landmarks ingestion pipeline.
    
    Returns:
        Dict with success status and counts
    """
    logger.info("Starting landmarks ingestion")
    
    # Fetch data
    landmarks = fetch_landmarks()
    if not landmarks:
        return {'success': False, 'error': 'No data fetched'}
    
    # Transform
    records = []
    for landmark in landmarks:
        transformed = transform_landmark(landmark)
        if transformed:
            records.append(transformed)
    
    logger.info(f"Transformed {len(records)} landmarks")
    
    if not records:
        return {'success': False, 'error': 'No valid records'}
    
    # Load to database
    sql = """
        INSERT INTO landmarks (
            object_id, lpc_name, address, borough, block, lot, bbl,
            geom, centroid
        ) VALUES %s
        ON CONFLICT (object_id) DO UPDATE SET
            lpc_name = EXCLUDED.lpc_name,
            address = EXCLUDED.address,
            borough = EXCLUDED.borough,
            block = EXCLUDED.block,
            lot = EXCLUDED.lot,
            bbl = EXCLUDED.bbl,
            geom = EXCLUDED.geom,
            centroid = EXCLUDED.centroid
    """
    
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                # Use raw SQL for geometry handling
                for r in records:
                    try:
                        if r[7]:  # Has geometry
                            cur.execute("""
                                INSERT INTO landmarks (object_id, lpc_name, address, borough, block, lot, bbl, geom, centroid)
                                VALUES (%s, %s, %s, %s, %s, %s, %s, ST_GeomFromEWKT(%s), ST_SetSRID(ST_MakePoint(%s, %s), 4326))
                                ON CONFLICT (object_id) DO UPDATE SET
                                    lpc_name = EXCLUDED.lpc_name,
                                    address = EXCLUDED.address,
                                    geom = EXCLUDED.geom,
                                    centroid = EXCLUDED.centroid
                            """, (r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9]))
                    except Exception as e:
                        logger.warning(f"Failed to insert landmark {r[0]}: {e}")
                        continue

                conn.commit()

        logger.info(f"Upserted {len(records)} landmarks")
        return {'success': True, 'upserted_count': len(records)}
        
    except psycopg2.Error as e:
        logger.error(f"Database error: {e}")
        return {'success': False, 'error': str(e)}


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    result = run_landmarks_ingestion()
    print(result)
