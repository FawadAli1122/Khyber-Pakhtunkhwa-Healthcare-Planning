"""This project's own bundled, private PostgreSQL instance - never the
machine's existing PostgreSQL 16 service. Owns the instance's lifecycle
(scripts/lib/local_db.py Part B, added in Task 2 of this plan) and
exposes generic fetch_all/insert_many/delete_by_id helpers on top of
psycopg2, used by the three admin-overlay store modules
(server/supplemental_data.py, server/metric_overrides.py,
server/bot_facilities.py) and by scripts/07_merge_facilities.py's direct
read of the bot_facilities table. Lives in scripts/lib/, not server/, so
both server/ and plain pipeline scripts can import it directly - matching
this project's established one-way import constraint (server/ imports
from scripts/lib/, never the reverse). See docs/superpowers/specs/
2026-08-16-bundled-local-database-design.md.
"""
import csv
import datetime
import decimal
import re
import secrets
import subprocess
from pathlib import Path

import keyring
import psycopg2
from psycopg2 import sql
from psycopg2.extras import Json, RealDictCursor

SERVICE_NAME = "kp-healthcare-plan"  # matches server/keystore.py's own SERVICE_NAME exactly - see this plan's Global Constraints for why this module manages its own keyring access rather than importing server.keystore
LOCAL_DB_PASSWORD_KEY = "local_db_password"

DB_NAME = "kp_healthcare"
DB_USER = "kp_admin"
PORT = 5544


class LocalDbError(Exception):
    """Raised when bootstrapping or connecting to the bundled database
    fails - message safe to show the admin directly, never a raw
    traceback."""


def _get_password():
    value = keyring.get_password(SERVICE_NAME, LOCAL_DB_PASSWORD_KEY)
    if value is None:
        raise LocalDbError("Local database not initialized yet - call local_db.ensure_running() first")
    return value


def get_connection():
    conn = psycopg2.connect(
        host="localhost", port=PORT, dbname=DB_NAME, user=DB_USER,
        password=_get_password(), cursor_factory=RealDictCursor,
    )
    # Without this, psycopg2 falls back to the OS's own codepage on
    # Windows (cp1252, not UTF-8) for every string sent to Postgres -
    # invisible until real non-ASCII data actually flows through (e.g. a
    # Marham/OSM-scraped facility name or address containing a character
    # outside cp1252), at which point every INSERT/UPDATE carrying it
    # raises UnicodeEncodeError. Found live via
    # scripts/25_sync_processed_to_db.py's first real run against
    # facilities_merged.csv. Every table in this schema is UTF-8 text
    # data (Postgres's own server_encoding is UTF-8, set at initdb time),
    # so the client side must match explicitly rather than trust the
    # platform-dependent default.
    conn.set_client_encoding("UTF8")
    return conn


def _normalize_value(v):
    # Every column in this schema was TEXT until custom tables (Part C)
    # introduced real DATE/NUMERIC columns - RealDictCursor returns those
    # as native datetime.date/decimal.Decimal objects, not strings,
    # which every existing caller (JSON responses, json.dumps in AI
    # prompts, plain string rendering) isn't prepared for. Normalized
    # here so fetch_all always returns plain JSON-safe values, matching
    # what every caller has always assumed.
    if v is None:
        return ""
    if isinstance(v, (datetime.date, datetime.datetime)):
        return v.isoformat()
    if isinstance(v, decimal.Decimal):
        return float(v)
    return v


def fetch_all(table, order_by=None, column_map=None):
    column_map = column_map or {}
    reverse_map = {v: k for k, v in column_map.items()}
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            query = f"SELECT * FROM {table}"
            if order_by:
                query += f" ORDER BY {order_by}"
            cur.execute(query)
            rows = cur.fetchall()
    finally:
        conn.close()
    return [
        {reverse_map.get(k, k): _normalize_value(v) for k, v in dict(row).items()}
        for row in rows
    ]


def insert_many(table, fieldnames, records, column_map=None):
    column_map = column_map or {}
    db_columns = [column_map.get(f, f) for f in fieldnames]
    columns_sql = ", ".join(db_columns)
    placeholders = ", ".join(["%s"] * len(db_columns))
    values = [tuple(r.get(f, "") for f in fieldnames) for r in records]
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.executemany(f"INSERT INTO {table} ({columns_sql}) VALUES ({placeholders})", values)
        conn.commit()
    finally:
        conn.close()


