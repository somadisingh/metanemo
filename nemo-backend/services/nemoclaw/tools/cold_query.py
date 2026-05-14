"""
cold_query Tool - PostGIS Spatial Queries for Cold Data

Queries cached PostGIS spatial data for restaurants, collisions,
and other static datasets near GPS coordinates using ST_DWithin.

Property 15: cold_query Result Structure and Hazard Flag
Property 16: cold_query DB Error Raises Structured Error
"""

import os
import logging
from typing import List, Optional

import psycopg2
from psycopg2.extras import RealDictCursor

from .types import ColdQueryResult, CollisionResult, HazardType

logger = logging.getLogger(__name__)


def get_db_connection():
    """Get database connection from environment."""
    return psycopg2.connect(
        host=os.environ.get('POSTGRES_HOST', 'localhost'),
        port=int(os.environ.get('POSTGRES_PORT', '5432')),
        database=os.environ.get('POSTGRES_DB', 'metaglass'),
        user=os.environ.get('POSTGRES_USER', 'metaglass'),
        password=os.environ.get('POSTGRES_PASSWORD', ''),
    )


def cold_query(
    latitude: float,
    longitude: float,
    radius_meters: int = 500
) -> List[ColdQueryResult]:
    """
    Query PostGIS restaurants table for records within radius_meters of the
    given coordinates using ST_DWithin on a GIST-indexed geometry column.
    
    Returns a list of dicts with keys: name, address, grade, score, hazard.
    Raises RuntimeError on database exception.
    
    Args:
        latitude: GPS latitude (WGS84)
        longitude: GPS longitude (WGS84)
        radius_meters: Search radius in meters (default 500)
        
    Returns:
        List of ColdQueryResult with restaurant hazard data
        
    Raises:
        RuntimeError: On database exception (includes input params per Property 16)
    """
    sql = """
        WITH ranked AS (
            SELECT 
                dba as name,
                CONCAT(building, ' ', street, ', ', zipcode) as address,
                grade,
                score,
                cuisine_description,
                (grade = 'C' OR score > 28) AS hazard,
                ST_Distance(
                    geom::geography,
                    ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography
                ) as distance_meters,
                ROW_NUMBER() OVER (
                    PARTITION BY COALESCE(camis, dba)
                    ORDER BY inspection_date DESC NULLS LAST, score DESC NULLS LAST
                ) AS rn
            FROM restaurants
            WHERE ST_DWithin(
                geom::geography,
                ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography,
                %s
            )
            AND inspection_date > CURRENT_DATE - INTERVAL '1 year'
        )
        SELECT name, address, grade, score, cuisine_description, hazard, distance_meters
        FROM ranked
        WHERE rn = 1
        ORDER BY distance_meters ASC
        LIMIT 20
    """
    
    params = (longitude, latitude, longitude, latitude, radius_meters)
    
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        conn.close()
        
        results = []
        for row in rows:
            result = ColdQueryResult(
                name=row['name'] or 'Unknown',
                address=row['address'] or '',
                grade=row['grade'],
                score=row['score'],
                cuisine_description=row.get('cuisine_description'),
                hazard=bool(row['hazard']),
                distance_meters=float(row['distance_meters'] or 0),
                hazard_type=HazardType.RESTAURANT
            )
            results.append(result)
        
        logger.info(f"cold_query found {len(results)} restaurants within {radius_meters}m")
        return results
        
    except psycopg2.Error as e:
        error_msg = (
            f"cold_query database error: {e}. "
            f"Input params: latitude={latitude}, longitude={longitude}, radius_meters={radius_meters}"
        )
        logger.error(error_msg)
        raise RuntimeError(error_msg) from e


def query_collision_hotspots(
    latitude: float,
    longitude: float,
    radius_meters: int = 500
) -> List[CollisionResult]:
    """
    Query collision hotspots near coordinates.
    
    Args:
        latitude: GPS latitude
        longitude: GPS longitude
        radius_meters: Search radius
        
    Returns:
        List of CollisionResult with pedestrian collision data
    """
    sql = """
        SELECT 
            COALESCE(on_street_name, '') || 
            CASE WHEN cross_street_name IS NOT NULL 
                 THEN ' & ' || cross_street_name 
                 ELSE '' 
            END as location,
            crash_date::text,
            number_of_pedestrians_injured,
            number_of_pedestrians_killed,
            collision_severity(
                number_of_persons_injured, number_of_persons_killed,
                number_of_pedestrians_injured, number_of_pedestrians_killed
            ) as severity_score,
            ST_Distance(
                geom::geography,
                ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography
            ) as distance_meters
        FROM collisions
        WHERE ST_DWithin(
            geom::geography,
            ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography,
            %s
        )
        AND crash_date > CURRENT_DATE - INTERVAL '3 years'
        AND (number_of_pedestrians_injured > 0 OR number_of_pedestrians_killed > 0)
        ORDER BY severity_score DESC, distance_meters ASC
        LIMIT 10
    """
    
    params = (longitude, latitude, longitude, latitude, radius_meters)
    
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        conn.close()
        
        results = []
        for row in rows:
            result = CollisionResult(
                location=row['location'] or 'Unknown intersection',
                crash_date=row['crash_date'] or '',
                pedestrians_injured=row['number_of_pedestrians_injured'] or 0,
                pedestrians_killed=row['number_of_pedestrians_killed'] or 0,
                severity_score=row['severity_score'] or 0,
                distance_meters=float(row['distance_meters'] or 0)
            )
            results.append(result)
        
        logger.info(f"Found {len(results)} collision hotspots within {radius_meters}m")
        return results
        
    except psycopg2.Error as e:
        error_msg = f"Collision query error: {e}. Params: lat={latitude}, lon={longitude}"
        logger.error(error_msg)
        raise RuntimeError(error_msg) from e


