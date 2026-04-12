# pseudoMetaGlass

Edge-native AI wearable system connecting Meta Ray-Ban Smart Glasses to an Acer GN100 (NVIDIA GB10 Grace Blackwell) for real-time urban safety guidance.

## Architecture

```
Meta Ray-Ban Glasses
        │
        ▼ (Bluetooth)
   Smartphone App
        │
        ▼ (Tailscale VPN)
┌───────────────────────────────────────────────────────┐
│  GN100 - Docker Compose                               │
│                                                       │
│  ┌─────────────┐    ┌─────────────┐    ┌──────────┐ │
│  │  Middleware │───▶│  NemoClaw   │───▶│ Nemotron │ │
│  │  (Node.js)  │    │  (FastAPI)  │    │   NIM    │ │
│  └──────┬──────┘    └──────┬──────┘    └──────────┘ │
│         │                  │                         │
│  ┌──────▼──────┐    ┌──────▼──────┐                 │
│  │  Riva ASR   │    │  PostGIS    │◀── Data Pipeline│
│  │  Riva TTS   │    │  (Cold DB)  │                 │
│  └─────────────┘    └─────────────┘                 │
└───────────────────────────────────────────────────────┘
```

## Quick Start

```bash
# Copy environment config
cp .env.example .env
# Edit .env with your credentials

# Start all services
docker-compose up -d

# Check logs
docker-compose logs -f middleware
```

## Services

| Service | Port | Description |
|---------|------|-------------|
| middleware | 8080 | WebSocket server for phone connection |
| nemoclaw | 8090 | AI agent with tool dispatch |
| postgres-postgis | 5432 | Spatial database for cold data |
| data-pipeline | - | Nightly batch ingestion |
| riva-asr | 50051 | Speech recognition (Parakeet) |
| riva-tts | 50052 | Speech synthesis (FastPitch) |
| nemotron-nim | 8000 | LLM inference (Nemotron 3 Nano) |

## Data Sources

### Cold (PostGIS - Nightly Refresh)
- DOHMH Restaurant Inspections (health grades)
- NYPD Motor Vehicle Collisions (Vision Zero)
- Heat Vulnerability Index
- Pedestrian Mobility Network
- Subway Entrances (ADA status)

### Hot (Live APIs)
- 311 Service Requests (noise, construction, hazards)
- MTA GTFS-RT (service alerts, delays)

## API Endpoints

### Middleware WebSocket
```
ws://<TAILSCALE_IP>:8080/ws
```

Binary frame protocol:
- `0x01` - Audio frame (PCM 16kHz/16-bit/mono)
- `0x02` - Vision frame (JPEG + GPS)

### NemoClaw Agent
```
POST /v1/agent
{
  "text": "Is this restaurant safe?",
  "image_b64": "<base64 JPEG>",
  "latitude": 40.7128,
  "longitude": -74.0060
}
```

## Testing

```bash
# Middleware tests
cd services/middleware && npm test

# Data pipeline tests
cd services/data-pipeline && pytest

# NemoClaw tests
cd services/nemoclaw && pytest
```

## Design Properties

See the design document for the 35 correctness properties that define system behavior, including:

- P15: cold_query hazard flag (grade C OR score > 28)
- P17: hot_query results sorted by distance
- P19: Cooldown cache 30-minute TTL
- P21: Restaurant data cleaning rules

## Hackathon Metrics

The system tracks "Successful Hazard Avoidances" in the telemetry_events table:

```sql
SELECT 
  COUNT(*) FILTER (WHERE user_followed = true) as avoidances,
  json_agg(DISTINCT hazard_type) as hazard_types
FROM telemetry_events
WHERE timestamp > NOW() - INTERVAL '4 hours';
```
