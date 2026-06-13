"""Authentication dependencies for the user-profile service."""
import os
import secrets
from fastapi import Header, HTTPException, status
import structlog

log = structlog.get_logger()

# API key for service-to-service authentication.
# Must be set via environment variable in production.
SCORE_API_KEY: str = os.environ.get("SCORE_API_KEY", "")


async def verify_api_key(x_api_key: str = Header(..., alias="X-API-Key")) -> str:
    """Validate the X-API-Key header against the configured SCORE_API_KEY.

    Args:
        x_api_key: The API key provided in the request header.

    Returns:
        The validated API key string.

    Raises:
        HTTPException: 401 if SCORE_API_KEY is not configured or key doesn't match.
    """
    if not SCORE_API_KEY:
        log.error("score_api_key_not_configured")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Service authentication not configured",
        )
    if not secrets.compare_digest(x_api_key, SCORE_API_KEY):
        log.warning("invalid_api_key_attempt")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )
    return x_api_key
