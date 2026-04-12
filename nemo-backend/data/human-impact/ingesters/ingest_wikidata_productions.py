"""
Wikidata Filming Locations Ingestion v2

Splits by production type to avoid query timeouts.
Retries on 429/502/503 with backoff.
"""

import os, logging, time
import requests
import psycopg2
from psycopg2.extras import execute_values

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SPARQL = "https://query.wikidata.org/sparql"
UA = "pseudoMetaGlass/1.0"

# Split by type to keep each query small
PRODUCTION_TYPES = {
    "film":               "wd:Q11424",
    "television_series":  "wd:Q5398426",
    "tv_film":            "wd:Q1366112",
    "miniseries":         "wd:Q63952888",
    "documentary":        "wd:Q21191270",
}

QUERY_TEMPLATE = """
SELECT ?wikidataId ?productionName ?locationName ?lat ?lon ?year WHERE {{
  ?production wdt:P915 ?loc .
  ?loc wdt:P625 ?coord .
  ?production wdt:P31 {type_id} .
  BIND(geof:latitude(?coord) AS ?lat)
  BIND(geof:longitude(?coord) AS ?lon)
  FILTER(?lat > 40.4 && ?lat < 40.9 && ?lon > -74.3 && ?lon < -73.7)
  OPTIONAL {{ ?production wdt:P577 ?pubDate . BIND(YEAR(?pubDate) AS ?year) }}
  BIND(STR(?production) AS ?wikidataId)
  SERVICE wikibase:label {{
    bd:serviceParam wikibase:language "en" .
    ?production rdfs:label ?productionName .
    ?loc rdfs:label ?locationName .
  }}
}}
LIMIT 500 OFFSET {offset}
"""

CREATE_TABLE = """
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
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(wikidata_id, latitude, longitude)
);
CREATE INDEX IF NOT EXISTS wikidata_productions_geom_idx ON wikidata_productions USING GIST (geom);
"""

def get_conn():
    return psycopg2.connect(
        host=os.environ.get('POSTGRES_HOST','localhost'),
        port=int(os.environ.get('POSTGRES_PORT','5432')),
        database=os.environ.get('POSTGRES_DB','metaglass'),
        user=os.environ.get('POSTGRES_USER','metaglass'),
        password=os.environ.get('POSTGRES_PASSWORD',''),
    )

def sparql_fetch(query, retries=3):
    for attempt in range(retries):
        try:
            r = requests.get(SPARQL,
                params={"format":"json","query":query},
                headers={"User-Agent": UA, "Accept": "application/sparql-results+json"},
                timeout=45)
            if r.status_code in (429, 502, 503):
                wait = 10 * (attempt + 1)
                logger.warning(f"HTTP {r.status_code} — retrying in {wait}s")
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.json()["results"]["bindings"]
        except requests.RequestException as e:
            logger.warning(f"Request error (attempt {attempt+1}): {e}")
            time.sleep(5 * (attempt + 1))
    return []

def run():
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(CREATE_TABLE)
    conn.commit()
    logger.info("Table ready")

    grand_total = 0

    for type_name, type_id in PRODUCTION_TYPES.items():
        logger.info(f"Fetching type: {type_name}")
        offset = 0
        type_total = 0

        while True:
            query = QUERY_TEMPLATE.format(type_id=type_id, offset=offset)
            rows = sparql_fetch(query)
            if not rows:
                break

            records = []
            for r in rows:
                try:
                    lat = float(r["lat"]["value"])
                    lon = float(r["lon"]["value"])
                    records.append((
                        r["wikidataId"]["value"].split("/")[-1],
                        r.get("productionName",{}).get("value","Unknown"),
                        type_name.replace("_"," "),
                        r.get("locationName",{}).get("value"),
                        int(r["year"]["value"]) if r.get("year") else None,
                        lat, lon,
                    ))
                except (KeyError, ValueError):
                    continue

            if records:
                # Deduplicate within batch on (wikidata_id, lat, lon)
                seen = set()
                deduped = []
                for rec in records:
                    key = (rec[0], rec[5], rec[6])
                    if key not in seen:
                        seen.add(key)
                        deduped.append(rec)
                records = deduped
                with conn.cursor() as cur:
                    execute_values(cur, """
                        INSERT INTO wikidata_productions
                            (wikidata_id, production_name, production_type,
                             location_name, year, latitude, longitude, geom)
                        VALUES %s
                        ON CONFLICT (wikidata_id, latitude, longitude) DO UPDATE SET
                            production_name = EXCLUDED.production_name,
                            production_type = EXCLUDED.production_type,
                            location_name   = EXCLUDED.location_name,
                            year            = EXCLUDED.year,
                            updated_at      = NOW()
                    """, [(wid, name, ptype, loc, yr, lat, lon,
                           f"SRID=4326;POINT({lon} {lat})")
                          for wid, name, ptype, loc, yr, lat, lon in records])
                conn.commit()
                type_total += len(records)
                logger.info(f"  {type_name}: +{len(records)} (offset {offset})")

            if len(rows) < 500:
                break
            offset += 500
            time.sleep(2)  # polite delay between pages

        logger.info(f"  {type_name} done: {type_total} records")
        grand_total += type_total
        time.sleep(3)  # polite delay between types

    conn.close()
    logger.info(f"Ingestion complete. Grand total: {grand_total}")

    # Verify
    conn2 = get_conn()
    with conn2.cursor() as cur:
        cur.execute("SELECT COUNT(*), COUNT(DISTINCT production_name) FROM wikidata_productions")
        total, unique = cur.fetchone()
    conn2.close()
    logger.info(f"DB now has {total} rows, {unique} unique production names")

if __name__ == "__main__":
    run()
