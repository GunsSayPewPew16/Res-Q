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
# The delivery orders a demo run is holding, newest last. One list for the process rather
# than one per session: an order belongs to the two accounts on it, not to whichever of
# them happened to propose it, so the other side has to be able to read it back. In demo
# mode every account carries id 0 — there is no table to tell two of them apart by — so
# each stored order records the contact of each side beside the ids, which is what those
# two accounts are found by. With persistence on the same rows are the table's own.
DEMO_ORDERS = []

DEMO_ORDER_SEQ = count(1)

# The logged days a demo account has, keyed by the contact it signed in with. Demo users
# all carry id 0, so a contact is what a row can be filed under in a process that has no
# database; with persistence on the same rows are the account's own rows in the table.
DEMO_HISTORY = {}

# The claims a demo run is holding, oldest first. A claim belongs to the two sides of a
# board post — the donor who logged it and the shelter that reached for it — and not to the
# session that made it, so like the delivery orders these live for the process rather than
# for a token. With persistence on the same rows are the `claims` table's own.
DEMO_CLAIMS = []

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
    occupancy     INTEGER,
    business_name_changed_at TEXT,
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

CREATE TABLE IF NOT EXISTS claims (
    -- One record per claim, in the shape the claim recorder wrote and the rationing pass
    -- reads: the board row that was claimed, and who claimed it. The record is kept whole
    -- in `payload` rather than spread across columns, because it is one shape shared by
    -- four readers — the API, the recorder, the pass and the file — and a table that
    -- mirrored every field would be a fifth place to keep in step. What the table adds is
    -- what a query needs: the post it joins on, when it happened, and the claimant, so one
    -- shelter cannot claim one post twice.
    record_id    TEXT PRIMARY KEY,
    post_id      TEXT NOT NULL,
    claimant_key TEXT NOT NULL,
    claimed_at   TEXT NOT NULL,
    payload      TEXT NOT NULL,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (post_id, claimant_key)
);

