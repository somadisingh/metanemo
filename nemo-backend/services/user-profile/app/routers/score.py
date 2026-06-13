"""Alert priority scoring router."""
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
import structlog
import app.db as db
from app.models import ScoreRequest, ScoreResponse
from app import modifier
from app.auth import get_authenticated_user

router = APIRouter()
log = structlog.get_logger()


@router.post("/score", response_model=ScoreResponse)
async def score_alert(req: ScoreRequest, user_id: str = Depends(get_authenticated_user)) -> ScoreResponse:
    """Compute a personalized priority score and suppression decision for an alert.

    Requires authentication via X-User-ID header. Scoring is scoped per-user
    so each user's engagement history and interests are isolated.

    Args:
        req: ScoreRequest with alert_type, coordinates, timestamp, and optional ids.
        user_id: Authenticated user ID from X-User-ID header.

    Returns:
        ScoreResponse with priority_score, suppressed, and proactive fields.
    """
    conn = db.get_conn()
    try:
        result = modifier.compute_score(conn, req, user_id)
        return result
    except Exception as exc:
        log.error("score_endpoint_error", error=str(exc), exc_info=True)
        return JSONResponse(status_code=500, content={"error": "internal_server_error"})
    finally:
        db.put_conn(conn)
