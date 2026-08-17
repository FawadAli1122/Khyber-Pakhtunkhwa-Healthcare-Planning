"""Connects to a PostgreSQL database, lists its tables, and renders a
table's rows as pipe-delimited text - the database-specific counterpart to
document_extraction.py. No AI, no validation, no storage logic of its own;
its output feeds supplemental_data.add_from_document() exactly the way an
uploaded CSV file's extracted text does. See docs/superpowers/specs/
2026-08-15-database-ingestion-phase4c-design.md.
"""
import psycopg2

ROW_LIMIT = 200


class DbIngestionError(Exception):
    """Raised when a database connection or query fails - message safe to
    show the admin directly, never a raw psycopg2 exception or traceback."""


def _connect(conn_info):
    try:
        return psycopg2.connect(
            host=conn_info["host"],
            port=conn_info["port"],
            dbname=conn_info["database"],
            user=conn_info["user"],
            password=conn_info["password"],
            sslmode=conn_info.get("sslmode") or "prefer",
            connect_timeout=5,
        )
    except Exception as exc:
        raise DbIngestionError(str(exc)) from exc


def test_connection(conn_info):
    try:
        conn = _connect(conn_info)
    except DbIngestionError as exc:
        return False, str(exc)
    conn.close()
    return True, "Connected"


def list_tables(conn_info):
    conn = _connect(conn_info)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' ORDER BY table_name"
            )
            return [row[0] for row in cur.fetchall()]
    except DbIngestionError:
        raise
    except Exception as exc:
        raise DbIngestionError(str(exc)) from exc
    finally:
        conn.close()


def fetch_table_text(conn_info, table_name, row_limit=ROW_LIMIT):
    known_tables = list_tables(conn_info)
    if table_name not in known_tables:
        raise DbIngestionError(f"Unknown table: {table_name!r}")

    conn = _connect(conn_info)
    try:
        with conn.cursor() as cur:
            cur.execute(f'SELECT * FROM "{table_name}" LIMIT %s', (row_limit,))
            columns = [desc[0] for desc in cur.description]
            rows = cur.fetchall()
    except Exception as exc:
        raise DbIngestionError(str(exc)) from exc
    finally:
        conn.close()

    lines = [f"(showing first {row_limit} rows)", " | ".join(columns)]
    for row in rows:
        lines.append(" | ".join("" if cell is None else str(cell) for cell in row))
    return "\n".join(lines)
