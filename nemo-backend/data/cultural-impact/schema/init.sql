-- Cultural Impact Wing - PostGIS Schema
-- Datasets: Film Permits, Landmarks, Public Art, Cultural Venues

-- ============================================================================
-- FILM PERMITS (Mayor's Office of Media & Entertainment)
-- Hot dataset: Active filming locations
-- Security: No personal contact info stored, only public permit data
-- ============================================================================
CREATE TABLE IF NOT EXISTS film_permits (
    event_id        TEXT        PRIMARY KEY,
    event_type      TEXT,                        -- 'Shooting Permit', 'Theater Load', etc.
    start_datetime  TIMESTAMP,
    end_datetime    TIMESTAMP,
    parking_held    TEXT,                        -- Street location description
    borough         TEXT,
    category        TEXT,                        -- 'Television', 'Film', 'Commercial'
    subcategory     TEXT,                        -- 'Episodic series', 'Feature', etc.
    zipcode         TEXT,
    -- Derived fields for spatial queries
    geom            GEOMETRY(Point, 4326),
    created_at      TIMESTAMP   DEFAULT NOW(),
    updated_at      TIMESTAMP   DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS film_permits_geom_gist 
    ON film_permits USING GIST (geom);
CREATE INDEX IF NOT EXISTS film_permits_start_idx 
    ON film_permits (start_datetime DESC);
CREATE INDEX IF NOT EXISTS film_permits_category_idx 
    ON film_permits (category);
CREATE INDEX IF NOT EXISTS film_permits_active_idx 
    ON film_permits (start_datetime, end_datetime);

-- ============================================================================
-- LANDMARKS (Landmarks Preservation Commission)
-- Cold dataset: Historic buildings and districts
-- Security: Public historical data only
-- ============================================================================
CREATE TABLE IF NOT EXISTS landmarks (
    id              SERIAL      PRIMARY KEY,
    object_id       TEXT        UNIQUE,
    lpc_name        TEXT        NOT NULL,        -- Official landmark name
    address         TEXT,
    borough         TEXT,
    block           TEXT,
    lot             TEXT,
    bbl             TEXT,                        -- Borough-Block-Lot
    designation_date DATE,
    landmark_type   TEXT,                        -- 'Individual', 'Interior', 'Scenic'
    style           TEXT,                        -- Architectural style
    architect       TEXT,
    year_built      TEXT,
    description     TEXT,                        -- Historical description
    geom            GEOMETRY(MultiPolygon, 4326),
    centroid        GEOMETRY(Point, 4326),       -- For distance queries
    created_at      TIMESTAMP   DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS landmarks_geom_gist 
    ON landmarks USING GIST (geom);
CREATE INDEX IF NOT EXISTS landmarks_centroid_gist 
    ON landmarks USING GIST (centroid);
CREATE INDEX IF NOT EXISTS landmarks_name_idx 
    ON landmarks (lpc_name);
CREATE INDEX IF NOT EXISTS landmarks_borough_idx 
    ON landmarks (borough);

-- ============================================================================
-- MUSEUMS AND CULTURAL VENUES
-- Cold dataset: Museums, galleries, theaters, cultural centers
-- Security: Public venue info only, no personal data
-- ============================================================================
CREATE TABLE IF NOT EXISTS cultural_venues (
    id              SERIAL      PRIMARY KEY,
    name            TEXT        NOT NULL,
    venue_type      TEXT,                        -- 'Museum', 'Gallery', 'Theater', etc.
    address         TEXT,
    city            TEXT,
    zipcode         TEXT,
    borough         TEXT,
    website         TEXT,                        -- Public website only
    -- Note: Phone numbers stripped for privacy
    geom            GEOMETRY(Point, 4326),
    created_at      TIMESTAMP   DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS venues_geom_gist 
    ON cultural_venues USING GIST (geom);
CREATE INDEX IF NOT EXISTS venues_type_idx 
    ON cultural_venues (venue_type);
CREATE INDEX IF NOT EXISTS venues_name_idx 
    ON cultural_venues (name);

-- ============================================================================
-- PUBLIC ART (NYC Parks & DOT Art)
-- Cold dataset: Murals, sculptures, installations
-- Security: Artist names are public, no personal contact info
-- ============================================================================
CREATE TABLE IF NOT EXISTS public_art (
    id              SERIAL      PRIMARY KEY,
    title           TEXT,
    artist          TEXT,
    art_type        TEXT,                        -- 'Sculpture', 'Mural', 'Installation'
    location_desc   TEXT,
    park_name       TEXT,
    borough         TEXT,
    installed_date  DATE,
    description     TEXT,
    geom            GEOMETRY(Point, 4326),
    created_at      TIMESTAMP   DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS art_geom_gist 
    ON public_art USING GIST (geom);
CREATE INDEX IF NOT EXISTS art_type_idx 
    ON public_art (art_type);
CREATE INDEX IF NOT EXISTS art_artist_idx 
    ON public_art (artist);

-- ============================================================================
-- HISTORIC DISTRICTS (LPC District Boundaries)
-- Cold dataset: Neighborhood historic district boundaries
-- ============================================================================
CREATE TABLE IF NOT EXISTS historic_districts (
    id              SERIAL      PRIMARY KEY,
    district_name   TEXT        NOT NULL,
    borough         TEXT,
    designation_date DATE,
    description     TEXT,
    geom            GEOMETRY(MultiPolygon, 4326),
    created_at      TIMESTAMP   DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS districts_geom_gist 
    ON historic_districts USING GIST (geom);

-- ============================================================================
-- VIEWS FOR COMMON QUERIES
-- ============================================================================

-- Active film permits (currently filming or within next 7 days)
CREATE OR REPLACE VIEW active_film_permits AS
SELECT 
    event_id, event_type, start_datetime, end_datetime,
    parking_held, borough, category, subcategory, geom
FROM film_permits
WHERE start_datetime <= NOW() + INTERVAL '7 days'
  AND end_datetime >= NOW() - INTERVAL '1 day';

-- Nearby landmarks with distance
CREATE OR REPLACE FUNCTION nearby_landmarks(
    lat DOUBLE PRECISION,
    lon DOUBLE PRECISION,
    radius_m INTEGER DEFAULT 200
)
RETURNS TABLE (
    lpc_name TEXT,
    address TEXT,
    landmark_type TEXT,
    style TEXT,
    year_built TEXT,
    description TEXT,
    distance_meters DOUBLE PRECISION
) AS $$
BEGIN
    RETURN QUERY
    SELECT 
        l.lpc_name,
        l.address,
        l.landmark_type,
        l.style,
        l.year_built,
        l.description,
        ST_Distance(
            l.centroid::geography,
            ST_SetSRID(ST_MakePoint(lon, lat), 4326)::geography
        ) as distance_meters
    FROM landmarks l
    WHERE ST_DWithin(
        l.centroid::geography,
        ST_SetSRID(ST_MakePoint(lon, lat), 4326)::geography,
        radius_m
    )
    ORDER BY distance_meters;
END;
$$ LANGUAGE plpgsql;

COMMENT ON TABLE film_permits IS 'NYC Film Permits - Hot cultural dataset';
COMMENT ON TABLE landmarks IS 'LPC Individual Landmarks - Cold cultural dataset';
COMMENT ON TABLE cultural_venues IS 'Museums, galleries, theaters - Cold cultural dataset';
COMMENT ON TABLE public_art IS 'Public art installations - Cold cultural dataset';
