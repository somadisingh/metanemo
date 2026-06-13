"""Authentication dependency for the user-profile service.

Extracts and validates the user identity from the X-User-ID request header.
In production, this header is set by the upstream API gateway after authenticating
the user (e.g., via JWT validation). Direct access to this service without the
gateway is blocked by network policy.
"""
import re
from fastapi import Header, HTTPException

# Regex pattern for valid user IDs: alphanumeric, hyphens, underscores, 1-128 chars
_USER_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_\-]{1,128}$")


async def get_authenticated_user(x_user_id: str = Header(..., alias="X-User-ID")) -> str:
    """Extract and validate the authenticated user ID from the request header.

    Args:
        x_user_id: The X-User-ID header value set by the upstream auth gateway.

    Returns:
        The validated user_id string.

    Raises:
        HTTPException 401 if header is missing.
        HTTPException 422 if user_id format is invalid.
    """
    if not x_user_id or not x_user_id.strip():
        raise HTTPException(status_code=401, detail="Missing X-User-ID header")

    user_id = x_user_id.strip()
    if not _USER_ID_PATTERN.match(user_id):
        raise HTTPException(
            status_code=422,
            detail="Invalid X-User-ID format: must be 1-128 alphanumeric, hyphen, or underscore characters"
        )

    return user_id
