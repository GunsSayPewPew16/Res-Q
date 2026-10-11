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
POST /api/orders turns a chosen pair into a delivery order.

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
    GET  /api/matches               rank the counterparts near the account, nearest first
    GET  /api/orders                list the delivery orders the account is part of
    POST /api/orders                bind the account and one counterpart into an order
    GET  /api/weather               the real conditions at a point, in the analyser's words
    GET  /api/surplus-forecast      forecast today's surplus (surplus.py pipeline)
    GET  /api/surplus-outlook       the week ahead at a point, a forecast per day

Only the standard library is used for the site and session work, so the server
runs as before with nothing to install; the live weather wants nothing but a
connection, and the surplus forecast additionally wants pandas and scikit-learn and
answers 503 with install instructions without them.

The handler lives here; everything it calls lives in the module that owns it, so
the API can be inspected a topic at a time:

    config.py     every constant, limit and vocabulary, and the demo stores
    db.py         the SQLite connection and the schema
    helpers.py    request cleaning and naming
    security.py   password hashing and checking
    geo.py        distance, the pre-filter box, the offset point
    accounts.py   impact figures and the demo accounts and peers
    matching.py   what makes two accounts deliverable
    orders.py     the order's stored shape
    weather.py    live conditions from Open-Meteo, in the model's three words
    surplus.py    the surplus calculator: train once, forecast per request