def query_heat_vulnerability(
    latitude: float,
    longitude: float
) -> Optional[dict]:
    """
    Query Heat Vulnerability Index for the location.
    
    Args:
        latitude: GPS latitude
        longitude: GPS longitude
        
    Returns:
        Dict with HVI data or None if not found
    """
    sql = """
        SELECT 
            nta_name,
            hvi_score,
            surface_temp,
            green_space_pct
        FROM heat_vulnerability_index
        WHERE ST_Contains(
            geom,
            ST_SetSRID(ST_MakePoint(%s, %s), 4326)
        )
        LIMIT 1
    """
    
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, (longitude, latitude))
            row = cur.fetchone()
        conn.close()
        
        if row:
            return {
                'neighborhood': row['nta_name'],
                'hvi_score': row['hvi_score'],
                'surface_temp': float(row['surface_temp']) if row['surface_temp'] else None,
                'green_space_pct': float(row['green_space_pct']) if row['green_space_pct'] else None
            }
        return None
        
    except psycopg2.Error as e:
        logger.error(f"HVI query error: {e}")
        return None


def query_accessibility(
    latitude: float,
    longitude: float,
    radius_meters: int = 200
) -> dict:
    """
    Query accessibility features near coordinates.
    
    Args:
        latitude: GPS latitude
        longitude: GPS longitude
        radius_meters: Search radius
        
    Returns:
        Dict with accessibility data
    """
    # Query pedestrian ramps
    ramps_sql = """
        SELECT COUNT(*) as total,
               COUNT(*) FILTER (WHERE ada_compliant = true) as ada_compliant
        FROM pedestrian_mobility
        WHERE ST_DWithin(
            geom::geography,
            ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography,
            %s
        )
    """
    
    # Query subway entrances
    subway_sql = """
        SELECT station_name, line, ada, ada_notes,
               ST_Distance(
                   geom::geography,
                   ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography
               ) as distance_meters
        FROM subway_entrances
        WHERE ST_DWithin(
            geom::geography,
            ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography,
            %s
        )
        ORDER BY distance_meters
        LIMIT 5
    """
    
    result = {
        'ramps_total': 0,
        'ramps_ada': 0,
        'subway_entrances': []
    }
    
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Ramps
            cur.execute(ramps_sql, (longitude, latitude, radius_meters))
            ramps = cur.fetchone()
            if ramps:
                result['ramps_total'] = ramps['total'] or 0
                result['ramps_ada'] = ramps['ada_compliant'] or 0
            
            # Subway
            cur.execute(subway_sql, (longitude, latitude, longitude, latitude, radius_meters))
            for row in cur.fetchall():
                result['subway_entrances'].append({
                    'station': row['station_name'],
                    'line': row['line'],
                    'ada': bool(row['ada']),
                    'ada_notes': row['ada_notes'],
                    'distance_meters': float(row['distance_meters'] or 0)
                })
        
        conn.close()
        return result
        
    except psycopg2.Error as e:
        logger.error(f"Accessibility query error: {e}")
        return result


def cold_query_by_cuisine(
    latitude: float,
    longitude: float,
    cuisine: str = None,
    radius_meters: int = 800
) -> List[ColdQueryResult]:
    """
    Query restaurants filtered by cuisine type, ordered by distance.

    Args:
        latitude: GPS latitude
        longitude: GPS longitude
        cuisine: Cuisine description to filter by (partial match, case-insensitive)
        radius_meters: Search radius in meters (default 800)

    Returns:
        List of ColdQueryResult ordered by distance ascending, with name/grade/score/distance
    """
    # Build SQL as a plain string (no f-string) to avoid psycopg2 % conflict
    sql = (
        "WITH ranked AS ("
        "SELECT dba as name, "
        "CONCAT(building, ' ', street, ', ', zipcode) as address, "
        "grade, score, cuisine_description, "
        "(grade = 'C' OR score > 28) AS hazard, "
        "ST_Distance(geom::geography, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography) as distance_meters, "
        "ROW_NUMBER() OVER (PARTITION BY COALESCE(camis, dba) "
        "ORDER BY inspection_date DESC NULLS LAST, score DESC NULLS LAST) AS rn "
        "FROM restaurants "
        "WHERE ST_DWithin(geom::geography, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, %s) "
    )
    params = [longitude, latitude, longitude, latitude, radius_meters]

    if cuisine:
        sql += "AND LOWER(cuisine_description) LIKE LOWER(%s) "
        params.append("%" + cuisine + "%")

    sql += (
        "AND inspection_date > CURRENT_DATE - INTERVAL '1 year'"
        ") "
        "SELECT name, address, grade, score, cuisine_description, hazard, distance_meters "
        "FROM ranked WHERE rn = 1 "
        "ORDER BY distance_meters ASC LIMIT 15"
    )

    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        conn.close()

        results = []
        for row in rows:
            result = ColdQueryResult(
                name=row['name'] or 'Unknown',
                address=row['address'] or '',
                grade=row['grade'],
                score=row['score'],
                cuisine_description=row.get('cuisine_description'),
                hazard=bool(row['hazard']),
                distance_meters=float(row['distance_meters'] or 0),
                hazard_type=HazardType.RESTAURANT
            )
            results.append(result)

        logger.info(f"cold_query_by_cuisine found {len(results)} {cuisine or 'any'} restaurants within {radius_meters}m")
        return results

    except psycopg2.Error as e:
        error_msg = f"cold_query_by_cuisine error: {e}. cuisine={cuisine}, lat={latitude}, lon={longitude}"
        logger.error(error_msg)
        raise RuntimeError(error_msg) from e
