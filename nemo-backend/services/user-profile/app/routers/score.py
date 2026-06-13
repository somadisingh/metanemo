"""Alert priority scoring router."""
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
import structlog
import app.db as db
from app.models import ScoreRequest, ScoreResponse
from app import modifier
from app.auth import verify_api_key

router = APIRouter()
log = structlog.get_logger()


@router.post("/score", response_model=ScoreResponse, dependencies=[Depends(verify_api_key)])
async def score_alert(req: ScoreRequest) -> ScoreResponse:
    """Compute a personalized priority score and suppression decision for an alert.

    Requires a valid X-API-Key header for authentication.

    Args:
        req: ScoreRequest with alert_type, coordinates, timestamp, and optional ids.

    Returns:
        ScoreResponse with priority_score, suppressed, and proactive fields.
    """
    conn = db.get_conn()
    try:
        result = modifier.compute_score(conn, req)
        return result
    except Exception as exc:
        log.error("score_endpoint_error", error=str(exc), exc_info=True)
        return JSONResponse(status_code=500, content={"error": "internal_server_error"})
    finally:
        db.put_conn(conn)
