# Nemo Assistant

An edge-native AI wearable system that connects Meta Ray-Ban Smart Glasses to a smartphone relay, which tunnels over a Tailscale mesh VPN to an NVIDIA DGX Spark (GB10 Grace Blackwell) running all AI inference locally. The system provides real-time spoken alerts about food safety, street conditions, transit disruptions, pedestrian hazards, filming locations, architecture, and neighborhood context by combining continuous voice transcription, intermittent camera vision, and live NYC Open Data.

No paid cloud compute. No third-party AI APIs. All inference runs on-device.

---

## Architecture Overview

```
Meta Ray-Ban Glasses (Bluetooth audio)
        |
        v
iOS Application (NemoAssistant)
  - Captures PCM audio from glasses mic via AVAudioSession (BluetoothHFP)
  - Streams audio frames over WebSocket to middleware
  - Sends GPS location updates every location change
  - Receives text responses and speaks them via AVSpeechSynthesizer
  - Receives proactive alert notifications every 5 minutes
        |
        | WebSocket (Acer Veriton, ws://[tailscale-ip]:8080)
        | Tailscale mesh VPN (WireGuard)
        v
NVIDIA DGX Spark — Ubuntu 22.04, Docker Compose, metaglass-net bridge
  |
  |-- middleware (Node.js 20)
  |     WebSocket server, ASR client, NemoClaw HTTP client,
  |     proactive alert engine, cooldown cache
  |
  |-- riva-asr (NeMo Parakeet TDT 0.6B v3)
  |     Speech-to-text, 16kHz PCM input, HTTP /transcribe
  |
  |-- nemoclaw (Python 3.11, FastAPI)
  |     Intent classification, tool dispatch, response synthesis
  |     Calls user-profile for personalization scoring
  |
  |-- nemotron-nim (Nemotron-3-Nano-30B-A3B via llama-cpp-python)
  |     LLM inference, OpenAI-compatible HTTP API on port 8000
  |     Full GPU offload on GB10, n_ctx=4096
  |
  |-- user-profile (Python 3.11, FastAPI)
  |     Behavioral personalization engine
  |     Tracks place visits, transit patterns, alert engagement
  |     Computes priority scores and suppression decisions
  |
  |-- postgres-postgis (PostgreSQL 15 + PostGIS 3.4)
  |     Spatial restaurant data, collision hotspots, landmarks
  |     Film permit data, Wikidata productions, subway entrances
  |     User behavioral data (place_visits, transit_patterns, etc.)
  |
  |-- data-pipeline (Python 3.11)
        Nightly ingestion of NYC Open Data and Wikidata
        APScheduler cron at 02:00 UTC
```

---

## Hardware

- **NVIDIA DGX Spark** — Acer Veriton GN100 with NVIDIA GB10 Grace Blackwell SoC
- **GPU** — NVIDIA GB10, 128GB unified memory, CUDA 13.0, Driver 580.142
- **Storage** — 3.67TB NVMe
- **Network** — Tailscale mesh VPN (WireGuard) for secure phone-to-server tunnel

---

## Tech Stack

### AI and Inference

| Component | Technology | Version | Purpose |
|---|---|---|---|
| LLM | Nemotron-3-Nano-30B-A3B (GGUF) | llama-cpp-python 0.3.20 | Intent classification, response synthesis, off-topic answering |
| ASR | NeMo Parakeet TDT 0.6B v3 | NeMo Toolkit 2.2.0 | Speech-to-text transcription from glasses mic |
| TTS | iOS AVSpeechSynthesizer | iOS native | Text-to-speech output through glasses speakers |
| Inference runtime | llama-cpp-python | 0.3.20 | GGUF model serving with full GPU offload |
| CUDA | CUDA Toolkit | 13.0.2 | GPU acceleration for all inference |

### Backend Services

| Service | Technology | Version | Purpose |
|---|---|---|---|
| Middleware | Node.js + Express + ws | Node 20, Express 4.18, ws 8.14 | WebSocket server, ASR client, proactive alert engine |
| NemoClaw agent | Python + FastAPI + uvicorn | FastAPI 0.104, uvicorn 0.24 | Tool dispatch, intent routing, response synthesis |
| User profile | Python + FastAPI + uvicorn | FastAPI 0.115, uvicorn 0.30 | Behavioral personalization, priority scoring |
| Data pipeline | Python + APScheduler | APScheduler 3.10 | Nightly NYC Open Data ingestion |
| Database | PostgreSQL + PostGIS | PostgreSQL 15, PostGIS 3.4 | Spatial queries, behavioral data, all datasets |

### Data and Spatial

