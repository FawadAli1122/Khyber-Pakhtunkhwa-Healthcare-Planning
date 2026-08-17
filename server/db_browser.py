"""Generic read/edit access to every real table in the bundled local
database - both the app's own known overlay/registry tables and any
custom_<slug> table - for the "view the whole database" admin/Telegram
feature. Distinct from db_ingestion.py (read-only access to an
*external* database the admin configures) and from custom_data.py/
supplemental_data.py/etc. (which each know their own fixed table
schema) - this module only ever queries information_schema and calls
local_db's already-generic fetch_all()/update_by_id(). See
docs/superpowers/specs/2026-08-17-database-browser-design.md.
"""
from scripts.lib import local_db

_INT_TYPES = ("bigint", "smallint")
_FLOAT_TYPES = ("numeric", "real", "double precision")


def list_tables():
    return local_db.list_all_tables()


def get_table_columns(table):
    if table not in local_db.list_all_tables():
        return None
    return local_db.list_columns(table)


def get_table_rows(table):
    """Ordered by id - not every table in this schema has a timestamp
    column (custom_table_columns has neither added_at nor created_at),
    but every table has "id TEXT PRIMARY KEY", so ordering by it is
    fully deterministic (the same set of rows always sorts identically
    across separate calls) even though the resulting order isn't
    meaningful (ids are random uuid hex, not sequential) - this is what
    lets /localedit's row-number resolution trust that the Nth row
    /localview showed is still the Nth row on a later, separate fetch."""
    if table not in local_db.list_all_tables():
        return None
    return local_db.fetch_all(table, order_by="id")


def _coerce_value(raw_value, pg_type):
    if pg_type.startswith("int") or pg_type in _INT_TYPES:
        return int(raw_value)
    if pg_type in _FLOAT_TYPES:
        return float(raw_value)
    return raw_value


def update_row(table, record_id, fields):
    """fields: {column_name: raw_value}, raw_value always a string from
    the browser/bot. Returns True/False (row found/not found) or None
    (table doesn't exist). Raises ValueError for an unknown column name,
    empty `fields`, or a value that fails coercion for its column's real
    Postgres type - always caught at the call site (admin route: 400;
    Telegram: inline error reply), never silently dropped."""
    if table not in local_db.list_all_tables():
        return None
    if not fields:
        raise ValueError("No fields to update")
    columns = {c["name"]: c["type"] for c in local_db.list_columns(table)}
    coerced = {}
    for name, raw_value in fields.items():
        if name not in columns:
            raise ValueError(f"Unknown column: {name!r}")
        coerced[name] = _coerce_value(raw_value, columns[name])
    return local_db.update_by_id(table, record_id, coerced)
