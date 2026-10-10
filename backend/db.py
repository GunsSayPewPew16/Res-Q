"""Res-Q backend database access.

The SQLite connection and the schema. One connection shape (`connect`) returns
rows as dictionaries, soreading an account column by name is the same in demo
and persistent mode; `init_db` creates the tables and carries older databases
forward, column by column, without a migration tool.
"""

import json
import sqlite3

from config import DB_PATH, SCHEMA

def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    with connect() as conn:
        conn.executescript(SCHEMA)
        # Databases created while each question took a single answer carry singular
        # columns; add the list columns and carry the old answers across as one-item lists.
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
        if "delivery_days" not in columns:
            conn.execute("ALTER TABLE users ADD COLUMN delivery_days TEXT")
        if "surplus_types" not in columns:
            conn.execute("ALTER TABLE users ADD COLUMN surplus_types TEXT")
        # Accounts created before the dashboard map saved anything have no delivery
        # location: add the columns so a confirmed location has somewhere to land.
        for column, kind in (
            ("delivery_address", "TEXT"),
            ("delivery_lat", "REAL"),
            ("delivery_lng", "REAL"),
            ("delivery_confirmed_at", "TEXT"),
        ):
            if column not in columns:
                conn.execute("ALTER TABLE users ADD COLUMN %s %s" % (column, kind))
        # The donor's impact figures live on the donor's profile too. A profile created
        # before them gets the columns here and its first figures the first time it is
        # read; a recipient's columns simply stay NULL.
        for column, kind in (
            ("surplus_saved", "REAL"),
            ("meals_served", "INTEGER"),
            ("orders_completed", "INTEGER"),
        ):
            if column not in columns:
                conn.execute("ALTER TABLE users ADD COLUMN %s %s" % (column, kind))
        if "delivery_day" in columns or "surplus_type" in columns:
            old_day = "delivery_day" if "delivery_day" in columns else "NULL"
            old_type = "surplus_type" if "surplus_type" in columns else "NULL"
            rows = conn.execute(
                "SELECT id, %s AS delivery_day, %s AS surplus_type FROM users "
                "WHERE delivery_days IS NULL AND surplus_types IS NULL" % (old_day, old_type)
            ).fetchall()
            for row in rows:
                days = [row["delivery_day"]] if row["delivery_day"] else []
                types = [row["surplus_type"]] if row["surplus_type"] else []
                if not days and not types:
                    continue
                conn.execute(
                    "UPDATE users SET delivery_days = ?, surplus_types = ? WHERE id = ?",
                    (json.dumps(days), json.dumps(types), row["id"]),
                )

def users_by_ids(conn, ids):
    """Load several accounts in one query, keyed by id."""
    unique = [user_id for user_id in dict.fromkeys(ids) if user_id is not None]
    if not unique:
        return {}
    rows = conn.execute(
        "SELECT * FROM users WHERE id IN (%s)" % ", ".join("?" * len(unique)), unique
    ).fetchall()
    return {row["id"]: dict(row) for row in rows}
