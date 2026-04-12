"""Visit recording routers for place, transit, and location events."""
from fastapi import APIRouter
import h3
import structlog
import app.db as db
from app.models import PlaceVisitRequest, TransitVisitRequest, LocationRequest

router = APIRouter()
log = structlog.get_logger()


@router.post("/visits/place")
async def record_place_visit(req: PlaceVisitRequest) -> dict:
    """Record a place visit event in place_visits.

    Args:
        req: PlaceVisitRequest with place_id, latitude, longitude.

    Returns:
        Dict with recorded=True on success.
    """
    conn = db.get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO place_visits (place_id, latitude, longitude) VALUES (%s, %s, %s)",
                (req.place_id, req.latitude, req.longitude)
            )
        conn.commit()
    finally:
        db.put_conn(conn)
    return {"recorded": True}


@router.post("/visits/transit")
async def record_transit_visit(req: TransitVisitRequest) -> dict:
    """Record a transit pattern visit, upserting visit_count in transit_patterns.

    Args:
        req: TransitVisitRequest with route_id and ISO 8601 timestamp.

    Returns:
        Dict with recorded=True on success.
    """
    hour = req.timestamp.hour
    dow = req.timestamp.weekday()
    conn = db.get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO transit_patterns (route_id, hour_of_day, day_of_week, visit_count)
                VALUES (%s, %s, %s, 1)
                ON CONFLICT (route_id, hour_of_day, day_of_week)
                DO UPDATE SET visit_count = transit_patterns.visit_count + 1
                """,
                (req.route_id, hour, dow)
            )
        conn.commit()
    finally:
        db.put_conn(conn)
    return {"recorded": True}


@router.post("/visits/location")
async def record_location(req: LocationRequest) -> dict:
    """Record a GPS location visit, mapping to an H3 cell at resolution 9.

    Args:
        req: LocationRequest with latitude and longitude.

    Returns:
        Dict with recorded=True on success.
    """
    cell = h3.geo_to_h3(req.latitude, req.longitude, 9)
    conn = db.get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO neighborhood_visits (h3_cell, visited_date)
                VALUES (%s, CURRENT_DATE)
                ON CONFLICT DO NOTHING
                """,
                (cell,)
            )
        conn.commit()
    finally:
        db.put_conn(conn)
    return {"recorded": True}