"""

import datetime
import json
import os
import secrets
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

# The two label vocabularies the forecast accepts. They are the training data's own
# values — days of the week and three weather words — so anything else cannot be
# forecast against silently.
SURPLUS_DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
                "Saturday", "Sunday")
SURPLUS_WEATHERS = ("Sunny", "Cloudy", "Rainy")

from accounts import (
    demo_lookup,
    demo_peer_users,
    fill_donor_metrics,
    remember_demo_account,
    seed_donor_metrics,
)
from config import (
    DB_PATH,
    DEFAULT_MATCH_LIMIT,
    DEFAULT_RADIUS_KM,
    DEMO_ORDER_SEQ,
    DEMO_ORDERS,
    DEMO_SESSIONS,
    DELIVERY_DAYS,
    DONOR_METRIC_FIELDS,
    DONOR_SURPLUS,
    EMAIL_RE,
    FIRM_SURPLUS,
    FRONTEND_DIR,
    LIST_FIELDS,
    LIVE_ORDER_STATUSES,
    MAX_ADDRESS_CHARS,
    MAX_MATCH_LIMIT,
    MAX_PICKS,
    MAX_RADIUS_KM,
    METRIC_DECIMALS,
    PERSIST,
    PUBLIC_FIELDS,
    RECIPIENT_NEEDS,
    WEATHER_MAX_OUTLOOK_DAYS,
    WEATHER_OUTLOOK_DAYS,
)
from db import connect, init_db, users_by_ids
from geo import bounding_box, haversine_km, offset_point
from helpers import (
    clean_email,
    clean_id,
    clean_phone,
    clean_selection,
    decode_list,
    demo_display_name,
    display_name,
    password_problem,
    query_number,
)
from matching import routing_problem, shared_categories
from orders import order_row
from security import hash_password, verify_password
from surplus import forecast_surplus, train_surplus_model
from weather import WeatherUnavailable, fetch_outlook, fetch_weather


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
        if path == "/api/weather":
            return self.api_weather()
        if path == "/api/surplus-forecast":
            return self.api_surplus_forecast()
        if path == "/api/surplus-outlook":
            return self.api_surplus_outlook()
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
        if path == "/api/orders":
            return self.api_order_create()
        if path == "/api/surplus-forecast":
            return self.api_surplus_forecast()
        if path == "/api/dev/switch-role":
            return self.api_switch_role()
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
                "business_name": (data.get("businessName") or "").strip() or None,
                "email": email,
                "phone": phone,
                "dial_code": dial_code,
                # The address rides along as well, so demo mode answers /api/me with the
                # same shape persistent mode does; the dashboard map reads it to open on
                # the address the account onboarded with.
                "street": (address.get("street") or "").strip() or None,
                "sub_locality": (address.get("subLocality") or "").strip() or None,
                "locality": (address.get("locality") or "").strip() or None,
                "province": (address.get("province") or "").strip() or None,
                "city": (address.get("city") or "").strip() or None,
                "postal": (address.get("postal") or "").strip() or None,
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
            remember_demo_account(user)
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

        # Demo mode: sign anyone in. A contact registered earlier in this run signs in as
        # the account it registered, role and all, whether or not that session is still
        # open; anything else gets a donor session so no account has to be created first.
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
        token = self.bearer_token() or (self.read_json() or {}).get("token", "")
        if token:
            if not PERSIST:
                DEMO_SESSIONS.pop(token, None)
                return self.send_json(200, {"ok": True, "demo": True})
            with connect() as conn:
                conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
                conn.commit()
        return self.send_json(200, {"ok": True})

    # ------------------------------------------------------------------- forecast
    def query_point(self, query):
        """Read a lat/lng pair out of a query string, or the 400 it deserves.

        Returns `(lat, lng, None)` for a point on the globe and `(None, None, body)` for
        anything else. Both weather readings are made *at* a point, and they are checked
        the way the delivery location checks them: a point the site itself would refuse to
        store is a point nobody has an address for, so nothing is read there.
        """
        try:
            lat = float((query.get("lat") or [""])[0])
            lng = float((query.get("lng") or [""])[0])
        except (TypeError, ValueError):
            return None, None, {
                "error": "lat and lng have to be numbers: the weather is read at a point.",
                "field": "lat",
            }
        if lat != lat or lng != lng or not -90 <= lat <= 90 or not -180 <= lng <= 180:
            return None, None, {
                "error": "Those coordinates are not a place on the map.",
                "field": "lat",
            }
        return lat, lng, None

    def weather_unavailable(self, err):
        """The one 502 both weather readings answer with when the sky cannot be read."""
        return self.send_json(502, {"error": str(err), "code": "weather_unavailable"})

    def model_dependencies_missing(self):
        """The one 503 both surplus endpoints answer with, install line and all."""
        return self.send_json(
            503,
            {
                "error": "The surplus model needs pandas and scikit-learn, which are"
                         " not installed on this server. Install them (pip install"
                         " pandas scikit-learn) and restart it.",
                "code": "model_dependencies_missing",
            },
        )

    def api_weather(self):
        """The real sky at a point, in the vocabulary the analyser was trained on.

        The reading comes from Open-Meteo — keyless, like the map's tiles — and
        `weather.py` is the one place that decides which of Sunny, Cloudy and Rainy a WMO
        code becomes. An unreachable service is answered as 502 rather than guessed at,
        because a forecast drawn under an invented sky is worse than one that says the sky
        could not be read.
        """
        lat, lng, problem = self.query_point(parse_qs(urlparse(self.path).query))
        if problem:
            return self.send_json(400, problem)

        try:
            weather = fetch_weather(lat, lng)
        except WeatherUnavailable as err:
            # The message says what went wrong in the service's own terms; the dashboard
            # puts it on the analyser's banner rather than running the model under a sky
            # nobody read.
            return self.weather_unavailable(err)
        return self.send_json(200, {"ok": True, "weather": weather})

    def api_surplus_outlook(self):
        """The week's surplus at a point: each day's own sky, run through the model.

        This is what the analyser's weekly chart is drawn from. The weather is what makes
        one day differ from the next — and the day's own name brings the model's weekend
        bump with it — so the week is read as one daily outlook and every day is forecast
        with its own day and its own sky, today first, at the same point the banner's sky
        was read at. `days` defaults to a week and is pulled into what the service will
        answer, so a caller cannot ask for a week that does not exist. An unreachable
        service, or a run without pandas and scikit-learn, is answered as 502 and 503
        respectively: a chart drawn under invented skies is worse than one that says the
        week could not be read.
        """
        query = parse_qs(urlparse(self.path).query)
        lat, lng, problem = self.query_point(query)
        if problem:
            return self.send_json(400, problem)
        days = query_number(query, "days", WEATHER_OUTLOOK_DAYS, 1, WEATHER_MAX_OUTLOOK_DAYS)
        if days is None:
            return self.send_json(
                400,
                {"error": "days has to be a whole number of days.", "field": "days"},
            )

        try:
            outlook = fetch_outlook(lat, lng, int(days))
        except WeatherUnavailable as err:
            return self.weather_unavailable(err)

        series = []
        model = None
        try:
            for day in outlook["days"]:
                answer = forecast_surplus(
                    day_of_week=day["day_of_week"], weather=day["label"]
                )
                series.append(
                    dict(day, predicted_surplus_kg=answer["predicted_surplus_kg"])
                )
                # Every day carries the same cached pipeline and its score, so the week
                # carries them once rather than seven times over.
                model = {"mae_kg": answer["mae_kg"], "model": answer["model"]}
        except ImportError:
            return self.model_dependencies_missing()

        return self.send_json(
            200, {"ok": True, "outlook": dict(outlook, days=series, **(model or {}))}
        )

    def forecast_param_problem(self, data):
        """Validate one forecast request's answers before the model sees them.

        Returns the cleaned arguments, or an error body the caller sends back as
        its 400. The labels have to come from the vocabularies the training data
        was built on — seven day names, three weather words — because anything
        else would be forecast against silently. Numeric features are refused when
        they are not numbers or sit below zero, rather than guessed at.
        """
        cleaned = {}
        raw_dow = (data.get("day_of_week") or "").strip()
        if raw_dow and raw_dow not in SURPLUS_DAYS:
            return None, {
                "error": "day_of_week has to be a day name, Monday through Sunday.",
                "field": "day_of_week",
            }
        if raw_dow:
            cleaned["day_of_week"] = raw_dow
        raw_weather = (data.get("weather") or "").strip()
        if raw_weather and raw_weather not in SURPLUS_WEATHERS:
            return None, {
                "error": "weather has to be Sunny, Cloudy or Rainy: the conditions"
                         " the model was trained on.",
                "field": "weather",
            }
        if raw_weather:
            cleaned["weather"] = raw_weather
        for name in ("expected_customers", "surplus_yesterday", "surplus_last_week",
                     "surplus_rolling_14"):
            if name not in data:
                continue
            try:
                value = float(data[name])
            except (TypeError, ValueError):
                return None, {
                    "error": name + " has to be a number.",
                    "field": name,
                }
            if value != value or value < 0:
                return None, {
                    "error": name + " has to be 0 or more.",
                    "field": name,
                }
            cleaned[name] = value
        return cleaned, None

    def api_surplus_forecast(self):
        """Today's surplus, forecast by the pipeline in surplus.py.

        The model is trained on synthetic daily history once and cached, so the
        first request pays for the forest and the rest do not. Every feature is
        optional: a parameter that is not a usable number is refused rather than
        guessed at, because a forecast nobody can trace to its inputs is not one
        worth drawing on the analyser screen.
        """
        if self.command == "POST":
            data = self.read_json()
            if data is None:
                return self.send_json(400, {"error": "Malformed request body."})
        else:
            data = {}
            query = parse_qs(urlparse(self.path).query)
            for name in ("day_of_week", "weather"):
                value = (query.get(name) or [""])[0].strip()
                if value:
                    data[name] = value
            for name in ("expected_customers", "surplus_yesterday", "surplus_last_week",
                         "surplus_rolling_14"):
                raw = (query.get(name) or [""])[0].strip()
                if raw:
                    data[name] = raw

        cleaned, problem = self.forecast_param_problem(data)
        if problem:
            return self.send_json(400, problem)

        try:
            forecast = forecast_surplus(**cleaned)
        except ImportError:
            # Surplus.py raises the plain ImportError with install instructions when
            # pandas is missing; scikit-learn's missing import is a ModuleNotFoundError,
            # which subclasses it. Either way the answer is the same 503.
            return self.model_dependencies_missing()
        return self.send_json(200, {"ok": True, "forecast": forecast})

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

        category = (data.get("category") or "").strip().lower()
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
        scheduled_for = (data.get("scheduledFor") or "").strip().lower() or None
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
        role = (data.get("role") or "").strip().lower()
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

        address = (data.get("address") or "").strip()
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
            "        rejected as a duplicate. Accounts registered in this run sign back\n"
            "        in for as long as the process runs. Set RESQ_PERSIST=on to store\n"
            "        accounts in a database.",
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
