"""
Shared dataclasses for NemoClaw tool inputs and outputs.
"""

from dataclasses import dataclass, field
from typing import Optional, List
from enum import Enum


class HazardType(str, Enum):
    """Types of hazards detected by the system."""
    RESTAURANT = "restaurant"
    COLLISION = "collision"
    NOISE = "noise"
    TRANSIT = "transit"
    CONSTRUCTION = "construction"
    AIR_QUALITY = "air_quality"
    HEAT = "heat"
    ACCESSIBILITY = "accessibility"


class Severity(str, Enum):
    """Severity levels for hazards."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class ColdQueryResult:
    """
    Result from cold_query tool (PostGIS spatial query).
    
    Attributes:
        name: Business or location name
        address: Street address
        grade: Restaurant health grade (A, B, C) or None
        score: Restaurant inspection score or None
        hazard: True if this is a hazard (grade C or score > 28)
        distance_meters: Distance from query point
        hazard_type: Type of hazard
    """
    name: str
    address: str
    grade: Optional[str] = None
    score: Optional[int] = None
    cuisine_description: Optional[str] = None
    hazard: bool = False
    distance_meters: float = 0.0
    hazard_type: HazardType = HazardType.RESTAURANT


@dataclass
class CollisionResult:
    """
    Result for collision hotspot query.
    
    Attributes:
        location: Street intersection or address
        crash_date: Date of most recent crash
        pedestrians_injured: Number of pedestrians injured
        pedestrians_killed: Number of pedestrians killed
        severity_score: Calculated severity score
        distance_meters: Distance from query point
    """
    location: str
    crash_date: str
    pedestrians_injured: int = 0
    pedestrians_killed: int = 0
    severity_score: int = 0
    distance_meters: float = 0.0


@dataclass
class HotQueryItem:
    """
    Single item from hot_query tool (live API data).
    
    Attributes:
        type: Source type ("311", "MTA", "FDNY")
        description: Human-readable description
        severity: Severity level
        distance_meters: Distance from query point
        complaint_type: Specific complaint type (for 311)
        status: Current status
    """
    type: str
    description: str
    severity: Severity
    distance_meters: float
    complaint_type: Optional[str] = None
    status: Optional[str] = None


@dataclass
class HotQueryResult:
    """
    Result from hot_query tool (live API queries).
    
    Attributes:
        results: List of hazard items sorted by distance
        partial: True if one or more APIs failed
        error_sources: List of API sources that failed
    """
    results: List[HotQueryItem] = field(default_factory=list)
    partial: bool = False
    error_sources: List[str] = field(default_factory=list)


@dataclass
class QoLScore:
    """
    Quality of Life score for a location.
    
    Attributes:
        score: Numeric score (negative = worse)
        level: Classification level
        action: Recommended action
        hazards: List of detected hazards
        amenities: List of nearby amenities
    """
    score: int
    level: str  # "good", "fair", "moderate", "poor", "critical"
    action: str  # "none", "note", "inform", "warn", "reroute"
    hazards: List[dict] = field(default_factory=list)
    amenities: List[dict] = field(default_factory=list)


@dataclass
class AgentResponse:
    """
    Response from the NemoClaw agent.
    
    Attributes:
        text: Natural language response for TTS
        tool_used: Name of tool that was invoked (if any)
        tool_result: Raw result from tool (if any)
        confidence: Classification confidence (0-1)
        requires_clarification: True if agent needs more info
    """
    text: str
    tool_used: Optional[str] = None
    tool_result: Optional[dict] = None
    confidence: float = 1.0
    requires_clarification: bool = False
