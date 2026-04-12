-- pseudoMetaGlass PostGIS Schema Initialization
-- Creates tables for Cold datasets and telemetry

CREATE EXTENSION IF NOT EXISTS postgis;

-- ============================================================================
-- RESTAURANTS TABLE (DOHMH NYC Restaurant Inspection Results)
-- Cold dataset: Health grades A/B/C and critical violations
-- ============================================================================
CREATE TABLE IF NOT EXISTS restaurants (
    inspection_id   TEXT        PRIMARY KEY,
    camis           TEXT,                        -- Unique restaurant ID
    dba             TEXT        NOT NULL,        -- Doing Business As (name)
    boro            TEXT,
    building        TEXT,
    street          TEXT,
    zipcode         TEXT,
    phone           TEXT,
    cuisine_description TEXT,
    inspection_date DATE,
    action          TEXT,
    violation_code  TEXT,
    violation_description TEXT,
    critical_flag   TEXT,                        -- 'Critical' or 'Not Critical'
    score           INTEGER,
    grade           CHAR(1),                     -- A, B, C, Z, P
    grade_date      DATE,
    record_date     DATE,
    inspection_type TEXT,
    geom            GEOMETRY(Point, 4326),
    created_at      TIMESTAMP   DEFAULT NOW(),
    updated_at      TIMESTAMP   DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS restaurants_geom_gist 
    ON restaurants USING GIST (geom);
CREATE INDEX IF NOT EXISTS restaurants_camis_idx 
    ON restaurants (camis);
CREATE INDEX IF NOT EXISTS restaurants_grade_idx 
    ON restaurants (grade);
CREATE INDEX IF NOT EXISTS restaurants_inspection_date_idx 
    ON restaurants (inspection_date DESC);

-- ============================================================================
-- COLLISIONS TABLE (NYPD Motor Vehicle Collisions / Vision Zero)
-- Cold dataset: Historically dangerous intersections
-- ============================================================================
CREATE TABLE IF NOT EXISTS collisions (
    collision_id        TEXT        PRIMARY KEY,
    crash_date          DATE        NOT NULL,
    crash_time          TIME,
    borough             TEXT,
    zip_code            TEXT,
    on_street_name      TEXT,
    cross_street_name   TEXT,
    off_street_name     TEXT,
    number_of_persons_injured   INTEGER DEFAULT 0,
    number_of_persons_killed    INTEGER DEFAULT 0,
    number_of_pedestrians_injured INTEGER DEFAULT 0,
    number_of_pedestrians_killed  INTEGER DEFAULT 0,
    number_of_cyclist_injured     INTEGER DEFAULT 0,
    number_of_cyclist_killed      INTEGER DEFAULT 0,
    number_of_motorist_injured    INTEGER DEFAULT 0,
    number_of_motorist_killed     INTEGER DEFAULT 0,
    contributing_factor_vehicle_1 TEXT,
    contributing_factor_vehicle_2 TEXT,
    vehicle_type_code_1 TEXT,
    vehicle_type_code_2 TEXT,
    geom                GEOMETRY(Point, 4326),
    created_at          TIMESTAMP   DEFAULT NOW(),
    updated_at          TIMESTAMP   DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS collisions_geom_gist 
    ON collisions USING GIST (geom);
CREATE INDEX IF NOT EXISTS collisions_crash_date_idx 
    ON collisions (crash_date DESC);
CREATE INDEX IF NOT EXISTS collisions_pedestrian_idx 
    ON collisions (number_of_pedestrians_injured, number_of_pedestrians_killed);

-- ============================================================================
-- HEAT VULNERABILITY INDEX (Environmental Equity)
-- Cold dataset: Neighborhood heat risk levels 1-5
-- ============================================================================
CREATE TABLE IF NOT EXISTS heat_vulnerability_index (
    id              SERIAL      PRIMARY KEY,
    nta_code        TEXT        UNIQUE,
    nta_name        TEXT,
    borough         TEXT,
    hvi_score       INTEGER     CHECK (hvi_score >= 1 AND hvi_score <= 5),
    surface_temp    DECIMAL(5,2),
    green_space_pct DECIMAL(5,2),
    ac_pct          DECIMAL(5,2),
    poverty_pct     DECIMAL(5,2),
    geom            GEOMETRY(MultiPolygon, 4326),
    created_at      TIMESTAMP   DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS hvi_geom_gist 
    ON heat_vulnerability_index USING GIST (geom);
CREATE INDEX IF NOT EXISTS hvi_score_idx 
    ON heat_vulnerability_index (hvi_score);

-- ============================================================================
-- PEDESTRIAN MOBILITY NETWORK (Accessibility)
-- Cold dataset: ADA ramps, crosswalks, pedestrian infrastructure
-- ============================================================================
CREATE TABLE IF NOT EXISTS pedestrian_mobility (
    id              SERIAL      PRIMARY KEY,
    feature_id      TEXT        UNIQUE,
    feature_type    TEXT,                        -- 'ramp', 'crosswalk', 'signal'
    ada_compliant   BOOLEAN     DEFAULT false,
    condition       TEXT,
    borough         TEXT,
    street_name     TEXT,
    geom            GEOMETRY(Point, 4326),
    created_at      TIMESTAMP   DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ped_mobility_geom_gist 
    ON pedestrian_mobility USING GIST (geom);
CREATE INDEX IF NOT EXISTS ped_mobility_ada_idx 
    ON pedestrian_mobility (ada_compliant);

-- ============================================================================
-- SUBWAY ENTRANCES (Transit Accessibility)
-- Cold dataset: Station entrances with ADA status
-- ============================================================================
CREATE TABLE IF NOT EXISTS subway_entrances (
    id              SERIAL      PRIMARY KEY,
    station_id      TEXT,
    station_name    TEXT        NOT NULL,
    line            TEXT,
    division        TEXT,
    entrance_type   TEXT,
    entry           BOOLEAN     DEFAULT true,
    exit_only       BOOLEAN     DEFAULT false,
    vending         BOOLEAN,
    staffing        TEXT,
    ada             BOOLEAN     DEFAULT false,
    ada_notes       TEXT,
    north_south_street TEXT,
    east_west_street   TEXT,
    corner          TEXT,
    geom            GEOMETRY(Point, 4326),
    created_at      TIMESTAMP   DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS subway_geom_gist 
    ON subway_entrances USING GIST (geom);
CREATE INDEX IF NOT EXISTS subway_ada_idx 
    ON subway_entrances (ada);
CREATE INDEX IF NOT EXISTS subway_station_idx 
    ON subway_entrances (station_name);

-- ============================================================================
-- DRINKING FOUNTAINS (Amenity)
-- Cold dataset: Public hydration points
-- ============================================================================
CREATE TABLE IF NOT EXISTS drinking_fountains (
    id              SERIAL      PRIMARY KEY,
    park_name       TEXT,
    location        TEXT,
    borough         TEXT,
    operational     BOOLEAN     DEFAULT true,
    geom            GEOMETRY(Point, 4326),
    created_at      TIMESTAMP   DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS fountains_geom_gist 
    ON drinking_fountains USING GIST (geom);

-- ============================================================================
-- COOLING CENTERS (Amenity)
-- Cold dataset: Emergency heat relief locations
-- ============================================================================
CREATE TABLE IF NOT EXISTS cooling_centers (
    id              SERIAL      PRIMARY KEY,
    facility_name   TEXT        NOT NULL,
    facility_type   TEXT,
    address         TEXT,
    borough         TEXT,
    zipcode         TEXT,
    phone           TEXT,
    hours           TEXT,
    extended_hours  BOOLEAN     DEFAULT false,
    geom            GEOMETRY(Point, 4326),
    created_at      TIMESTAMP   DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS cooling_geom_gist 
    ON cooling_centers USING GIST (geom);

-- ============================================================================
-- 311 COMPLAINTS CACHE (Hot/Cold hybrid)
-- Cached recent complaints for faster spatial queries
-- ============================================================================
CREATE TABLE IF NOT EXISTS complaints_311 (
    unique_key      TEXT        PRIMARY KEY,
    created_date    TIMESTAMP   NOT NULL,
    closed_date     TIMESTAMP,
    agency          TEXT,
    agency_name     TEXT,
    complaint_type  TEXT        NOT NULL,
    descriptor      TEXT,
    location_type   TEXT,
    incident_zip    TEXT,
    incident_address TEXT,
    street_name     TEXT,
    cross_street_1  TEXT,
    cross_street_2  TEXT,
    borough         TEXT,
    status          TEXT,
    resolution_description TEXT,
    geom            GEOMETRY(Point, 4326),
    created_at      TIMESTAMP   DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS complaints_geom_gist 
    ON complaints_311 USING GIST (geom);
CREATE INDEX IF NOT EXISTS complaints_type_idx 
    ON complaints_311 (complaint_type);
CREATE INDEX IF NOT EXISTS complaints_created_idx 
    ON complaints_311 (created_date DESC);
CREATE INDEX IF NOT EXISTS complaints_status_idx 
    ON complaints_311 (status);

-- ============================================================================
-- TELEMETRY EVENTS (Human Impact Measurement)
-- Tracks AI suggestions and user responses for hackathon metrics
-- ============================================================================
CREATE TABLE IF NOT EXISTS telemetry_events (
    id                  SERIAL      PRIMARY KEY,
    event_type          TEXT        NOT NULL,    -- 'hazard_detected', 'reroute_suggested', etc.
    session_id          TEXT,
    latitude            DECIMAL(10, 7),
    longitude           DECIMAL(10, 7),
    hazard_type         TEXT,                    -- 'restaurant', 'collision', 'noise', 'transit'
    hazard_id           TEXT,                    -- Reference to source record
    qol_score           INTEGER,
    ai_suggestion       TEXT,
    user_followed       BOOLEAN,
    response_time_ms    INTEGER,
    timestamp           TIMESTAMP   DEFAULT NOW(),
    outcome_timestamp   TIMESTAMP
);

CREATE INDEX IF NOT EXISTS telemetry_session_idx 
    ON telemetry_events (session_id);
CREATE INDEX IF NOT EXISTS telemetry_timestamp_idx 
    ON telemetry_events (timestamp DESC);
CREATE INDEX IF NOT EXISTS telemetry_hazard_idx 
    ON telemetry_events (hazard_type);

-- ============================================================================
-- HELPER FUNCTIONS
-- ============================================================================

-- Function to calculate hazard flag for restaurants
CREATE OR REPLACE FUNCTION is_restaurant_hazard(grade CHAR(1), score INTEGER)
RETURNS BOOLEAN AS $$
BEGIN
    RETURN grade = 'C' OR score > 28;
END;
$$ LANGUAGE plpgsql IMMUTABLE;

-- Function to get collision severity score
CREATE OR REPLACE FUNCTION collision_severity(
    persons_injured INTEGER,
    persons_killed INTEGER,
    pedestrians_injured INTEGER,
    pedestrians_killed INTEGER
) RETURNS INTEGER AS $$
BEGIN
    RETURN (COALESCE(persons_killed, 0) * 100) + 
           (COALESCE(pedestrians_killed, 0) * 50) +
           (COALESCE(pedestrians_injured, 0) * 10) +
           (COALESCE(persons_injured, 0) * 5);
END;
$$ LANGUAGE plpgsql IMMUTABLE;

-- ============================================================================
-- VIEWS FOR COMMON QUERIES
-- ============================================================================

-- Active hazardous restaurants (grade C or score > 28)
CREATE OR REPLACE VIEW hazardous_restaurants AS
SELECT 
    inspection_id, camis, dba, street, zipcode, 
    grade, score, cuisine_description, inspection_date, geom
FROM restaurants
WHERE is_restaurant_hazard(grade, score)
  AND inspection_date > CURRENT_DATE - INTERVAL '1 year';

-- Recent pedestrian-involved collisions (last 3 years)
CREATE OR REPLACE VIEW pedestrian_collision_hotspots AS
SELECT 
    collision_id, crash_date, on_street_name, cross_street_name,
    number_of_pedestrians_injured, number_of_pedestrians_killed,
    collision_severity(
        number_of_persons_injured, number_of_persons_killed,
        number_of_pedestrians_injured, number_of_pedestrians_killed
    ) as severity_score,
    geom
FROM collisions
WHERE crash_date > CURRENT_DATE - INTERVAL '3 years'
  AND (number_of_pedestrians_injured > 0 OR number_of_pedestrians_killed > 0);

-- Active 311 noise complaints (last 24 hours)
CREATE OR REPLACE VIEW active_noise_complaints AS
SELECT 
    unique_key, created_date, complaint_type, descriptor,
    incident_address, borough, status, geom
FROM complaints_311
WHERE complaint_type ILIKE '%noise%'
  AND created_date > NOW() - INTERVAL '24 hours'
  AND status NOT IN ('Closed');

COMMENT ON TABLE restaurants IS 'DOHMH NYC Restaurant Inspection Results - Cold dataset';
COMMENT ON TABLE collisions IS 'NYPD Motor Vehicle Collisions / Vision Zero - Cold dataset';
COMMENT ON TABLE heat_vulnerability_index IS 'NYC Heat Vulnerability Index by NTA - Cold dataset';
COMMENT ON TABLE complaints_311 IS '311 Service Requests cache - Hot/Cold hybrid';
COMMENT ON TABLE telemetry_events IS 'pseudoMetaGlass human impact measurement';
