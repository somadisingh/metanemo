"""Health check router for the user-profile service."""
from fastapi import APIRouter
from fastapi.responses import JSONResponse
import app.db as db

router = APIRouter()


@router.get("/health")
async def health_check() -> JSONResponse:
    """Return service health status including database connectivity.

    Returns:
        HTTP 200 with status ok, or HTTP 503 if database is unavailable.
    """
    conn = None
    try:
        conn = db.get_conn()
        healthy = db.ping(conn)
    except Exception:
        healthy = False
    finally:
        if conn is not None:
            db.put_conn(conn)

    if healthy:
        return JSONResponse(status_code=200, content={"status": "ok"})
    return JSONResponse(status_code=503, content={"status": "degraded", "reason": "database_unavailable"})