| Component | Technology | Version | Purpose |
|---|---|---|---|
| Spatial indexing | PostGIS ST_DWithin + GIST | 3.4 | Radius-based queries for all location lookups |
| Geospatial indexing | H3 (Uber) | 3.7.7 | Hexagonal neighborhood cells for familiarity scoring |
| ORM / migrations | Alembic + psycopg2 | Alembic 1.13, psycopg2 2.9.9 | Schema migrations, connection pooling |
| Data processing | pandas + geopandas + shapely | pandas 2.x, geopandas 0.14 | CSV ingestion, geometry construction |

### Networking and Infrastructure

| Component | Technology | Purpose |
|---|---|---|
| VPN | Tailscale (WireGuard) | Secure mesh tunnel between phone and DGX |
| Container orchestration | Docker Compose | All services on metaglass-net bridge network |
| HTTP client | axios 1.6 | Middleware to NemoClaw communication |
| Logging | pino 8.17 (Node.js), structlog 24.4 (Python) | Structured JSON logs across all services |

### iOS Application

| Component | Technology | Purpose |
|---|---|---|
| WebSocket | URLSessionWebSocketTask | Persistent connection to middleware |
| Audio capture | AVAudioSession + AVAudioEngine | PCM capture from glasses mic via BluetoothHFP |
| Speech output | AVSpeechSynthesizer | Text-to-speech routed to glasses speakers |
| Location | CoreLocation | GPS coordinates sent with every audio frame |
| Notifications | UNUserNotificationCenter | Proactive alert display as local notifications |
| Protocol | Acer Veriton (JSON over WebSocket) | Message framing compatible with existing iOS client |

---

## Datasets

| Dataset | Source | Update Frequency | Used For |
|---|---|---|---|
| NYC Restaurant Inspections | NYC Open Data (Socrata) | Nightly | Health grade warnings, hazard detection |
| NYC 311 Complaints | NYC Open Data (Socrata) | Live (hot_query) | Street condition alerts, noise, construction |
| MTA GTFS-RT | MTA Real-Time Feeds | Live (hot_query) | Subway delay and service change alerts |
| Vision Zero Collisions | NYC Open Data (Socrata) | Nightly | Pedestrian collision hotspot warnings |
| NYC Film Permits | NYC Open Data (Socrata) | Nightly | Active filming locations (no production names) |
| Wikidata Filming Locations | Wikidata SPARQL | Nightly | Named film and TV productions by location (1,145 titles) |
| NYC Landmarks | NYC LPC | Static | Historic building identification |
| NYC Cultural Venues | NYC Open Data | Static | Museums, galleries, theaters |
| NYC Subway Entrances | NYC Open Data | Static | Nearest station lookup with ADA status |
| Heat Vulnerability Index | NYC DOHMH | Static | Neighborhood heat risk scoring |
| NYC Cooling Centers | NYC Open Data | Static | Nearest cooling center lookup |

---

## Services Detail

### Middleware (Node.js 20)

Implements the WebSocket protocol so the iOS app connects without modification. Handles:

- Binary and JSON WebSocket message demultiplexing
- PCM audio accumulation and forwarding to Parakeet ASR via HTTP
- NemoClaw HTTP client for agent queries
- Proactive alert engine: fires every 5 minutes per connected client, fetches situation report, formats as bullet points, pushes via WebSocket
- Admin endpoint `POST /admin/alert` for manual alert triggering
- Per-client state tracking (GPS coordinates, processing state)
- Cooldown cache for manual alert deduplication (30-minute TTL)

### NemoClaw Agent (Python 3.11)

FastAPI service implementing the full query pipeline:

- Intent classification via Nemotron LLM with 12 distinct intents
- Tool dispatch to 10 specialized query functions
- Off-topic detection: general knowledge questions answered directly by LLM without touching any dataset
- Non-local city detection for food queries
- Two-step synthesis: Nemotron for analysis, Ollama/Nemotron for final spoken output
- User-profile integration: records location, place visits, transit patterns; checks priority scores before surfacing alerts
- Positive reinforcement signal posting after every successful response

Supported intents and their tools:

| Intent | Tool | Data Source |
|---|---|---|
| food | cold_query | PostGIS restaurants |
| cuisine | cuisine_query | PostGIS restaurants filtered by cuisine |
| transit | hot_query | MTA GTFS-RT live feed |
| safety | hot_query | NYC 311 Socrata live feed |
| collision | collision_query | PostGIS Vision Zero data |
| heat | heat_query | PostGIS HVI data |
| accessibility | accessibility_query | PostGIS subway entrances |
| cultural | cultural_query | PostGIS landmarks + venues + film permits |
| architecture | architecture_query | PostGIS landmarks (buildings only) |
| film | film_query | Wikidata productions + NYC film permits |
| subway_station | subway_query | PostGIS subway entrances |
| general | all datasets | Combined situation report |

