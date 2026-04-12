"""
hot_query Tool - Live API Queries for Real-Time Data

Fetches live NYC 311 Socrata complaints and MTA GTFS-RT alerts
near GPS coordinates. Merges and sorts results by distance.

Property 17: hot_query Result Structure and Distance Ordering
Property 18: hot_query Partial Result on Single API Failure
"""

import os
import logging
from datetime import datetime, timedelta
from typing import List, Tuple
from dataclasses import asdict

import requests
import psycopg2
from psycopg2.extras import RealDictCursor

from .types import HotQueryItem, HotQueryResult, Severity

logger = logging.getLogger(__name__)

# API endpoints
SOCRATA_311_ENDPOINT = "https://data.cityofnewyork.us/resource/erm2-nwe9.json"
MTA_ALERTS_ENDPOINT = "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/camsys%2Fsubway-alerts"


def get_db_connection():
    """Get database connection."""
    return psycopg2.connect(
        host=os.environ.get('POSTGRES_HOST', 'localhost'),
        port=int(os.environ.get('POSTGRES_PORT', '5432')),
        database=os.environ.get('POSTGRES_DB', 'metaglass'),
        user=os.environ.get('POSTGRES_USER', 'metaglass'),
        password=os.environ.get('POSTGRES_PASSWORD', ''),
    )


