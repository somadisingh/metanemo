"""
Edge Case Handler - Handles unusual situations and data anomalies.

Covers scenarios users might face that require special handling:
1. Location-based edge cases (parks, water, tunnels, airports)
2. Time-based edge cases (late night, rush hour, holidays)
3. Data quality issues (missing data, stale data, conflicting info)
4. Transit edge cases (transfers, express/local, weekend service)
5. Safety edge cases (multiple hazards, emergency situations)
6. Accessibility edge cases (wheelchair users, vision impaired)
"""

import os
import logging
from datetime import datetime, time
from typing import Optional, List, Dict, Any, Tuple
from dataclasses import dataclass
from enum import Enum

import psycopg2
from psycopg2.extras import RealDictCursor

from .types import Severity, HotQueryItem

logger = logging.getLogger(__name__)


class EdgeCaseType(str, Enum):
    """Types of edge cases detected."""
    NO_SUBWAY_NEARBY = "no_subway_nearby"
    LATE_NIGHT = "late_night"
    RUSH_HOUR = "rush_hour"
    WEEKEND_SERVICE = "weekend_service"
    HOLIDAY_SERVICE = "holiday_service"
    IN_PARK = "in_park"
    NEAR_WATER = "near_water"
    AIRPORT_AREA = "airport_area"
    TUNNEL_AREA = "tunnel_area"
    MULTIPLE_HAZARDS = "multiple_hazards"
    NO_ADA_ACCESS = "no_ada_access"
    STALE_DATA = "stale_data"
    HIGH_CRIME_AREA = "high_crime_area"
    CONSTRUCTION_ZONE = "construction_zone"
    SCHOOL_ZONE = "school_zone"
    HOSPITAL_ZONE = "hospital_zone"


@dataclass
class EdgeCaseAlert:
    """Alert generated from edge case detection."""
    case_type: EdgeCaseType
    message: str
    severity: Severity
    recommendation: str
    affected_services: List[str] = None
    
    def __post_init__(self):
        if self.affected_services is None:
            self.affected_services = []


def get_db_connection():
    """Get database connection."""
    return psycopg2.connect(
        host=os.environ.get('POSTGRES_HOST', 'localhost'),
        port=int(os.environ.get('POSTGRES_PORT', '5432')),
        database=os.environ.get('POSTGRES_DB', 'metaglass'),
        user=os.environ.get('POSTGRES_USER', 'metaglass'),
        password=os.environ.get('POSTGRES_PASSWORD', ''),
    )


# ============================================================================
# TIME-BASED EDGE CASES
# ============================================================================

def check_time_based_cases() -> List[EdgeCaseAlert]:
    """Check for time-based edge cases."""
    alerts = []
    now = datetime.now()
    current_time = now.time()
    weekday = now.weekday()  # 0=Monday, 6=Sunday
    
    # Late night (midnight to 5am) - reduced service, safety concerns
    if time(0, 0) <= current_time <= time(5, 0):
        alerts.append(EdgeCaseAlert(
            case_type=EdgeCaseType.LATE_NIGHT,
            message="Late night hours - subway runs less frequently",
            severity=Severity.MEDIUM,
            recommendation="Check train arrival times. Consider rideshare for safety.",
            affected_services=["subway", "bus"]
        ))
    
    # Rush hour (7-9am, 5-7pm weekdays)
    is_morning_rush = time(7, 0) <= current_time <= time(9, 30)
    is_evening_rush = time(16, 30) <= current_time <= time(19, 0)
    if weekday < 5 and (is_morning_rush or is_evening_rush):
        alerts.append(EdgeCaseAlert(
            case_type=EdgeCaseType.RUSH_HOUR,
            message="Rush hour - expect crowded trains and delays",
            severity=Severity.LOW,
            recommendation="Allow extra travel time. Express trains may be faster.",
            affected_services=["subway", "bus"]
        ))
    
    # Weekend service changes
    if weekday >= 5:  # Saturday or Sunday
        alerts.append(EdgeCaseAlert(
            case_type=EdgeCaseType.WEEKEND_SERVICE,
            message="Weekend service - some lines run differently",
            severity=Severity.LOW,
            recommendation="Check for planned work. Some stations may be skipped.",
            affected_services=["subway"]
        ))
    
    return alerts


