"""Reinforcement signal recording router."""
from fastapi import APIRouter, Depends
import structlog
import app.db as db
from app.models import SignalRequest
from app.auth import get_authenticated_user

router = APIRouter()
log = structlog.get_logger()


@router.post("/signals")
async def record_signal(req: SignalRequest, user_id: str = Depends(get_authenticated_user)) -> dict:
    """Record a positive or negative reinforcement signal for an alert type.

    Requires authentication via X-User-ID header. Signals are scoped per-user
    so one user's feedback cannot affect another user's alert scoring.

    Args:
        req: SignalRequest with alert_type and signal direction.
        user_id: Authenticated user ID from X-User-ID header.

    Returns:
        Dict with recorded=True on success.
    """
    column = "positive_count" if req.signal == "positive" else "negative_count"
    conn = db.get_conn()
    try:
        with conn.cursor() as cur:
            # Upsert per-user engagement row
            cur.execute(
                f"""INSERT INTO alert_engagements (user_id, alert_type, positive_count, negative_count)
                    VALUES (%s, %s, 0, 0)
                    ON CONFLICT (user_id, alert_type) DO NOTHING""",
                (user_id, req.alert_type)
            )
            cur.execute(
                f"UPDATE alert_engagements SET {column} = {column} + 1 WHERE user_id = %s AND alert_type = %s",
                (user_id, req.alert_type)
            )
        conn.commit()
    finally:
        db.put_conn(conn)
    log.info("signal_recorded", user_id=user_id, alert_type=req.alert_type, signal=req.signal)
    return {"recorded": True}
