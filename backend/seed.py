"""Res-Q backend test donors.

Five kitchens with six weeks of surplus history each, so the calculator the analyser
screen is built on can be exercised against a real past instead of its own defaults: what
each kitchen served, what the sky over it was, and how much surplus came out of the day.

The fixture is built to be *readable*, not just plentiful:

- **The weather is real.** Each donor's days carry the conditions Open-Meteo's archive
  actually recorded at that donor's own pinned coordinates. If the archive cannot be
  reached the days fall back to a deterministic pattern instead — and a day from that
  pattern carries no WMO code, so a fallback row says plainly that it is not a reading.
- **The customers are the fixture's, the surplus follows the shape the model was trained
  on**: a weekday rhythm, the weekend bump of four kilos, and a little noise, with the
  kitchen's size deciding the base. A test that ran against some other shape would prove
  nothing about this model.
- **Everything is seeded per donor**, so the same five kitchens come back the same way
  every time the server starts, and a second seeding adds nothing: a kitchen's past is a
  record, and a record does not change because a seed ran again.

In demo mode the server seeds these on startup, since nothing is written to disk. With
persistence on it happens only when `RESQ_SEED=on` asks for it. Either way they sign in
with the password below — `dana@resq.test` and the rest, listed in the README.
"""

import datetime
import json
import random

from accounts import seed_donor_metrics
from config import DEMO_ACCOUNTS, SEED_HISTORY_DAYS
from helpers import clean_email
from history import write_history
from security import hash_password
from weather import WeatherUnavailable, fetch_archive

TEST_DONOR_PASSWORD = "ResqDemo1!"

# The fixture's own five kitchens. The coordinates are pinned rather than geocoded: they
# are what the archive is asked about and what a delivery pin would be confirmed at, and
# a fixture that moved with an address lookup would not be a fixture.
TEST_DONORS = (
    {"first_name": "Dana", "last_name": "Whitfield", "business_name": "Whitfield Bakery",
     "email": "dana@resq.test", "street": "60 Queen Street West", "city": "Toronto",
     "province": "Ontario", "postal": "M5H 2M5", "firm_type": "retail",
     "surplus_types": ("bakery_items", "packaged_goods"),
     "lat": 43.6522, "lng": -79.3805, "customers": (170, 250), "seed": 1041},
    {"first_name": "Omar", "last_name": "Haddad", "business_name": "Haddad Grocers",
     "email": "omar@resq.test", "street": "1180 Rue Sainte-Catherine Ouest",
     "city": "Montréal", "province": "Québec", "postal": "H3B 1K1", "firm_type": "retail",
     "surplus_types": ("fresh_produce", "dairy_beverages"),
     "lat": 45.4973, "lng": -73.5790, "customers": (200, 300), "seed": 1042},
    {"first_name": "Priya", "last_name": "Raman", "business_name": "Raman Kitchens",
     "email": "priya@resq.test", "street": "87 Elm Street", "city": "Toronto",
     "province": "Ontario", "postal": "M5G 1H2", "firm_type": "eatery",
     "surplus_types": ("prepared_meals", "dairy_beverages"),
     "lat": 43.6567, "lng": -79.3862, "customers": (110, 190), "seed": 1043},
    {"first_name": "Luis", "last_name": "Ortega", "business_name": "Ortega Deli",
     "email": "luis@resq.test", "street": "601 Biscayne Boulevard", "city": "Miami",
     "province": "Florida", "postal": "33132", "firm_type": "eatery",
     "surplus_types": ("prepared_meals", "bakery_items"),
     "lat": 25.78136, "lng": -80.18794, "customers": (230, 330), "seed": 1044},
    {"first_name": "Mei", "last_name": "Chen", "business_name": "Chen Noodle House",
     "email": "mei@resq.test", "street": "55 Bay Street", "city": "Toronto",
     "province": "Ontario", "postal": "M5J 2R6", "firm_type": "eatery",
     "surplus_types": ("prepared_meals", "fresh_produce"),
     "lat": 43.6482, "lng": -79.3794, "customers": (90, 170), "seed": 1045},
)

