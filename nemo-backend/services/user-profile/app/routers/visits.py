"""Visit recording routers for place, transit, and location events."""
from fastapi import APIRouter, Depends
import h3
import structlog
import app.db as db
from app.models import PlaceVisitRequest, TransitVisitRequest, LocationRequest
from app.auth import get_authenticated_user

router = APIRouter()
log = structlog.get_logger()


@router.post("/visits/place")
async def record_place_visit(req: PlaceVisitRequest, user_id: str = Depends(get_authenticated_user)) -> dict:
    """Record a place visit event in place_visits.

    Requires authentication via X-User-ID header.

    Args:
        req: PlaceVisitRequest with place_id, latitude, longitude.
        user_id: Authenticated user ID from X-User-ID header.

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
async def record_transit_visit(req: TransitVisitRequest, user_id: str = Depends(get_authenticated_user)) -> dict:
    """Record a transit pattern visit, upserting visit_count in transit_patterns.

    Requires authentication via X-User-ID header.

    Args:
        req: TransitVisitRequest with route_id and ISO 8601 timestamp.
        user_id: Authenticated user ID from X-User-ID header.

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
async def record_location(req: LocationRequest, user_id: str = Depends(get_authenticated_user)) -> dict:
    """Record a GPS location visit, mapping to an H3 cell at resolution 9.

    Requires authentication via X-User-ID header.

    Args:
        req: LocationRequest with latitude and longitude.
        user_id: Authenticated user ID from X-User-ID header.

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