CREATE TABLE IF NOT EXISTS surplus_history (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id            INTEGER NOT NULL,
    date               TEXT    NOT NULL,
    weather            TEXT,
    weather_code       INTEGER,
    temp_c             REAL,
    customers_served   INTEGER,
    expected_customers REAL,
    surplus_kg         REAL,
    created_at         TEXT    NOT NULL DEFAULT (datetime('now')),
    -- One row per kitchen per day: the log is a timeline, and a day cannot be logged
    -- twice, so a re-seed cannot double a kitchen's own past.
    UNIQUE (user_id, date),
    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
);
"""

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Cross-origin access. In development the pages are served by this very server, so their
# calls are same-origin and need none of this; deployed apart — the pages on Vercel, the
# API on Render — every call is cross-origin and the API has to say which origins it
# answers, because the browser will not read an answer that does not name its own origin.
#
# RESQ_ALLOWED_ORIGINS is a comma-separated list. An entry is either an exact origin
# (`https://res-q.vercel.app`) or a wildcard for one domain's subdomains
# (`*.vercel.app`), which is what preview deployments need: Vercel gives every branch its
# own hostname, so pinning a single exact origin would refuse every preview. Nothing is
# allowed by default, so a deployment nobody configured talks to its own pages only.
def _origin_list(value):
    """A comma-separated origin list, trimmed the way the CORS check compares them."""
    return tuple(part.strip().rstrip("/").lower()
                 for part in (value or "").split(",") if part.strip())


ALLOWED_ORIGINS = _origin_list(os.environ.get("RESQ_ALLOWED_ORIGINS", ""))

# What a preflight is told the API accepts. The pages send JSON with a bearer token, so
# those are the two headers worth naming — a browser preflights a request carrying
# `Authorization`, and an unanswered preflight is a call that never happens.
CORS_ALLOWED_METHODS = "GET, POST, OPTIONS"

CORS_ALLOWED_HEADERS = "Authorization, Content-Type"

CORS_MAX_AGE_SECONDS = 600

PUBLIC_FIELDS = (
    "id", "role", "first_name", "last_name", "business_name",
    "business_name_changed_at", "email", "phone",
    "dial_code", "street", "sub_locality", "locality", "province", "city",
    "postal", "firm_type", "delivery_days", "surplus_types", "occupancy",
    "delivery_address", "delivery_lat", "delivery_lng", "delivery_confirmed_at",
    "surplus_saved", "meals_served", "orders_completed",
    "created_at",
)

# The delivery location confirmed on the dashboard map. The coordinates are the point
# for routing; the address is what the map showed at that point when it was confirmed.
DELIVERY_FIELDS = ("delivery_address", "delivery_lat", "delivery_lng", "delivery_confirmed_at")

MAX_ADDRESS_CHARS = 400

# The largest body any form here sends is a registration, well under a kilobyte; a
# request that declares more than this is answered as unreadable rather than read, so a
# thread cannot be parked waiting on bytes a client only promised.
MAX_REQUEST_BODY_BYTES = 64 * 1024

# Live weather for the analyser. Read from Open-Meteo, which is keyless like the map's
# tiles and the address lookups, so nothing has to be registered to run this server.
# The analyser's model knows three words for the sky; weather.py is the one place that
# decides which of them a WMO code becomes, and these are the constants that call needs.
WEATHER_API_URL = "https://api.open-meteo.com/v1/forecast"

# What is asked for: the sky right now, and today's own summary, because the analyser
# forecasts today's surplus. Local time, so "today" is the address's day, not UTC's.
WEATHER_CURRENT_FIELDS = "temperature_2m,precipitation,weather_code,wind_speed_10m"
WEATHER_DAILY_FIELDS = "weather_code,precipitation_sum,temperature_2m_max,temperature_2m_min"
WEATHER_TIMEOUT_SECONDS = 8
WEATHER_SOURCE = "Open-Meteo"
WEATHER_SOURCE_URL = "https://open-meteo.com/"

# The same service read backwards: the conditions those days actually had, which is what
# the test donors' logs are filled with so their weather is real rather than invented.
WEATHER_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

# The analyser's weekly chart: the same service read as a daily outlook, one entry per
# day from today onwards, and every day run through the model. Seven is a week's worth
# of bars; the ceiling is the sixteen days Open-Meteo will forecast at all.
WEATHER_OUTLOOK_DAYS = 7
WEATHER_MAX_OUTLOOK_DAYS = 16

# The surplus history: one row per kitchen per day — what that kitchen served, what the
# sky over it was, and how much surplus came out of the day. It is the donor's own log,
# and it is where the analyser's model reads its past from: the three surplus lags and
# the expected customers are derived from these rows, which is what the model's own
# defaults are a fallback for rather than the answer every kitchen gets.
HISTORY_FIELDS = (
    "date", "weather", "weather_code", "temp_c",
    "customers_served", "expected_customers", "surplus_kg",
)

# How far back a reading looks. Six weeks is enough for the fourteen-day window, the
# week-old lag and the day-old lag to all be real numbers at once.
HISTORY_READ_DAYS = 42

# The test donors the calculator is exercised against: five kitchens with six weeks of
# logs each. In demo mode the server seeds them on startup, since nothing is written to
# disk; with persistence on, RESQ_SEED=on asks for it, so a database nobody meant to
# touch is never written to uninvited.
SEED_HISTORY_DAYS = 42

# The one demo account the README names at the top, and the reason it is worth naming: a
# Manhattan kitchen whose log covers a **year** of days rather than six weeks, so the
# analyser is read and its model fitted against a kitchen that has been through every
# season — a holiday week, a heatwave, a fortnight of rain. Its weather is the archive's
# own for each of those days, one request for the whole year.
DEMO_DONOR_HISTORY_DAYS = 365

_seed_flag = os.environ.get("RESQ_SEED", "").strip().lower()

SEED_ON_START = (not PERSIST) if not _seed_flag else _seed_flag in ("on", "1", "true", "yes")

# The seven day-to-day rows of a seed, and the fourteen-day window the model averages
# over, are the model's own business and live in surplus.py.

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

# The establishments a donor's goods profile offers. Named here because the goods profile
# and the rule that vets its categories both read the same two words.
FIRM_TYPES = ("retail", "eatery")

# A shelter's occupancy: how many people it takes in. Asked of recipients only — a donor
# has no shelter — and kept inside a range a real building could hold, so a slipped digit
# is refused rather than stored.
MIN_OCCUPANCY = 1

MAX_OCCUPANCY = 100000

# Both roles may rename their business, but each rename costs a month: the first is free,
# and every one after it has to sit at least this far past the last. `business_name_changed_at`
# is what the wait is measured from, and it rides back on the profile so the page can say
# when the next change falls due.
BUSINESS_NAME_MONTHS = 1

MAX_BUSINESS_NAME_CHARS = 120

# Timestamps this API writes are UTC and in the shape a browser reads back with `new
# Date(...)` without help, which is what lets the page turn one into "in 23 days" itself.
STAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

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

# The rationing pass. When several shelters have claimed the same surplus post, no single
# one of them gets the whole item: the post is split between its claimants in proportion
# to how much each of them needs it, and this is the score that decides by how much.
#
# The formula is the project's own, and the three names are the fields the model is asked
# to fill, so the arithmetic in `rationing.py` and the prompt it sends cannot drift apart:
#
#     (distance_score * 0.3) + (need_score * 0.4) + (expiry_urgency_multiplier * 3.0)
#
# Distance and need are weighted against each other; urgency is *multiplied* rather than
# weighted, because it is the one term that changes what the goods are worth at all —
# produce that spoils tonight is worth nothing tomorrow, and no distance is short enough
# to save it. That is also why it carries the largest coefficient: it dominates a
# comparison between two claims on food that is close to its end.
RATION_WEIGHTS = {
    "distance_score": 0.3,
    "need_score": 0.4,
    "expiry_urgency_multiplier": 3.0,
}

# The same formula as one line, so an answer can carry the arithmetic it used.
RATION_FORMULA = "(distance_score * 0.3) + (need_score * 0.4) + (expiry_urgency_multiplier * 3.0)"

# Distance is scored against the same radius a match is measured inside, so a claimant at
# the edge of that radius scores nothing on the term and one at the donor's own door
# scores all of it. Every score the formula takes is a fraction of one for this reason:
# the three terms are only comparable if they are all on the same scale.
RATION_DISTANCE_RADIUS_KM = DEFAULT_RADIUS_KM

# What the pass is willing to believe a single post weighs, in kilograms. A quantity that
# parses to more than this is a parsing accident rather than a pallet of food, so the
# assumed pack weight stands in for it.
RATION_MAX_POST_KG = 5000.0

# How many claimant pairs one pass will show the model. A pass is one request, and a
# request that carries a hundred claimants is a request the model answers slowly and
# unevenly; anything past this is scored by the pass's own reading of the same fields.
RATION_MAX_PAIRS = 40

# A share is a percentage of a post, so one decimal is as fine as it is honest.
RATION_PERCENT_DECIMALS = 1

# Where the claim recorder writes what it records, and where the rationing pass reads the
# same file from the other side of that boundary: one post, many claimants, tied together
# by the post id each record carries. The API records claims itself now (`claims.py`), so
# this file is no longer the only place they live — but a local run that used the
# standalone recorder still has its claims read from here instead of them being lost.
CLAIM_RECORDS_PATH = os.path.join(BACKEND_DIR, "claim_records.json")

# The one claim shape. A claim is the board row that was claimed, in the six fields the
# board itself carries, plus the shelter that reached for it — copied from the profile of
# whoever was signed in rather than taken from the request, so a claim cannot be filed
# against somebody else's account. Anything else a caller sends is dropped.
CLAIM_POST_FIELDS = ("id", "donor_name", "item_name", "quantity", "location",
                     "goods_type", "created_at")

CLAIM_RECIPIENT_FIELDS = ("id", "first_name", "last_name", "business_name", "email",
                          "phone", "dial_code", "city", "province", "postal",
                          "delivery_address", "delivery_lat", "delivery_lng",
                          "delivery_confirmed_at", "surplus_types", "delivery_days")

# The columns the allocation is written back to on the board, and what they mean. The
# rationing pass computes a share; publishing it is what puts that share in front of the
# dohar who owns the goods as well as the shelter that gets them, on the row they are both
# already looking at. `supabase/migrations/20261011000000_add_allocation_to_surplus_posts.sql`
# adds them.
ALLOCATION_FIELDS = ("allocated_kg", "allocation_percent", "allocation_score",
                     "allocated_to", "allocated_at")

# The board itself, for the one thing this backend ever writes to it. The pages hold the
# publishable key and write the board directly; the API needs its own copy of the project's
# URL and a key to publish an allocation with. Either key works and they differ in reach: a
# publishable key goes through the table's row level security, whose update policy already
# allows a claimed row's allocation to be written, while a secret (service) key bypasses
# those policies and is the one to use when the board's policies are tightened later.
# Nothing is published while these are unset, which is the default.
SUPABASE_URL = (os.environ.get("RESQ_SUPABASE_URL")
                or os.environ.get("SUPABASE_URL")
                or "").strip().rstrip("/")

SUPABASE_KEY = (os.environ.get("RESQ_SUPABASE_KEY")
                or os.environ.get("RESQ_SUPABASE_SERVICE_KEY")
                or os.environ.get("SUPABASE_SERVICE_KEY")
                or "").strip()

SUPABASE_TABLE = "surplus_posts"

SUPABASE_TIMEOUT_SECONDS = 8

# The model that reads the qualitative half of a claim: how much a written quantity comes
# to, how perishable the item is, and how pressing a shelter's need looks. It is optional
# by design — with no key configured the pass answers from its own reading of the same
# fields, and a call that fails falls back to it the same way, so a ration is never held
# up by a model being unavailable. Any OpenAI-compatible endpoint works: the base URL is
# a setting, which is what lets a self-hosted or gateway endpoint stand in.
LLM_API_KEY = (os.environ.get("RESQ_LLM_API_KEY")
               or os.environ.get("OPENAI_API_KEY")
               or "").strip()

LLM_MODEL = os.environ.get("RESQ_LLM_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"

LLM_BASE_URL = (os.environ.get("RESQ_LLM_BASE_URL", "https://api.openai.com/v1").strip()
                or "https://api.openai.com/v1")

# A rationing pass waits on the model rather than failing without it, but not for long:
# the pass's own reading of the same fields is a perfectly good answer, so a model that
# has not spoken within this many seconds is one the pass stops waiting for.
LLM_TIMEOUT_SECONDS = 12

# What the model is told to be. Temperature zero because the same claims should ration the
# same way twice: a share that moved between two reads of one post would be a number
# nobody could act on.
LLM_TEMPERATURE = 0

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
