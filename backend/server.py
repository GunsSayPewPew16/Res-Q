#!/usr/bin/env python3
"""
Res-Q backend.

Serves the static site from the sibling frontend/ folder together with a small
JSON API. By default it runs in DEMO mode: onboarding answers are accepted and a
session is returned, but nothing is written to a database and duplicate details
are not rejected, so the site can be walked through without creating a new
credential each time.

Matching is the second half of the API: once an account has confirmed its delivery
location, GET /api/matches ranks the counterparts near it that share a category, and
POST /api/orders turns a chosen pair into a delivery order. A logged surplus item goes
through the same machinery: POST /api/submit-surplus scores it against the registered
shelters and answers with the one that should take it.

    python3 backend/server.py                   # demo mode, port 8080
    RESQ_PERSIST=on python3 backend/server.py   # store accounts in SQLite (backend/resq.db)

Endpoints
    GET  /api/health                liveness probe
    POST /api/register              create an account (409 when the email or phone is taken)
    POST /api/login                 sign in with an email/phone and password
    GET  /api/me                    return the signed-in user for a bearer token
    POST /api/logout                drop the session
    POST /api/profile               save the goods profile (establishment + surplus category)
    POST /api/delivery-location     save the location confirmed on the dashboard map
    POST /api/submit-surplus        match a logged surplus item to the shelter that takes it
    GET  /api/matches               rank the counterparts near the account, nearest first
    GET  /api/orders                list the delivery orders the account is part of
    POST /api/orders                bind the account and one counterpart into an order

Only the standard library is used, so there is nothing to install.
"""

import datetime
import hashlib
import hmac
import json
import math
import os
import random
import re
import secrets
import sqlite3
import sys
import time
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from itertools import count
from urllib.parse import parse_qs, quote, urlparse

# This folder is the backend: its code and its database file live here. The pages it
# serves live beside it in frontend/, so the two are resolved separately — the site's
# document root is the pages' folder, never this one.
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.join(os.path.dirname(BACKEND_DIR), "frontend")
DB_PATH = os.environ.get("RESQ_DB", os.path.join(BACKEND_DIR, "resq.db"))