def delete_by_id(table, record_id):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(f"DELETE FROM {table} WHERE id = %s", (record_id,))
            deleted = cur.rowcount == 1
        conn.commit()
    finally:
        conn.close()
    return deleted


ROOT = Path(__file__).resolve().parent.parent.parent
PG_BIN = Path(r"C:\Program Files\PostgreSQL\16\bin")
DATA_DIR = ROOT / "data" / "pgdata"
PROCESSED = ROOT / "data" / "processed"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS supplemental_records (
    id TEXT PRIMARY KEY,
    district TEXT, facility TEXT, category TEXT, label TEXT, detail TEXT,
    source_document TEXT, added_at TEXT
);
CREATE TABLE IF NOT EXISTS metric_overrides (
    id TEXT PRIMARY KEY,
    district TEXT, file TEXT, column_name TEXT, value TEXT, reason TEXT,
    source TEXT, added_at TEXT
);
CREATE TABLE IF NOT EXISTS bot_facilities (
    id TEXT PRIMARY KEY,
    name TEXT, district TEXT, lat TEXT, lon TEXT, category TEXT,
    added_at TEXT, added_by TEXT
);
CREATE TABLE IF NOT EXISTS custom_tables (
    id TEXT PRIMARY KEY,
    label TEXT, table_name TEXT, created_at TEXT,
    report_title TEXT, report_narrative TEXT, report_placement TEXT
);
CREATE TABLE IF NOT EXISTS custom_table_columns (
    id TEXT PRIMARY KEY,
    custom_table_id TEXT, label TEXT, column_name TEXT, column_type TEXT
);
"""

# Legacy CSV fieldnames, duplicated here (not imported from the server
# modules that own them today - scripts/lib/ never imports server/) so
# the one-time migration below can read them without crossing that
# boundary. Only used once, at first bootstrap.
_LEGACY_CSV_MIGRATIONS = [
    ("supplemental_records.csv", "supplemental_records",
     ("id", "district", "facility", "category", "label", "detail", "source_document", "added_at"), None),
    ("metric_overrides.csv", "metric_overrides",
     ("id", "district", "file", "column", "value", "reason", "source", "added_at"), {"column": "column_name"}),
    ("bot_facilities.csv", "bot_facilities",
     ("id", "name", "district", "lat", "lon", "category", "added_at", "added_by"), None),
]


def is_initialized():
    return DATA_DIR.exists()


def _run(args, error_message):
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        raise LocalDbError(f"{error_message}: {result.stderr[-500:]}")
    return result


def initialize():
    password = secrets.token_urlsafe(32)
    keyring.set_password(SERVICE_NAME, LOCAL_DB_PASSWORD_KEY, password)

    DATA_DIR.parent.mkdir(parents=True, exist_ok=True)
    pwfile = DATA_DIR.parent / ".local_db_initdb_pw.tmp"
    pwfile.write_text(password, encoding="utf-8")
    try:
        _run(
            [str(PG_BIN / "initdb.exe"), "-D", str(DATA_DIR), "-U", DB_USER,
             "--auth=scram-sha-256", f"--pwfile={pwfile}",
             # Without these, initdb silently picks up the Windows OS
             # locale's own codepage (WIN1252 on this machine) as
             # server_encoding, which can never be changed in place after
             # creation - any non-Latin1 character (a Marham/OSM-scraped
             # facility name, for example) then fails to insert with
             # UntranslatableCharacter. Found live via
             # scripts/25_sync_processed_to_db.py's first real run.
             # --locale=C avoids the same locale-compatibility trap for
             # collation/ctype (Postgres restricts a database's encoding
             # to what its LC_CTYPE/LC_COLLATE allow unless they're "C") -
             # this app never depends on locale-aware sorting (every
             # ordering in this codebase is by id/added_at, done in
             # Python or via a plain column ORDER BY, never
             # locale-sensitive text collation).
             "--encoding=UTF8", "--locale=C"],
            "Failed to initialize the local database",
        )
    finally:
        pwfile.unlink(missing_ok=True)

    start()

    bootstrap_conn = psycopg2.connect(
        host="localhost", port=PORT, dbname="postgres", user=DB_USER, password=password,
    )
    bootstrap_conn.autocommit = True
    try:
        with bootstrap_conn.cursor() as cur:
            cur.execute(f"CREATE DATABASE {DB_NAME}")
    finally:
        bootstrap_conn.close()

    apply_schema()

    _migrate_legacy_csvs()


def apply_schema():
    """Idempotent (every statement is CREATE TABLE IF NOT EXISTS) -
    called on every ensure_running(), not just first-ever initialize().
    Without this, a table added to SCHEMA_SQL after an installation's
    first bootstrap would never actually get created on that existing
    installation - discovered live while verifying this exact scenario
    (custom_tables/custom_table_columns, added by the admin-custom-tables
    feature, missing from an already-initialized database from an
    earlier session). See docs/superpowers/specs/
    2026-08-16-admin-custom-tables-design.md."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()


