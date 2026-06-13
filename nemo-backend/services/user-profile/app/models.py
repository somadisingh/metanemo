"""Pydantic request and response models for the user-profile service."""
from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel, Field


class PlaceVisitRequest(BaseModel):
    """Request body for recording a place visit."""
    place_id: str = Field(..., min_length=1, max_length=256)
    latitude: float
    longitude: float


class TransitVisitRequest(BaseModel):
    """Request body for recording a transit pattern visit."""
    route_id: str = Field(..., min_length=1, max_length=256)
    timestamp: datetime


class LocationRequest(BaseModel):
    """Request body for recording a raw GPS location."""
    latitude: float
    longitude: float


class SignalRequest(BaseModel):
    """Request body for recording a reinforcement signal."""
    alert_type: Literal["restaurant", "transit", "safety", "discovery"]
    signal: Literal["positive", "negative"]


class InterestRequest(BaseModel):
    """Request body for updating an interest weight."""
    topic: Literal["food", "transit", "safety"]
    delta: float = Field(..., ge=-1.0, le=1.0)


class ScoreRequest(BaseModel):
    """Request body for computing a personalized alert priority score."""
    alert_type: Literal["restaurant", "transit", "safety", "discovery"]
    latitude: float
    longitude: float
    timestamp: datetime
    place_id: Optional[str] = Field(None, max_length=256)
    route_id: Optional[str] = Field(None, max_length=256)


class ScoreResponse(BaseModel):
    """Response body for the alert priority scoring endpoint."""
    priority_score: float
    suppressed: bool
    proactive: bool = False