# Demo mode (the default for now): onboarding answers are accepted and a session is
# handed back, but nothing is written to SQLite and duplicate details are never
# rejected, so the whole site can be walked through without registering a new
# credential every time. Accounts are kept in memory for the life of the process.
# Start the server with RESQ_PERSIST=on to store accounts in the database again.
PERSIST = os.environ.get("RESQ_PERSIST", "off").strip().lower() in ("on", "1", "true", "yes")
DEMO_SESSIONS = {}
# Demo orders belong to a session token the way the sessions themselves do, and go when
# the process does. The counter hands out ids that look like the persistent ones.
DEMO_ORDERS = {}
DEMO_ORDER_SEQ = count(1)

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
    delivery_address TEXT,
    delivery_lat  REAL,
    delivery_lng  REAL,
    delivery_confirmed_at TEXT,
    surplus_saved REAL,
    meals_served  INTEGER,
    orders_completed INTEGER,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sessions (
    token      TEXT    PRIMARY KEY,
    user_id    INTEGER NOT NULL,
    created_at TEXT    NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS orders (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    donor_id        INTEGER NOT NULL,
    recipient_id    INTEGER NOT NULL,
    category        TEXT    NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'proposed',
    scheduled_for   TEXT,
    pickup_address  TEXT,
    pickup_lat      REAL,
    pickup_lng      REAL,
    dropoff_address TEXT,
    dropoff_lat     REAL,
    dropoff_lng     REAL,
    distance_km     REAL,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (donor_id) REFERENCES users (id) ON DELETE CASCADE,
    FOREIGN KEY (recipient_id) REFERENCES users (id) ON DELETE CASCADE
);
"""

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PUBLIC_FIELDS = (
    "id", "role", "first_name", "last_name", "business_name", "email", "phone",
    "dial_code", "street", "sub_locality", "locality", "province", "city",
    "postal", "firm_type", "delivery_days", "surplus_types",
    "delivery_address", "delivery_lat", "delivery_lng", "delivery_confirmed_at",
    "surplus_saved", "meals_served", "orders_completed",
    "created_at",
)

# The delivery location confirmed on the dashboard map. The coordinates are the point
# for routing; the address is what the map showed at that point when it was confirmed.
DELIVERY_FIELDS = ("delivery_address", "delivery_lat", "delivery_lng", "delivery_confirmed_at")
# The two columns a confirmed dashboard pin lives in, which is what places a shelter.
PIN_FIELDS = ("delivery_lat", "delivery_lng")
MAX_ADDRESS_CHARS = 400
# The largest body any form here sends is a registration, well under a kilobyte; a
# request that declares more than this is answered as unreadable rather than read, so a
# thread cannot be parked waiting on bytes a client only promised.
MAX_REQUEST_BODY_BYTES = 64 * 1024
# The Log surplus form's own input limits, so the route never accepts more than a form
# could have sent, and nothing a page types can be used to grow a row without bound.
MAX_ITEM_CHARS = 120
MAX_QUANTITY_CHARS = 80

# The three impact figures the donor dashboard shows. They belong to the donor's own
# profile and to no other role: they are that donor's record of what their surplus
# became. Surplus saved is a weight in kilograms, kept to two decimals — fractions of a
# kilo matter when food is weighed — while meals served and orders completed are whole
# counts.
DONOR_METRIC_FIELDS = ("surplus_saved", "meals_served", "orders_completed")
METRIC_DECIMALS = 2

# Nothing has come out of the pipeline yet, so a new donor profile starts from the
# plausible range the dashboard's placeholders used. A routing pass that completes an
# order would overwrite these with what actually moved.
NEW_DONOR_SURPLUS_KG = (1200.0, 4800.0)
NEW_DONOR_MEALS = (2400, 9600)
NEW_DONOR_ORDERS = (40, 260)

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

# A delivery is worth proposing only when both sides have a point on the map: the donor's
# pickup and the recipient's drop-off. Candidates are therefore filtered by distance from
# the caller's own confirmed pin, must share at least one category, and are ranked
# nearest first, because distance is what a delivery costs.
DEFAULT_RADIUS_KM = 25.0
MAX_RADIUS_KM = 250.0
DEFAULT_MATCH_LIMIT = 10
MAX_MATCH_LIMIT = 50
KM_PER_DEGREE = 111.32
EARTH_RADIUS_KM = 6371.0088

# A surplus item and a shelter first meet the hard filter: the shelter has to accept the
# kind of item on offer, and the two pins have to sit within HARD_FILTER_RADIUS_KM of each
# other along the road. The road measurement comes from OSRM's public demo router — the
# same service the root 'import requests.py' experiment called — so it needs no key and no
# package. That is an outbound call, hence a short timeout and a handled failure rather
# than a fatal one.
HARD_FILTER_RADIUS_KM = 15.0
OSRM_TIMEOUT_SECONDS = 5.0
OSRM_ROUTE_URL = (
    "https://router.project-osrm.org/route/v1/driving/%s,%s;%s,%s?overview=false"
)
# The same pair of pins is measured again on every refresh and the router is shared with
# the rest of the internet, so successful answers are remembered for the life of the
# process; a full cache starts over rather than evicting one entry at a time.
ROAD_DISTANCE_CACHE = {}
ROAD_DISTANCE_CACHE_LIMIT = 512

# An address typed on the website carries no coordinates, so the same Nominatim geocoder the
# root 'import requests.py' experiment called turns it into a pin before anything is
# measured. Its usage policy asks for one request a second and an identifying agent, so
# calls are spaced and signed; a hit is remembered like a road distance is.
NOMINATIM_SEARCH_URL = "https://nominatim.openstreetmap.org/search?format=json&limit=1&q=%s"
GEOCODING_TIMEOUT_SECONDS = 5.0
NOMINATIM_MIN_INTERVAL_SECONDS = 1.0
GEOCODER_USER_AGENT = "resq-backend/1.0 (prototype; github.com/GunsSayPewPew16/Res-Q)"
GEOCODE_CACHE = {}
GEOCODE_CACHE_LIMIT = 256
GEOCODE_LAST_CALL_AT = 0.0
# The address columns the onboarding form fills, in the order they read back as one line.
ADDRESS_PART_FIELDS = ("street", "sub_locality", "locality", "city", "province", "postal")

# The allocation score is the prototype's weighting: how close the drive is, how much the
# shelter needs the goods, and how soon they expire — 0.3, 0.4 and 3.0. Proximity counts
# down from the filter radius, and the urgency multiplier is 1 / (hours + 0.1), so goods
# nearly out of time outrank a comfortable run by design.
DISTANCE_SCORE_CEILING_KM = 15.0
DISTANCE_SCORE_WEIGHT = 0.3
NEED_SCORE_WEIGHT = 0.4
URGENCY_WEIGHT = 3.0
URGENCY_SMOOTHING_HOURS = 0.1
SCORE_DECIMALS = 2

# The allocation score needs a need score and an expiry time, and the Log surplus form
# collects neither, so a submission fills both with what is written here rather than
# guessing halfway through the weighting: a shelter nobody has rated carries the middle of
# the prototype's 1-to-10 scale, and logged goods are taken to keep for a day.
DEFAULT_SHELTER_NEED_SCORE = 5.0
DEFAULT_SURPLUS_EXPIRY_HOURS = 24.0

# A logged item arrives as free text ("Crates of ripe tomatoes"), so the submission route
# reads the words a shop would use and files the item under one of the profile categories.
# A keyword matches at a word start inside the item or the quantity, so "tomato" catches
# "tomatoes"; the quantity only decides when the item name said nothing. Nothing matching
# is an answer too — the caller falls back to the donor's own goods profile rather than
# putting the item on a shelf nobody asked for.
SURPLUS_KEYWORDS = (
    ("prepared_meals", ("meal", "cooked", "curry", "soup", "rice", "biryani", "stew",
                        "sandwich", "pizza", "roast", "casserole", "dinner", "lunch",
                        "leftover")),
    ("fresh_produce", ("produce", "vegetable", "veggie", "fruit", "tomato", "potato",
                       "onion", "carrot", "lettuce", "salad", "spinach", "apple",
                       "banana", "orange", "berr", "mushroom", "pepper", "cabbage",
                       "herb", "melon", "cucumber", "broccoli", "avocado", "lemon")),
    ("bakery_items", ("bakery", "bread", "bagel", "bun", "roll", "pastry", "croissant",
                      "cake", "muffin", "cookie", "biscuit", "doughnut", "donut",
                      "loaf", "loaves")),
    ("dairy_beverages", ("dairy", "milk", "cheese", "yogurt", "yoghurt", "butter",
                         "cream", "juice", "beverage", "drink", "smoothie", "kefir")),
    ("household_essentials", ("soap", "detergent", "hygiene", "shampoo", "sanitiser",
                              "sanitizer", "cleaning", "cleaner", "tissue", "toothpaste",
                              "diaper", "nappy", "paper towel", "bleach", "wipes")),
    ("packaged_goods", ("packaged", "packet", "canned", "tin", "jar", "carton", "box",
                        "pasta", "noodle", "flour", "grain", "cereal", "snack", "chips",
                        "bottle", "oil", "sauce", "spice", "sugar", "salt", "coffee",
                        "tea")),
)

# An order starts life proposed. Until the status transitions land that is the only
# status one can be in, and these are the two that stop a second order for the same pair
# and category being proposed on top of the first.
LIVE_ORDER_STATUSES = ("proposed", "accepted")

# Demo mode has no second account to match against, so the pipeline is walked through
# against fabricated counterparts. Each one sits a fixed distance from the caller's own
# confirmed pin, on a fixed bearing, so the numbers stay plausible wherever the pin was
# dropped and a counterpart is always in the same place for a given caller. Every donor
# category is covered by a recipient here and every recipient need by a donor, so
# whichever role is signed in has something to match. They exist in demo mode only.
DEMO_PEERS = (
    {"id": 9001, "role": "recipient", "contact": "Dana Whitfield",
     "business_name": "Harbour Lights Shelter", "street": "12 Harbour Lane",
     "distance_km": 1.2, "bearing": 20,
     "surplus_types": ("prepared_meals", "fresh_produce"),
     "delivery_days": ("sat", "tue")},
    {"id": 9002, "role": "recipient", "contact": "Marcus Ilori",
     "business_name": "Riverside Community Pantry", "street": "88 Riverside Walk",
     "distance_km": 2.8, "bearing": 115,
     "surplus_types": ("packaged_goods", "household_essentials"),
     "delivery_days": ("mon", "thu")},
    {"id": 9003, "role": "recipient", "contact": "Priya Raghunathan",
     "business_name": "Maple Court Seniors Centre", "street": "4 Maple Court",
     "distance_km": 4.6, "bearing": 200,
     "surplus_types": ("dairy_beverages", "prepared_meals"),
     "delivery_days": ("sat", "wed")},
    {"id": 9004, "role": "recipient", "contact": "Ruth Okonjo",
     "business_name": "Westbrook Family Kitchen", "street": "301 Westbrook Road",
     "distance_km": 7.9, "bearing": 285,
     "surplus_types": ("fresh_produce", "dairy_beverages"),
     "delivery_days": ("sun", "fri")},
    {"id": 9005, "role": "donor", "contact": "Elena Marchetti", "firm_type": "retail",
     "business_name": "Greenway Grocers", "street": "50 Greenway Parade",
     "distance_km": 2.1, "bearing": 35,
     "surplus_types": ("fresh_produce", "packaged_goods"),
     "delivery_days": ()},
    {"id": 9006, "role": "donor", "contact": "Tom Bexley", "firm_type": "eatery",
     "business_name": "Corner Bakehouse", "street": "9 Corner Street",
     "distance_km": 3.4, "bearing": 150,
     "surplus_types": ("bakery_items", "prepared_meals"),
     "delivery_days": ()},
    {"id": 9007, "role": "donor", "contact": "Aisha Karim", "firm_type": "retail",
     "business_name": "Lakeshore Market", "street": "220 Lakeshore Drive",
     "distance_km": 5.7, "bearing": 240,
     "surplus_types": ("dairy_beverages", "household_essentials"),
     "delivery_days": ()},
    {"id": 9008, "role": "donor", "contact": "Owen Trask", "firm_type": "eatery",
     "business_name": "Sunlit Deli", "street": "77 Sunlit Avenue",
     "distance_km": 9.3, "bearing": 320,
     "surplus_types": ("dairy_beverages", "bakery_items"),
     "delivery_days": ()},
)


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


def seed_donor_metrics():
    """The impact figures a new donor profile starts with."""
    return {
        "surplus_saved": round(random.uniform(*NEW_DONOR_SURPLUS_KG), METRIC_DECIMALS),
        "meals_served": random.randint(*NEW_DONOR_MEALS),
        "orders_completed": random.randint(*NEW_DONOR_ORDERS),
    }


def fill_donor_metrics(user):
    """Give a donor profile the impact figures it is missing, in place.

    Returns True when anything was filled. A profile created before the figures existed
    gets them the first time it is read, so a donor never has to wait for a fresh
    sign-in to see their own numbers. A recipient is left alone: the figures are the
    donor's, and a recipient profile carries none of them.
    """
    if user.get("role") != "donor":
        return False
    missing = [field for field in DONOR_METRIC_FIELDS if user.get(field) is None]
    if not missing:
        return False
    fresh = seed_donor_metrics()
    for field in missing:
        user[field] = fresh[field]
    return True


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


def display_name(user):
    """What to call an account in a match or an order: the business, if there is one."""
    if not user:
        return "Res-Q user"
    business = (user.get("business_name") or "").strip()
    if business:
        return business
    person = ("%s %s" % (user.get("first_name") or "", user.get("last_name") or "")).strip()
    return person or "Res-Q user"


def clean_id(value):
    """A user id as it arrives over JSON: a whole number, a numeric string, or None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    if isinstance(value, str):
        text = value.strip()
        if text.lstrip("+-").isdigit():
            return int(text)
    return None


def text_answer(data, name):
    """One text field out of a request body, as a string whatever the client sent.

    A JSON body can carry a number, a list or an object where the form promised a string,
    and ``value.strip()`` on one of those raises in the middle of a handler — which ends
    the request with no answer at all instead of the field's own 400. Anything that is not
    a string reads as absent, exactly like a key that was never sent.
    """
    value = data.get(name)
    return value.strip() if isinstance(value, str) else ""


def query_number(query, name, default, minimum, maximum):
    """Read a bounded numeric query parameter; None when it is not a number at all.

    A value outside the range is pulled to the nearest end rather than refused, so a
    request that asks for everything simply gets the largest answer allowed.
    """
    raw = (query.get(name) or [""])[0].strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return None
    if value != value:
        return None
    return max(minimum, min(maximum, value))


def haversine_km(lat1, lng1, lat2, lng2):
    """Great-circle distance between two points on the map, in kilometres."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lng2 - lng1)
    a = (math.sin(d_phi / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2)
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def location_of(record):
    """The (lat, lng) a surplus item or shelter carries, under its "location" key."""
    location = record.get("location")
    if not isinstance(location, (list, tuple)) or len(location) != 2:
        raise ValueError("a surplus item or shelter needs a location as (lat, lng)")
    return float(location[0]), float(location[1])


def address_text(record):
    """A record's address as one line: its "address" text, or its stored parts joined."""
    address = str(record.get("address") or "").strip()
    if address:
        return address
    parts = [str(record.get(field) or "").strip() for field in ADDRESS_PART_FIELDS]
    return ", ".join(part for part in parts if part)


def geocode_address(address):
    """The (lat, lng) a written address stands for, from the Nominatim geocoder.

    The website's onboarding collects an address but no coordinates, so the same service
    the root 'import requests.py' experiment called resolves it here, before any distance
    is measured. Calls are spaced to the service's one-a-second policy and signed with an
    identifying agent; a hit is remembered for the life of the process. Returns None when
    the service cannot answer or knows no such place.
    """
    global GEOCODE_LAST_CALL_AT
    key = " ".join(str(address).split()).lower()
    if key in GEOCODE_CACHE:
        return GEOCODE_CACHE[key]
    wait = NOMINATIM_MIN_INTERVAL_SECONDS - (time.monotonic() - GEOCODE_LAST_CALL_AT)
    if wait > 0:
        time.sleep(wait)
    GEOCODE_LAST_CALL_AT = time.monotonic()
    url = NOMINATIM_SEARCH_URL % quote(key)
    request = urllib.request.Request(
        url, headers={"User-Agent": GEOCODER_USER_AGENT, "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=GEOCODING_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, list) or not payload:
        return None
    try:
        lat = float(payload[0]["lat"])
        lng = float(payload[0]["lon"])
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    if len(GEOCODE_CACHE) >= GEOCODE_CACHE_LIMIT:
        GEOCODE_CACHE.clear()
    GEOCODE_CACHE[key] = (lat, lng)
    return lat, lng


def pin_for(record):
    """The (lat, lng) a record can be placed at, resolving a written address if needed.

    Coordinates come first because they are exact: a "location" pair, like the
    prototype's, or the delivery_lat/delivery_lng pair the dashboard map writes. A record
    carrying only an address — what the website's onboarding collects — is geocoded
    instead, so an address typed on the site feeds the same measurements a confirmed pin
    does. Raises ValueError when no place can be found for the record.
    """
    if record.get("location") is not None:
        return location_of(record)
    lat, lng = record.get("delivery_lat"), record.get("delivery_lng")
    if lat is not None and lng is not None:
        return float(lat), float(lng)
    address = address_text(record)
    if address:
        pin = geocode_address(address)
        if pin is not None:
            return pin
        raise ValueError("could not place this address on the map: " + address)
    raise ValueError("a record needs a location, a delivery pin or an address")


def road_distance_km(lat1, lng1, lat2, lng2):
    """Driving distance between two pins, in kilometres, from the OSRM router.

    haversine_km measures through the earth; a delivery drives, so the hard filter
    measures along the road instead — the call the root 'import requests.py' experiment
    made, written with urllib so the backend still asks for nothing outside the standard
    library. Returns None when the router cannot answer, leaving the caller to decide
    what a missing road distance means.
    """
    key = (round(lat1, 5), round(lng1, 5), round(lat2, 5), round(lng2, 5))
    if key in ROAD_DISTANCE_CACHE:
        return ROAD_DISTANCE_CACHE[key]
    # OSRM wants longitude before latitude, and the two pins joined by a semicolon.
    url = OSRM_ROUTE_URL % (key[1], key[0], key[3], key[2])
    try:
        with urllib.request.urlopen(url, timeout=OSRM_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("code") != "Ok":
        return None
    try:
        metres = float(payload["routes"][0]["distance"])
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    distance = round(metres / 1000.0, METRIC_DECIMALS)
    if len(ROAD_DISTANCE_CACHE) >= ROAD_DISTANCE_CACHE_LIMIT:
        ROAD_DISTANCE_CACHE.clear()
    ROAD_DISTANCE_CACHE[key] = distance
    return distance


def pair_distance_km(surplus, shelter, fallback_to_straight_line=True):
    """How far one surplus item sits from one shelter, measured along the road.

    Road distance is the measurement; when the router cannot answer and the fallback is
    allowed the great-circle distance stands in, and None comes back only when the road
    distance was required and could not be had. Either record is placed by pin_for, so an
    address is geocoded exactly when its coordinates are missing.
    """
    lat1, lng1 = pin_for(surplus)
    lat2, lng2 = pin_for(shelter)
    distance = road_distance_km(lat1, lng1, lat2, lng2)
    if distance is None:
        if not fallback_to_straight_line:
            return None
        distance = haversine_km(lat1, lng1, lat2, lng2)
    return distance


def hard_filter(surplus, shelter, max_distance_km=HARD_FILTER_RADIUS_KM,
                fallback_to_straight_line=True):
    """Whether a surplus item may go to a shelter at all, before any ranking.

    The two gates the prototype applied, in the same order: the shelter has to accept
    this kind of item ("item_type" against its "accepted_types"), and the business and
    the shelter have to be within max_distance_km of each other along the road rather
    than through the earth. Either side is placed by pin_for, so a record carrying only
    the address typed on the website measures exactly like one carrying a pin.

    The road measurement is an outbound call, so when the router cannot answer and
    fallback_to_straight_line is on (the default) the great-circle distance stands in,
    letting an offline run still filter instead of dropping every candidate. Pass
    fallback_to_straight_line=False to insist on road distance only.
    """
    if surplus.get("item_type") not in (shelter.get("accepted_types") or ()):
        return False
    distance = pair_distance_km(surplus, shelter, fallback_to_straight_line)
    if distance is None:
        return False
    return distance <= max_distance_km


def match_score(surplus, shelter, distance_km):
    """The weighted score one valid pair gets: the prototype's formula, kept whole.

    distance_score * 0.3 + need_score * 0.4 + urgency_multiplier * 3.0, where the
    distance score counts down from the filter radius, the need score is the shelter's
    own 1-to-10, and the urgency multiplier is 1 / (hours to expiry + 0.1) — goods nearly
    out of time outrank a comfortable run by design. Raises ValueError when the pair
    carries no need score or no expiry time to work with.
    """
    try:
        need_score = float(shelter["current_need_score"])
        expiry_hours = float(surplus["expiry_hours"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("a scored pair needs a need score and an expiry time")
    distance_score = max(0.0, DISTANCE_SCORE_CEILING_KM - distance_km)
    urgency_multiplier = 1.0 / (expiry_hours + URGENCY_SMOOTHING_HOURS)
    return round(
        distance_score * DISTANCE_SCORE_WEIGHT
        + need_score * NEED_SCORE_WEIGHT
        + urgency_multiplier * URGENCY_WEIGHT,
        SCORE_DECIMALS,
    )


def allocate_surplus(surplus_items, shelters, max_distance_km=HARD_FILTER_RADIUS_KM,
                     fallback_to_straight_line=True):
    """The prototype's greedy plan: score every valid pair, then hand out the best first.

    Every pair that passes hard_filter is scored by match_score and sorted from the
    highest score down, ties settled by the shorter drive. Walking that list once is the
    whole allocation: the first time an item or a shelter appears it is taken, so no item
    is promised twice and no shelter is asked to take two deliveries. A pair that cannot
    be worked with — an address with no place on the map, goods without an expiry time —
    is left out rather than stopping the rest of the plan.
    """
    candidates = []
    for surplus in surplus_items:
        for shelter in shelters:
            try:
                if not hard_filter(surplus, shelter, max_distance_km,
                                   fallback_to_straight_line):
                    continue
                distance = pair_distance_km(surplus, shelter, fallback_to_straight_line)
                if distance is None:
                    continue
                score = match_score(surplus, shelter, distance)
            except ValueError:
                continue
            candidates.append({
                "item_id": surplus.get("item_id"),
                "business": surplus.get("business_name"),
                "shelter_id": shelter.get("shelter_id"),
                "shelter_name": shelter.get("name"),
                "distance_km": round(distance, METRIC_DECIMALS),
                "match_score": score,
            })

    candidates.sort(key=lambda candidate: (
        -candidate["match_score"],
        candidate["distance_km"],
        str(candidate["item_id"]),
        str(candidate["shelter_id"]),
    ))

    taken_items = set()
    taken_shelters = set()
    plan = []
    for candidate in candidates:
        if candidate["item_id"] in taken_items or candidate["shelter_id"] in taken_shelters:
            continue
        plan.append(candidate)
        taken_items.add(candidate["item_id"])
        taken_shelters.add(candidate["shelter_id"])
    return plan


def classify_surplus(item, quantity=""):
    """The profile category a logged item belongs to, read from the words in it.

    The item name is read first and the quantity only when the name said nothing, and
    every keyword has to start a word, so "tomato" catches "tomatoes" without "oil"
    firing on "boiled". Returns None when nothing points at a category.
    """
    for text in (item, quantity):
        haystack = " ".join(str(text or "").lower().split())
        if not haystack:
            continue
        for category, keywords in SURPLUS_KEYWORDS:
            if any(re.search(r"\b" + re.escape(word), haystack) for word in keywords):
                return category
    return None


def shelter_record(user, need_score=DEFAULT_SHELTER_NEED_SCORE):
    """A registered account read as a shelter the allocator can score.

    What a recipient needs ("surplus_types") is what a shelter accepts, and the shelter's
    name is the recipient's business name where it has one. The prototype ranked shelters
    on a 1-to-10 need score nothing collects yet, so every shelter carries the documented
    middle of that scale until a caller has a better number to pass in.
    """
    shelter = {
        "shelter_id": user.get("id"),
        "name": user.get("business_name") or display_name(user),
        "accepted_types": decode_list(user.get("surplus_types")),
        "current_need_score": need_score,
    }
    for field in PIN_FIELDS + ADDRESS_PART_FIELDS:
        if user.get(field) is not None:
            shelter[field] = user[field]
    return shelter


def bounding_box(lat, lng, radius_km):
    """A coarse box around a point, so the database discards the far away first.

    Every candidate still gets measured properly; the box only keeps the rows the query
    has to look at to those that could possibly be inside the radius. A degree of
    longitude shrinks towards the poles, hence the cosine.
    """
    lat_delta = radius_km / KM_PER_DEGREE
    width = KM_PER_DEGREE * max(math.cos(math.radians(lat)), 0.01)
    lng_delta = min(radius_km / width, 180.0)
    return lat - lat_delta, lat + lat_delta, lng - lng_delta, lng + lng_delta


def offset_point(lat, lng, distance_km, bearing_deg):
    """A point at a bearing, distance_km from another: flat enough for demo data."""
    angle = math.radians(bearing_deg)
    width = KM_PER_DEGREE * max(math.cos(math.radians(lat)), 0.01)
    north = distance_km * math.cos(angle) / KM_PER_DEGREE
    east = distance_km * math.sin(angle) / width
    return lat + north, lng + east


def shared_categories(mine, theirs):
    """The categories both sides have, in the caller's own order, without repeats."""
    return [name for name in mine if name in theirs]


def routing_problem(user):
    """Why this account cannot be matched or ordered with yet, as a 400 body, or None.

    Matching measures between two confirmed pins. The address typed at onboarding is not
    one — it was never turned into coordinates — so the dashboard map is the only place a
    location with a point behind it comes from.
    """
    if user.get("delivery_lat") is None or user.get("delivery_lng") is None:
        return {
            "error": "Confirm your delivery location before matching: a match is a"
                     " distance between two pins.",
            "field": "delivery_lat",
            "code": "no_location",
        }
    if not decode_list(user.get("surplus_types")):
        return {
            "error": "Save your goods profile first: matching pairs the categories you"
                     " have or need.",
            "field": "surplus_types",
            "code": "no_categories",
        }
    return None


def demo_peer_users(caller, lat, lng):
    """The fabricated counterparts for this caller, keyed by id, other role only.

    Each peer is placed relative to the caller's own confirmed pin, so the ranks and
    distances move with the pin instead of being tied to one city, and the same peer id
    resolves to the same place every time it is asked for.
    """
    peers = {}
    for peer in DEMO_PEERS:
        if peer["role"] == caller.get("role"):
            continue
        peer_lat, peer_lng = offset_point(lat, lng, peer["distance_km"], peer["bearing"])
        first, _, last = peer["contact"].partition(" ")
        place = [part for part in
                 (peer["street"], caller.get("city"), caller.get("province")) if part]
        peers[peer["id"]] = {
            "id": peer["id"],
            "role": peer["role"],
            "first_name": first,
            "last_name": last,
            "business_name": peer["business_name"],
            "email": None,
            "phone": None,
            "dial_code": None,
            "street": peer["street"],
            "sub_locality": None,
            "locality": None,
            "province": caller.get("province"),
            "city": caller.get("city"),
            "postal": None,
            "firm_type": peer.get("firm_type"),
            "delivery_days": list(peer["delivery_days"]),
            "surplus_types": list(peer["surplus_types"]),
            "delivery_address": ", ".join(place) or None,
            "delivery_lat": peer_lat,
            "delivery_lng": peer_lng,
            "delivery_confirmed_at": None,
            "demo": True,
        }
    return peers


def users_by_ids(conn, ids):
    """Load several accounts in one query, keyed by id."""
    unique = [user_id for user_id in dict.fromkeys(ids) if user_id is not None]
    if not unique:
        return {}
    rows = conn.execute(
        "SELECT * FROM users WHERE id IN (%s)" % ", ".join("?" * len(unique)), unique
    ).fetchall()
    return {row["id"]: dict(row) for row in rows}


def order_row(order_id, donor, recipient, category, scheduled_for, distance_km, created_at):
    """One order in the shape it is stored in, so both modes hand back the same thing."""
    return {
        "id": order_id,
        "donor_id": donor["id"],
        "recipient_id": recipient["id"],
        "category": category,
        "status": "proposed",
        "scheduled_for": scheduled_for,
        "pickup_address": donor.get("delivery_address"),
        "pickup_lat": donor.get("delivery_lat"),
        "pickup_lng": donor.get("delivery_lng"),
        "dropoff_address": recipient.get("delivery_address"),
        "dropoff_lat": recipient.get("delivery_lat"),
        "dropoff_lng": recipient.get("delivery_lng"),
        "distance_km": distance_km,
        "created_at": created_at,
    }


class ResQHandler(SimpleHTTPRequestHandler):
    server_version = "ResQ/1.0"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=FRONTEND_DIR, **kwargs)

    # ------------------------------------------------------------------ helpers
    def send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        # A header that is not a number cannot say how long the body is, and a negative
        # one makes the read below run to end-of-stream; neither should be able to kill
        # the request thread part-way through.
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            return None
        if length <= 0:
            return {}
        if length > MAX_REQUEST_BODY_BYTES:
            return None
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        # Every endpoint here reads an object; a body of any other shape is malformed, not
        # something to trip over halfway through a handler.
        return data if isinstance(data, dict) else None

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
        user = {field: row[field] for field in PUBLIC_FIELDS
                if field in keys and field not in DONOR_METRIC_FIELDS}
        for field in LIST_FIELDS:
            if field in user:
                user[field] = decode_list(user[field])
        # The impact figures ride along only on a profile that has them — a donor's do from
        # the moment it is created, a recipient's never do — and in their own shapes: a
        # weight with its two decimals, whole counts for the other two. A recipient's
        # answer therefore carries no sign of them at all rather than a row of nulls.
        for field in DONOR_METRIC_FIELDS:
            if field not in keys or row[field] is None:
                continue
            user[field] = (round(float(row[field]), METRIC_DECIMALS)
                           if field == "surplus_saved" else int(row[field]))
        return user

    def donor_user_with_metrics(self, row):
        """A donor's profile as the dashboard sees it, with its figures filled in.

        The figures are written once, the first time a profile that predates them is
        read, so an account from before this change shows its own numbers straight away
        instead of blanks. A profile that already has them is returned untouched.
        """
        user = self.public_user(row)
        if fill_donor_metrics(user):
            with connect() as conn:
                conn.execute(
                    "UPDATE users SET surplus_saved = ?, meals_served = ?,"
                    " orders_completed = ? WHERE id = ?",
                    (
                        user["surplus_saved"],
                        user["meals_served"],
                        user["orders_completed"],
                        user["id"],
                    ),
                )
                conn.commit()
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
        if path == "/api/matches":
            return self.api_matches()
        if path == "/api/orders":
            return self.api_orders()
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
        if path == "/api/delivery-location":
            return self.api_delivery_location()
        if path == "/api/submit-surplus":
            return self.api_submit_surplus()
        if path == "/api/orders":
            return self.api_order_create()
        if path == "/api/dev/switch-role":
            return self.api_switch_role()
        return self.send_json(404, {"error": "Unknown endpoint."})

    # ------------------------------------------------------------------ handlers
    def api_register(self):
        data = self.read_json()
        if data is None:
            return self.send_json(400, {"error": "Malformed request body."})

        role = text_answer(data, "role").lower() or "donor"
        if role not in ("donor", "recipient"):
            role = "donor"

        first_name = text_answer(data, "firstName")
        last_name = text_answer(data, "lastName")
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
        value = text_answer(contact, "value")
        dial_code = text_answer(contact, "dial") or None
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

        password = text_answer(data, "password")
        problem = password_problem(password)
        if problem:
            return self.send_json(400, {"error": problem, "field": "password"})

        address = data.get("address")
        if not isinstance(address, dict):
            address = {}

        # The impact figures are the donor's own from the moment the profile exists, so a
        # donor starts with a plausible set of them; a recipient's stay empty, because
        # the figures are not theirs.
        metrics = (seed_donor_metrics() if role == "donor"
                   else {field: None for field in DONOR_METRIC_FIELDS})

        # Demo mode: take the answers, keep them in memory for this run, store nothing.
        if not PERSIST:
            user = {
                "id": 0,
                "role": role,
                "first_name": first_name,
                "last_name": last_name,
                "business_name": text_answer(data, "businessName") or None,
                "email": email,
                "phone": phone,
                "dial_code": dial_code,
                # The address rides along as well, so demo mode answers /api/me with the
                # same shape persistent mode does; the dashboard map reads it to open on
                # the address the account onboarded with.
                "street": text_answer(address, "street") or None,
                "sub_locality": text_answer(address, "subLocality") or None,
                "locality": text_answer(address, "locality") or None,
                "province": text_answer(address, "province") or None,
                "city": text_answer(address, "city") or None,
                "postal": text_answer(address, "postal") or None,
                "firm_type": None,
                "delivery_days": [],
                "surplus_types": [],
                "delivery_address": None,
                "delivery_lat": None,
                "delivery_lng": None,
                "delivery_confirmed_at": None,
                "demo": True,
            }
            # A recipient's profile carries no impact figures at all, so the keys are not
            # invented for one; a donor gets all three here and now.
            for field in DONOR_METRIC_FIELDS:
                if metrics[field] is not None:
                    user[field] = metrics[field]
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
                       password_hash, password_salt,
                       surplus_saved, meals_served, orders_completed
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    role,
                    first_name,
                    last_name,
                    text_answer(data, "businessName") or None,
                    email,
                    phone,
                    dial_code,
                    text_answer(address, "street") or None,
                    text_answer(address, "subLocality") or None,
                    text_answer(address, "locality") or None,
                    text_answer(address, "province") or None,
                    text_answer(address, "city") or None,
                    text_answer(address, "postal") or None,
                    digest,
                    salt,
                    metrics["surplus_saved"],
                    metrics["meals_served"],
                    metrics["orders_completed"],
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

        contact = text_answer(data, "contact")
        password = text_answer(data, "password")
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
            # Signing in always lands on a donor profile that has its figures: the demo
            # holds nothing else to read them from, so they are given here.
            fill_donor_metrics(user)
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
            user = self.donor_user_with_metrics(row)

        return self.send_json(200, {"ok": True, "token": token, "user": user})

    def api_me(self):
        token = self.bearer_token()
        if not PERSIST:
            user = DEMO_SESSIONS.get(token)
            if user is None:
                return self.send_json(
                    401, {"error": "Your session has expired. Please sign in again."}
                )
            # The session's profile is the object the dashboard reads, so a donor whose
            # figures are missing gets them here too and keeps them for the session's life.
            fill_donor_metrics(user)
            return self.send_json(200, {"ok": True, "demo": True, "user": user})
        with connect() as conn:
            row = self.user_for_token(conn, token)
        if row is None:
            return self.send_json(
                401, {"error": "Your session has expired. Please sign in again."}
            )
        return self.send_json(200, {"ok": True, "user": self.donor_user_with_metrics(row)})

    def api_logout(self):
        # The token may arrive in the body instead of the header, so it is read the way
        # every other text field is: anything that is not a string counts as absent,
        # rather than being bound into the delete below.
        token = self.bearer_token() or text_answer(self.read_json() or {}, "token")
        if token:
            if not PERSIST:
                DEMO_SESSIONS.pop(token, None)
                return self.send_json(200, {"ok": True, "demo": True})
            with connect() as conn:
                conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
                conn.commit()
        return self.send_json(200, {"ok": True})

    # ------------------------------------------------------------------ matching
    def api_matches(self):
        """Rank the counterparts near the signed-in account, nearest first.

        A counterpart is a candidate only when the two sides share at least one category
        — what the donor has spare against what the recipient needs — and both have a
        confirmed pin, because a match without two points on the map is not a delivery
        anyone can run. Everything else is ranking: the nearest first, since distance is
        what a delivery costs.
        """
        query = parse_qs(urlparse(self.path).query)
        radius = query_number(query, "radius_km", DEFAULT_RADIUS_KM, 1.0, MAX_RADIUS_KM)
        if radius is None:
            return self.send_json(
                400,
                {"error": "radius_km has to be a number of kilometres.", "field": "radius_km"},
            )
        limit = query_number(query, "limit", DEFAULT_MATCH_LIMIT, 1, MAX_MATCH_LIMIT)
        if limit is None:
            return self.send_json(
                400,
                {"error": "limit has to be a whole number of matches.", "field": "limit"},
            )
        limit = int(limit)

        token = self.bearer_token()
        if not PERSIST:
            session_user = DEMO_SESSIONS.get(token)
            if session_user is None:
                return self.send_json(
                    401,
                    {
                        "error": "Your session has expired. Please sign in again.",
                        "code": "no_session",
                    },
                )
            me = dict(session_user)
        else:
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
            me = dict(row)

        problem = routing_problem(me)
        if problem:
            return self.send_json(400, problem)

        lat = float(me["delivery_lat"])
        lng = float(me["delivery_lng"])
        mine = decode_list(me.get("surplus_types"))
        other_role = "recipient" if me.get("role") == "donor" else "donor"

        if not PERSIST:
            candidates = list(demo_peer_users(me, lat, lng).values())
        else:
            # The box is only a pre-filter; the radius is settled by measuring.
            min_lat, max_lat, min_lng, max_lng = bounding_box(lat, lng, radius)
            with connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM users WHERE role = ? AND id != ?"
                    " AND delivery_lat IS NOT NULL AND delivery_lng IS NOT NULL"
                    " AND delivery_lat BETWEEN ? AND ? AND delivery_lng BETWEEN ? AND ?",
                    (other_role, me["id"], min_lat, max_lat, min_lng, max_lng),
                ).fetchall()
            candidates = [dict(row) for row in rows]

        matches = []
        for candidate in candidates:
            shared = shared_categories(mine, decode_list(candidate.get("surplus_types")))
            if not shared:
                continue
            distance = haversine_km(
                lat, lng, float(candidate["delivery_lat"]), float(candidate["delivery_lng"])
            )
            if distance > radius:
                continue
            matches.append(self.match_payload(candidate, shared, distance))

        matches.sort(key=lambda match: (match["distance_km"], match["user_id"]))
        payload = {
            "ok": True,
            "role": me.get("role"),
            "radius_km": radius,
            "count": len(matches[:limit]),
            "matches": matches[:limit],
        }
        if not PERSIST:
            payload["demo"] = True
        return self.send_json(200, payload)

    def match_payload(self, user, shared, distance_km):
        """One candidate counterpart as a dashboard reads it."""
        entry = {
            "user_id": user["id"],
            "role": user.get("role"),
            "name": display_name(user),
            "business_name": user.get("business_name"),
            "city": user.get("city"),
            "province": user.get("province"),
            "distance_km": round(distance_km, METRIC_DECIMALS),
            "shared_categories": shared,
            "delivery_days": decode_list(user.get("delivery_days")),
            "delivery_confirmed_at": user.get("delivery_confirmed_at"),
        }
        if user.get("demo"):
            entry["demo"] = True
        return entry

    # -------------------------------------------------------------------- orders
    # ------------------------------------------------------- surplus submission
    def api_submit_surplus(self):
        """Take one logged surplus item and answer with the shelter that should get it.

        The Log surplus form sends what the donor typed, and the account behind the token
        supplies the pickup point unless the form typed a different one. Every registered
        recipient counts as a shelter — what a recipient needs ("surplus_types") is what a
        shelter accepts — and the ones that can be placed on the map go through the same
        filter, score and greedy allocation the rest of the pipeline uses. The best pair
        comes back with its shelter name and score, or a plain reason nothing matched.
        """
        data = self.read_json()
        if data is None:
            return self.send_json(400, {"error": "Malformed request body."})

        token = self.bearer_token()
        if not PERSIST:
            session_user = DEMO_SESSIONS.get(token)
            if session_user is None:
                return self.send_json(
                    401,
                    {
                        "error": "Your session has expired. Please sign in again.",
                        "code": "no_session",
                    },
                )
            me = dict(session_user)
        else:
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
            me = dict(row)

        item = text_answer(data, "item")[:MAX_ITEM_CHARS]
        if not item:
            return self.send_json(
                400,
                {"error": "Name the item being logged so it can be matched.", "field": "item"},
            )
        quantity = text_answer(data, "quantity")[:MAX_QUANTITY_CHARS]

        # The category decides which shelters can take the item at all. The form collects
        # none, so it is read from the words typed; a caller that knows better can send
        # "category" and it is used as given.
        category = text_answer(data, "category").lower()
        if category and category not in DONOR_SURPLUS:
            return self.send_json(
                400,
                {
                    "error": "That is not a surplus category we match on.",
                    "field": "category",
                    "code": "bad_category",
                },
            )
        if not category:
            category = classify_surplus(item, quantity)
            if not category:
                profile_types = decode_list(me.get("surplus_types"))
                category = profile_types[0] if profile_types else None
        if not category:
            uncategorised = {
                "ok": True,
                "match": None,
                "item": item,
                "reason": "Could not tell what kind of surplus this is. Name the item"
                          " more plainly, or set the goods profile on your account.",
            }
            if not PERSIST:
                uncategorised["demo"] = True
            return self.send_json(200, uncategorised)

        expiry_hours = data.get("expiryHours")
        if expiry_hours in (None, ""):
            expiry_hours = DEFAULT_SURPLUS_EXPIRY_HOURS
        else:
            try:
                expiry_hours = float(expiry_hours)
            except (TypeError, ValueError):
                expiry_hours = None
            if expiry_hours is None or expiry_hours <= 0:
                return self.send_json(
                    400,
                    {
                        "error": "expiryHours has to be a number of hours above zero.",
                        "field": "expiryHours",
                    },
                )

        typed = text_answer(data, "location")[:MAX_ADDRESS_CHARS]
        surplus = {
            "item_id": me.get("id"),
            "business_name": me.get("business_name"),
            "item_type": category,
            "expiry_hours": expiry_hours,
        }
        # A pickup point typed into the form wins over the account's own saved address,
        # because that is where the goods actually are; the account's confirmed pin is
        # used otherwise, and it needs no geocoding at all.
        if typed and typed != (me.get("delivery_address") or "").strip():
            surplus["address"] = typed
        else:
            for field in PIN_FIELDS + ADDRESS_PART_FIELDS:
                if me.get(field) is not None:
                    surplus[field] = me[field]
        try:
            lat, lng = pin_for(surplus)
        except ValueError:
            return self.send_json(
                400,
                {
                    "error": "That pickup point is not a place the map knows. Confirm your"
                             " delivery location on the dashboard, or type an address it can"
                             " find.",
                    "field": "location",
                    "code": "no_pickup",
                },
            )

        if PERSIST:
            with connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM users WHERE role = 'recipient' AND id != ?",
                    (me.get("id"),),
                ).fetchall()
            shelters = [shelter_record(dict(row)) for row in rows]
        else:
            # The fabricated counterparts are the other role's, and a shelter is always a
            # recipient, so they are read as a donor's peers wherever the item came from.
            peers = demo_peer_users(
                {"role": "donor", "city": me.get("city"), "province": me.get("province")},
                lat, lng,
            )
            shelters = [shelter_record(peer) for peer in peers.values()]

        placed = []
        unplaced = 0
        for shelter in shelters:
            try:
                pin_for(shelter)
            except ValueError:
                unplaced += 1
                continue
            placed.append(shelter)
        takers = [shelter for shelter in placed
                  if category in (shelter.get("accepted_types") or ())]
        plan = allocate_surplus([surplus], placed) if takers else []

        payload = {
            "ok": True,
            "item": item,
            "category": category,
            "expiry_hours": expiry_hours,
            "pickup": {"lat": round(lat, METRIC_DECIMALS), "lng": round(lng, METRIC_DECIMALS)},
            "shelters_considered": len(placed),
            "shelters_unplaced": unplaced,
            "match": None,
        }
        if not PERSIST:
            # Every other demo answer says so, and the shelters just scored against are
            # the fabricated ones, so this answer is marked the same way.
            payload["demo"] = True
        if plan:
            best = plan[0]
            payload["match"] = {
                "shelter_id": best["shelter_id"],
                "shelter_name": best["shelter_name"],
                "match_score": best["match_score"],
                "distance_km": best["distance_km"],
            }
        elif not shelters:
            payload["reason"] = "No registered shelter is on the network yet."
        elif not takers:
            payload["reason"] = ("No registered shelter is asking for "
                                 + category.replace("_", " ") + " right now.")
        else:
            payload["reason"] = ("No shelter that needs this item is within "
                                 + str(HARD_FILTER_RADIUS_KM) + " km of the pickup point.")
        return self.send_json(200, payload)

    def api_orders(self):
        """Every delivery order the signed-in account is part of, newest first."""
        token = self.bearer_token()
        if not PERSIST:
            if token not in DEMO_SESSIONS:
                return self.send_json(
                    401,
                    {
                        "error": "Your session has expired. Please sign in again.",
                        "code": "no_session",
                    },
                )
            orders = list(reversed(DEMO_ORDERS.get(token, [])))
            return self.send_json(
                200, {"ok": True, "demo": True, "count": len(orders), "orders": orders}
            )

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
            me = dict(row)
            rows = conn.execute(
                "SELECT * FROM orders WHERE donor_id = ? OR recipient_id = ?"
                " ORDER BY id DESC",
                (me["id"], me["id"]),
            ).fetchall()
            account_ids = ([order["donor_id"] for order in rows]
                           + [order["recipient_id"] for order in rows])
            people = users_by_ids(conn, account_ids)
            orders = [
                self.order_payload(dict(order), people.get(order["donor_id"]),
                                   people.get(order["recipient_id"]))
                for order in rows
            ]

        return self.send_json(200, {"ok": True, "count": len(orders), "orders": orders})

    def api_order_create(self):
        """Bind the signed-in account and one counterpart into a delivery order.

        The order snapshots both pins and the address each side confirmed, because what
        routing reads is the donor's pickup against the recipient's drop-off, and keeping
        the addresses beside the coordinates leaves the pair readable later. Only the
        caller's own account can be one side of it; the other side is named, never
        written to.
        """
        data = self.read_json()
        if data is None:
            return self.send_json(400, {"error": "Malformed request body."})

        token = self.bearer_token()
        counterpart_id = clean_id(data.get("counterpartId"))
        if counterpart_id is None:
            return self.send_json(
                400,
                {
                    "error": "Send counterpartId: the account on the other side of"
                             " the delivery.",
                    "field": "counterpartId",
                },
            )

        if not PERSIST:
            session_user = DEMO_SESSIONS.get(token)
            if session_user is None:
                return self.send_json(
                    401,
                    {
                        "error": "Your session has expired. Please sign in again.",
                        "code": "no_session",
                    },
                )
            me = dict(session_user)
        else:
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
            me = dict(row)

        problem = routing_problem(me)
        if problem:
            return self.send_json(400, problem)

        # The counterpart is named by the request, so it is looked up the same way the
        # caller was: in the directory when there is one, among the demo peers otherwise.
        if not PERSIST:
            peers = demo_peer_users(me, float(me["delivery_lat"]), float(me["delivery_lng"]))
            peer = peers.get(counterpart_id)
        else:
            with connect() as conn:
                found = conn.execute(
                    "SELECT * FROM users WHERE id = ?", (counterpart_id,)
                ).fetchone()
            peer = dict(found) if found else None

        if peer is None:
            return self.send_json(
                404,
                {
                    "error": "No account matches counterpartId.",
                    "field": "counterpartId",
                    "code": "no_counterpart",
                },
            )
        if peer.get("role") == me.get("role"):
            return self.send_json(
                400,
                {
                    "error": "A delivery runs between a donor and a recipient, not two"
                             " of the same.",
                    "field": "counterpartId",
                    "code": "role_mismatch",
                },
            )
        if peer.get("delivery_lat") is None or peer.get("delivery_lng") is None:
            return self.send_json(
                400,
                {
                    "error": display_name(peer) + " has not confirmed a delivery location"
                             " yet, so there is no second pin to deliver between.",
                    "field": "counterpartId",
                    "code": "no_counterpart_location",
                },
            )

        category = text_answer(data, "category").lower()
        if not category:
            return self.send_json(
                400,
                {"error": "Name the category the delivery carries.", "field": "category"},
            )
        if (category not in decode_list(me.get("surplus_types"))
                or category not in decode_list(peer.get("surplus_types"))):
            return self.send_json(
                400,
                {
                    "error": "The two sides do not have that category in common.",
                    "field": "category",
                    "code": "not_shared",
                },
            )

        if me.get("role") == "donor":
            donor, recipient = me, peer
        else:
            donor, recipient = peer, me

        # A scheduled day is the recipient's to prefer, so a day they did not ask for is
        # refused rather than quietly booked.
        scheduled_for = text_answer(data, "scheduledFor").lower() or None
        if scheduled_for is not None:
            if scheduled_for not in DELIVERY_DAYS:
                return self.send_json(
                    400,
                    {
                        "error": "Schedule the delivery on Saturday through Friday.",
                        "field": "scheduledFor",
                        "code": "bad_day",
                    },
                )
            preferred = decode_list(recipient.get("delivery_days"))
            if preferred and scheduled_for not in preferred:
                return self.send_json(
                    400,
                    {
                        "error": display_name(recipient) + " asked for deliveries on "
                                 + ", ".join(preferred) + ".",
                        "field": "scheduledFor",
                        "code": "not_preferred",
                    },
                )

        distance = round(haversine_km(
            float(donor["delivery_lat"]), float(donor["delivery_lng"]),
            float(recipient["delivery_lat"]), float(recipient["delivery_lng"]),
        ), METRIC_DECIMALS)

        # One live order per pair and category: proposing the same delivery twice would
        # put the same goods on the road twice.
        if PERSIST:
            with connect() as conn:
                open_row = conn.execute(
                    "SELECT id FROM orders WHERE donor_id = ? AND recipient_id = ?"
                    " AND category = ? AND status IN (%s)"
                    % ", ".join("?" * len(LIVE_ORDER_STATUSES)),
                    (donor["id"], recipient["id"], category) + LIVE_ORDER_STATUSES,
                ).fetchone()
            open_order = open_row["id"] if open_row else None
        else:
            open_order = next(
                (order["id"] for order in DEMO_ORDERS.get(token, [])
                 if order["category"] == category
                 and order["status"] in LIVE_ORDER_STATUSES
                 and {order["donor"]["user_id"], order["recipient"]["user_id"]}
                 == {me["id"], peer["id"]}),
                None,
            )
        if open_order is not None:
            return self.send_json(
                409,
                {
                    "error": "That delivery is already on the books: order "
                             + str(open_order) + " covers the same pair and category.",
                    "code": "duplicate_order",
                },
            )

        if not PERSIST:
            stored = order_row(
                next(DEMO_ORDER_SEQ), donor, recipient, category, scheduled_for, distance,
                datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            )
            order = self.order_payload(stored, donor, recipient)
            DEMO_ORDERS.setdefault(token, []).append(order)
            return self.send_json(201, {"ok": True, "demo": True, "order": order})

        with connect() as conn:
            cursor = conn.execute(
                """INSERT INTO orders (
                       donor_id, recipient_id, category, scheduled_for,
                       pickup_address, pickup_lat, pickup_lng,
                       dropoff_address, dropoff_lat, dropoff_lng, distance_km
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    donor["id"],
                    recipient["id"],
                    category,
                    scheduled_for,
                    donor.get("delivery_address"),
                    donor["delivery_lat"],
                    donor["delivery_lng"],
                    recipient.get("delivery_address"),
                    recipient["delivery_lat"],
                    recipient["delivery_lng"],
                    distance,
                ),
            )
            order_id = cursor.lastrowid
            conn.commit()
            stored = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        return self.send_json(
            201, {"ok": True, "order": self.order_payload(dict(stored), donor, recipient)}
        )

    def order_payload(self, order, donor, recipient):
        """One order as a dashboard reads it: who is on each side, and where."""
        distance = order.get("distance_km")
        return {
            "id": order["id"],
            "status": order["status"],
            "category": order["category"],
            "scheduled_for": order["scheduled_for"],
            "distance_km": (round(float(distance), METRIC_DECIMALS)
                            if distance is not None else None),
            "created_at": order["created_at"],
            "donor": {
                "user_id": order["donor_id"],
                "name": display_name(donor),
                "address": order["pickup_address"],
                "lat": order["pickup_lat"],
                "lng": order["pickup_lng"],
            },
            "recipient": {
                "user_id": order["recipient_id"],
                "name": display_name(recipient),
                "address": order["dropoff_address"],
                "lat": order["dropoff_lat"],
                "lng": order["dropoff_lng"],
            },
        }

    # ------------------------------------------------------------------ dev helper
    def api_switch_role(self):
        """TEMPORARY, demo mode only: hand back a session for the other dashboard.

        This exists so the two dashboards can be previewed without signing out and back
        in, which is exactly why it mints a session without a password. With persistence
        on it answers 404 like any unknown endpoint, so it can never be pointed at a real
        account, and the button that calls it is marked as temporary in both pages.
        """
        if PERSIST:
            return self.send_json(404, {"error": "Unknown endpoint."})
        current = DEMO_SESSIONS.get(self.bearer_token())
        if current is None:
            return self.send_json(
                401, {"error": "Your session has expired. Please sign in again."}
            )
        data = self.read_json()
        if data is None:
            return self.send_json(400, {"error": "Malformed request body."})
        role = text_answer(data, "role").lower()
        if role not in ("donor", "recipient"):
            return self.send_json(
                400,
                {"error": "Ask for the donor or the recipient dashboard.", "field": "role"},
            )
        # The switched session is the same person wearing the other role, so the two
        # dashboards can be compared side by side. The answers the role it is leaving owns
        # are dropped, and a recipient never carries a donor's figures.
        switched = dict(current)
        switched["role"] = role
        switched["firm_type"] = None
        switched["delivery_days"] = []
        switched["surplus_types"] = []
        for field in DONOR_METRIC_FIELDS:
            switched.pop(field, None)
        fill_donor_metrics(switched)
        token = "demo-" + secrets.token_urlsafe(12)
        DEMO_SESSIONS[token] = switched
        return self.send_json(
            200, {"ok": True, "demo": True, "token": token, "user": switched}
        )

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
        firm_type = text_answer(data, "firmType").lower()
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

    def api_delivery_location(self):
        """Save the location confirmed on the dashboard map.

        The coordinates are the point routing will use; the address is what the map
        showed at that point when it was confirmed. Only the signed-in account can set
        its own location, and there is no way to set somebody else's.
        """
        data = self.read_json()
        if data is None:
            return self.send_json(400, {"error": "Malformed request body."})

        token = self.bearer_token()
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

        address = text_answer(data, "address")
        if not address:
            return self.send_json(
                400,
                {
                    "error": "There is no address to save yet: place the pin first.",
                    "field": "address",
                },
            )
        if len(address) > MAX_ADDRESS_CHARS:
            address = address[:MAX_ADDRESS_CHARS]

        # A location without usable coordinates is nothing routing can use, so it is
        # rejected rather than stored as a half-answer.
        try:
            lat = float(data.get("lat"))
            lng = float(data.get("lng"))
        except (TypeError, ValueError):
            return self.send_json(
                400,
                {"error": "That location came through without usable coordinates.", "field": "lat"},
            )
        if lat != lat or lng != lng or not -90 <= lat <= 90 or not -180 <= lng <= 180:
            return self.send_json(
                400,
                {"error": "Those coordinates are not a place on the map.", "field": "lat"},
            )

        if not PERSIST:
            session_user["delivery_address"] = address
            session_user["delivery_lat"] = lat
            session_user["delivery_lng"] = lng
            session_user["delivery_confirmed_at"] = datetime.datetime.now(
                datetime.timezone.utc
            ).strftime("%Y-%m-%d %H:%M:%S")
            return self.send_json(200, {"ok": True, "demo": True, "user": session_user})

        with connect() as conn:
            conn.execute(
                "UPDATE users SET delivery_address = ?, delivery_lat = ?, delivery_lng = ?,"
                " delivery_confirmed_at = datetime('now') WHERE id = ?",
                (address, lat, lng, row["id"]),
            )
            conn.commit()
            updated = conn.execute("SELECT * FROM users WHERE id = ?", (row["id"],)).fetchone()
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