# ============================================================================
# LOCATION-BASED EDGE CASES
# ============================================================================

def check_location_based_cases(
    latitude: float,
    longitude: float
) -> List[EdgeCaseAlert]:
    """Check for location-based edge cases."""
    alerts = []
    
    # Check if near airports
    airports = [
        {"name": "JFK", "lat": 40.6413, "lon": -73.7781, "radius": 3000},
        {"name": "LaGuardia", "lat": 40.7769, "lon": -73.8740, "radius": 2000},
        {"name": "Newark", "lat": 40.6895, "lon": -74.1745, "radius": 3000},
    ]
    
    for airport in airports:
        dist = _haversine_distance(latitude, longitude, airport["lat"], airport["lon"])
        if dist < airport["radius"]:
            alerts.append(EdgeCaseAlert(
                case_type=EdgeCaseType.AIRPORT_AREA,
                message=f"Near {airport['name']} Airport",
                severity=Severity.LOW,
                recommendation="AirTrain available. Check terminal before heading to gate.",
                affected_services=["airtrain", "subway"]
            ))
            break
    
    # Check if in Central Park or major parks (limited transit)
    parks = [
        {"name": "Central Park", "lat": 40.7829, "lon": -73.9654, "radius": 1500},
        {"name": "Prospect Park", "lat": 40.6602, "lon": -73.9690, "radius": 800},
        {"name": "Flushing Meadows", "lat": 40.7400, "lon": -73.8408, "radius": 1000},
    ]
    
    for park in parks:
        dist = _haversine_distance(latitude, longitude, park["lat"], park["lon"])
        if dist < park["radius"]:
            alerts.append(EdgeCaseAlert(
                case_type=EdgeCaseType.IN_PARK,
                message=f"Inside {park['name']} - limited transit access",
                severity=Severity.LOW,
                recommendation="Head to park perimeter for subway access.",
                affected_services=["subway"]
            ))
            break
    
    # Check if near water (limited crossing options)
    # East River, Hudson River areas
    water_zones = [
        {"name": "East River waterfront", "lat": 40.7075, "lon": -73.9975, "radius": 200},
        {"name": "Hudson River waterfront", "lat": 40.7580, "lon": -74.0000, "radius": 200},
    ]
    
    for zone in water_zones:
        dist = _haversine_distance(latitude, longitude, zone["lat"], zone["lon"])
        if dist < zone["radius"]:
            alerts.append(EdgeCaseAlert(
                case_type=EdgeCaseType.NEAR_WATER,
                message=f"Near {zone['name']}",
                severity=Severity.LOW,
                recommendation="Ferry service may be available. Check NYC Ferry app.",
                affected_services=["ferry"]
            ))
            break
    
    return alerts