# What the fallback weather draws from, and the temperatures a season-free pattern can
# plausibly carry. Only used when the archive cannot be reached.
FALLBACK_WEATHERS = (("Sunny", 5), ("Cloudy", 3), ("Rainy", 2))

WEEKEND_BUMP_KG = 4.0

SURPLUS_PER_CUSTOMER = 0.15

SURPLUS_NOISE_KG = 2.5


def donor_address(donor):
    """A donor's one-line address, the way the delivery card writes one."""
    return ", ".join(part for part in
                     (donor["street"], donor["city"], donor["province"], donor["postal"]) if part)


def archived_weather(donor, start, end):
    """The archive's own labels for a donor's coordinates, or None when unreachable.

    Returns {date: {"weather", "weather_code", "temp_c"}}: the model's word for the day,
    the WMO code behind it and the day's high. None means the archive could not be read,
    which the caller answers with the deterministic pattern.
    """
    try:
        archive = fetch_archive(donor["lat"], donor["lng"], start.isoformat(), end.isoformat())
    except WeatherUnavailable:
        return None
    return {
        day["date"]: {"weather": day["label"], "weather_code": day["code"], "temp_c": day["high_c"]}
        for day in archive["days"]
    }


def donor_days(donor, days=SEED_HISTORY_DAYS, today=None, archive=True):
    """One kitchen's logged days, oldest first, ending yesterday.

    Ending yesterday is what makes the log a *past* a forecast can read: today's own
    surplus is the thing being forecast, so a row for today would be the answer rather
    than the history. Customers come from the donor's own seeded range, the surplus from
    the same shape the model was trained on, and the expectation recorded with each day is
    that kitchen's expanding average for that weekday — the formula the training data
    built the feature with, so the log means what the model means by it.
    """
    today = today or datetime.date.today()
    dates = [today - datetime.timedelta(days=back)
             for back in range(days, 0, -1)]

    weather = {}
    if archive:
        weather = archived_weather(donor, dates[0], dates[-1]) or {}

    rng = random.Random(donor["seed"])
    fallback_rng = random.Random(donor["seed"] + 1)
    weekday_totals = {}
    weekday_counts = {}
    rows = []
    for day in dates:
        stamped = day.isoformat()
        reading = weather.get(stamped)
        if reading is None:
            # No code and no archive reading: the temperature is the pattern's own guess,
            # and the missing code is what marks the row as not a measurement. The pattern
            # draws from its own generator, so a donor's customers and surplus come out the
            # same whether or not the archive answered.
            label = fallback_rng.choices([name for name, _ in FALLBACK_WEATHERS],
                                         weights=[weight for _, weight in FALLBACK_WEATHERS])[0]
            reading = {"weather": label, "weather_code": None,
                       "temp_c": round(14 + fallback_rng.uniform(-5, 9), 1)}

        customers = rng.randint(*donor["customers"])
        weekend = WEEKEND_BUMP_KG if day.weekday() >= 5 else 0.0
        surplus = customers * SURPLUS_PER_CUSTOMER + weekend + rng.gauss(0, SURPLUS_NOISE_KG)

        # The expectation recorded *with* the day, from the days before it: the first time a
        # weekday comes round there is nothing to average, and a log says so rather than
        # making a number up.
        key = day.weekday()
        seen = weekday_counts.get(key, 0)
        expected = round(weekday_totals.get(key, 0.0) / seen, 1) if seen else None
        weekday_totals[key] = weekday_totals.get(key, 0.0) + customers
        weekday_counts[key] = seen + 1

        rows.append({
            "date": stamped,
            "weather": reading["weather"],
            "weather_code": reading["weather_code"],
            "temp_c": reading["temp_c"],
            "customers_served": customers,
            "expected_customers": expected,
            "surplus_kg": round(max(surplus, 0.0), 2),
        })
    return rows


