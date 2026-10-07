#!/usr/bin/env python3
"""
Res-Q backend.

Serves the static site from this folder together with a small JSON API. By
default it runs in DEMO mode: onboarding answers are accepted and a session is
returned, but nothing is written to a database and duplicate details are not
rejected, so the site can be walked through without creating a new credential
each time.

    python3 server.py                    # demo mode, port 8080
    RESQ_PERSIST=on python3 server.py     # store accounts in SQLite (frontend/resq.db)

Endpoints
    GET  /api/health                liveness probe
    POST /api/register              create an account (409 when the email or phone is taken)
    POST /api/login                 sign in with an email/phone and password
    GET  /api/me                    return the signed-in user for a bearer token
    POST /api/logout                drop the session
    POST /api/profile               save the goods profile (establishment + surplus category)

Only the standard library is used, so there is nothing to install.
"""

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("RESQ_DB", os.path.join(ROOT, "resq.db"))

# Demo mode (the default for now): onboarding answers are accepted and a session is
# handed back, but nothing is written to SQLite and duplicate details are never
# rejected, so the whole site can be walked through without registering a new
# credential every time. Accounts are kept in memory for the life of the process.
# Start the server with RESQ_PERSIST=on to store accounts in the database again.
PERSIST = os.environ.get("RESQ_PERSIST", "off").strip().lower() in ("on", "1", "true", "yes")
DEMO_SESSIONS = {}

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    role          TEXT    NOT NULL DEFAULT 'donor',
    first_name    TEXT    NOT NULL,
    last_name     TEXT    NOT NULL,
    business_name TEXT,
    email         TEXT    UNIQUE,
    phone         TEXT    UNIQUE,
    dial_code     TEXT,
    street        TEXT,
    sub_locality  TEXT,
    locality      TEXT,
    province      TEXT,
    city          TEXT,
    postal        TEXT,
    password_hash TEXT    NOT NULL,
    password_salt TEXT    NOT NULL,
    firm_type     TEXT,
    delivery_days TEXT,
    surplus_types TEXT,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sessions (
    token      TEXT    PRIMARY KEY,
    user_id    INTEGER NOT NULL,
    created_at TEXT    NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
);
"""

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PUBLIC_FIELDS = (
    "id", "role", "first_name", "last_name", "business_name", "email", "phone",
    "dial_code", "street", "sub_locality", "locality", "province", "city",
    "postal", "firm_type", "delivery_days", "surplus_types", "created_at",
)

# These two arrive as lists and are stored as JSON text, so a row has to be decoded
# before it goes out.
LIST_FIELDS = ("delivery_days", "surplus_types")

# The goods profile asks different questions per role: donors describe where the goods
# come from, recipients when they want them delivered and what they need.
DELIVERY_DAYS = ("sat", "sun", "mon", "tue", "wed", "thu", "fri")
DONOR_SURPLUS = (
    "prepared_meals", "fresh_produce", "bakery_items", "packaged_goods",
    "dairy_beverages", "household_essentials",
)
RECIPIENT_NEEDS = (
    "prepared_meals", "fresh_produce", "dairy_beverages", "household_essentials",
    "packaged_goods",
)

# Which firm classifications may hand over each surplus category. This mirrors the rule
# the goods profile applies to the dropdown, so a crafted request cannot save a category
# the chosen establishment does not actually produce.
FIRM_SURPLUS = {
    "prepared_meals": ("retail", "eatery"),
    "fresh_produce": ("retail",),
    "bakery_items": ("retail", "eatery"),
    "packaged_goods": ("retail",),
    "dairy_beverages": ("retail", "eatery"),
    "household_essentials": ("retail",),
}

# Every profile question takes at most two answers.
MAX_PICKS = 2


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def clean_selection(value, allowed, limit=MAX_PICKS):
    """Normalise a 1..limit multi-select answer. None means it is not a valid answer.

    A bare string is accepted so a caller that still sends a single value keeps working.
    """
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return None
    picked = []
    for item in value:
        if not isinstance(item, str):
            return None
        key = item.strip().lower()
        if not key or key in picked:
            continue
        if key not in allowed:
            return None
        picked.append(key)
    if not picked or len(picked) > limit:
        return None
    return picked


def decode_list(value):
    """Read a stored JSON list back as a list of strings."""
    if isinstance(value, list):
        return value
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, str)]


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


def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120_000)
    return digest.hex(), salt


def verify_password(password, digest, salt):
    candidate, _ = hash_password(password, salt)
    return hmac.compare_digest(candidate, digest)


def demo_lookup(email, phone):
    """Return a demo user registered earlier in this process, if there is one."""
    for user in DEMO_SESSIONS.values():
        if email and user.get("email") == email:
            return dict(user)
        if phone and user.get("phone") == phone:
            return dict(user)
    return None


def demo_display_name(contact):
    """Turn an unknown email or phone into something friendly to greet."""
    if contact and "@" in contact:
        local = contact.split("@")[0]
        parts = [p for p in re.split(r"[._-]+", local) if p]
        if parts:
            return parts[0].capitalize()
    return "Res-Q user"


def password_problem(password):
    if len(password) < 8:
        return "Please use at least 8 characters for your password."
    if not re.search(r"[A-Z]", password):
        return "Please include at least 1 uppercase letter in your password."
    if not re.search(r"\d", password):
        return "Please include at least 1 number in your password."
    return None


def clean_email(value):
    return (value or "").strip().lower()


def clean_phone(value):
    return re.sub(r"[^\d]", "", value or "")


class ResQHandler(SimpleHTTPRequestHandler):
    server_version = "ResQ/1.0"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT, **kwargs)

    # ------------------------------------------------------------------ helpers
    def send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

    def bearer_token(self):
        header = self.headers.get("Authorization") or ""
        if header.lower().startswith("bearer "):
            return header[7:].strip()
        return ""

    def user_for_token(self, conn, token):
        if not token:
            return None
        return conn.execute(
            "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token = ?",
            (token,),
        ).fetchone()

    def public_user(self, row):
        keys = row.keys()
        user = {field: row[field] for field in PUBLIC_FIELDS if field in keys}
        for field in LIST_FIELDS:
            if field in user:
                user[field] = decode_list(user[field])
        return user

    def start_session(self, conn, user_id):
        token = secrets.token_urlsafe(24)
        conn.execute("INSERT INTO sessions (token, user_id) VALUES (?, ?)", (token, user_id))
        return token

    # ------------------------------------------------------------------- routes
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/health":
            return self.send_json(
                200,
                {
                    "ok": True,
                    "mode": "persistent" if PERSIST else "demo",
                    "database": os.path.basename(DB_PATH) if PERSIST else None,
                    "demo_sessions": len(DEMO_SESSIONS),
                },
            )
        if path == "/api/me":
            return self.api_me()
        if path.startswith("/api/"):
            return self.send_json(404, {"error": "Unknown endpoint."})
        return super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/register":
            return self.api_register()
        if path == "/api/login":
            return self.api_login()
        if path == "/api/logout":
            return self.api_logout()
        if path == "/api/profile":
            return self.api_profile()
        return self.send_json(404, {"error": "Unknown endpoint."})

    # ------------------------------------------------------------------ handlers
    def api_register(self):
        data = self.read_json()
        if data is None:
            return self.send_json(400, {"error": "Malformed request body."})

        role = (data.get("role") or "donor").strip().lower()
        if role not in ("donor", "recipient"):
            role = "donor"

        first_name = (data.get("firstName") or "").strip()
        last_name = (data.get("lastName") or "").strip()
        if not first_name or not last_name:
            return self.send_json(
                400, {"error": "Please fill in your name to continue.", "field": "firstName"}
            )

        # Anything that is not the expected shape is treated as an empty one, so a
        # malformed request is answered rather than killing the request thread.
        contact = data.get("contact")
        if not isinstance(contact, dict):
            contact = {}
        kind = "phone" if (contact.get("type") == "phone") else "email"
        value = (contact.get("value") or "").strip()
        dial_code = (contact.get("dial") or "").strip() or None
        email = phone = None

        if kind == "email":
            email = clean_email(value)
            if not email:
                return self.send_json(
                    400,
                    {"error": "Please enter your email address to continue.", "field": "email"},
                )
            if not EMAIL_RE.match(email):
                return self.send_json(
                    400,
                    {
                        "error": "That does not look like a valid email address.",
                        "field": "email",
                    },
                )
        else:
            phone = clean_phone(value)
            if not phone:
                return self.send_json(
                    400,
                    {"error": "Please enter your phone number to continue.", "field": "phone"},
                )
            if len(phone) < 6:
                return self.send_json(
                    400, {"error": "That phone number looks too short.", "field": "phone"}
                )

        password = data.get("password") or ""
        problem = password_problem(password)
        if problem:
            return self.send_json(400, {"error": problem, "field": "password"})

        address = data.get("address")
        if not isinstance(address, dict):
            address = {}

        # Demo mode: take the answers, keep them in memory for this run, store nothing.
        if not PERSIST:
            user = {
                "id": 0,
                "role": role,
                "first_name": first_name,
                "last_name": last_name,
                "business_name": (data.get("businessName") or "").strip() or None,
                "email": email,
                "phone": phone,
                "dial_code": dial_code,
                "firm_type": None,
                "delivery_days": [],
                "surplus_types": [],
                "demo": True,
            }
            token = "demo-" + secrets.token_urlsafe(12)
            DEMO_SESSIONS[token] = user
            return self.send_json(201, {"ok": True, "demo": True, "token": token, "user": user})

        digest, salt = hash_password(password)

        with connect() as conn:
            if email:
                existing = conn.execute(
                    "SELECT id FROM users WHERE email = ?", (email,)
                ).fetchone()
                if existing:
                    return self.send_json(
                        409,
                        {
                            "error": "An account already exists for " + email
                            + ". Sign in instead, or use a different email address.",
                            "field": "email",
                            "code": "duplicate_email",
                        },
                    )
            if phone:
                existing = conn.execute(
                    "SELECT id FROM users WHERE phone = ?", (phone,)
                ).fetchone()
                if existing:
                    return self.send_json(
                        409,
                        {
                            "error": "An account already exists for this phone number."
                            " Sign in instead, or use a different number.",
                            "field": "phone",
                            "code": "duplicate_phone",
                        },
                    )

            cursor = conn.execute(
                """INSERT INTO users (
                       role, first_name, last_name, business_name, email, phone, dial_code,
                       street, sub_locality, locality, province, city, postal,
                       password_hash, password_salt
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    role,
                    first_name,
                    last_name,
                    (data.get("businessName") or "").strip() or None,
                    email,
                    phone,
                    dial_code,
                    (address.get("street") or "").strip() or None,
                    (address.get("subLocality") or "").strip() or None,
                    (address.get("locality") or "").strip() or None,
                    (address.get("province") or "").strip() or None,
                    (address.get("city") or "").strip() or None,
                    (address.get("postal") or "").strip() or None,
                    digest,
                    salt,
                ),
            )
            user_id = cursor.lastrowid
            token = self.start_session(conn, user_id)
            conn.commit()
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()

        return self.send_json(201, {"ok": True, "token": token, "user": self.public_user(row)})

    def api_login(self):
        data = self.read_json()
        if data is None:
            return self.send_json(400, {"error": "Malformed request body."})

        contact = (data.get("contact") or "").strip()
        password = data.get("password") or ""
        if not contact or not password:
            return self.send_json(
                400,
                {
                    "error": "Enter the email or phone you registered with, plus your password.",
                    "field": "contact",
                },
            )

        # Demo mode: sign anyone in. A contact registered earlier in this run keeps its
        # details; anything else gets a session so no account has to be created first.
        if not PERSIST:
            email = clean_email(contact) if "@" in contact else None
            phone = None if email else clean_phone(contact)
            user = demo_lookup(email, phone) or {
                "id": 0,
                "role": "donor",
                "first_name": demo_display_name(email or phone),
                "last_name": "",
                "email": email,
                "phone": phone,
                "demo": True,
            }
            token = "demo-" + secrets.token_urlsafe(12)
            DEMO_SESSIONS[token] = user
            return self.send_json(200, {"ok": True, "demo": True, "token": token, "user": user})

        with connect() as conn:
            if "@" in contact:
                row = conn.execute(
                    "SELECT * FROM users WHERE email = ?", (clean_email(contact),)
                ).fetchone()
                label = clean_email(contact)
            else:
                row = conn.execute(
                    "SELECT * FROM users WHERE phone = ?", (clean_phone(contact),)
                ).fetchone()
                label = contact

            if row is None:
                return self.send_json(
                    404,
                    {
                        "error": "No Res-Q account matches " + label
                        + ". Onboard to create one.",
                        "field": "contact",
                        "code": "no_account",
                    },
                )
            if not verify_password(password, row["password_hash"], row["password_salt"]):
                return self.send_json(
                    401,
                    {
                        "error": "That password does not match our records.",
                        "field": "password",
                        "code": "bad_password",
                    },
                )

            token = self.start_session(conn, row["id"])
            conn.commit()
            user = self.public_user(row)

        return self.send_json(200, {"ok": True, "token": token, "user": user})

    def api_me(self):
        token = self.bearer_token()
        if not PERSIST:
            user = DEMO_SESSIONS.get(token)
            if user is None:
                return self.send_json(
                    401, {"error": "Your session has expired. Please sign in again."}
                )
            return self.send_json(200, {"ok": True, "demo": True, "user": user})
        with connect() as conn:
            row = self.user_for_token(conn, token)
        if row is None:
            return self.send_json(
                401, {"error": "Your session has expired. Please sign in again."}
            )
        return self.send_json(200, {"ok": True, "user": self.public_user(row)})

    def api_logout(self):
        token = self.bearer_token() or (self.read_json() or {}).get("token", "")
        if token:
            if not PERSIST:
                DEMO_SESSIONS.pop(token, None)
                return self.send_json(200, {"ok": True, "demo": True})
            with connect() as conn:
                conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
                conn.commit()
        return self.send_json(200, {"ok": True})

    def api_profile(self):
        data = self.read_json()
        if data is None:
            return self.send_json(400, {"error": "Malformed request body."})

        token = self.bearer_token()

        # Resolve the signed-in user first: the role decides which answers are expected.
        if PERSIST:
            with connect() as conn:
                row = self.user_for_token(conn, token)
            if row is None:
                return self.send_json(
                    401,
                    {
                        "error": "Your session has expired. Please sign in again.",
                        "code": "no_session",
                    },
                )
            session_user = self.public_user(row)
        else:
            session_user = DEMO_SESSIONS.get(token)
            if session_user is None:
                return self.send_json(
                    401,
                    {
                        "error": "Your session has expired. Please sign in again.",
                        "code": "no_session",
                    },
                )

        role = session_user.get("role", "donor")
        firm_type = (data.get("firmType") or "").strip().lower()
        # Both questions take up to MAX_PICKS answers. The singular keys are still read so
        # a page from before the multi-select change keeps working.
        raw_days = data.get("deliveryDays", data.get("deliveryDay"))
        raw_types = data.get("surplusTypes", data.get("surplusType"))
        delivery_days = []
        surplus_types = []

        if role == "recipient":
            days = clean_selection(raw_days, DELIVERY_DAYS)
            if days is None:
                return self.send_json(
                    400,
                    {
                        "error": "Select up to 2 days you would prefer deliveries.",
                        "field": "deliveryDays",
                    },
                )
            needs = clean_selection(raw_types, RECIPIENT_NEEDS)
            if needs is None:
                return self.send_json(
                    400,
                    {"error": "Select up to 2 goods you need.", "field": "surplusTypes"},
                )
            firm_type = None
            delivery_days = days
            surplus_types = needs
        else:
            if firm_type not in ("retail", "eatery"):
                return self.send_json(
                    400,
                    {
                        "error": "Select whether you are a retail store or an eatery.",
                        "field": "firmType",
                    },
                )
            categories = clean_selection(raw_types, DONOR_SURPLUS)
            if categories is None:
                return self.send_json(
                    400,
                    {
                        "error": "Select up to 2 usual surplus categories.",
                        "field": "surplusTypes",
                    },
                )
            offered = tuple(
                name for name in DONOR_SURPLUS if firm_type in FIRM_SURPLUS.get(name, ())
            )
            if [name for name in categories if name not in offered]:
                return self.send_json(
                    400,
                    {
                        "error": "That establishment cannot offer one of the categories you picked.",
                        "field": "surplusTypes",
                    },
                )
            surplus_types = categories

        if not PERSIST:
            session_user["firm_type"] = firm_type
            session_user["delivery_days"] = delivery_days
            session_user["surplus_types"] = surplus_types
            return self.send_json(200, {"ok": True, "demo": True, "user": session_user})

        with connect() as conn:
            conn.execute(
                "UPDATE users SET firm_type = ?, delivery_days = ?, surplus_types = ? WHERE id = ?",
                (
                    firm_type,
                    json.dumps(delivery_days),
                    json.dumps(surplus_types),
                    session_user["id"],
                ),
            )
            conn.commit()
            updated = conn.execute(
                "SELECT * FROM users WHERE id = ?", (session_user["id"],)
            ).fetchone()

        return self.send_json(200, {"ok": True, "user": self.public_user(updated)})

    # ------------------------------------------------------------------ caching
    def end_headers(self):
        # Nothing here should be cached: pages change often and JSON is per-user.
        self.send_header("Cache-Control", "no-store, must-revalidate")
        super().end_headers()

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    if PERSIST:
        init_db()
    server = ThreadingHTTPServer(("127.0.0.1", port), ResQHandler)
    if PERSIST:
        print("Res-Q backend on http://127.0.0.1:%d" % port, flush=True)
        print("  mode: PERSISTENT — accounts are stored in %s" % DB_PATH, flush=True)
    else:
        print("Res-Q backend on http://127.0.0.1:%d" % port, flush=True)
        print(
            "  mode: DEMO — nothing is written to a database and no detail is\n"
            "        rejected as a duplicate. Set RESQ_PERSIST=on to store accounts.",
            flush=True,
        )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
