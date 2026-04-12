"""Interest weight update router."""
from fastapi import APIRouter
import app.db as db
from app.models import InterestRequest

router = APIRouter()


@router.post("/interests")
async def update_interest(req: InterestRequest) -> dict:
    """Update the interest weight for a topic by applying a delta, clamped to [0.0, 1.0].

    Args:
        req: InterestRequest with topic and delta.

    Returns:
        Dict with updated=True and new_weight float.
    """
    conn = db.get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT weight FROM interest_weights WHERE topic = %s", (req.topic,))
            row = cur.fetchone()
            current = row[0] if row else 0.5
            new_weight = max(0.0, min(1.0, current + req.delta))
            cur.execute(
                "UPDATE interest_weights SET weight = %s WHERE topic = %s",
                (new_weight, req.topic)
            )
        conn.commit()
    finally:
        db.put_conn(conn)
    return {"updated": True, "new_weight": new_weight}
