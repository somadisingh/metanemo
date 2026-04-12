"""
Wikidata Filming Locations Ingestion

Fetches NYC filming locations with production names from Wikidata SPARQL.
Free, no API key required. Covers films, TV series, documentaries.

Table: wikidata_productions
Source: https://query.wikidata.org/sparql
"""

import os
import logging
import time
from typing import Optional

import requests
import psycopg2
from psycopg2.extras import execute_values

logger = logging.getLogger(__name__)

WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"
USER_AGENT = "pseudoMetaGlass/1.0 (https://github.com/pseudometaglass)"

SPARQL_QUERY = """
SELECT ?wikidataId ?productionName ?productionType ?locationName ?lat ?lon ?year WHERE {
  ?production wdt:P915 ?loc .
  ?loc wdt:P625 ?coord .
  ?production wdt:P31 ?type .
  VALUES ?type {
    wd:Q11424      # film
    wd:Q5398426    # television series
    wd:Q24856      # film series
    wd:Q1366112    # television film
    wd:Q63952888   # miniseries
    wd:Q506240     # television special
    wd:Q21191270   # television documentary
  }
  BIND(geof:latitude(?coord) AS ?lat)
  BIND(geof:longitude(?coord) AS ?lon)
  FILTER(?lat > 40.4 && ?lat < 40.9 && ?lon > -74.3 && ?lon < -73.7)
  OPTIONAL { ?production wdt:P577 ?pubDate . BIND(YEAR(?pubDate) AS ?year) }
  BIND(STR(?production) AS ?wikidataId)
  SERVICE wikibase:label {
    bd:serviceParam wikibase:language "en" .
    ?production rdfs:label ?productionName .
    ?type rdfs:label ?productionType .
    ?loc rdfs:label ?locationName .
  }
}
ORDER BY DESC(?year)
"""

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS wikidata_productions (
    id              SERIAL PRIMARY KEY,
    wikidata_id     TEXT NOT NULL,
    production_name TEXT NOT NULL,
    production_type TEXT,
    location_name   TEXT,
    year            INTEGER,
    latitude        DOUBLE PRECISION NOT NULL,
    longitude       DOUBLE PRECISION NOT NULL,
    geom            GEOMETRY(Point, 4326),
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(wikidata_id, latitude, longitude)
);
CREATE INDEX IF NOT EXISTS wikidata_productions_geom_idx
    ON wikidata_productions USING GIST (geom);
CREATE INDEX IF NOT EXISTS wikidata_productions_name_idx
    ON wikidata_productions (production_name);
"""


def get_db_connection():
    return psycopg2.connect(
        host=os.environ.get('POSTGRES_HOST', 'localhost'),
        port=int(os.environ.get('POSTGRES_PORT', '5432')),
        database=os.environ.get('POSTGRES_DB', 'metaglass'),
        user=os.environ.get('POSTGRES_USER', 'metaglass'),
        password=os.environ.get('POSTGRES_PASSWORD', ''),
    )


def fetch_wikidata_productions(offset: int = 0, limit: int = 500):
    """Fetch a page of NYC filming locations from Wikidata SPARQL."""
    paginated_query = SPARQL_QUERY + f"\nLIMIT {limit} OFFSET {offset}"
    resp = requests.get(
        WIKIDATA_SPARQL,
        params={"format": "json", "query": paginated_query},
        headers={"User-Agent": USER_AGENT, "Accept": "application/sparql-results+json"},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["results"]["bindings"]


def run_wikidata_ingestion():
    """
    Fetch all NYC filming locations from Wikidata and upsert into wikidata_productions.
    Paginates through all results in batches of 500.
    """
    logger.info("Starting Wikidata productions ingestion")
    conn = get_db_connection()

    try:
        with conn.cursor() as cur:
            cur.execute(CREATE_TABLE_SQL)
        conn.commit()
        logger.info("Table wikidata_productions ensured")

        total_inserted = 0
        offset = 0
        batch_size = 500

        while True:
            logger.info(f"Fetching Wikidata batch offset={offset}")
            try:
                rows = fetch_wikidata_productions(offset=offset, limit=batch_size)
            except requests.RequestException as e:
                logger.error(f"Wikidata SPARQL request failed: {e}")
                break

            if not rows:
                break

            records = []
            for r in rows:
                try:
                    lat = float(r["lat"]["value"])
                    lon = float(r["lon"]["value"])
                    records.append((
                        r["wikidataId"]["value"].split("/")[-1],  # Q-id only
                        r.get("productionName", {}).get("value", "Unknown"),
                        r.get("productionType", {}).get("value"),
                        r.get("locationName", {}).get("value"),
                        int(r["year"]["value"]) if r.get("year") else None,
                        lat,
                        lon,
                    ))
                except (KeyError, ValueError):
                    continue

            if records:
                with conn.cursor() as cur:
                    execute_values(cur, """
                        INSERT INTO wikidata_productions
                            (wikidata_id, production_name, production_type, location_name, year, latitude, longitude, geom)
                        VALUES %s
                        ON CONFLICT (wikidata_id, latitude, longitude) DO UPDATE SET
                            production_name = EXCLUDED.production_name,
                            production_type = EXCLUDED.production_type,
                            location_name   = EXCLUDED.location_name,
                            year            = EXCLUDED.year,
                            updated_at      = NOW()
                    """, [
                        (wid, name, ptype, loc, yr, lat, lon,
                         f"SRID=4326;POINT({lon} {lat})")
                        for wid, name, ptype, loc, yr, lat, lon in records
                    ])
                conn.commit()
                total_inserted += len(records)
                logger.info(f"Upserted {len(records)} records (total so far: {total_inserted})")

            if len(rows) < batch_size:
                break

            offset += batch_size
            time.sleep(1)  # be polite to Wikidata

        logger.info(f"Wikidata ingestion complete. Total upserted: {total_inserted}")
        return total_inserted

    except psycopg2.Error as e:
        conn.rollback()
        logger.error(f"DB error during Wikidata ingestion: {e}")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    run_wikidata_ingestion()
