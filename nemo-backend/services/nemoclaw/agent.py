"""
NemoClaw Agent - Agent Framework

Handles intent classification via Nemotron, tool dispatch to cold_query
and hot_query, and natural language response synthesis.

Property 12: Intent Classification Routes to Correct Tool
Property 13: Tool Result Synthesized to Natural Language
Property 14: Low Confidence Returns Clarifying Question
"""

import os
import sys
import logging
import base64
from typing import Optional, Dict, Any
from dataclasses import asdict

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
import re
import requests

from tools import (
    cold_query, hot_query, get_nearby_subway_routes,
    query_collision_hotspots, query_heat_vulnerability, query_accessibility,
    cultural_query, query_film_permits, query_landmarks, query_cultural_venues,
    detect_edge_cases, EdgeCaseAlert,
    ColdQueryResult, HotQueryResult, AgentResponse, Severity
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = FastAPI(title="NemoClaw Agent", version="1.0.0")

# Nemotron NIM configuration
NEMOTRON_HOST = os.environ.get('NEMOTRON_NIM_HOST', 'nemotron-nim')
NEMOTRON_PORT = os.environ.get('NEMOTRON_NIM_PORT', '8000')
NEMOTRON_MODEL = os.environ.get('NEMOTRON_MODEL_NAME', 'nemotron-3-nano-30b-a3b')
CONFIDENCE_THRESHOLD = float(os.environ.get('NEMOCLAW_CONFIDENCE_THRESHOLD', '0.7'))

NEMOTRON_ENDPOINT = f"http://{NEMOTRON_HOST}:{NEMOTRON_PORT}/v1/chat/completions"

# Ollama fallback configuration
OLLAMA_HOST = os.environ.get('OLLAMA_HOST', 'host.docker.internal')
OLLAMA_PORT = os.environ.get('OLLAMA_PORT', '11434')
OLLAMA_MODEL = os.environ.get('OLLAMA_MODEL', 'llama3.2:3b')
OLLAMA_ENDPOINT = f"http://{OLLAMA_HOST}:{OLLAMA_PORT}/api/chat"
USE_OLLAMA = os.environ.get('USE_OLLAMA', 'true').lower() == 'true'
SOCRATA_311_ENDPOINT = os.environ.get('SOCRATA_311_ENDPOINT', 'https://data.cityofnewyork.us/resource/erm2-nwe9.json')

# User-profile service endpoint
USER_PROFILE_URL = os.environ.get('USER_PROFILE_URL', 'http://user-profile:8081')
SCORE_API_KEY = os.environ.get('SCORE_API_KEY', '')

# Alert type → topic mapping for interest updates
ALERT_TYPE_TOPIC = {
    'restaurant': 'food',
    'transit': 'transit',
    'safety': 'safety',
    'discovery': 'food',
}

def _post_user_profile(path: str, payload: dict) -> Optional[dict]:
    """Fire-and-forget POST to user-profile; returns response dict or None on error."""
    try:
        headers = {}
        if SCORE_API_KEY:
            headers['X-API-Key'] = SCORE_API_KEY
        r = requests.post(f"{USER_PROFILE_URL}{path}", json=payload, headers=headers, timeout=2)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.warning(f"user-profile {path} failed: {e}")
        return None

def _score_alert(alert_type: str, latitude: float, longitude: float,
                 timestamp: str, place_id: Optional[str] = None,
                 route_id: Optional[str] = None) -> dict:
    """Call /score and return the response dict; falls back to unsuppressed 0.5 on error."""
    payload = {
        'alert_type': alert_type,
        'latitude': latitude,
        'longitude': longitude,
        'timestamp': timestamp,
    }
    if place_id:
        payload['place_id'] = place_id
    if route_id:
        payload['route_id'] = route_id
    result = _post_user_profile('/score', payload)
    if result is None:
        return {'priority_score': 0.5, 'suppressed': False, 'proactive': False}
    return result


def _fetch_latest_citywide_nypd_alert() -> Optional[dict]:
    """Fetch the single latest citywide NYPD-related 311 alert."""
    app_token = os.environ.get('SOCRATA_APP_TOKEN', '')
    headers = {'X-App-Token': app_token} if app_token else {}
    params = {
        '$limit': 1,
        '$order': 'created_date DESC',
        '$where': "created_date IS NOT NULL AND (upper(agency) = 'NYPD' OR upper(agency_name) like '%POLICE%')",
        '$select': 'created_date,complaint_type,descriptor,status,borough,incident_zip,agency,agency_name',
    }
    try:
        response = requests.get(SOCRATA_311_ENDPOINT, headers=headers, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        if not data:
            return None
        return data[0]
    except Exception as e:
        logger.error(f"Citywide NYPD alert fetch failed: {e}")
        return None


def _fetch_citywide_mta_alerts(limit: int = 20) -> list:
    """Fetch latest citywide MTA subway alerts (no nearby-route filtering)."""
    alerts_url = "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/camsys%2Fsubway-alerts.json"
    try:
        response = requests.get(alerts_url, timeout=15)
        response.raise_for_status()
        data = response.json()
        entities = data.get('entity', [])

        def _severity_rank(text: str) -> int:
            t = text.lower()
            if any(x in t for x in ['bypass', 'no service', 'suspended', 'not running', 'skip']):
                return 3
            if any(x in t for x in ['delay', 'slow', 'crowding', 'reroute']):
                return 2
            return 1

        seen = set()
        parsed = []
        for entity in entities:
            alert = entity.get('alert', {})
            header = alert.get('header_text', {})
            translations = header.get('translation', [])
            text = ''
            for t in translations:
                if t.get('language') == 'en':
                    text = t.get('text', '')
                    break
            if not text and translations:
                text = translations[0].get('text', '')
            if not text:
                continue

            informed = alert.get('informed_entity', [])
            routes = sorted({ie.get('route_id') for ie in informed if ie.get('route_id')})
            key = (text[:180], tuple(routes))
            if key in seen:
                continue
            seen.add(key)
            parsed.append({
                'text': text[:180],
                'routes': routes,
                'severity_rank': _severity_rank(text),
            })

        parsed.sort(key=lambda x: x['severity_rank'], reverse=True)
        return parsed[:limit]
    except Exception as e:
        logger.error(f"Citywide MTA alert fetch failed: {e}")
        return []


# Request/Response models
class AgentRequest(BaseModel):
    text: Optional[str] = None
    image_b64: Optional[str] = Field(None, max_length=10_000_000)
    latitude: float
    longitude: float
    timestamp: Optional[str] = None
    user_profile: Optional[Dict[str, Any]] = None


class AgentResponseModel(BaseModel):
    text: str
    tool_used: Optional[str] = None
    confidence: float = 1.0
    requires_clarification: bool = False
    hazards: list = []
    qol_score: Optional[int] = None


# System prompt for intent classification
CLASSIFICATION_PROMPT = """Output ONLY this JSON, no thinking, no text:
italian restaurant/chinese restaurant/mexican food/japanese food/indian food/thai food/french restaurant/greek food/korean food/sushi near me/pizza near me/burger near me/specific cuisine/what [cuisine] restaurants -> {"intent":"cuisine","confidence":0.95,"tool":"cuisine_query","clarification_needed":false}
food/restaurant/eat/grade/dining/open -> {"intent":"food","confidence":0.95,"tool":"cold_query","clarification_needed":false}
accessible/wheelchair/ada/disability/ramp/elevator/accessible subway -> {"intent":"accessibility","confidence":0.95,"tool":"accessibility_query","clarification_needed":false}
nearest subway/closest subway station/subway station near me/what subway is near/which station/subway entrance -> {"intent":"subway_station","confidence":0.95,"tool":"subway_query","clarification_needed":false}
transit/subway/mta/bus/delay/train -> {"intent":"transit","confidence":0.95,"tool":"hot_query","clarification_needed":false}
show all subway alerts/all mta alerts/citywide subway alerts/subway alerts across the city -> {"intent":"transit_citywide","confidence":0.95,"tool":"hot_query","clarification_needed":false}
nypd alerts/police alerts/latest nypd incident/crime alerts -> {"intent":"nypd_alerts","confidence":0.95,"tool":"hot_query","clarification_needed":false}
noise/311/construction/street/hazard -> {"intent":"safety","confidence":0.95,"tool":"hot_query","clarification_needed":false}
crash/collision/accident/dangerous intersection/pedestrian safety -> {"intent":"collision","confidence":0.95,"tool":"collision_query","clarification_needed":false}
heat/hot/cooling center/temperature/heatwave -> {"intent":"heat","confidence":0.95,"tool":"heat_query","clarification_needed":false}
architecture/building/buildings/famous building/historic building/skyscraper/structure -> {"intent":"architecture","confidence":0.95,"tool":"architecture_query","clarification_needed":false}
film shoot/filming/movie shoot/tv shoot/shooting permit/what are they filming -> {"intent":"film","confidence":0.95,"tool":"film_query","clarification_needed":false}
film/movie/landmark/monument/museum/culture/famous/historic/site/sites of interest/points of interest/things to see/attractions -> {"intent":"cultural","confidence":0.95,"tool":"cultural_query","clarification_needed":false}
safety briefing/around me/area/nearby/what is here/how is it/what's going on -> {"intent":"general","confidence":0.95,"tool":"general","clarification_needed":false}
general knowledge/science/history/nature/weather/sports/cooking/health/technology/anything not NYC location-specific -> {"intent":"off_topic","confidence":0.95,"tool":"none","clarification_needed":false}
unclear -> {"intent":"unclear","confidence":0.3,"tool":"none","clarification_needed":true}
"""

# System prompt for response synthesis
SYNTHESIS_PROMPT = """Rephrase the following data as ONE spoken sentence. Output ONLY the sentence, nothing else.
"""


def validate_environment() -> bool:
    """Validate required environment variables."""
    required = ['POSTGRES_HOST', 'POSTGRES_USER', 'POSTGRES_PASSWORD', 'POSTGRES_DB']
    missing = [var for var in required if not os.environ.get(var)]
    
    if missing:
        logger.fatal(f"Missing required environment variables: {missing}")
        return False
    return True


def call_ollama(messages: list, max_tokens: int = 150, temperature: float = 0.7) -> Optional[str]:
    """
    Call Ollama for inference (fallback when Nemotron unavailable).
    """
    try:
        # Convert OpenAI format to Ollama format
        ollama_messages = [{"role": m["role"], "content": m["content"]} for m in messages]
        
        response = requests.post(
            OLLAMA_ENDPOINT,
            json={
                "model": OLLAMA_MODEL,
                "messages": ollama_messages,
                "stream": False,
                "options": {
                    "num_predict": max_tokens,
                    "temperature": temperature
                }
            },
            timeout=60
        )
        response.raise_for_status()
        
        data = response.json()
        return data.get('message', {}).get('content', '')
        
    except requests.RequestException as e:
        logger.error(f"Ollama API error: {e}")
        return None


def call_nemotron(
    messages: list,
    max_tokens: int = 150,
    temperature: float = 0.7
) -> Optional[str]:
    """
    Call Nemotron NIM for inference.
    
    Args:
        messages: Chat messages in OpenAI format
        max_tokens: Maximum response tokens
        temperature: Sampling temperature
        
    Returns:
        Response text or None on error
    """
    try:
        response = requests.post(
            NEMOTRON_ENDPOINT,
            json={
                "model": NEMOTRON_MODEL,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            },
            timeout=60
        )
        response.raise_for_status()
        
        data = response.json()
        return data['choices'][0]['message']['content']
        
    except requests.RequestException as e:
        logger.error(f"Nemotron API error: {e}")
        return None


def call_llm(messages: list, max_tokens: int = 150, temperature: float = 0.7) -> Optional[str]:
    """
    Call LLM for inference - tries Nemotron first, falls back to Ollama.
    """
    # Try Nemotron first (primary)
    result = call_nemotron(messages, max_tokens, temperature)
    if result:
        return result
    
    logger.warning("Nemotron failed, falling back to Ollama...")
    
    # Fallback to Ollama
    if USE_OLLAMA:
        result = call_ollama(messages, max_tokens, temperature)
        if result:
            return result
    
    logger.error("Both Nemotron and Ollama failed")
    return None


def classify_intent(text: str, image_b64: Optional[str] = None) -> Dict[str, Any]:
    """
    Classify user intent using Nemotron.
    
    Args:
        text: User's spoken text
        image_b64: Optional base64 encoded image
        
    Returns:
        Dict with intent, confidence, tool, clarification_needed
    """
    messages = [
        {"role": "system", "content": CLASSIFICATION_PROMPT},
        {"role": "user", "content": text or "What's around me?"}
    ]
    
    # Add image context if provided
    if image_b64:
        messages[1]["content"] = f"[User is looking at something] {text or 'What is this?'}"
    
    response = call_ollama(messages, max_tokens=80, temperature=0.1) or call_nemotron(messages, max_tokens=80, temperature=0.1)
    
    if not response:
        # Default to general safety check on API failure
        return {
            "intent": "safety",
            "confidence": 0.5,
            "tool": "hot_query",
            "clarification_needed": False
        }
    
    # Parse JSON response
    try:
        import json
        import re
        # Strip Nemotron <think>...</think> reasoning block
        response = re.sub(r"<think>.*?</think>", "", response, flags=re.DOTALL).strip()
        # Also strip bare thinking text before </think> (Nemotron omits opening tag)
        if "</think>" in response:
            response = response.split("</think>", 1)[-1].strip()
        # Extract JSON object from response
        m = re.search(r"\{[^{}]+\}", response, re.DOTALL)
        if m: response = m.group(0)
        # Extract JSON from response (handle markdown code blocks)
        if "```" in response:
            response = response.split("```")[1]
            if response.startswith("json"):
                response = response[4:]
        
        result = json.loads(response.strip())
        return result
    except (json.JSONDecodeError, IndexError):
        logger.warning(f"Failed to parse classification response: {response}")
        return {
            "intent": "unclear",
            "confidence": 0.3,
            "tool": "none",
            "clarification_needed": True
        }


def synthesize_response(tool_result: Any, intent: str, user_text: str) -> str:
    """
    Two-step synthesis:
    1. Nemotron thinks freely and produces a full analysis
    2. Ollama summarizes Nemotron's output into 3-4 spoken lines
    """
    # Build data context from tool results — strip emoji to avoid confusing Nemotron
    import re as _re
    def clean(text: str) -> str:
        return _re.sub(r'[^\x20-\x7E]', ' ', _re.sub(r' +', ' ', text)).strip()

    if isinstance(tool_result, HotQueryResult):
        hazards = [r for r in tool_result.results if r.severity in [Severity.HIGH, Severity.MEDIUM]]
        if hazards:
            context = f"Found {len(hazards)} active MTA/311 alerts nearby:\n" + "\n".join(
                [f"- {clean(h.description)[:100]}" for h in hazards[:10]]
            )
        else:
            context = "No significant hazards detected nearby."
    elif isinstance(tool_result, list):
        # Could be ColdQueryResult list or CollisionResult list
        if tool_result:
            if hasattr(tool_result[0], 'hazard'):
                # Restaurant results - include all nearby places (not only hazardous)
                lines = []
                for r in tool_result[:10]:
                    cuisine = getattr(r, 'cuisine_description', None) or 'Various'
                    grade = r.grade or 'N/A'
                    score = r.score if r.score is not None else 'N/A'
                    lines.append(f"- {r.name} ({cuisine}), grade={grade}, score={score}, {r.distance_meters:.0f}m")
                hazard_count = sum(1 for r in tool_result if r.hazard)
                context = (
                    f"Nearest restaurants across cuisines ({len(tool_result)} found, {hazard_count} with health concerns):\n"
                    + "\n".join(lines)
                )
            elif hasattr(tool_result[0], 'severity_score'):
                # Collision results
                context = f"Found {len(tool_result)} pedestrian collision hotspots nearby:\n" + "\n".join(
                    [f"- {r.location}, {r.crash_date}, {r.pedestrians_injured} injured, {r.pedestrians_killed} killed" for r in tool_result[:5]]
                )
            else:
                context = f"Found {len(tool_result)} results nearby."
        else:
            if intent in ('food', 'cuisine'):
                context = "No restaurants found nearby."
            elif intent == 'collision':
                context = "No collision hotspots found nearby."
            else:
                context = "No results found nearby."
    elif isinstance(tool_result, dict) and 'type' not in tool_result:
        if 'subway_entrances' in tool_result:
            # Accessibility results
            entrances = tool_result.get('subway_entrances', [])
            ada_count = sum(1 for e in entrances if e.get('ada'))
            context = f"Found {len(entrances)} subway entrances nearby, {ada_count} are ADA accessible:\n" + "\n".join(
                [f"- {e['station']} ({e['line']}), ADA: {'Yes' if e['ada'] else 'No'}, {e['distance_meters']:.0f}m away" for e in entrances[:5]]
            )
        elif 'hvi_score' in tool_result:
            # Heat vulnerability
            context = f"Heat vulnerability for {tool_result.get('neighborhood', 'this area')}: HVI score {tool_result.get('hvi_score')}/5, surface temp {tool_result.get('surface_temp')}F, green space {tool_result.get('green_space_pct')}%"
        elif isinstance(tool_result, dict) and 'hot' in tool_result and 'collisions' in tool_result:
            # General multi-dataset result — build context from all sources, LLM picks what's relevant
            hot = tool_result['hot']
            collisions = tool_result.get('collisions', [])
            heat = tool_result.get('heat', {})
            accessibility = tool_result.get('accessibility', {})
            cultural = tool_result.get('cultural')
            user_q = tool_result.get('user_question', '')

            parts = []

            hot_hazards = [r for r in hot.results if r.severity in [Severity.HIGH, Severity.MEDIUM]]
            if hot_hazards:
                parts.append(f"{len(hot_hazards)} active 311/MTA alerts:\n" + "\n".join(
                    [f"- [{r.type}] {clean(r.description)[:100]}" for r in hot_hazards[:5]]
                ))
            else:
                parts.append("No active 311 or MTA alerts nearby.")

            if collisions:
                fatal = [c for c in collisions if c.pedestrians_killed > 0]
                parts.append(f"{len(collisions)} collision hotspots nearby" +
                              (f", {len(fatal)} fatal" if fatal else "") + ":\n" +
                              "\n".join([f"- {c.location}, {c.crash_date}" for c in collisions[:3]]))

            if heat.get('hvi_score'):
                parts.append(f"Heat vulnerability: HVI {heat['hvi_score']}/5, surface temp {heat.get('surface_temp')}F")

            if accessibility.get('subway_entrances'):
                entrances = accessibility['subway_entrances']
                ada = sum(1 for e in entrances if e.get('ada'))
                parts.append(f"{len(entrances)} subway entrances nearby, {ada} ADA accessible")

            if cultural and hasattr(cultural, 'landmarks') and (cultural.landmarks or cultural.film_permits or cultural.venues):
                lm_names = [l.name for l in cultural.landmarks[:2]]
                if lm_names:
                    parts.append(f"Nearby landmarks: {', '.join(lm_names)}")

            context = f"User asked: {user_q}\n\nArea data:\n" + "\n".join(p for p in parts if p)
        else:
            context = str(tool_result)[:500]
    elif isinstance(tool_result, dict) and tool_result.get('type') == 'subway_station':
        stations = tool_result.get('stations', [])
        if stations:
            lines_out = []
            for s in stations:
                name = s.get('name', 'Unknown')
                trains = s.get('lines', '').strip()
                dist = s.get('distance', 0)
                ada = ' (ADA accessible)' if s.get('ada') else ''
                lines_out.append(f"{name} — {trains}{ada}, {dist:.0f}m away")
            return "Nearest subway stations:\n" + "\n".join(lines_out)
        else:
            return "No subway stations found nearby." 
    elif isinstance(tool_result, dict) and tool_result.get('type') == 'architecture':
        # Architecture query — famous buildings only
        landmarks = tool_result.get('landmarks', [])
        if landmarks:
            lines = []
            for l in landmarks[:8]:
                parts_l = [l.name]
                if l.style:
                    parts_l.append(l.style)
                if l.year_built:
                    parts_l.append(f"built {l.year_built}")
                parts_l.append(f"{l.distance_meters:.0f}m away")
                lines.append("- " + ", ".join(parts_l))
            context = f"Famous buildings and architecture nearby ({len(landmarks)} found):\n" + "\n".join(lines)
        else:
            context = "No notable buildings or architecture found nearby."

    elif isinstance(tool_result, dict) and tool_result.get('type') == 'heat':
        heat = tool_result.get('result') or {}
        if heat.get('hvi_score'):
            context = (
                f"Heat vulnerability for {heat.get('neighborhood', 'this area')}: "
                f"HVI {heat.get('hvi_score')}/5, surface temp {heat.get('surface_temp')}F, "
                f"green space {heat.get('green_space_pct')}%."
            )
        else:
            context = "No heat vulnerability data found nearby."

    elif isinstance(tool_result, dict) and tool_result.get('type') == 'film':
        # Named productions from Wikidata + active permits from NYC Open Data
        permits = tool_result.get('permits', [])
        productions = tool_result.get('productions', [])
        lines = []
        # Named productions first (Wikidata — has movie/show names)
        if productions:
            lines.append(f"{len(productions)} named production(s) filmed near this location:")
            for p in productions[:8]:
                year_str = f" ({p.year})" if p.year else ""
                type_str = f" [{p.production_type}]" if p.production_type else ""
                loc_str = f" at {p.location_name}" if p.location_name else ""
                lines.append(f"- {p.production_name}{year_str}{type_str}{loc_str}, {p.distance_meters:.0f}m away")
        # Active permits (NYC Open Data — no names but shows current activity)
        if permits:
            active = [p for p in permits if p.is_active]
            if active:
                lines.append(f"{len(active)} active shoot(s) in progress (permit only, no title available):")
                for p in active[:3]:
                    lines.append(f"- {p.category} ({p.subcategory or p.event_type}) at {p.location[:60]}, {p.distance_meters:.0f}m away")
        context = "\n".join(lines) if lines else "No film or TV productions found near this location."

    elif isinstance(tool_result, dict) and tool_result.get('type') == 'cuisine':
        # Cuisine query — name, grade, distance ordered list
        restaurants = tool_result.get('restaurants', [])
        cuisine = tool_result.get('cuisine', 'requested cuisine')
        if restaurants:
            lines = []
            for r in restaurants[:10]:
                grade_str = f"Grade {r.grade}" if r.grade else "No grade"
                score_str = f"score {r.score}" if r.score else ""
                dist_str = f"{r.distance_meters:.0f}m away"
                hazard_str = ", health concern" if r.hazard else ""
                line = f"- {r.name}, {grade_str}"
                if score_str:
                    line += f", {score_str}"
                line += f", {dist_str}{hazard_str}"
                lines.append(line)
            context = f"{len(restaurants)} {cuisine or 'matching'} restaurants nearby ordered by distance:\n" + "\n".join(lines)
        else:
            context = f"No {cuisine or 'matching'} restaurants found nearby."

    elif hasattr(tool_result, 'film_permits'):
        # CulturalQueryResult (general cultural query — all types)
        parts = []
        if tool_result.film_permits:
            parts.append(f"{len(tool_result.film_permits)} active film permits:\n" + "\n".join(
                [f"- {p.category} ({p.subcategory or p.event_type}) at {p.location[:60]}, {p.distance_meters:.0f}m away"
                 for p in tool_result.film_permits[:3]]
            ))
        if tool_result.landmarks:
            parts.append(f"{len(tool_result.landmarks)} landmarks:\n" + "\n".join(
                [f"- {l.name}, {l.style or 'historic'}, {l.distance_meters:.0f}m away"
                 for l in tool_result.landmarks[:5]]
            ))
        if tool_result.venues:
            parts.append(f"{len(tool_result.venues)} cultural venues:\n" + "\n".join(
                [f"- {v.name} ({v.venue_type}), {v.distance_meters:.0f}m away"
                 for v in tool_result.venues[:5]]
            ))
        context = "\n".join(parts) if parts else "No cultural data found nearby." 
    else:
        context = "Area looks clear."

    # Pick system prompt and Ollama instruction based on intent type
    _intent_type = intent or "safety"
    if _intent_type == "nypd_alerts":
        if isinstance(tool_result, dict) and tool_result.get('type') == 'nypd_citywide_latest':
            latest = tool_result.get('alert')
            if latest:
                complaint = latest.get('complaint_type') or 'Unknown issue'
                descriptor = latest.get('descriptor') or ''
                status = latest.get('status') or 'Unknown'
                borough = latest.get('borough') or latest.get('incident_zip') or 'NYC'
                created = str(latest.get('created_date') or '').replace('T', ' ').replace('Z', '').strip()
                details = f"{complaint}: {descriptor}" if descriptor else complaint
                when = f" at {created}" if created else ""
                return f"Latest citywide NYPD alert: {details} in {borough}, status {status}{when}."
            return "No latest citywide NYPD alerts found."

        hot_payload = tool_result.get('result') if isinstance(tool_result, dict) else tool_result
        if isinstance(hot_payload, HotQueryResult):
            police_related = []
            for r in hot_payload.results:
                if r.type != "311":
                    continue
                desc = (r.description or "").lower()
                ctype = (r.complaint_type or "").lower()
                if any(k in desc or k in ctype for k in ("police", "nypd", "crime", "assault", "robbery", "weapon", "gun")):
                    police_related.append(r)
            if police_related:
                lines = [f"- {clean(a.description)[:120]}" for a in police_related[:8]]
                return "Latest NYPD-related alerts nearby:\n" + "\n".join(lines)
        return "No latest NYPD-related alerts found nearby."

    if _intent_type == "transit":
        hot_payload = tool_result.get('result') if isinstance(tool_result, dict) else tool_result
        nearby_routes = tool_result.get('nearby_routes', []) if isinstance(tool_result, dict) else []
        if isinstance(hot_payload, HotQueryResult):
            mta_alerts = [r for r in hot_payload.results if r.type == "MTA"]
            if mta_alerts:
                lines = [f"- {clean(a.description)[:120]}" for a in mta_alerts[:8]]
                return "Active subway/MTA alerts nearby:\n" + "\n".join(lines)
        if nearby_routes:
            return "No active subway/MTA alerts found nearby for lines " + "/".join(nearby_routes[:6]) + "."
        return "No active subway/MTA alerts found nearby."

    if _intent_type == "transit_citywide":
        alerts = tool_result.get('alerts', []) if isinstance(tool_result, dict) else []
        if alerts:
            lines = []
            for a in alerts[:12]:
                route_prefix = f"[{'/'.join(a.get('routes', []))}] " if a.get('routes') else ""
                lines.append(f"- {route_prefix}{clean(a.get('text', ''))}")
            return "Citywide subway/MTA alerts (latest):\n" + "\n".join(lines)
        return "No active citywide subway/MTA alerts found."

    if _intent_type == "film":
        _sys = "You are a film location guide for NYC. Report only film and TV production names, years, and locations. Do not mention restaurants, transit lines, or safety data."
        _ollama = "State only the film and TV production facts from the DATA section in 2-3 plain spoken sentences. Do not mention restaurants, transit, or safety. No bullet points, no emoji, no markdown."
    elif _intent_type == "architecture":
        _sys = "You are an NYC architecture guide. Report only building names, styles, and distances."
        _ollama = "State only the building and architecture facts from the DATA section in 2-3 plain spoken sentences. No bullet points, no emoji, no markdown."
    elif _intent_type == 'subway_station':
        _sys = "You are an NYC transit guide. Report only the nearest subway station name and the train lines it serves."
        _ollama = "State only the subway station name, its train lines, and distance from the DATA section in 1-2 plain spoken sentences. No bullet points, no emoji, no markdown."

    elif _intent_type in ("cuisine", "food"):
        _sys = "You are an NYC restaurant guide. Report restaurant names, health grades, and distances."
        _ollama = "State only the restaurant facts from the DATA section in 2-3 plain spoken sentences. Include restaurant names and grades. No bullet points, no emoji, no markdown."
    elif _intent_type == "cultural":
        _sys = "You are an NYC cultural guide. Report landmarks, venues, and cultural sites."
        _ollama = "State only the cultural and landmark facts from the DATA section in 2-3 plain spoken sentences. No bullet points, no emoji, no markdown."
    else:
        _sys = "You are an NYC urban assistant. Summarize the following data for someone walking nearby."
        _ollama = "State the raw facts from the DATA section in 2-3 plain spoken sentences. Report only what the data says. No bullet points, no emoji, no markdown, no advice."

    # For deterministic list-style intents, return directly (no LLM rewriting).
    if _intent_type in ('subway_station', 'food', 'cuisine', 'cultural', 'architecture', 'heat'):
        return context

    # Step 1: Nemotron summarizes the data freely (thinking is fine)
    nemotron_messages = [
        {"role": "system", "content": _sys},
        {"role": "user", "content": f"User asked: {user_text}\n\nData: {context}"}
    ]
    nemotron_output = call_nemotron(nemotron_messages, max_tokens=600, temperature=0.3)

    if not nemotron_output:
        return call_ollama(
            [{"role": "user", "content": f"Summarize in 2 sentences: {context}"}],
            max_tokens=80, temperature=0.3
        ) or context

    # Step 2: Ollama strips the thinking, returns only the factual answer
    # Pass BOTH the raw data AND Nemotron's output so Ollama knows what the facts are
    ollama_messages = [
        {
            "role": "system",
            "content": "You will receive raw data facts and an AI analysis. " + _ollama
        },
        {
            "role": "user",
            "content": f"DATA:\n{context}\n\nANALYSIS (ignore the advice, just use the data above):\n{nemotron_output[:300]}"
        }
    ]

    summary = call_ollama(ollama_messages, max_tokens=120, temperature=0.1)

    if summary and len(summary.strip()) > 10:
        return summary.strip()

    if "hazard" in context.lower() or "concern" in context.lower() or "alert" in context.lower():
        return "There are some safety concerns nearby."
    return "Area looks clear."


@app.get("/")
async def root():
    """Root endpoint with API info."""
    return {
        "service": "NemoClaw Agent",
        "version": "1.0.0",
        "endpoints": {
            "health": "/health",
            "docs": "/docs",
            "cold_query": "POST /v1/cold_query?latitude=X&longitude=Y&radius_meters=Z",
            "hot_query": "POST /v1/hot_query?latitude=X&longitude=Y",
            "collisions": "POST /v1/collisions?latitude=X&longitude=Y&radius_meters=Z",
            "accessibility": "POST /v1/accessibility?latitude=X&longitude=Y&radius_meters=Z",
            "heat": "POST /v1/heat?latitude=X&longitude=Y",
            "agent": "POST /v1/agent"
        }
    }


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "service": "nemoclaw"}


@app.post("/v1/agent", response_model=AgentResponseModel)
async def process_request(request: AgentRequest):
    """
    Main agent endpoint - processes multimodal input and returns safety guidance.
    
    Property 12: Routes to correct tool based on intent
    Property 13: Synthesizes natural language response
    Property 14: Returns clarifying question on low confidence
    """
    logger.info(f"Agent request: lat={request.latitude}, lon={request.longitude}, text={request.text}")
    
    # Record location visit for every request (fire-and-forget)
    from datetime import datetime, timezone
    _req_timestamp = datetime.now(timezone.utc).isoformat()
    _post_user_profile('/visits/location', {
        'latitude': request.latitude,
        'longitude': request.longitude,
    })

    # Step 1: Classify intent
    classification = classify_intent(request.text, request.image_b64)
    logger.info(f"Classification: {classification}")
    
    confidence = classification.get('confidence', 0.5)
    tool = classification.get('tool', 'none')
    
    intent = classification.get('intent', 'unclear')

    # Valid location-specific intents that should hit tools
    LOCATION_INTENTS = {
        'food', 'cuisine', 'transit', 'safety', 'collision',
        'heat', 'accessibility', 'cultural', 'architecture',
        'film', 'general', 'subway_station', 'nypd_alerts', 'transit_citywide'
    }

    # Pre-pass: if LLM returned a non-standard intent but user text has cuisine + location keywords
    _CUISINE_KW = {'italian','chinese','mexican','japanese','indian','thai','french','greek',
                   'korean','sushi','ramen','pizza','burger','burgers','american','caribbean',
                   'spanish','vietnamese','turkish','mediterranean','ethiopian','peruvian','brazilian'}
    _RESTAURANT_KW = {'restaurant', 'restaurants', 'food', 'dining', 'cafe', 'cafes'}
    _POI_KW = {'site of interest', 'sites of interest', 'point of interest', 'points of interest',
               'things to see', 'attraction', 'attractions', 'monument', 'monuments', 'landmark', 'landmarks'}
    _ARCH_KW = {'architecture', 'architectural', 'building', 'buildings', 'skyscraper', 'historic building'}
    _TRANSIT_ALERT_KW = {
        'alert', 'alerts', 'delay', 'delays', 'service change', 'service changes',
        'status', 'outage', 'disruption', 'disruptions', 'suspended', 'suspension',
        'planned work', 'weekend service', 'reroute', 'rerouted'
    }
    _TRANSIT_MODE_KW = {'subway', 'mta', 'train', 'trains', 'transit', 'bus', 'buses'}
    _CITYWIDE_KW = {'citywide', 'across the city', 'entire city', 'all city', 'all nyc', 'nyc-wide', 'all'}
    _NYPD_ALERT_KW = {'nypd', 'police', 'crime', 'public safety', 'incident', 'incidents'}
    _NON_LOCAL = {'paris', 'london', 'tokyo', 'rome', 'berlin', 'madrid', 'beijing', 'shanghai', 'dubai', 'sydney',
                  'toronto', 'chicago', 'los angeles', 'san francisco', 'miami', 'boston', 'seattle', 'las vegas'}
    _LOC_KW = ['near me','nearby','near here','around me','close by','in this area']
    _utl = (request.text or '').lower()
    if intent not in LOCATION_INTENTS and any(k in _utl for k in _CUISINE_KW):
        if any(l in _utl for l in _LOC_KW):
            intent = 'cuisine'
            tool = 'cuisine_query'
    if any(k in _utl for k in _RESTAURANT_KW):
        if any(k in _utl for k in _CUISINE_KW):
            intent = 'cuisine'
            tool = 'cuisine_query'
        elif not any(city in _utl for city in _NON_LOCAL):
            intent = 'food'
            tool = 'cold_query'
    if any(k in _utl for k in _POI_KW):
        intent = 'cultural'
        tool = 'cultural_query'
    if any(k in _utl for k in _ARCH_KW) and not any(k in _utl for k in _POI_KW):
        intent = 'architecture'
        tool = 'architecture_query'
    # Transit alerts/status should use live hot_query, not nearest-station lookup.
    if any(k in _utl for k in _TRANSIT_ALERT_KW) and any(k in _utl for k in _TRANSIT_MODE_KW):
        intent = 'transit'
        tool = 'hot_query'
    # Citywide transit alerts explicitly request all-city coverage.
    if any(k in _utl for k in _TRANSIT_ALERT_KW) and any(k in _utl for k in _TRANSIT_MODE_KW):
        if ('all subway alerts' in _utl) or ('all mta alerts' in _utl) or ('show all' in _utl and 'alert' in _utl) or any(k in _utl for k in _CITYWIDE_KW):
            intent = 'transit_citywide'
            tool = 'hot_query'
    # NYPD/police alerts should use hot_query with safety-focused formatting.
    if any(k in _utl for k in _TRANSIT_ALERT_KW) and any(k in _utl for k in _NYPD_ALERT_KW):
        intent = 'nypd_alerts'
        tool = 'hot_query'
    VALID_TOOLS = {
        'cold_query', 'cuisine_query', 'hot_query', 'collision_query',
        'heat_query', 'accessibility_query', 'cultural_query',
        'architecture_query', 'film_query', 'general', 'subway_query'
    }

    # Off-topic: intent not in location set, or tool hallucinated, or explicitly off_topic
    # Map of valid intent→tool pairs
    INTENT_TOOL_MAP = {
        'food': 'cold_query', 'cuisine': 'cuisine_query',
        'transit': 'hot_query', 'safety': 'hot_query',
        'collision': 'collision_query', 'heat': 'heat_query',
        'accessibility': 'accessibility_query', 'cultural': 'cultural_query',
        'architecture': 'architecture_query', 'film': 'film_query', 'subway_station': 'subway_query',
        'nypd_alerts': 'hot_query', 'transit_citywide': 'hot_query',
        'general': 'general',
    }
    is_off_topic = (
        intent == 'off_topic'
        or intent not in LOCATION_INTENTS
        or (tool == 'none' and intent not in ('unclear',))
        or (tool not in VALID_TOOLS and tool != 'none')  # hallucinated tool
    )
    # General intent with knowledge-seeking phrases → off_topic
    _KNOW_PHRASES = ['how do i ','how to ','what is a ','what is the history',
                     'explain ','tell me about','give me a recipe','recipe for',
                     'history of','definition of','what does ','why is ','why does',
                     'who invented','when was ','how does ']
    if not is_off_topic and intent == 'general':
        if any(p in _utl for p in _KNOW_PHRASES):
            is_off_topic = True

    # Check if food/restaurant query is about a non-local city
    _non_local = ['paris', 'london', 'tokyo', 'rome', 'berlin', 'madrid', 'beijing', 'shanghai', 'dubai', 'sydney', 'toronto', 'chicago', 'los angeles', 'san francisco', 'miami', 'boston', 'seattle', 'las vegas']
    _user_lower_check = (request.text or '').lower()
    if not is_off_topic and intent in ('food', 'cuisine') and any(c in _user_lower_check for c in _non_local):
        is_off_topic = True
    # Food/cuisine general knowledge (Michelin, recipes, history) → off_topic
    _FOOD_KNOW = ['michelin','what is a ','history of','how to make','recipe','origin of','invented']
    if not is_off_topic and intent in ('food', 'cuisine'):
        if any(p in _utl for p in _FOOD_KNOW) and not any(l in _utl for l in _LOC_KW):
            is_off_topic = True
    # Only keep cuisine intent if an explicit cuisine keyword exists.
    if intent == 'cuisine' and not any(k in _utl for k in _CUISINE_KW):
        intent = 'food'
        tool = 'cold_query'

    # Override tool with the correct one for the intent (prevents LLM hallucinating wrong tool)
    if not is_off_topic and intent in INTENT_TOOL_MAP:
        tool = INTENT_TOOL_MAP[intent]

    if is_off_topic:
        direct_answer = call_llm(
            [
                {"role": "system", "content": "You are a helpful assistant. Answer the user's question directly and concisely in 1-3 sentences. Do not show reasoning."},
                {"role": "user", "content": request.text or "Hello"}
            ],
            max_tokens=150,
            temperature=0.7
        )
        # Strip Nemotron <think>...</think> reasoning tokens
        if direct_answer:
            direct_answer = re.sub(r"<think>.*?</think>", "", direct_answer, flags=re.DOTALL).strip()
            if "</think>" in direct_answer:
                direct_answer = direct_answer.split("</think>", 1)[-1].strip()
        return AgentResponseModel(
            text=direct_answer or "I'm not sure about that one.",
            tool_used=None,
            confidence=confidence,
            requires_clarification=False
        )

    # Property 14: Low confidence / truly unclear — ask for clarification
    if confidence < CONFIDENCE_THRESHOLD or classification.get('clarification_needed'):
        return AgentResponseModel(
            text="I'm not sure what you're looking for. Are you asking about food safety, street conditions, or transit?",
            confidence=confidence,
            requires_clarification=True
        )
    
    # Step 2: Execute appropriate tool (Property 12)
    tool_result = None
    hazards = []
    
    try:
        if tool == 'cold_query':
            # Check if user is asking for a specific cuisine — redirect to cuisine_query
            _cuisine_map = {
                'italian': 'Italian', 'chinese': 'Chinese', 'mexican': 'Mexican',
                'japanese': 'Japanese', 'indian': 'Indian', 'thai': 'Thai',
                'french': 'French', 'greek': 'Greek', 'korean': 'Korean',
                'sushi': 'Japanese', 'sushi bar': 'Japanese', 'ramen': 'Japanese', 'pizza': 'Pizza', 'burger': 'Hamburgers', 'burgers': 'Hamburgers',
                'american': 'American', 'caribbean': 'Caribbean', 'spanish': 'Spanish',
                'vietnamese': 'Vietnamese', 'turkish': 'Turkish',
                'mediterranean': 'Mediterranean', 'ethiopian': 'African',
                'peruvian': 'Latin', 'brazilian': 'Brazilian',
            }
            _user_lower = (request.text or '').lower()
            _matched = next((v for k, v in _cuisine_map.items() if k in _user_lower), None)
            if _matched:
                from tools.cold_query import cold_query_by_cuisine
                _results = cold_query_by_cuisine(request.latitude, request.longitude, cuisine=_matched)
                tool_result = {'type': 'cuisine', 'cuisine': _matched, 'restaurants': _results, 'user_question': request.text or ''}
                hazards = [{'name': r.name, 'grade': r.grade} for r in _results if r.hazard]
            else:
                raw_results = cold_query(request.latitude, request.longitude)
                # Record place visits and score each result
                filtered = []
                for r in raw_results:
                    pid = getattr(r, 'place_id', None) or getattr(r, 'inspection_id', None)
                    _post_user_profile('/visits/place', {
                        'place_id': pid or r.name,
                        'latitude': request.latitude,
                        'longitude': request.longitude,
                    })
                    score_resp = _score_alert('restaurant', request.latitude, request.longitude,
                                              _req_timestamp, place_id=pid or r.name)
                    if not score_resp.get('suppressed', False):
                        filtered.append(r)
                tool_result = filtered
                hazards = [asdict(r) for r in tool_result if r.hazard]
            
        elif tool == 'hot_query':
            if intent == 'transit_citywide':
                citywide_alerts = _fetch_citywide_mta_alerts(limit=20)
                tool_result = {
                    'type': 'transit_citywide',
                    'alerts': citywide_alerts,
                }
                response_text = synthesize_response(tool_result, intent, request.text or "")
                _post_user_profile('/signals', {'alert_type': 'transit', 'signal': 'positive'})
                _post_user_profile('/interests', {'topic': 'transit', 'delta': 0.05})
                return AgentResponseModel(
                    text=response_text,
                    tool_used='hot_query',
                    confidence=confidence,
                    requires_clarification=False,
                    hazards=[],
                    qol_score=0
                )

            if intent == 'nypd_alerts':
                latest_nypd = _fetch_latest_citywide_nypd_alert()
                tool_result = {
                    'type': 'nypd_citywide_latest',
                    'alert': latest_nypd,
                }
                hazards = []
                # Skip nearby hot query for NYPD intent; this is citywide latest-only by design.
                response_text = synthesize_response(tool_result, intent, request.text or "")
                _post_user_profile('/signals', {'alert_type': 'safety', 'signal': 'positive'})
                _post_user_profile('/interests', {'topic': 'safety', 'delta': 0.05})
                return AgentResponseModel(
                    text=response_text,
                    tool_used='hot_query',
                    confidence=confidence,
                    requires_clarification=False,
                    hazards=[],
                    qol_score=0
                )

            hot_result = hot_query(request.latitude, request.longitude)
            # Score and record transit patterns
            filtered_results = []
            for r in hot_result.results:
                if r.type == 'MTA':
                    route_id = getattr(r, 'route_id', None)
                    _post_user_profile('/visits/transit', {
                        'route_id': route_id or 'unknown',
                        'timestamp': _req_timestamp,
                    })
                    score_resp = _score_alert('transit', request.latitude, request.longitude,
                                              _req_timestamp, route_id=route_id)
                else:
                    score_resp = _score_alert('safety', request.latitude, request.longitude,
                                              _req_timestamp)
                if not score_resp.get('suppressed', False):
                    filtered_results.append(r)
            hot_result.results = filtered_results
            hazards = [asdict(r) for r in hot_result.results if r.severity in [Severity.HIGH, Severity.MEDIUM]]
            if intent == 'transit':
                nearby_routes, _stations = get_nearby_subway_routes(request.latitude, request.longitude)
                tool_result = {
                    'type': 'transit_alerts',
                    'result': hot_result,
                    'nearby_routes': sorted(nearby_routes),
                }
            elif intent == 'nypd_alerts':
                tool_result = {
                    'type': 'nypd_alerts',
                    'result': hot_result,
                }
            else:
                tool_result = hot_result
        elif tool == 'collision_query':
            tool_result = query_collision_hotspots(request.latitude, request.longitude)
            hazards = [asdict(r) for r in tool_result]

        elif tool == 'accessibility_query':
            tool_result = query_accessibility(request.latitude, request.longitude)
            # accessibility returns a dict, not a list
            hazards = []

        elif tool == 'heat_query':
            result = query_heat_vulnerability(request.latitude, request.longitude)
            tool_result = {'type': 'heat', 'result': result if result else {}}
            hazards = []

        elif tool == 'cultural_query':
            tool_result = cultural_query(request.latitude, request.longitude, radius_meters=1200)
            hazards = []

        elif tool == 'subway_query':
            # Nearest subway stations with all lines they serve
            _routes, _stations = get_nearby_subway_routes(request.latitude, request.longitude)
            tool_result = {
                'type': 'subway_station',
                'stations': _stations[:2],
                'user_question': request.text or ''
            }
            hazards = []

        elif tool == 'architecture_query':
            # Only landmarks — no venues or film permits
            from tools.cultural_query import query_landmarks
            landmarks = query_landmarks(request.latitude, request.longitude, radius_meters=500)
            # Filter to buildings with style or architect info (famous/notable buildings)
            notable = [l for l in landmarks if l.style or l.year_built]
            tool_result = {'type': 'architecture', 'landmarks': notable or landmarks, 'user_question': request.text or ''}
            hazards = []

        elif tool == 'film_query':
            # Named productions from Wikidata + active permits from NYC Open Data
            from tools.cultural_query import query_film_permits, query_wikidata_productions
            permits = query_film_permits(request.latitude, request.longitude, radius_meters=1000)
            shooting = [p for p in permits if 'shoot' in p.event_type.lower() or p.category in ('Television', 'Film', 'Commercial', 'Still Photography')]
            productions = query_wikidata_productions(request.latitude, request.longitude, radius_meters=2000)
            tool_result = {
                'type': 'film',
                'permits': shooting or permits,
                'productions': productions,
                'user_question': request.text or ''
            }
            hazards = []

        elif tool == 'cuisine_query':
            # Restaurant search with cuisine filter extracted from user text
            import re as _re
            user_text_lower = (request.text or '').lower()
            cuisine_keywords = {
                'italian': 'Italian', 'chinese': 'Chinese', 'mexican': 'Mexican',
                'japanese': 'Japanese', 'indian': 'Indian', 'thai': 'Thai',
                'french': 'French', 'greek': 'Greek', 'korean': 'Korean',
                'sushi': 'Japanese', 'sushi bar': 'Japanese', 'ramen': 'Japanese', 'pizza': 'Pizza', 'burger': 'Hamburgers', 'burgers': 'Hamburgers',
                'american': 'American', 'caribbean': 'Caribbean', 'spanish': 'Spanish',
                'vietnamese': 'Vietnamese', 'turkish': 'Turkish', 'mediterranean': 'Mediterranean',
                'ethiopian': 'African', 'peruvian': 'Latin', 'brazilian': 'Brazilian',
            }
            matched_cuisine = next(
                (v for k, v in cuisine_keywords.items() if k in user_text_lower),
                None
            )
            from tools.cold_query import cold_query_by_cuisine
            results = cold_query_by_cuisine(request.latitude, request.longitude, cuisine=matched_cuisine)
            tool_result = {'type': 'cuisine', 'cuisine': matched_cuisine, 'restaurants': results, 'user_question': request.text or ''}
            hazards = [{'name': r.name, 'grade': r.grade} for r in results if r.hazard]

        else:
            # General conversational query — gather all datasets, synthesize only what's relevant
            hot_result = hot_query(request.latitude, request.longitude)
            collision_result = query_collision_hotspots(request.latitude, request.longitude)
            heat_result = query_heat_vulnerability(request.latitude, request.longitude) or {}
            accessibility_result = query_accessibility(request.latitude, request.longitude)
            cultural_result = cultural_query(request.latitude, request.longitude)

            hazards = [asdict(r) for r in hot_result.results if r.severity in [Severity.HIGH, Severity.MEDIUM]]
            hazards.extend([asdict(r) for r in collision_result])

            tool_result = {
                "hot": hot_result,
                "collisions": collision_result,
                "heat": heat_result,
                "accessibility": accessibility_result,
                "cultural": cultural_result,
                "user_question": request.text or "",
            }
            tool = "general"
            
    except RuntimeError as e:
        logger.error(f"Tool execution error: {e}")
        return AgentResponseModel(
            text="I'm having trouble checking the area right now. Please try again.",
            tool_used=tool,
            confidence=confidence,
            hazards=[]
        )
    
    # Step 3: Synthesize response (Property 13)
    response_text = synthesize_response(tool_result, intent, request.text or "")
    
    # Calculate simple QoL score
    qol_score = 0
    for h in hazards:
        severity = h.get('severity', 'low')
        if severity == 'high' or severity == Severity.HIGH:
            qol_score -= 10
        elif severity == 'medium' or severity == Severity.MEDIUM:
            qol_score -= 5
        else:
            qol_score -= 2
    
    # Post positive engagement signal (user asked → positive intent)
    intent = classification.get('intent', '')
    _alert_type_for_signal = {
        'food': 'restaurant', 'transit': 'transit',
        'safety': 'safety', 'collision': 'safety',
        'general': 'safety', 'cultural': 'discovery',
        'nypd_alerts': 'safety', 'transit_citywide': 'transit',
    }.get(intent, 'safety')
    _post_user_profile('/signals', {
        'alert_type': _alert_type_for_signal,
        'signal': 'positive',
    })
    # Update interest weight
    _topic = ALERT_TYPE_TOPIC.get(_alert_type_for_signal, 'safety')
    _post_user_profile('/interests', {'topic': _topic, 'delta': 0.05})

    return AgentResponseModel(
        text=response_text,
        tool_used=tool,
        confidence=confidence,
        requires_clarification=False,
        hazards=hazards[:10],  # Limit to 10 hazards
        qol_score=qol_score
    )


@app.post("/v1/cold_query")
async def cold_query_endpoint(latitude: float, longitude: float, radius_meters: int = 500):
    """Direct cold_query endpoint for testing."""
    try:
        results = cold_query(latitude, longitude, radius_meters)
        return {"results": [asdict(r) for r in results]}
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/v1/hot_query")
async def hot_query_endpoint(latitude: float, longitude: float):
    """Direct hot_query endpoint for testing."""
    try:
        result = hot_query(latitude, longitude)
        return {
            "results": [asdict(r) for r in result.results],
            "partial": result.partial,
            "error_sources": result.error_sources
        }
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/v1/collisions")
async def collisions_endpoint(latitude: float, longitude: float, radius_meters: int = 500):
    """Query collision hotspots near coordinates."""
    try:
        results = query_collision_hotspots(latitude, longitude, radius_meters)
        return {"results": [asdict(r) for r in results]}
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/v1/accessibility")
async def accessibility_endpoint(latitude: float, longitude: float, radius_meters: int = 200):
    """Query accessibility features (subway entrances, ramps) near coordinates."""
    try:
        result = query_accessibility(latitude, longitude, radius_meters)
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/v1/heat")
async def heat_endpoint(latitude: float, longitude: float):
    """Query heat vulnerability index for location."""
    try:
        result = query_heat_vulnerability(latitude, longitude)
        return {"result": result}
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/v1/edge_cases")
async def edge_cases_endpoint(
    latitude: float,
    longitude: float,
    require_ada: bool = False
):
    """
    Detect edge cases and special situations at location.
    
    Returns alerts for:
    - Time-based issues (late night, rush hour, weekend service)
    - Location-based issues (parks, airports, water)
    - Transit issues (no subway nearby, multiple lines affected)
    - Accessibility issues (no ADA access)
    - Safety issues (multiple hazards, construction zones)
    - Data quality issues (stale data)
    """
    try:
        # Get nearby subway routes
        nearby_routes, nearby_stations = get_nearby_subway_routes(latitude, longitude)
        
        # Get current alerts and complaints
        hot_result = hot_query(latitude, longitude)
        mta_alerts = [r for r in hot_result.results if r.type == "MTA"]
        complaints_311 = [r for r in hot_result.results if r.type == "311"]
        
        # Get collision data
        collisions = query_collision_hotspots(latitude, longitude)
        collision_dicts = [asdict(c) for c in collisions]
        
        # Detect edge cases
        alerts = detect_edge_cases(
            latitude=latitude,
            longitude=longitude,
            nearby_routes=nearby_routes,
            mta_alerts=mta_alerts,
            complaints_311=complaints_311,
            collisions=collision_dicts,
            require_ada=require_ada
        )
        
        return {
            "alerts": [
                {
                    "type": alert.case_type.value,
                    "message": alert.message,
                    "severity": alert.severity.value,
                    "recommendation": alert.recommendation,
                    "affected_services": alert.affected_services
                }
                for alert in alerts
            ],
            "nearby_stations": nearby_stations[:5],
            "nearby_routes": list(nearby_routes)
        }
        
    except Exception as e:
        logger.error(f"Edge case detection error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/v1/situation_report")
async def situation_report_endpoint(
    latitude: float,
    longitude: float,
    require_ada: bool = False
):
    """
    Comprehensive situation report combining all data sources.
    
    Returns a complete picture of the user's surroundings including:
    - Nearby restaurants with health grades
    - Active 311 complaints
    - MTA service alerts (prioritized by nearby stations)
    - Collision hotspots
    - Accessibility info
    - Edge case alerts
    """
    try:
        # Gather all data
        restaurants = cold_query(latitude, longitude, radius_meters=300)
        hot_result = hot_query(latitude, longitude)
        collisions = query_collision_hotspots(latitude, longitude, radius_meters=300)
        accessibility = query_accessibility(latitude, longitude)
        
        # Get nearby routes for context
        nearby_routes, nearby_stations = get_nearby_subway_routes(latitude, longitude)
        
        # Detect edge cases
        mta_alerts = [r for r in hot_result.results if r.type == "MTA"]
        complaints_311 = [r for r in hot_result.results if r.type == "311"]
        
        edge_alerts = detect_edge_cases(
            latitude=latitude,
            longitude=longitude,
            nearby_routes=nearby_routes,
            mta_alerts=mta_alerts,
            complaints_311=complaints_311,
            collisions=[asdict(c) for c in collisions],
            require_ada=require_ada
        )
        
        # Calculate overall safety score
        safety_score = 100
        
        # Deduct for hazardous restaurants
        hazardous_restaurants = [r for r in restaurants if r.hazard]
        safety_score -= len(hazardous_restaurants) * 5
        
        # Deduct for high-severity alerts
        high_severity = [r for r in hot_result.results if r.severity == Severity.HIGH]
        safety_score -= len(high_severity) * 10
        
        # Deduct for collision hotspots
        fatal_collisions = [c for c in collisions if c.pedestrians_killed > 0]
        safety_score -= len(fatal_collisions) * 15
        
        # Deduct for edge case alerts
        for alert in edge_alerts:
            if alert.severity == Severity.HIGH:
                safety_score -= 10
            elif alert.severity == Severity.MEDIUM:
                safety_score -= 5
        
        safety_score = max(0, min(100, safety_score))
        
        # Determine safety level
        if safety_score >= 80:
            safety_level = "good"
        elif safety_score >= 60:
            safety_level = "fair"
        elif safety_score >= 40:
            safety_level = "moderate"
        elif safety_score >= 20:
            safety_level = "poor"
        else:
            safety_level = "critical"
        
        return {
            "location": {"latitude": latitude, "longitude": longitude},
            "safety_score": safety_score,
            "safety_level": safety_level,
            "summary": {
                "restaurants_nearby": len(restaurants),
                "restaurants_hazardous": len(hazardous_restaurants),
                "active_complaints": len(complaints_311),
                "mta_alerts": len(mta_alerts),
                "collision_hotspots": len(collisions),
                "edge_case_alerts": len(edge_alerts)
            },
            "nearby_transit": {
                "stations": nearby_stations[:3],
                "routes": list(nearby_routes)
            },
            "accessibility": accessibility,
            "alerts": [
                {
                    "type": alert.case_type.value,
                    "message": alert.message,
                    "severity": alert.severity.value,
                    "recommendation": alert.recommendation
                }
                for alert in edge_alerts[:5]
            ],
            "top_hazards": [
                asdict(r) for r in hot_result.results 
                if r.severity in [Severity.HIGH, Severity.MEDIUM]
            ][:5]
        }
        
    except Exception as e:
        logger.error(f"Situation report error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/v1/cultural")
async def cultural_endpoint(
    latitude: float,
    longitude: float,
    radius_meters: int = 300
):
    """
    Query cultural data - film permits, landmarks, museums.
    
    Returns immersive narrative content for AR experience.
    """
    try:
        result = cultural_query(latitude, longitude, radius_meters)
        
        return {
            "narrative": result.narrative,
            "film_permits": [
                {
                    "event_type": fp.event_type,
                    "category": fp.category,
                    "subcategory": fp.subcategory,
                    "location": fp.location,
                    "is_active": fp.is_active,
                    "distance_meters": fp.distance_meters
                }
                for fp in result.film_permits
            ],
            "landmarks": [
                {
                    "name": lm.name,
                    "address": lm.address,
                    "style": lm.style,
                    "year_built": lm.year_built,
                    "distance_meters": lm.distance_meters
                }
                for lm in result.landmarks
            ],
            "venues": [
                {
                    "name": v.name,
                    "venue_type": v.venue_type,
                    "website": v.website,
                    "distance_meters": v.distance_meters
                }
                for v in result.venues
            ]
        }
        
    except Exception as e:
        logger.error(f"Cultural query error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    if not validate_environment():
        sys.exit(1)
    
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8090)
