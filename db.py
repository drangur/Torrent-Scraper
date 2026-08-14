"""Optional Postgres output for scraper.py: writes each discovered magnet
link into a `scraped_torrents` table, alongside the existing torrents.json
file output. Uses psycopg (v3). All functions are no-ops-safe to import even
if psycopg isn't installed; connect() will raise a clear error in that case.
"""

import logging

logger = logging.getLogger('scraper')

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS scraped_torrents (
    id            SERIAL PRIMARY KEY,
    info_hash     TEXT NOT NULL UNIQUE,
    name          TEXT NOT NULL,
    magnet        TEXT NOT NULL,
    source_site   TEXT NOT NULL,
    source_url    TEXT NOT NULL,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

UPSERT_SQL = """
INSERT INTO scraped_torrents (info_hash, name, magnet, source_site, source_url)
VALUES (%s, %s, %s, %s, %s)
ON CONFLICT (info_hash) DO UPDATE
    SET last_seen_at = now(),
        name = EXCLUDED.name,
        source_url = EXCLUDED.source_url;
"""


def connect(db_config):
    """Open a connection using a dict with host/port/dbname/user/password
    (any of which may be omitted to fall back to psycopg/libpq defaults).
    """
    import psycopg
    kwargs = {k: v for k, v in {
        'host': db_config.get('host'),
        'port': db_config.get('port'),
        'dbname': db_config.get('dbname'),
        'user': db_config.get('user'),
        'password': db_config.get('password'),
    }.items() if v is not None}
    return psycopg.connect(**kwargs)


def ensure_table(conn):
    with conn.cursor() as cur:
        cur.execute(CREATE_TABLE_SQL)
    conn.commit()


def upsert_magnet(conn, info_hash, name, magnet, source_site, source_url):
    """Insert a new row, or bump last_seen_at/refresh name+source_url if the
    info_hash is already known. Returns True if this was a new row.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM scraped_torrents WHERE info_hash = %s",
            (info_hash,),
        )
        is_new = cur.fetchone() is None
        cur.execute(UPSERT_SQL, (info_hash, name, magnet, source_site, source_url))
    conn.commit()
    return is_new