### User Profile Service (Python 3.11)

Behavioral personalization engine running on port 8081:

- Tracks place visits, transit patterns, neighborhood familiarity (H3 resolution 9), alert engagement, and interest weights
- Computes priority scores using engagement ratio, interest weights, visit penalties, and familiarity bonuses
- Suppresses alerts below a configurable threshold (default 0.2)
- Proactive transit surfacing: flags routes with 5+ visits at the same hour/day
- Nightly cleanup job at 03:00 UTC removes data older than configurable retention window (default 90 days)
- All data remains on-device; no external transmission

### Data Pipeline (Python 3.11)

APScheduler-based nightly ingestion at 02:00 UTC:

- Restaurant inspections: fetches CSV from Socrata, drops no-violation rows, upserts with PostGIS geometry
- Vision Zero collisions: fetches JSON, filters to last 3 years, upserts with geometry
- Wikidata productions: SPARQL query for NYC filming locations by production type, paginated in batches of 500, filters generic city-centroid coordinates
- All ingesters use psycopg2 transactions with rollback on failure

---

## Alert System

The proactive alert system pushes location-based notifications to the iOS app without user interaction:

- Timer starts 15 seconds after WebSocket connection (allows GPS to arrive)
- Fires every 5 minutes while connected, regardless of content repetition
- Fetches full situation report: MTA alerts, 311 complaints, collision hotspots, edge case alerts
- Formats results as bullet points
- Sends via WebSocket as `serverContent.proactiveAlert.bullets`
- iOS app renders bullets as a local `UNUserNotificationCenter` notification

Manual trigger available via `POST /admin/alert` on the middleware HTTP server.

---

## Personalization

The user-profile service implements a local reinforcement learning loop:

- Every agent request records a GPS location visit (H3 cell at resolution 9)
- Restaurant queries record place visits and check suppression scores
- Transit queries record route patterns and check proactive flags
- Successful responses post positive engagement signals and increment interest weights
- Interest weights (food, transit, safety) adjust alert priority scores over time
- Neighborhood familiarity reduces discovery alert verbosity in frequently visited areas

---

## Running the Stack

Requirements: Docker, Docker Compose, Tailscale, NVIDIA GPU with CUDA 13.0+

```bash
cp .env.example .env
# Fill in DATABASE_URL, MTA_API_KEY, SOCRATA_APP_TOKEN, TAILSCALE_GN100_IP

docker compose up -d
```

Services start in dependency order. The user-profile service runs Alembic migrations on startup. The data pipeline runs its first ingestion at 02:00 UTC.

Health checks:

```bash
curl http://localhost:8081/health   # user-profile
curl http://localhost:8080/health   # middleware
curl http://localhost:8090/health   # nemoclaw
```

Manual alert trigger:

```bash
curl -X POST http://[tailscale-ip]:8080/admin/alert
```

---

## Environment Variables

| Variable | Service | Description |
|---|---|---|
| DATABASE_URL | user-profile, nemoclaw | PostgreSQL connection string |
| POSTGRES_HOST / PORT / USER / PASSWORD / DB | nemoclaw, data-pipeline | Individual DB connection params |
| TAILSCALE_GN100_IP | middleware | DGX Tailscale IP for WebSocket binding |
| NEMOCLAW_HOST / PORT | middleware | NemoClaw service address |
| MTA_API_KEY | nemoclaw | MTA GTFS-RT API key |
| SOCRATA_APP_TOKEN | nemoclaw | NYC Open Data app token |
| BEHAVIOR_RETENTION_DAYS | user-profile | Days to retain behavioral data (default 90) |
| LOG_LEVEL | all services | Logging verbosity (default INFO) |
| NEMOTRON_NIM_HOST / PORT | nemoclaw | Nemotron inference server address |
| NEMOTRON_MODEL_NAME | nemoclaw | Model identifier for LLM calls |
| NEMOCLAW_CONFIDENCE_THRESHOLD | nemoclaw | Minimum classification confidence (default 0.7) |

---

## Project Structure

```
pseudo-meta-glass/
├── docker-compose.yml
├── .env.example
├── services/
│   ├── middleware/          Node.js WebSocket server and alert engine
│   ├── nemoclaw/            Python agent with tool dispatch
│   ├── user-profile/        Python personalization service
│   ├── riva-asr/            NeMo Parakeet ASR HTTP server
│   └── riva-tts/            NeMo FastPitch TTS HTTP server (unused, replaced by iOS AVSpeechSynthesizer)
└── data/
    └── human-impact/        Data pipeline and ingesters
```