def calculate_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculate approximate distance in meters between two points.
    Uses simple equirectangular approximation (good enough for NYC scale).
    """
    import math
    
    R = 6371000  # Earth radius in meters
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    
    x = (lon2 - lon1) * math.cos((lat1_rad + lat2_rad) / 2)
    y = lat2 - lat1
    
    return R * math.sqrt(x*x + y*y) * math.pi / 180


def classify_severity(complaint_type: str, descriptor: str = "") -> Severity:
    """Classify severity based on complaint type."""
    high_severity = ['Scaffold Safety', 'Hazardous Materials', 'Gas Leak', 'Fire']
    medium_severity = ['Construction', 'Street Condition', 'Sidewalk Condition', 'Air Quality']
    
    combined = f"{complaint_type} {descriptor}".lower()
    
    if any(h.lower() in combined for h in high_severity):
        return Severity.HIGH
    if any(m.lower() in combined for m in medium_severity):
        return Severity.MEDIUM
    return Severity.LOW


def fetch_311_live(
    latitude: float,
    longitude: float,
    radius_meters: int = 500
) -> Tuple[List[HotQueryItem], bool]:
    """
    Fetch live 311 complaints from Socrata API.
    
    Args:
        latitude: GPS latitude
        longitude: GPS longitude
        radius_meters: Search radius
        
    Returns:
        Tuple of (list of HotQueryItem, success boolean)
    """
    app_token = os.environ.get('SOCRATA_APP_TOKEN')
    
    headers = {}
    if app_token:
        headers['X-App-Token'] = app_token
    
    # Time filter - last 24 hours
    since = (datetime.now() - timedelta(hours=24)).strftime('%Y-%m-%dT%H:%M:%S')
    
    # Socrata proximity filter
    params = {
        '$limit': 50,
        '$where': f"created_date >= '{since}' AND within_circle(location, {latitude}, {longitude}, {radius_meters})",
        '$order': 'created_date DESC'
    }
    
    try:
        response = requests.get(
            SOCRATA_311_ENDPOINT,
            headers=headers,
            params=params,
            timeout=10
        )
        response.raise_for_status()
        
        data = response.json()
        results = []
        
        for item in data:
            # Extract coordinates
            location = item.get('location', {})
            item_lat = float(location.get('latitude', 0)) if location else 0
            item_lon = float(location.get('longitude', 0)) if location else 0
            
            if item_lat == 0 or item_lon == 0:
                continue
            
            distance = calculate_distance(latitude, longitude, item_lat, item_lon)
            complaint_type = item.get('complaint_type', 'Unknown')
            descriptor = item.get('descriptor', '')
            
            results.append(HotQueryItem(
                type="311",
                description=f"{complaint_type}: {descriptor}" if descriptor else complaint_type,
                severity=classify_severity(complaint_type, descriptor),
                distance_meters=distance,
                complaint_type=complaint_type,
                status=item.get('status', 'Open')
            ))
        
        logger.info(f"Fetched {len(results)} live 311 complaints")
        return results, True
        
    except requests.RequestException as e:
        logger.error(f"311 API error: {e}")
        return [], False


def fetch_311_cached(
    latitude: float,
    longitude: float,
    radius_meters: int = 500
) -> List[HotQueryItem]:
    """
    Fetch 311 complaints from local PostGIS cache.
    Fallback when live API is unavailable.
    """
    sql = """
        SELECT 
            complaint_type,
            descriptor,
            status,
            ST_Distance(
                geom::geography,
                ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography
            ) as distance_meters
        FROM complaints_311
        WHERE ST_DWithin(
            geom::geography,
            ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography,
            %s
        )
        AND status NOT IN ('Closed')
        ORDER BY distance_meters
        LIMIT 50
    """
    
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, (longitude, latitude, longitude, latitude, radius_meters))
            rows = cur.fetchall()
        conn.close()
        
        results = []
        for row in rows:
            complaint_type = row['complaint_type'] or 'Unknown'
            descriptor = row['descriptor'] or ''
            
            results.append(HotQueryItem(
                type="311",
                description=f"{complaint_type}: {descriptor}" if descriptor else complaint_type,
                severity=classify_severity(complaint_type, descriptor),
                distance_meters=float(row['distance_meters'] or 0),
                complaint_type=complaint_type,
                status=row['status']
            ))
        
        return results
        
    except psycopg2.Error as e:
        logger.error(f"311 cache query error: {e}")
        return []


def get_nearby_subway_routes(latitude: float, longitude: float, radius_meters: int = 400) -> Tuple[set, List[dict]]:
    """
    Find subway routes serving stations near the user's location.
    
    Args:
        latitude: GPS latitude
        longitude: GPS longitude
        radius_meters: Search radius (default 400m ~ 5 min walk)
        
    Returns:
        Tuple of (set of route letters, list of nearby station info)
    """
    sql = """
        SELECT 
            station_name,
            line,
            ada,
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
        LIMIT 10
    """
    
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, (longitude, latitude, longitude, latitude, radius_meters))
            rows = cur.fetchall()
        conn.close()
        
        routes = set()
        stations = []
        seen_stations = set()
        
        for row in rows:
            station_name = row['station_name']
            line = row['line'] or ''
            
            # Parse space-separated route letters (e.g., "A C E" -> {'A', 'C', 'E'})
            for route in line.split():
                routes.add(route.strip())
            
            # Track unique stations
            if station_name not in seen_stations:
                seen_stations.add(station_name)
                stations.append({
                    'name': station_name,
                    'lines': line,
                    'ada': row['ada'],
                    'distance': float(row['distance_meters'])
                })
        
        logger.info(f"Found {len(stations)} nearby stations with routes: {routes}")
        return routes, stations
        
    except psycopg2.Error as e:
        logger.error(f"Subway query error: {e}")
        return set(), []


def fetch_mta_alerts(
    latitude: float,
    longitude: float
) -> Tuple[List[HotQueryItem], bool]:
    """
    Fetch MTA service alerts from GTFS-RT feed, prioritized by nearby stations.
    
    Uses the public JSON endpoint (no API key required for alerts).
    Alerts include: service changes, delays, station bypasses, planned work.
    
    Alerts affecting routes at nearby stations are prioritized and marked as relevant.
    
    Args:
        latitude: GPS latitude
        longitude: GPS longitude
        
    Returns:
        Tuple of (list of HotQueryItem, success boolean)
    """
    # Get routes serving nearby stations
    nearby_routes, nearby_stations = get_nearby_subway_routes(latitude, longitude)
    
    # Public JSON endpoint - no API key needed for alerts
    alerts_url = "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/camsys%2Fsubway-alerts.json"
    
    try:
        response = requests.get(alerts_url, timeout=15)
        response.raise_for_status()
        
        data = response.json()
        relevant_alerts = []
        other_alerts = []
        entities = data.get('entity', [])
        
        for entity in entities:
            alert = entity.get('alert', {})
            
            # Get header text (plain text version)
            header = alert.get('header_text', {})
            translations = header.get('translation', [])
            text = ''
            for t in translations:
                if t.get('language') == 'en':
                    text = t.get('text', '')
                    break
            if not text and translations:
                text = translations[0].get('text', 'Service Alert')
            
            # Extract affected routes
            informed = alert.get('informed_entity', [])
            routes = set()
            for ie in informed:
                route = ie.get('route_id')
                if route:
                    routes.add(route)
            
            # Check if this alert affects nearby routes
            is_relevant = bool(routes & nearby_routes)
            
            # Determine severity based on alert content
            text_lower = text.lower()
            if any(x in text_lower for x in ['bypass', 'no service', 'suspended', 'not running', 'skip']):
                severity = Severity.HIGH
            elif any(x in text_lower for x in ['delay', 'slow', 'crowding', 'reroute']):
                severity = Severity.MEDIUM
            else:
                severity = Severity.LOW
            
            # Boost severity for relevant alerts
            if is_relevant and severity == Severity.LOW:
                severity = Severity.MEDIUM
            
            # Format description with routes
            route_str = f"[{']['.join(sorted(routes))}] " if routes else ""
            relevance_marker = "⚠️ " if is_relevant else ""
            description = f"{relevance_marker}{route_str}{text[:180]}"
            
            item = HotQueryItem(
                type="MTA",
                description=description,
                severity=severity,
                distance_meters=0 if is_relevant else 1000,  # Relevant alerts sort first
                status="active",
                complaint_type=",".join(sorted(routes)) if routes else "subway"
            )
            
            if is_relevant:
                relevant_alerts.append(item)
            else:
                other_alerts.append(item)
        
        # Combine: relevant alerts first (up to 15), then other high-severity (up to 5)
        results = relevant_alerts[:15]
        high_severity_others = [a for a in other_alerts if a.severity == Severity.HIGH][:5]
        results.extend(high_severity_others)
        
        logger.info(f"Fetched {len(relevant_alerts)} relevant + {len(high_severity_others)} other MTA alerts")
        return results, True
        
    except requests.RequestException as e:
        logger.error(f"MTA API error: {e}")
        return [], False


def hot_query(
    latitude: float,
    longitude: float
) -> HotQueryResult:
    """
    Fetch live NYC 311 and MTA GTFS-RT data near GPS coordinates.
    Merges and sorts results by distance_meters ascending.
    
    Returns dict with keys: results (list), partial (bool).
    Raises RuntimeError if both APIs are unreachable.
    
    Args:
        latitude: GPS latitude
        longitude: GPS longitude
        
    Returns:
        HotQueryResult with merged and sorted results
        
    Raises:
        RuntimeError: If both APIs fail (full data unavailability)
    """
    all_results = []
    error_sources = []
    
    # Fetch 311 data (try live first, fall back to cache)
    complaints_311, success_311 = fetch_311_live(latitude, longitude)
    if success_311:
        all_results.extend(complaints_311)
    else:
        # Try cached data
        cached_311 = fetch_311_cached(latitude, longitude)
        if cached_311:
            all_results.extend(cached_311)
        else:
            error_sources.append("311")
    
    # Fetch MTA alerts
    mta_alerts, success_mta = fetch_mta_alerts(latitude, longitude)
    if success_mta:
        all_results.extend(mta_alerts)
    else:
        error_sources.append("MTA")
    
    # Check for full failure (Property 18)
    # Only raise if we have NO results at all
    if len(all_results) == 0 and len(error_sources) == 2:
        raise RuntimeError(
            f"Full data unavailability: both 311 and MTA APIs unreachable. "
            f"Params: latitude={latitude}, longitude={longitude}"
        )
    
    # Sort by distance (Property 17)
    all_results.sort(key=lambda x: x.distance_meters)
    
    partial = len(error_sources) > 0
    
    logger.info(
        f"hot_query returned {len(all_results)} results "
        f"(partial={partial}, errors={error_sources})"
    )
    
    return HotQueryResult(
        results=all_results,
        partial=partial,
        error_sources=error_sources
    )
