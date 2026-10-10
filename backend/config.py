"""Res-Q backend configuration.

Every constant the API reads and answers with lives here, so the helpers beside
this file import their limits and vocabularies from one place instead of
hard-coding them deep in a body. The demo-mode stores are here too: they are
state, not behaviour, and the handlers mutate them.

Nothing in this file imports from the other backend modules, so it is the one
file every other file may lean on without a cycle.
"""

import os
import re
from itertools import count

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

# Registered demo accounts, by email and by phone. The sessions above come and go with
# signing in and out; the accounts stay, so the details used at sign-up sign in again as
# the role they registered with rather than being taken for a stranger.
DEMO_ACCOUNTS = {}

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

MAX_ADDRESS_CHARS = 400

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
