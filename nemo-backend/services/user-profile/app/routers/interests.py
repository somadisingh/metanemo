"""Interest weight update router."""
from fastapi import APIRouter, Depends
import app.db as db
from app.models import InterestRequest
from app.auth import get_authenticated_user

router = APIRouter()


@router.post("/interests")
async def update_interest(req: InterestRequest, user_id: str = Depends(get_authenticated_user)) -> dict:
    """Update the interest weight for a topic by applying a delta, clamped to [0.0, 1.0].

    Requires authentication via X-User-ID header. Interest weights are scoped
    per-user so one user's preferences cannot affect another user's alert scoring.

    Args:
        req: InterestRequest with topic and delta.
        user_id: Authenticated user ID from X-User-ID header.

    Returns:
        Dict with updated=True and new_weight float.
    """
    conn = db.get_conn()
    try:
        with conn.cursor() as cur:
            # Upsert per-user interest weight row with default 0.5
            cur.execute(
                """INSERT INTO interest_weights (user_id, topic, weight)
                   VALUES (%s, %s, 0.5)
                   ON CONFLICT (user_id, topic) DO NOTHING""",
                (user_id, req.topic)
            )
            cur.execute(
                "SELECT weight FROM interest_weights WHERE user_id = %s AND topic = %s",
                (user_id, req.topic)
            )
            row = cur.fetchone()
            current = row[0] if row else 0.5
            new_weight = max(0.0, min(1.0, current + req.delta))
            cur.execute(
                "UPDATE interest_weights SET weight = %s WHERE user_id = %s AND topic = %s",
                (new_weight, user_id, req.topic)
            )
        conn.commit()
    finally:
        db.put_conn(conn)
    return {"updated": True, "new_weight": new_weight}