def donor_profile(donor, metrics=None):
    """The account dict a test donor signs in as, in the shape the dashboards read.

    Everything the profile step and the delivery card would have written is filled in
    here — the goods category, the confirmed pin at the donor's own address and the impact
    figures — so a test account is a *working* donor the moment it signs in rather than one
    that has to be walked through onboarding first.
    """
    figures = metrics or seed_donor_metrics()
    profile = {
        "id": 0,
        "role": "donor",
        "first_name": donor["first_name"],
        "last_name": donor["last_name"],
        "business_name": donor["business_name"],
        "email": donor["email"],
        "phone": None,
        "dial_code": None,
        "street": donor["street"],
        "sub_locality": None,
        "locality": None,
        "province": donor["province"],
        "city": donor["city"],
        "postal": donor["postal"],
        "firm_type": donor.get("firm_type"),
        "delivery_days": [],
        "surplus_types": list(donor["surplus_types"]),
        "delivery_address": donor_address(donor),
        "delivery_lat": donor["lat"],
        "delivery_lng": donor["lng"],
        "delivery_confirmed_at": datetime.datetime.now(
            datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "demo": True,
    }
    profile.update(figures)
    return profile


def remember_donor(donor, metrics=None):
    """Put a test donor in the demo account store under its email, once."""
    key = clean_email(donor["email"])
    if key in DEMO_ACCOUNTS:
        return None
    profile = donor_profile(donor, metrics)
    DEMO_ACCOUNTS[key] = profile
    return profile


def store_donor(conn, donor, metrics=None):
    """Write a test donor into the users table, unless that email is already registered."""
    existing = conn.execute(
        "SELECT id FROM users WHERE email = ?", (donor["email"],)
    ).fetchone()
    if existing:
        return None
    digest, salt = hash_password(TEST_DONOR_PASSWORD)
    profile = donor_profile(donor, metrics)
    cursor = conn.execute(
        """INSERT INTO users (
               role, first_name, last_name, business_name, email, phone, dial_code,
               street, sub_locality, locality, province, city, postal,
               password_hash, password_salt, firm_type, delivery_days, surplus_types,
               delivery_address, delivery_lat, delivery_lng, delivery_confirmed_at,
               surplus_saved, meals_served, orders_completed
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "donor",
            donor["first_name"],
            donor["last_name"],
            donor["business_name"],
            donor["email"],
            None,
            None,
            donor["street"],
            None,
            None,
            donor["province"],
            donor["city"],
            donor["postal"],
            digest,
            salt,
            donor.get("firm_type"),
            json.dumps(profile["delivery_days"]),
            json.dumps(profile["surplus_types"]),
            profile["delivery_address"],
            profile["delivery_lat"],
            profile["delivery_lng"],
            profile["delivery_confirmed_at"],
            profile["surplus_saved"],
            profile["meals_served"],
            profile["orders_completed"],
        ),
    )
    conn.commit()
    stored = dict(profile)
    stored["id"] = cursor.lastrowid
    return stored


def seed_test_donors(conn=None, days=SEED_HISTORY_DAYS, today=None):
    """Create the five test donors and their logs, as far as they are not there already.

    Returns what it did: which donors were created, which were already registered, how
    many days were written and where the weather came from. Called once at startup in demo
    mode and with `RESQ_SEED=on` in persistent mode, and safe to call again — a donor that
    exists is skipped and a day already logged is left alone.
    """
    today = today or datetime.date.today()
    created = []
    skipped = []
    days_written = 0
    archived = 0
    patterned = 0

    for donor in TEST_DONORS:
        # The account comes first: a donor already registered is skipped before any day is
        # generated, which is what keeps a second startup from asking the archive anything
        # about kitchens it has already written.
        account = remember_donor(donor) if conn is None else store_donor(conn, donor)
        if account is None:
            skipped.append(donor["email"])
            continue

        rows = donor_days(donor, days=days, today=today)
        if any(row["weather_code"] is not None for row in rows):
            archived += 1
        else:
            patterned += 1
        created.append(donor["email"])
        days_written += write_history(rows, account, conn)

    return {
        "created": created,
        "skipped": skipped,
        "days_written": days_written,
        "weather_from_archive": archived,
        "weather_from_pattern": patterned,
        "password": TEST_DONOR_PASSWORD,
    }


def describe_test_donors():
    """The test donors as the README lists them: who they are and where they are."""
    return [
        {
            "email": donor["email"],
            "business": donor["business_name"],
            "address": donor_address(donor),
            "customers_per_day": donor["customers"],
        }
        for donor in TEST_DONORS
    ]
