"""
cultural_query Tool - Cultural Impact Spatial Queries

Queries cultural datasets: film permits, landmarks, museums, public art.
Transforms walks into immersive narrated experiences.

Security:
- No PII exposed
- Read-only queries
- Parameterized SQL (injection-safe)
"""

import os
import logging
from typing import List, Optional
from dataclasses import dataclass
from datetime import datetime

import psycopg2
from psycopg2.extras import RealDictCursor

logger = logging.getLogger(__name__)


@dataclass
class FilmPermit:
    """Active film/TV shoot nearby."""
    event_id: str
    event_type: str
    category: str
    subcategory: Optional[str]
    location: str
    borough: str
    start_time: Optional[str]
    end_time: Optional[str]
    distance_meters: float
    is_active: bool


@dataclass
class Landmark:
    """Historic landmark nearby."""
    name: str
    address: Optional[str]
    borough: Optional[str]
    landmark_type: Optional[str]
    style: Optional[str]
    year_built: Optional[str]
    description: Optional[str]
    distance_meters: float


@dataclass
class CulturalVenue:
    """Museum, gallery, theater nearby."""
    name: str
    venue_type: str
    address: Optional[str]
    website: Optional[str]
    distance_meters: float


@dataclass
class CulturalQueryResult:
    """Combined cultural query result."""
    film_permits: List[FilmPermit]
    landmarks: List[Landmark]
    venues: List[CulturalVenue]
    narrative: Optional[str] = None


def get_db_connection():
    """Get database connection."""
    return psycopg2.connect(
        host=os.environ.get('POSTGRES_HOST', 'localhost'),
        port=int(os.environ.get('POSTGRES_PORT', '5432')),
        database=os.environ.get('POSTGRES_DB', 'metaglass'),
        user=os.environ.get('POSTGRES_USER', 'metaglass'),
        password=os.environ.get('POSTGRES_PASSWORD', ''),
    )


def query_film_permits(
    latitude: float,
    longitude: float,
    radius_meters: int = 500
) -> List[FilmPermit]:
    """
    Query film permits near location.
    
    Returns recent filming activity in the area.
    """
    sql = """
        SELECT 
            event_id, event_type, category, subcategory,
            parking_held as location, borough,
            start_datetime, end_datetime,
            ST_Distance(
                geom::geography,
                ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography
            ) as distance_meters
        FROM film_permits
        WHERE ST_DWithin(
            geom::geography,
            ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography,
            %s
        )
        ORDER BY start_datetime DESC NULLS LAST, distance_meters ASC
        LIMIT 10
    """
    
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, (longitude, latitude, longitude, latitude, radius_meters))
            rows = cur.fetchall()
        conn.close()
        
        results = []
        for row in rows:
            # Check if permit is currently active
            is_active = False
            if row['start_datetime'] and row['end_datetime']:
                now = datetime.now()
                is_active = row['start_datetime'] <= now <= row['end_datetime']
            
            results.append(FilmPermit(
                event_id=row['event_id'],
                event_type=row['event_type'] or 'Filming',
                category=row['category'] or 'Production',
                subcategory=row['subcategory'],
                location=row['location'] or 'Nearby',
                borough=row['borough'] or '',
                start_time=str(row['start_datetime']) if row['start_datetime'] else None,
                end_time=str(row['end_datetime']) if row['end_datetime'] else None,
                distance_meters=float(row['distance_meters'] or 0),
                is_active=is_active
            ))
        
        logger.info(f"Found {len(results)} film permits within {radius_meters}m")
        return results
        
    except psycopg2.Error as e:
        logger.error(f"Film permits query error: {e}")
        return []


def query_landmarks(
    latitude: float,
    longitude: float,
    radius_meters: int = 200
) -> List[Landmark]:
    """
    Query historic landmarks near location.
    
    Returns landmarks with historical context for narration.
    """
    sql = """
        SELECT 
            lpc_name as name,
            address,
            borough,
            landmark_type,
            style,
            year_built,
            description,
            ST_Distance(
                centroid::geography,
                ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography
            ) as distance_meters
        FROM landmarks
        WHERE centroid IS NOT NULL
        AND ST_DWithin(
            centroid::geography,
            ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography,
            %s
        )
        ORDER BY distance_meters ASC
        LIMIT 10
    """
    
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, (longitude, latitude, longitude, latitude, radius_meters))
            rows = cur.fetchall()
        conn.close()
        
        results = []
        for row in rows:
            results.append(Landmark(
                name=row['name'] or 'Historic Site',
                address=row['address'],
                borough=row['borough'],
                landmark_type=row['landmark_type'],
                style=row['style'],
                year_built=row['year_built'],
                description=row['description'],
                distance_meters=float(row['distance_meters'] or 0)
            ))
        
        logger.info(f"Found {len(results)} landmarks within {radius_meters}m")
        return results
        
    except psycopg2.Error as e:
        logger.error(f"Landmarks query error: {e}")
        return []


