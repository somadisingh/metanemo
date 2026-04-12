"""Reinforcement signal recording router."""
from fastapi import APIRouter
import structlog
import app.db as db
from app.models import SignalRequest

router = APIRouter()
log = structlog.get_logger()


@router.post("/signals")
async def record_signal(req: SignalRequest) -> dict:
    """Record a positive or negative reinforcement signal for an alert type.

    Args:
        req: SignalRequest with alert_type and signal direction.

    Returns:
        Dict with recorded=True on success.
    """
    column = "positive_count" if req.signal == "positive" else "negative_count"
    conn = db.get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE alert_engagements SET {column} = {column} + 1 WHERE alert_type = %s",
                (req.alert_type,)
            )
        conn.commit()
    finally:
        db.put_conn(conn)
    log.info("signal_recorded", alert_type=req.alert_type, signal=req.signal)
    return {"recorded": True}
