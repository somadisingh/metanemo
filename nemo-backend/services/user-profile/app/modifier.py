"""AlertModifier: computes personalized priority scores for alerts."""
from typing import Optional
import h3
import psycopg2.extensions
import structlog
from app.models import ScoreRequest, ScoreResponse

log = structlog.get_logger()

SUPPRESS_THRESHOLD: float = 0.2
TOPIC_MAP: dict[str, str] = {
    "restaurant": "food",
    "transit": "transit",
    "safety": "safety",
    "discovery": "food",
}


def compute_familiarity_score(distinct_day_count: int) -> float:
    """Compute a familiarity score from the number of distinct days visited.

    Args:
        distinct_day_count: Number of distinct calendar days the H3 cell was visited.

    Returns:
        A float in [0.0, 1.0] where 1.0 means visited on 50+ distinct days.
    """
    return min(distinct_day_count / 50.0, 1.0)


def _get_engagement_ratio(conn: psycopg2.extensions.connection, alert_type: str, user_id: str) -> float:
    """Fetch the engagement ratio for an alert type from alert_engagements, scoped by user.

    Args:
        conn: Active psycopg2 connection.
        alert_type: The alert category string.
        user_id: The authenticated user's ID.

    Returns:
        positive_count / max(positive_count + negative_count, 1) as a float.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT positive_count, negative_count FROM alert_engagements WHERE user_id = %s AND alert_type = %s",
            (user_id, alert_type)
        )
        row = cur.fetchone()
    if row is None:
        return 0.5
    pos, neg = row
    return pos / max(pos + neg, 1)


def _get_interest_weight(conn: psycopg2.extensions.connection, alert_type: str, user_id: str) -> float:
    """Fetch the interest weight for the topic corresponding to an alert type, scoped by user.

    Args:
        conn: Active psycopg2 connection.
        alert_type: The alert category string.
        user_id: The authenticated user's ID.

    Returns:
        The interest weight float in [0.0, 1.0].
    """
    topic = TOPIC_MAP.get(alert_type, "food")
    with conn.cursor() as cur:
        cur.execute("SELECT weight FROM interest_weights WHERE user_id = %s AND topic = %s", (user_id, topic))
        row = cur.fetchone()
    return row[0] if row else 0.5


def _get_familiarity_from_db(conn: psycopg2.extensions.connection, lat: float, lon: float) -> float:
    """Compute familiarity score for a location from the neighborhood_visits table.

    Args:
        conn: Active psycopg2 connection.
        lat: Latitude in degrees.
        lon: Longitude in degrees.

    Returns:
        FamiliarityScore float in [0.0, 1.0].
    """
    cell = h3.geo_to_h3(lat, lon, 9)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(DISTINCT visited_date) FROM neighborhood_visits WHERE h3_cell = %s",
            (cell,)
        )
        row = cur.fetchone()
    count = row[0] if row else 0
    return compute_familiarity_score(count)


def compute_score(conn: psycopg2.extensions.connection, req: ScoreRequest, user_id: str) -> ScoreResponse:
    """Compute a personalized priority score and suppression decision for an alert.

    Args:
        conn: Active psycopg2 connection.
        req: The score request containing alert_type, coordinates, and optional ids.
        user_id: The authenticated user's ID for per-user data isolation.

    Returns:
        ScoreResponse with priority_score, suppressed, and proactive fields.
    """
    engagement_ratio = _get_engagement_ratio(conn, req.alert_type, user_id)
    interest_weight = _get_interest_weight(conn, req.alert_type, user_id)
    base = engagement_ratio * interest_weight

    adjusted = base
    proactive = False

    if req.alert_type == "restaurant" and req.place_id:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM place_visits WHERE place_id = %s",
                (req.place_id,)
            )
            row = cur.fetchone()
        prior_visits = row[0] if row else 0
        adjusted = base * max(0.0, 1.0 - prior_visits * 0.15)

    elif req.alert_type == "transit" and req.route_id:
        hour = req.timestamp.hour
        dow = req.timestamp.weekday()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT visit_count FROM transit_patterns WHERE route_id = %s AND hour_of_day = %s AND day_of_week = %s",
                (req.route_id, hour, dow)
            )
            row = cur.fetchone()
        visit_count = row[0] if row else 0
        adjusted = base * (1.0 + min(visit_count / 10.0, 0.5))
        proactive = visit_count >= 5

    elif req.alert_type == "discovery":
        familiarity = _get_familiarity_from_db(conn, req.latitude, req.longitude)
        adjusted = base * (1.0 - familiarity * 0.8)

    suppressed = adjusted < SUPPRESS_THRESHOLD

    log.debug("alert_scored",
              alert_type=req.alert_type,
              priority_score=round(adjusted, 4),
              suppressed=suppressed)

    return ScoreResponse(
        priority_score=round(adjusted, 6),
        suppressed=suppressed,
        proactive=proactive,
    )