def query_cultural_venues(
    latitude: float,
    longitude: float,
    radius_meters: int = 500
) -> List[CulturalVenue]:
    """
    Query museums, galleries, theaters near location.
    """
    sql = """
        SELECT 
            name,
            venue_type,
            address,
            website,
            ST_Distance(
                geom::geography,
                ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography
            ) as distance_meters
        FROM cultural_venues
        WHERE ST_DWithin(
            geom::geography,
            ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography,
            %s
        )
        ORDER BY distance_meters ASC
        LIMIT 10
    """
    
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, (longitude, latitude, longitude, latitude, radius_meters))
            rows = cur.fetchall()
        conn.close()
        
        results = []
        for row in rows:
            results.append(CulturalVenue(
                name=row['name'] or 'Cultural Venue',
                venue_type=row['venue_type'] or 'Venue',
                address=row['address'],
                website=row['website'],
                distance_meters=float(row['distance_meters'] or 0)
            ))
        
        logger.info(f"Found {len(results)} cultural venues within {radius_meters}m")
        return results
        
    except psycopg2.Error as e:
        logger.error(f"Cultural venues query error: {e}")
        return []



@dataclass
class WikidataProduction:
    """Named film/TV production with filming location from Wikidata."""
    wikidata_id: str
    production_name: str
    production_type: Optional[str]
    location_name: Optional[str]
    year: Optional[int]
    distance_meters: float


def query_wikidata_productions(
    latitude: float,
    longitude: float,
    radius_meters: int = 500
) -> list:
    """
    Query wikidata_productions table for named films/TV shows filmed near location.

    Args:
        latitude: GPS latitude
        longitude: GPS longitude
        radius_meters: Search radius in meters

    Returns:
        List of WikidataProduction ordered by distance ascending
    """
    # Exclude generic NYC centroid (40.7128, -74.0061) — 998 productions
    # pinned there with no specific location, not useful for "near me" queries
    sql = (
        "SELECT wikidata_id, production_name, production_type, location_name, year, "
        "ST_Distance(geom::geography, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography) AS distance_meters "
        "FROM wikidata_productions "
        "WHERE ST_DWithin(geom::geography, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, %s) "
        "AND NOT (ABS(latitude - 40.7128) < 0.001 AND ABS(longitude - (-74.0061)) < 0.001) "
        "AND NOT (ABS(latitude - 40.7283) < 0.001 AND ABS(longitude - (-73.9942)) < 0.001) "
        "ORDER BY distance_meters ASC LIMIT 15"
    )
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, (longitude, latitude, longitude, latitude, radius_meters))
            rows = cur.fetchall()
        conn.close()

        results = []
        for row in rows:
            results.append(WikidataProduction(
                wikidata_id=row['wikidata_id'],
                production_name=row['production_name'],
                production_type=row['production_type'],
                location_name=row['location_name'],
                year=row['year'],
                distance_meters=float(row['distance_meters'] or 0),
            ))

        logger.info(f"Found {len(results)} Wikidata productions within {radius_meters}m")
        return results

    except psycopg2.Error as e:
        logger.error(f"Wikidata productions query error: {e}")
        return []

def cultural_query(
    latitude: float,
    longitude: float,
    radius_meters: int = 300
) -> CulturalQueryResult:
    """
    Comprehensive cultural query - film, landmarks, venues.
    
    Returns combined results for immersive narration.
    """
    film_permits = query_film_permits(latitude, longitude, radius_meters)
    landmarks = query_landmarks(latitude, longitude, radius_meters)
    venues = query_cultural_venues(latitude, longitude, radius_meters)
    
    # Generate narrative hint
    narrative = None
    if film_permits and film_permits[0].is_active:
        fp = film_permits[0]
        narrative = f"They're filming {fp.category} nearby at {fp.location}."
    elif landmarks:
        lm = landmarks[0]
        narrative = f"You're near {lm.name}, a historic landmark."
    elif venues:
        v = venues[0]
        narrative = f"{v.name} is just {int(v.distance_meters)}m away."
    
    return CulturalQueryResult(
        film_permits=film_permits,
        landmarks=landmarks,
        venues=venues,
        narrative=narrative
    )