def _migrate_legacy_csvs():
    for filename, table, fieldnames, column_map in _LEGACY_CSV_MIGRATIONS:
        path = PROCESSED / filename
        if not path.exists():
            continue
        with open(path, newline="", encoding="utf-8") as f:
            records = list(csv.DictReader(f))
        if records:
            insert_many(table, fieldnames, records, column_map=column_map)


def start():
    status = subprocess.run(
        [str(PG_BIN / "pg_ctl.exe"), "status", "-D", str(DATA_DIR)],
        capture_output=True, text=True,
    )
    if status.returncode == 0:
        return  # already running
    # Deliberately not capture_output=True (unlike every other subprocess
    # call in this module): "pg_ctl start" spawns postgres.exe as a
    # persistent detached daemon, which on Windows inherits pg_ctl's own
    # stdout/stderr pipe handles from this call. Because postgres.exe
    # never exits, that inherited pipe end never sees EOF, so
    # subprocess.run(capture_output=True) blocks forever waiting for the
    # pipe to close - even though postgres itself starts and becomes
    # reachable immediately. Discovered live during this plan's own Task
    # 8 manual verification. stdout/stderr are discarded here; postgres's
    # own log already goes to server.log via -l, which the error path
    # below reads instead of stderr.
    log_path = DATA_DIR / "server.log"
    result = subprocess.run(
        [str(PG_BIN / "pg_ctl.exe"), "start", "-D", str(DATA_DIR), "-o", f"-p {PORT}",
         "-l", str(log_path), "-w"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    if result.returncode != 0:
        detail = log_path.read_text(encoding="utf-8", errors="replace")[-500:] if log_path.exists() else ""
        raise LocalDbError(f"Failed to start the local database: {detail}")


def stop():
    if not DATA_DIR.exists():
        return
    subprocess.run([str(PG_BIN / "pg_ctl.exe"), "stop", "-D", str(DATA_DIR), "-m", "fast"],
                    capture_output=True, text=True)


def ensure_running():
    if not is_initialized():
        initialize()
    else:
        start()
        apply_schema()


COLUMN_TYPE_SQL = {"text": "TEXT", "number": "NUMERIC", "date": "DATE", "boolean": "BOOLEAN", "json": "JSONB"}


def slugify(label):
    slug = re.sub(r"[^a-z0-9]+", "_", (label or "").strip().lower()).strip("_")
    if not slug:
        raise LocalDbError(f"{label!r} does not contain any usable characters for a name")
    return slug[:40]


def validate_identifier(name):
    if not re.match(r"^[a-z][a-z0-9_]*$", name or ""):
        raise LocalDbError(f"Invalid internal name: {name!r}")


def _sql_type_for(column_type):
    sql_type = COLUMN_TYPE_SQL.get(column_type)
    if sql_type is None:
        raise LocalDbError(f"Unknown column type: {column_type!r} (must be one of {sorted(COLUMN_TYPE_SQL)})")
    return sql_type


def create_table(table_name, columns):
    """columns: list of (column_name, column_type) tuples - column_type
    one of "text"/"number"/"date". Every admin-defined table also gets a
    fixed id/added_at pair, matching every other table in this schema."""
    validate_identifier(table_name)
    col_defs = [sql.SQL("id TEXT PRIMARY KEY"), sql.SQL("added_at TEXT")]
    for column_name, column_type in columns:
        validate_identifier(column_name)
        col_defs.append(sql.SQL("{} {}").format(sql.Identifier(column_name), sql.SQL(_sql_type_for(column_type))))
    statement = sql.SQL("CREATE TABLE {} ({})").format(sql.Identifier(table_name), sql.SQL(", ").join(col_defs))
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(statement)
        conn.commit()
    finally:
        conn.close()


def add_column(table_name, column_name, column_type):
    validate_identifier(table_name)
    validate_identifier(column_name)
    statement = sql.SQL("ALTER TABLE {} ADD COLUMN {} {}").format(
        sql.Identifier(table_name), sql.Identifier(column_name), sql.SQL(_sql_type_for(column_type))
    )
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(statement)
        conn.commit()
    finally:
        conn.close()


def drop_column(table_name, column_name):
    validate_identifier(table_name)
    validate_identifier(column_name)
    statement = sql.SQL("ALTER TABLE {} DROP COLUMN {}").format(
        sql.Identifier(table_name), sql.Identifier(column_name)
    )
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(statement)
        conn.commit()
    finally:
        conn.close()


def drop_table(table_name):
    validate_identifier(table_name)
    statement = sql.SQL("DROP TABLE {}").format(sql.Identifier(table_name))
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(statement)
        conn.commit()
    finally:
        conn.close()


def update_by_id(table, record_id, fields, column_map=None):
    column_map = column_map or {}
    set_columns = [column_map.get(k, k) for k in fields]
    set_clause = ", ".join(f"{col} = %s" for col in set_columns)
    values = list(fields.values()) + [record_id]
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE {table} SET {set_clause} WHERE id = %s", values)
            updated = cur.rowcount == 1
        conn.commit()
    finally:
        conn.close()
    return updated


def replace_table(table_name, columns, rows):
    """columns: [(column_name, column_type), ...] - column_type one of
    COLUMN_TYPE_SQL's keys. rows: [{"id": ..., <column>: ...}, ...] - every
    row must include "id" (the caller generates it, matching every other
    table's convention). Validates table_name and every column name via
    validate_identifier() before building any SQL - a bad name never
    reaches the database. One transaction: DROP TABLE IF EXISTS -> CREATE
    TABLE (id TEXT PRIMARY KEY + the given typed columns) -> bulk INSERT
    every row -> commit. A drop-and-recreate, not an ALTER-in-place
    migration - there's no existing data in these tables worth preserving
    across a schema change, unlike the admin-overlay tables. Used by
    scripts/25_sync_processed_to_db.py to reload data/processed/* into
    Postgres on every pipeline run. See docs/superpowers/specs/
    2026-08-17-processed-data-db-sync-design.md."""
    validate_identifier(table_name)
    col_defs = [sql.SQL("id TEXT PRIMARY KEY")]
    json_columns = set()
    for column_name, column_type in columns:
        validate_identifier(column_name)
        col_defs.append(sql.SQL("{} {}").format(sql.Identifier(column_name), sql.SQL(_sql_type_for(column_type))))
        if column_type == "json":
            json_columns.add(column_name)
    drop_statement = sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(table_name))
    create_statement = sql.SQL("CREATE TABLE {} ({})").format(sql.Identifier(table_name), sql.SQL(", ").join(col_defs))

    db_columns = ["id"] + [c for c, _ in columns]
    columns_sql = ", ".join(db_columns)
    placeholders = ", ".join(["%s"] * len(db_columns))
    insert_statement = f"INSERT INTO {table_name} ({columns_sql}) VALUES ({placeholders})"
    values = [
        tuple(Json(r[c]) if c in json_columns else r.get(c) for c in db_columns)
        for r in rows
    ]

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(drop_statement)
            cur.execute(create_statement)
            if values:
                cur.executemany(insert_statement, values)
        conn.commit()
    finally:
        conn.close()


def list_all_tables():
    """Every real table in the bundled database's public schema - both
    the app's own known overlay/registry tables and any dynamically-
    created custom_<slug> table. Queries information_schema directly
    rather than any app-level registry, so it can never be stale - the
    server/db_browser.py layer above uses this as the one place a
    user-supplied table name gets validated before it's ever used in a
    query built via f-string interpolation (fetch_all()/update_by_id()'s
    own established pattern)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' ORDER BY table_name"
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    return [row["table_name"] for row in rows]


def list_columns(table):
    """[{"name": str, "type": str}, ...] for `table`, via
    information_schema.columns - `type` is the raw Postgres type name
    (e.g. "text", "numeric", "date"), used by server/db_browser.py for
    lightweight edit-value coercion. `table` is passed as a bound query
    parameter here, not interpolated - safe regardless of whether the
    caller has validated it against list_all_tables() yet."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = %s ORDER BY ordinal_position",
                (table,),
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    return [{"name": r["column_name"], "type": r["data_type"]} for r in rows]