def _haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance in meters between two points."""
    import math
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1-a))


# ============================================================================
# TRANSIT-SPECIFIC EDGE CASES
# ============================================================================

def check_transit_edge_cases(
    latitude: float,
    longitude: float,
    nearby_routes: set,
    mta_alerts: List[HotQueryItem]
) -> List[EdgeCaseAlert]:
    """Check for transit-specific edge cases."""
    alerts = []
    
    # No subway nearby
    if not nearby_routes:
        alerts.append(EdgeCaseAlert(
            case_type=EdgeCaseType.NO_SUBWAY_NEARBY,
            message="No subway stations within walking distance",
            severity=Severity.MEDIUM,
            recommendation="Consider bus or rideshare. Check Citibike availability.",
            affected_services=["subway"]
        ))
    
    # Multiple lines affected (transfer problems)
    affected_routes = set()
    for alert in mta_alerts:
        if alert.severity in [Severity.HIGH, Severity.MEDIUM]:
            routes = alert.complaint_type.split(",") if alert.complaint_type else []
            affected_routes.update(routes)
    
    # Check if user's nearby routes are heavily affected
    user_affected = nearby_routes & affected_routes
    if len(user_affected) >= 2:
        alerts.append(EdgeCaseAlert(
            case_type=EdgeCaseType.MULTIPLE_HAZARDS,
            message=f"Multiple lines affected: {', '.join(sorted(user_affected))}",
            severity=Severity.HIGH,
            recommendation="Consider alternative routes or transportation modes.",
            affected_services=list(user_affected)
        ))
    
    # Express/Local confusion - common lines
    express_local_lines = {'2', '3', '4', '5', 'A', 'D', 'N', 'Q'}
    if nearby_routes & express_local_lines:
        # Check if there are skip alerts
        for alert in mta_alerts:
            if 'express' in alert.description.lower() or 'local' in alert.description.lower():
                alerts.append(EdgeCaseAlert(
                    case_type=EdgeCaseType.CONSTRUCTION_ZONE,
                    message="Express/Local service change in effect",
                    severity=Severity.MEDIUM,
                    recommendation="Verify if your stop is being served. Check platform signs.",
                    affected_services=list(nearby_routes & express_local_lines)
                ))
                break
    
    return alerts


# ============================================================================
# ACCESSIBILITY EDGE CASES
# ============================================================================

def check_accessibility_cases(
    latitude: float,
    longitude: float,
    require_ada: bool = False
) -> List[EdgeCaseAlert]:
    """Check for accessibility-related edge cases."""
    alerts = []
    
    if not require_ada:
        return alerts
    
    sql = """
        SELECT station_name, line, ada,
               ST_Distance(
                   geom::geography,
                   ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography
               ) as distance_meters
        FROM subway_entrances
        WHERE ST_DWithin(
            geom::geography,
            ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography,
            800
        )
        ORDER BY distance_meters
        LIMIT 10
    """
    
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, (longitude, latitude, longitude, latitude))
            rows = cur.fetchall()
        conn.close()
        
        ada_stations = [r for r in rows if r['ada']]
        non_ada_stations = [r for r in rows if not r['ada']]
        
        if not ada_stations and non_ada_stations:
            nearest_non_ada = non_ada_stations[0]
            alerts.append(EdgeCaseAlert(
                case_type=EdgeCaseType.NO_ADA_ACCESS,
                message=f"Nearest station ({nearest_non_ada['station_name']}) is not ADA accessible",
                severity=Severity.HIGH,
                recommendation="Seek alternative accessible station or use Access-A-Ride.",
                affected_services=["subway"]
            ))
        elif ada_stations:
            nearest_ada = ada_stations[0]
            if nearest_ada['distance_meters'] > 400:
                alerts.append(EdgeCaseAlert(
                    case_type=EdgeCaseType.NO_ADA_ACCESS,
                    message=f"Nearest ADA station is {int(nearest_ada['distance_meters'])}m away",
                    severity=Severity.MEDIUM,
                    recommendation=f"ADA access at {nearest_ada['station_name']} ({nearest_ada['line']})",
                    affected_services=["subway"]
                ))
                
    except psycopg2.Error as e:
        logger.error(f"Accessibility check error: {e}")
    
    return alerts


# ============================================================================
# SAFETY EDGE CASES
# ============================================================================

def check_safety_cases(
    latitude: float,
    longitude: float,
    complaints_311: List[HotQueryItem],
    collisions: List[dict]
) -> List[EdgeCaseAlert]:
    """Check for safety-related edge cases."""
    alerts = []
    
    # Multiple hazards in area
    high_severity_count = sum(
        1 for c in complaints_311 
        if c.severity in [Severity.HIGH, Severity.CRITICAL]
    )
    
    if high_severity_count >= 3:
        alerts.append(EdgeCaseAlert(
            case_type=EdgeCaseType.MULTIPLE_HAZARDS,
            message=f"{high_severity_count} active safety concerns in this area",
            severity=Severity.HIGH,
            recommendation="Exercise caution. Consider alternate route.",
            affected_services=[]
        ))
    
    # Construction zone detection
    construction_complaints = [
        c for c in complaints_311 
        if 'construction' in c.description.lower()
    ]
    if len(construction_complaints) >= 2:
        alerts.append(EdgeCaseAlert(
            case_type=EdgeCaseType.CONSTRUCTION_ZONE,
            message="Active construction zone - sidewalk may be blocked",
            severity=Severity.MEDIUM,
            recommendation="Watch for scaffolding and detours.",
            affected_services=[]
        ))
    
    # High collision area
    if collisions:
        recent_fatal = [c for c in collisions if c.get('pedestrians_killed', 0) > 0]
        if recent_fatal:
            alerts.append(EdgeCaseAlert(
                case_type=EdgeCaseType.HIGH_CRIME_AREA,
                message="Pedestrian fatality recorded at this intersection",
                severity=Severity.HIGH,
                recommendation="Use crosswalks. Watch for turning vehicles.",
                affected_services=[]
            ))
    
    return alerts


# ============================================================================
# DATA QUALITY EDGE CASES
# ============================================================================

def check_data_quality(
    latitude: float,
    longitude: float
) -> List[EdgeCaseAlert]:
    """Check for data quality issues."""
    alerts = []
    
    sql = """
        SELECT 
            (SELECT MAX(inspection_date) FROM restaurants) as last_restaurant,
            (SELECT MAX(crash_date) FROM collisions) as last_collision,
            (SELECT MAX(created_date) FROM complaints_311) as last_311
    """
    
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql)
            row = cur.fetchone()
        conn.close()
        
        now = datetime.now().date()
        
        # Check restaurant data freshness (should be within 30 days)
        if row['last_restaurant']:
            days_old = (now - row['last_restaurant']).days
            if days_old > 30:
                alerts.append(EdgeCaseAlert(
                    case_type=EdgeCaseType.STALE_DATA,
                    message=f"Restaurant data is {days_old} days old",
                    severity=Severity.LOW,
                    recommendation="Health grades may have changed. Check restaurant directly.",
                    affected_services=["restaurants"]
                ))
        
        # Check 311 data freshness (should be within 2 days)
        if row['last_311']:
            hours_old = (datetime.now() - row['last_311']).total_seconds() / 3600
            if hours_old > 48:
                alerts.append(EdgeCaseAlert(
                    case_type=EdgeCaseType.STALE_DATA,
                    message="311 complaint data may be outdated",
                    severity=Severity.LOW,
                    recommendation="Live conditions may differ from cached data.",
                    affected_services=["311"]
                ))
                
    except psycopg2.Error as e:
        logger.error(f"Data quality check error: {e}")
    
    return alerts


# ============================================================================
# MAIN EDGE CASE DETECTOR
# ============================================================================

def detect_edge_cases(
    latitude: float,
    longitude: float,
    nearby_routes: set = None,
    mta_alerts: List[HotQueryItem] = None,
    complaints_311: List[HotQueryItem] = None,
    collisions: List[dict] = None,
    require_ada: bool = False
) -> List[EdgeCaseAlert]:
    """
    Comprehensive edge case detection.
    
    Args:
        latitude: GPS latitude
        longitude: GPS longitude
        nearby_routes: Set of subway routes near user
        mta_alerts: List of MTA alerts
        complaints_311: List of 311 complaints
        collisions: List of collision data
        require_ada: Whether user needs ADA accessibility
        
    Returns:
        List of EdgeCaseAlert objects
    """
    all_alerts = []
    
    # Time-based checks (always run)
    all_alerts.extend(check_time_based_cases())
    
    # Location-based checks
    all_alerts.extend(check_location_based_cases(latitude, longitude))
    
    # Transit checks (if we have route data)
    if nearby_routes is not None and mta_alerts is not None:
        all_alerts.extend(check_transit_edge_cases(
            latitude, longitude, nearby_routes, mta_alerts
        ))
    
    # Accessibility checks
    all_alerts.extend(check_accessibility_cases(latitude, longitude, require_ada))
    
    # Safety checks
    if complaints_311 is not None:
        all_alerts.extend(check_safety_cases(
            latitude, longitude, complaints_311, collisions or []
        ))
    
    # Data quality checks
    all_alerts.extend(check_data_quality(latitude, longitude))
    
    # Sort by severity (HIGH first)
    severity_order = {Severity.CRITICAL: 0, Severity.HIGH: 1, Severity.MEDIUM: 2, Severity.LOW: 3}
    all_alerts.sort(key=lambda x: severity_order.get(x.severity, 4))
    
    logger.info(f"Detected {len(all_alerts)} edge cases")
    return all_alerts
