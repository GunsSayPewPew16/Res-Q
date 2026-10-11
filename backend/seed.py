"""Res-Q backend test accounts.

Six kitchens' logs, so the calculator the analyser screen is built on can be exercised
against a real past instead of its own defaults: what each kitchen served, what the sky
over it was, and how much surplus came out of the day. Five of them carry six weeks each;
the sixth is the demo account the README names at the top, a Manhattan kitchen with a
**year** of days behind it, which is the past the model is fitted to and the account the
analyser is shown off on. Beside them sits the other end of a delivery — one recipient
with a confirmed pin of its own, and one delivery already on the books between it and a
kitchen, so the recipient's console has an order to read and a route to draw rather than
an empty frame.

The fixture is built to be *readable*, not just plentiful:

- **The weather is real.** Each donor's days carry the conditions Open-Meteo's archive
  actually recorded at that donor's own pinned coordinates — a year of them for the demo
  kitchen, read in one request. If the archive cannot be reached the days fall back to a
  deterministic pattern instead, and a day from that pattern carries no WMO code, so a
  fallback row says plainly that it is not a reading.
- **The customers are the fixture's, the surplus follows the site's own shape**: a weekday
  rhythm, the weekend bump of four kilos, and a little noise, with the kitchen's size
  deciding the base. That shape is not decoration: these rows are what the model is
  *fitted* to, so the surplus it learns is this one.
- **Everything is seeded per donor**, so the same six kitchens come back the same way
  every time the server starts, and a second seeding adds nothing: a kitchen's past is a
  record, and a record does not change because a seed ran again.

In demo mode the server seeds these on startup, since nothing is written to disk. With
persistence on it happens only when `RESQ_SEED=on` asks for it. Either way they sign in
with the password below — `rosa@resq.test` and `dana@resq.test` and the rest, listed in
the README.
"""

import datetime
import json
import random

from accounts import seed_donor_metrics
from config import (
    DEMO_ACCOUNTS,
    DEMO_DONOR_HISTORY_DAYS,
    DEMO_ORDER_SEQ,
    DEMO_ORDERS,
    FINISHED_ORDER_STATUS,
    LIVE_ORDER_STATUSES,
    METRIC_DECIMALS,
    SEED_HISTORY_DAYS,
    SURPLUS_CATEGORIES_DAYS,
)
from geo import haversine_km, offset_point
from helpers import account_key, clean_email
from history import write_history
from orders import demo_order_pair, order_payload, order_row, remember_demo_order
from security import hash_password
from surplus_log import entry_key_day, write_entries
from weather import WeatherUnavailable, fetch_archive

# One password for every fixture account, donors and the recipient alike: these are demo
# accounts, and the README names them so anyone can sign in and look around.
TEST_PASSWORD = "ResqDemo1!"

# The demo account the README names at the top, in New York. It is the same kind of
# fixture as the five below — a working donor with a goods profile, a confirmed pin and
# its own figures — with one difference that is the whole point of it: its log covers a
# **year** of days instead of six weeks, so the analyser has a kitchen that has been
# through every season to be read, and the model has a year of real weather to be fitted
# to rather than six weeks of it. The coordinates are Chelsea Market's own on 9th Avenue,
# which is what the archive is asked about.
DEMO_DONOR = {
    "first_name": "Rosa", "last_name": "Alvarez", "business_name": "Chelsea Market Kitchen",
    "email": "rosa@resq.test", "street": "75 9th Avenue", "city": "New York",
    "province": "New York", "postal": "10011", "firm_type": "eatery",
    "surplus_types": ("prepared_meals", "bakery_items"),
    "lat": 40.74205, "lng": -74.00610, "customers": (240, 380), "seed": 1046,
    "history_days": DEMO_DONOR_HISTORY_DAYS,
}

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


# The other side of the board: the one recipient in the fixture, with a confirmed pin of
# its own downtown and a couple of days it will take a delivery on. Its needs are the two
# of the six categories a recipient may ask for that a kitchen in the fixture also
# produces, which is what makes the seeded delivery a real pair rather than two pins with
# nothing in common.
TEST_RECIPIENT = {
    "first_name": "Noor", "last_name": "Abadi", "business_name": "Fort York Food Bank",
    "email": "noor@resq.test", "street": "40 Fort York Boulevard", "city": "Toronto",
    "province": "Ontario", "postal": "M5V 3Z3",
    "needs": ("packaged_goods", "fresh_produce"),
    "delivery_days": ("tue", "thu"),
    # How many people the shelter takes in, stated the way a real one states it, so the
    # account settings screen opens on a number instead of a blank box.
    "occupancy": 140,
    "lat": 43.6386, "lng": -79.4005,
}

# Every kitchen the fixture creates, in the order they are written: the five short logs
# first, then the demo account's year. One tuple, so an account can never be seeded
# without also being one the README's lists describe.
FIXTURE_DONORS = TEST_DONORS + (DEMO_DONOR,)

# The delivery already on the books when the server comes up: the bakery two and a half
# kilometres from the food bank, its packaged goods, on one of the days the food bank
# asked for. It is *accepted* rather than proposed, because it stands for a delivery that
# has been agreed — which is the state a recipient's console draws the route for.
TEST_DELIVERY = {
    "donor_email": "dana@resq.test",
    "category": "packaged_goods",
    "scheduled_for": "thu",
}

# One claimed post, and three shelters that reached for it, for the rationing pass to read
# on a deployment nobody has used yet: a board with no claims on it has no ration to show,
# and a ration that is always a hundred percent of a post shows nothing about how the pass
# splits one. The item is written the way a shop actually logs it — packs and halves rather
# than a weight — because reading that text is the first thing the pass does.
TEST_CLAIM = {
    "donor_email": "mei@resq.test",
    "id": "8f1c6b2e-0a4d-4a6f-9b3a-5c7d2e1f0a91",
    "item_name": "End-of-day produce, pastries and milk",
    "quantity": "3 crates of tomatoes, 2 trays of pastries and a crate of milk",
    "goods_type": "fresh_produce",
    # Hours before now, so the fixture is the same shape whenever the server is up: the
    # pass reads how long a post has been sitting, and a fixed date would have it sitting
    # there for years.
    "posted_hours_ago": 9.0,
}

# What is known about the fixture's own recipient on that claim. The board carries no such
# figures — a claim says who claimed an item, not how many are waiting for it — so they are
# the fixture's own, and the pass reads a real claim without them.
TEST_RECIPIENT_CLAIM = {
    "households_waiting": 180,
    "days_since_last_delivery": 12,
    "claimed_hours_ago": 6.0,
}

# The kitchen the Miami book belongs to: Ortega Deli, one of the five short-log donors,
# named here because its finished deliveries and its open ones are the fixture's own.
TEST_KITCHEN_EMAIL = "luis@resq.test"

# The three shelters Ortega Deli's deliveries run to. A donor's dashboard reads its
# finished orders and its open deliveries off the books, and books with nothing in them
# read as a kitchen that has never sent anything — these are the accounts on the other end
# of the Miami kitchen's, each with a confirmed pin of its own so an order can carry both
# ends. The pins are placed at their own measured distances from the deli's pin, the way
# the fixture's claimants are placed from their kitchen's, rather than geocoded: a fixture
# that moved with an address lookup would not be a fixture.
#
# Their needs are the two categories that kitchen produces, because a shelter nothing can
# be delivered to is not the other end of a delivery.
TEST_SHELTERS = (
    {"first_name": "Ana", "last_name": "Cruz", "business_name": "Biscayne Bay Shelter",
     "email": "ana@resq.test", "street": "1450 NE 2nd Avenue", "city": "Miami",
     "province": "Florida", "postal": "33132",
     "needs": ("prepared_meals", "bakery_items"), "delivery_days": ("mon", "thu"),
     "occupancy": 180, "distance_km": 2.4, "bearing": 20},
    {"first_name": "Ines", "last_name": "Robles", "business_name": "Overtown Community Kitchen",
     "email": "ines@resq.test", "street": "750 NW 3rd Avenue", "city": "Miami",
     "province": "Florida", "postal": "33136",
     "needs": ("prepared_meals", "bakery_items"), "delivery_days": ("tue", "fri"),
     "occupancy": 240, "distance_km": 1.6, "bearing": 200},
    {"first_name": "Tomas", "last_name": "Padilla", "business_name": "Little Havana Food Pantry",
     "email": "tomas@resq.test", "street": "1200 SW 8th Street", "city": "Miami",
     "province": "Florida", "postal": "33135",
     "needs": ("bakery_items", "prepared_meals"), "delivery_days": ("wed", "sat"),
     "occupancy": 90, "distance_km": 3.1, "bearing": 250},
)

# The five deliveries the Miami kitchen has already run, **oldest first**: which shelter
# each went to, under which category, and how long ago it finished — the last is what the
# finished-orders cards date a card by, and what makes "latest" mean anything. Oldest first
# because that is the order they happened in, which is the order every other order is filed
# in: a live one is appended when it is proposed, so the books read as a timeline and their
# newest-first answer is a reading of that order rather than a second one. A finished
# delivery is an order rather than a post or a claim, because that is what a delivery that
# has gone out *is*; each pair appears under one category only, so a second seeding cannot
# write the same history twice.
TEST_ORTEGA_ORDERS = (
    {"shelter": "ines@resq.test", "category": "prepared_meals",
     "scheduled_for": "fri", "created_days_ago": 27.0, "finished_days_ago": 26.5},
    {"shelter": "ana@resq.test", "category": "bakery_items",
     "scheduled_for": "mon", "created_days_ago": 19.0, "finished_days_ago": 18.6},
    {"shelter": "tomas@resq.test", "category": "prepared_meals",
     "scheduled_for": "sun", "created_days_ago": 12.0, "finished_days_ago": 11.6},
    {"shelter": "ines@resq.test", "category": "bakery_items",
     "scheduled_for": "tue", "created_days_ago": 6.5, "finished_days_ago": 6.1},
    {"shelter": "ana@resq.test", "category": "prepared_meals",
     "scheduled_for": "thu", "created_days_ago": 3.0, "finished_days_ago": 2.6},
)

# The three items the Miami kitchen has on the board right now, each reached for by two or
# three shelters. This is what "more than one shelter wanted it" looks like on the books,
# and what the rationing pass splits: every claimant is one of the shelters above, so the
# donor's Ongoing Deliveries screen lists real shelters with real rations rather than
# invented ones. The items are written the way a deli actually logs surplus — trays,
# baskets and crates rather than a weight — because reading that text is the first thing
# the pass does. The hours are hours before now, so the fixture keeps its shape whenever
# the server is up.
TEST_ORTEGA_DELIVERIES = (
    {
        "id": "6d2f0c94-1a3b-4e57-8c21-9f4b7a5e0d18",
        "item_name": "Chef's specials and this morning's loaves",
        "quantity": "4 trays of prepared meals and 2 baskets of bread",
        "goods_type": "prepared_meals",
        "posted_hours_ago": 7.0,
        "claimants": (
            {"shelter": "ana@resq.test", "claimed_hours_ago": 5.5,
             "households_waiting": 180, "days_since_last_delivery": 11},
            {"shelter": "ines@resq.test", "claimed_hours_ago": 3.0,
             "households_waiting": 240, "days_since_last_delivery": 6},
            {"shelter": "tomas@resq.test", "claimed_hours_ago": 1.5,
             "households_waiting": 90, "days_since_last_delivery": 2},
        ),
    },
    {
        "id": "b48a1e77-2c69-4b0f-a5d3-7e1c9a6f3b52",
        "item_name": "Pastry counter and cold sandwiches",
        "quantity": "3 trays of sandwiches and 2 boxes of Danishes",
        "goods_type": "bakery_items",
        "posted_hours_ago": 21.0,
        "claimants": (
            {"shelter": "ines@resq.test", "claimed_hours_ago": 18.0,
             "households_waiting": 240, "days_since_last_delivery": 6},
            {"shelter": "tomas@resq.test", "claimed_hours_ago": 12.0,
             "households_waiting": 90, "days_since_last_delivery": 2},
        ),
    },
    {
        "id": "e07c5b31-9f84-4d2a-b6c8-3a5e1d9f7c40",
        "item_name": "Deli counter clear-out",
        "quantity": "2 crates of salad bowls and a tray of roast chicken",
        "goods_type": "prepared_meals",
        "posted_hours_ago": 30.0,
        "claimants": (
            {"shelter": "ana@resq.test", "claimed_hours_ago": 28.0,
             "households_waiting": 180, "days_since_last_delivery": 11},
            {"shelter": "ines@resq.test", "claimed_hours_ago": 25.5,
             "households_waiting": 240, "days_since_last_delivery": 6},
        ),
    },
)

# The other two shelters on the post. They exist only for this one claim, to give the split
# something to split: each sits its own distance from the donor's pin — measured from that
# pin rather than pinned absolutely, so the fixture is plausible wherever the kitchen is —
# and each reports a different household count and wait, which is what moves the scores
# apart.
TEST_CLAIMANTS = (
    {
        "id": 9101,
        "business_name": "Harbour Lights Shelter",
        "street": "12 Harbour Lane", "city": "Toronto", "province": "Ontario",
        "postal": "M5J 1A1",
        "needs": ("prepared_meals", "fresh_produce"),
        "distance_km": 1.4, "bearing": 20,
        "households_waiting": 260, "days_since_last_delivery": 9,
        "claimed_hours_ago": 5.0,
    },
    {
        "id": 9102,
        "business_name": "Riverside Community Pantry",
        "street": "88 Riverside Walk", "city": "Toronto", "province": "Ontario",
        "postal": "M5A 4K8",
        "needs": ("packaged_goods", "household_essentials"),
        "distance_km": 2.6, "bearing": 115,
        "households_waiting": 140, "days_since_last_delivery": 3,
        "claimed_hours_ago": 2.0,
    },
)


def account_address(person):
    """One account's address on a single line, the way the delivery card writes one."""
    return ", ".join(part for part in
                     (person["street"], person["city"], person["province"], person["postal"])
                     if part)


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
        # A donor has no shelter to state an occupancy for, and its name has never been
        # changed, so both are present and empty the way every other account answers them.
        "occupancy": None,
        "business_name_changed_at": None,
        "delivery_address": account_address(donor),
        "delivery_lat": donor["lat"],
        "delivery_lng": donor["lng"],
        "delivery_confirmed_at": datetime.datetime.now(
            datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "demo": True,
    }
    profile.update(figures)
    return profile


def recipient_profile(recipient):
    """The account dict the fixture's recipient signs in as, in the shape the dashboards read.

    A *working* recipient rather than one to be walked through onboarding: the goods it
    asks for, the days it will take a delivery on, and a confirmed pin at its own address,
    which is the pin a delivery is run against. It carries none of the impact figures,
    because those are the donor's own record and a recipient has no such record.
    """
    return {
        "id": 0,
        "role": "recipient",
        "first_name": recipient["first_name"],
        "last_name": recipient["last_name"],
        "business_name": recipient["business_name"],
        "email": recipient["email"],
        "phone": None,
        "dial_code": None,
        "street": recipient["street"],
        "sub_locality": None,
        "locality": None,
        "province": recipient["province"],
        "city": recipient["city"],
        "postal": recipient["postal"],
        "firm_type": None,
        "delivery_days": list(recipient["delivery_days"]),
        "surplus_types": list(recipient["needs"]),
        "occupancy": recipient.get("occupancy"),
        "business_name_changed_at": None,
        "delivery_address": account_address(recipient),
        "delivery_lat": recipient["lat"],
        "delivery_lng": recipient["lng"],
        "delivery_confirmed_at": datetime.datetime.now(
            datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "demo": True,
    }


def remember_profile(profile):
    """Put one fixture account in the demo account store under its email, once."""
    key = clean_email(profile["email"])
    if key in DEMO_ACCOUNTS:
        return None
    DEMO_ACCOUNTS[key] = profile
    return profile


def remember_donor(donor, metrics=None):
    """The same for a test donor."""
    return remember_profile(donor_profile(donor, metrics))


def remember_recipient(recipient):
    """The same for the fixture's recipient."""
    return remember_profile(recipient_profile(recipient))


def store_profile(conn, profile):
    """Write one fixture account into the users table, unless that email is registered.

    The row is built from the profile the account signs in with, so a stored account and a
    demo one answer with the same things. The last three columns are the donor's impact
    figures, which are NULL on the recipient: it has no such record.
    """
    existing = conn.execute(
        "SELECT id FROM users WHERE email = ?", (profile["email"],)
    ).fetchone()
    if existing:
        return None
    digest, salt = hash_password(TEST_PASSWORD)
    cursor = conn.execute(
        """INSERT INTO users (
               role, first_name, last_name, business_name, email, phone, dial_code,
               street, sub_locality, locality, province, city, postal,
               password_hash, password_salt, firm_type, delivery_days, surplus_types,
               occupancy, business_name_changed_at,
               delivery_address, delivery_lat, delivery_lng, delivery_confirmed_at,
               surplus_saved, meals_served, orders_completed
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            profile["role"],
            profile["first_name"],
            profile["last_name"],
            profile.get("business_name"),
            profile["email"],
            profile.get("phone"),
            profile.get("dial_code"),
            profile.get("street"),
            profile.get("sub_locality"),
            profile.get("locality"),
            profile.get("province"),
            profile.get("city"),
            profile.get("postal"),
            digest,
            salt,
            profile.get("firm_type"),
            json.dumps(profile.get("delivery_days") or []),
            json.dumps(profile.get("surplus_types") or []),
            profile.get("occupancy"),
            profile.get("business_name_changed_at"),
            profile.get("delivery_address"),
            profile.get("delivery_lat"),
            profile.get("delivery_lng"),
            profile.get("delivery_confirmed_at"),
            profile.get("surplus_saved"),
            profile.get("meals_served"),
            profile.get("orders_completed"),
        ),
    )
    conn.commit()
    stored = dict(profile)
    stored["id"] = cursor.lastrowid
    return stored


def store_donor(conn, donor, metrics=None):
    """Write a test donor into the users table, unless that email is already registered."""
    return store_profile(conn, donor_profile(donor, metrics))


def store_recipient(conn, recipient):
    """The same for the fixture's recipient."""
    return store_profile(conn, recipient_profile(recipient))


def test_account(email, conn=None):
    """One fixture account by email: out of the demo store, or out of the users table."""
    key = clean_email(email)
    if conn is None:
        return DEMO_ACCOUNTS.get(key)
    row = conn.execute("SELECT * FROM users WHERE email = ?", (key,)).fetchone()
    return dict(row) if row else None


def seed_delivery(conn=None):
    """Put the one agreed delivery on the books, unless it is already there.

    Returns the order's id, or None when that pair and category already had a live one, or
    when either side of it is not in the fixture. Both ends are found by email — from the
    demo store or from the users table — which is the same way a signed-in account finds
    its own deliveries, so a delivery seeded here is one the recipient really can read.
    """
    donor = test_account(TEST_DELIVERY["donor_email"], conn)
    recipient = test_account(TEST_RECIPIENT["email"], conn)
    if donor is None or recipient is None:
        return None

    category = TEST_DELIVERY["category"]
    if conn is None:
        pair = {account_key(donor), account_key(recipient)}
        for entry in DEMO_ORDERS:
            if (entry["order"]["category"] == category
                    and entry["order"]["status"] in LIVE_ORDER_STATUSES
                    and demo_order_pair(entry) == pair):
                return None
    else:
        existing = conn.execute(
            "SELECT id FROM orders WHERE donor_id = ? AND recipient_id = ? AND category = ?"
            " AND status IN (%s)" % ", ".join("?" * len(LIVE_ORDER_STATUSES)),
            (donor["id"], recipient["id"], category) + LIVE_ORDER_STATUSES,
        ).fetchone()
        if existing:
            return None

    distance = round(haversine_km(
        float(donor["delivery_lat"]), float(donor["delivery_lng"]),
        float(recipient["delivery_lat"]), float(recipient["delivery_lng"]),
    ), METRIC_DECIMALS)
    created_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    if conn is None:
        stored = order_row(
            next(DEMO_ORDER_SEQ), donor, recipient, category,
            TEST_DELIVERY["scheduled_for"], distance, created_at,
        )
        # Agreed rather than proposed: the fixture stands for a delivery that has been
        # settled between the two, which is the state a console draws a route for.
        stored["status"] = "accepted"
        order = order_payload(stored, donor, recipient)
        remember_demo_order(order, donor, recipient)
        return order["id"]

    cursor = conn.execute(
        """INSERT INTO orders (
               donor_id, recipient_id, category, status, scheduled_for,
               pickup_address, pickup_lat, pickup_lng,
               dropoff_address, dropoff_lat, dropoff_lng, distance_km, created_at
           ) VALUES (?, ?, ?, 'accepted', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            donor["id"],
            recipient["id"],
            category,
            TEST_DELIVERY["scheduled_for"],
            donor.get("delivery_address"),
            donor["delivery_lat"],
            donor["delivery_lng"],
            recipient.get("delivery_address"),
            recipient["delivery_lat"],
            recipient["delivery_lng"],
            distance,
            created_at,
        ),
    )
    conn.commit()
    return cursor.lastrowid


def is_fixture_recipient(caller, conn=None):
    """Whether this account is the fixture's own recipient.

    The fixture's claimed post is demo data with demo claimants on it, so a rationing pass
    reads it only for the one account it was written for. Anybody else's pass reads the
    claims the board really has — which is what keeps the fixture out of a run that has
    claims of its own.
    """
    email = clean_email((caller or {}).get("email"))
    if not email or email != clean_email(TEST_RECIPIENT["email"]):
        return False
    return test_account(email, conn) is not None


def is_fixture_donor(caller, conn=None):
    """Whether this account is the kitchen the fixture's claimed post belongs to.

    The same rule from the other end: the fixture's post is the one item on the board with
    demo claimants on it, and the account that posted it is the one account that may read
    their names beside their rations. Anybody else's pass reads the claims the board really
    has, which is what keeps the fixture out of a run with claims of its own.
    """
    email = clean_email((caller or {}).get("email"))
    if not email or email != clean_email(TEST_CLAIM["donor_email"]):
        return False
    return test_account(email, conn) is not None


def fixture_time(hours_ago, now):
    """A moment that many hours back, written the way the claim recorder writes one."""
    return (now - datetime.timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def fixture_claim(post, recipient, spec, donor_lat, donor_lng, now):
    """One fixture claim, in the shape a recorded claim is read in.

    The pass reads a claim as a post, the shelter that claimed it, when they claimed it, and
    the distance between the two pins — so the fixture is written in exactly that shape and
    nothing else, which is what makes it a test of the pass rather than of itself.
    """
    distance = round(haversine_km(float(donor_lat), float(donor_lng),
                                 float(recipient["delivery_lat"]),
                                 float(recipient["delivery_lng"])), METRIC_DECIMALS)
    metrics = {
        "households_waiting": spec["households_waiting"],
        "days_since_last_delivery": spec["days_since_last_delivery"],
    }
    return {
        "post": dict(post),
        "recipient": recipient,
        "claimed_at": fixture_time(spec["claimed_hours_ago"], now),
        "metrics": metrics,
        "donor_lat": donor_lat,
        "donor_lng": donor_lng,
        "distance_km": distance,
    }


def claim_fixture(conn=None, now=None):
    """The fixture's one claimed post, as the claims a rationing pass reads.

    Returns an empty list when the fixture's accounts are not there, which is every run that
    seeded nothing: the claim needs a kitchen to post it and a shelter to claim it. The two
    extra shelters are built here rather than registered, because they exist for this claim
    alone — each one placed its own measured distance from the donor's pin, so the fixture
    reads plausibly wherever that kitchen is.
    """
    donor = test_account(TEST_CLAIM["donor_email"], conn)
    recipient = test_account(TEST_RECIPIENT["email"], conn)
    if donor is None or recipient is None:
        return []
    donor_lat = donor.get("delivery_lat")
    donor_lng = donor.get("delivery_lng")
    if donor_lat is None or donor_lng is None or recipient.get("delivery_lat") is None:
        return []

    now = now or datetime.datetime.now(datetime.timezone.utc)
    post = {
        "id": TEST_CLAIM["id"],
        "donor_name": donor.get("business_name"),
        "item_name": TEST_CLAIM["item_name"],
        "quantity": TEST_CLAIM["quantity"],
        "location": donor.get("delivery_address") or account_address(donor),
        "goods_type": TEST_CLAIM["goods_type"],
        "created_at": fixture_time(TEST_CLAIM["posted_hours_ago"], now),
    }

    claims = [fixture_claim(post, recipient, TEST_RECIPIENT_CLAIM, donor_lat, donor_lng, now)]
    for claimant in TEST_CLAIMANTS:
        lat, lng = offset_point(donor_lat, donor_lng,
                                claimant["distance_km"], claimant["bearing"])
        shelter = {
            "id": claimant["id"],
            "role": "recipient",
            "first_name": None,
            "last_name": None,
            "business_name": claimant["business_name"],
            "email": None,
            "phone": None,
            "street": claimant["street"],
            "city": claimant["city"],
            "province": claimant["province"],
            "postal": claimant["postal"],
            "surplus_types": list(claimant["needs"]),
            "delivery_days": [],
            "delivery_address": account_address(claimant),
            "delivery_lat": lat,
            "delivery_lng": lng,
            "delivery_confirmed_at": None,
            "demo": True,
        }
        claims.append(fixture_claim(post, shelter, claimant, donor_lat, donor_lng, now))
    return claims


def donor_log_entries(donor, days):
    """One kitchen's logged month, split across the categories it handles.

    The log the analyser charts is not a second record of the kitchen: its kilos are that
    kitchen's *own* logged surplus — the rows beside it in `surplus_history` — split across
    the goods it says it handles, so what the month's chart adds up to is what the kitchen
    actually gave away. The split is drawn from the donor's own seed, which is what makes
    the month the same one every time the fixture is seeded again.

    Only the window the chart reads is written: a year of days would be a year of entries
    nothing looks at, and the log is the kitchen's *month*.
    """
    rng = random.Random(donor["seed"] + 11)
    categories = list(donor.get("surplus_types") or ())
    entries = []
    for row in days[-SURPLUS_CATEGORIES_DAYS:]:
        total = float(row.get("surplus_kg") or 0.0)
        if not categories or total <= 0:
            continue
        weights = [0.5 + rng.random() for _ in categories]
        shares = [round(total * weight / sum(weights), 2) for weight in weights]
        # The last share carries the rounding, so the day's categories add up to the day.
        shares[-1] = round(total - sum(shares[:-1]), 2)
        for category, kg in zip(categories, shares):
            if kg <= 0:
                continue
            entries.append({
                "entry_key": entry_key_day(row["date"], category),
                "logged_on": row["date"],
                "goods_type": category,
                "item_name": None,
                "kg": kg,
                "source": "logged",
            })
    return entries


def fixture_shelters():
    """The three shelters on Ortega Deli's books, each with its pin placed off the deli's."""
    donor = next(donor for donor in TEST_DONORS if donor["email"] == TEST_KITCHEN_EMAIL)
    shelters = []
    for spec in TEST_SHELTERS:
        lat, lng = offset_point(donor["lat"], donor["lng"],
                                spec["distance_km"], spec["bearing"])
        shelters.append(dict(spec, lat=lat, lng=lng))
    return shelters


def is_miami_kitchen(caller, conn=None):
    """Whether this account is the fixture's Miami kitchen, Ortega Deli.

    Its three open deliveries are demo data with demo shelters on them, so a rationing
    pass reads them only for an account they were written for: that kitchen's own, or one
    of the shelters that claimed them. Anybody else's pass reads the claims the board
    really has, which is what keeps the fixture out of a run that has claims of its own.
    """
    email = clean_email((caller or {}).get("email"))
    if not email or email != clean_email(TEST_KITCHEN_EMAIL):
        return False
    return test_account(email, conn) is not None


def is_miami_shelter(caller, conn=None):
    """Whether this account is one of the three shelters on the fixture kitchen's books.

    A shelter that is one of them reads the same three deliveries the kitchen does — the
    pass needs every claimant of a post to split it — and its own ration is what comes
    back, because a pass narrows its answer to the account that asked.
    """
    email = clean_email((caller or {}).get("email"))
    if not email:
        return False
    if not any(clean_email(spec["email"]) == email for spec in TEST_SHELTERS):
        return False
    return test_account(email, conn) is not None


def fixture_deliveries(conn=None, now=None):
    """The fixture's three open deliveries, as the claims a rationing pass reads.

    Each is one of Ortega Deli's items with the two or three shelters that reached for it,
    written in exactly the shape a recorded claim is read in — which is what makes the
    donor's Ongoing Deliveries screen a reading of the pass rather than of itself. Returns
    an empty list when the kitchen or the shelters are not there, which is every run that
    seeded nothing.
    """
    donor = test_account(TEST_KITCHEN_EMAIL, conn)
    if donor is None:
        return []
    donor_lat = donor.get("delivery_lat")
    donor_lng = donor.get("delivery_lng")
    if donor_lat is None or donor_lng is None:
        return []

    now = now or datetime.datetime.now(datetime.timezone.utc)
    claims = []
    for spec in TEST_ORTEGA_DELIVERIES:
        post = {
            "id": spec["id"],
            "donor_name": donor.get("business_name"),
            "item_name": spec["item_name"],
            "quantity": spec["quantity"],
            "location": donor.get("delivery_address") or account_address(donor),
            "goods_type": spec["goods_type"],
            "created_at": fixture_time(spec["posted_hours_ago"], now),
        }
        for claimant in spec["claimants"]:
            shelter = test_account(claimant["shelter"], conn)
            if shelter is None:
                continue
            claims.append(fixture_claim(post, shelter, claimant, donor_lat, donor_lng, now))
    return claims


def seed_ortega_orders(conn=None, now=None):
    """Put the Miami kitchen's five finished deliveries on the books, unless they are there.

    Returns the ids it wrote, oldest first. A pair already carrying a finished order under
    the same category is left alone, so a second seeding adds nothing: a kitchen's history
    is a record, and a record does not change because a seed ran again.
    """
    now = now or datetime.datetime.now(datetime.timezone.utc)
    donor = test_account(TEST_KITCHEN_EMAIL, conn)
    if donor is None:
        return []

    written = []
    for spec in TEST_ORTEGA_ORDERS:
        recipient = test_account(spec["shelter"], conn)
        if recipient is None:
            continue
        category = spec["category"]
        created_at = (now - datetime.timedelta(days=spec["created_days_ago"])) \
            .strftime("%Y-%m-%d %H:%M:%S")
        completed_at = (now - datetime.timedelta(days=spec["finished_days_ago"])) \
            .strftime("%Y-%m-%d %H:%M:%S")
        distance = round(haversine_km(
            float(donor["delivery_lat"]), float(donor["delivery_lng"]),
            float(recipient["delivery_lat"]), float(recipient["delivery_lng"]),
        ), METRIC_DECIMALS)

        if conn is None:
            pair = {account_key(donor), account_key(recipient)}
            already = any(
                entry["order"]["category"] == category
                and entry["order"]["status"] == FINISHED_ORDER_STATUS
                and demo_order_pair(entry) == pair
                for entry in DEMO_ORDERS
            )
            if already:
                continue
            stored = order_row(next(DEMO_ORDER_SEQ), donor, recipient, category,
                               spec["scheduled_for"], distance, created_at,
                               status=FINISHED_ORDER_STATUS, completed_at=completed_at)
            order = order_payload(stored, donor, recipient)
            remember_demo_order(order, donor, recipient)
            written.append(order["id"])
            continue

        existing = conn.execute(
            "SELECT id FROM orders WHERE donor_id = ? AND recipient_id = ?"
            " AND category = ? AND status = ?",
            (donor["id"], recipient["id"], category, FINISHED_ORDER_STATUS),
        ).fetchone()
        if existing:
            continue
        cursor = conn.execute(
            """INSERT INTO orders (
                   donor_id, recipient_id, category, status, scheduled_for,
                   pickup_address, pickup_lat, pickup_lng,
                   dropoff_address, dropoff_lat, dropoff_lng, distance_km,
                   created_at, completed_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                donor["id"], recipient["id"], category, FINISHED_ORDER_STATUS,
                spec["scheduled_for"],
                donor.get("delivery_address"), donor["delivery_lat"], donor["delivery_lng"],
                recipient.get("delivery_address"), recipient["delivery_lat"],
                recipient["delivery_lng"],
                distance, created_at, completed_at,
            ),
        )
        conn.commit()
        written.append(cursor.lastrowid)
    return written


def seed_test_accounts(conn=None, days=SEED_HISTORY_DAYS, today=None):
    """Create the fixture's accounts, their logs and their one delivery, as far as they are
    not there already.

    `days` is how far back the five short logs go; a kitchen that carries a window of its
    own — the demo account's year — is logged that far back instead, since one request to
    the archive answers a year as easily as six weeks.

    Returns what it did: which accounts were created, which were already registered, how
    many days were written, where the weather came from and whether the agreed delivery
    had to be written. Called once at startup in demo mode and with `RESQ_SEED=on` in
    persistent mode, and safe to call again — an account that exists is skipped, a day
    already logged is left alone, and a delivery already on the books is not doubled.
    """
    today = today or datetime.date.today()
    created = []
    skipped = []
    days_written = 0
    log_written = 0
    archived = 0
    patterned = 0

    for donor in FIXTURE_DONORS:
        # The account comes first: a donor already registered is skipped before any day is
        # generated, which is what keeps a second startup from asking the archive anything
        # about kitchens it has already written.
        account = remember_donor(donor) if conn is None else store_donor(conn, donor)
        if account is None:
            skipped.append(donor["email"])
            continue

        # A kitchen's own window: six weeks for the five short logs, a year for the demo
        # account, which carries `history_days` of its own.
        rows = donor_days(donor, days=donor.get("history_days", days), today=today)
        if any(row["weather_code"] is not None for row in rows):
            archived += 1
        else:
            patterned += 1
        created.append(donor["email"])
        days_written += write_history(rows, account, conn)
        # The same kitchen's logged month goes into the surplus log beside the history, so
        # the analyser's category chart has a past of its own — one that stays when the
        # board the month's posts were made on is cleared.
        log_written += write_entries(donor_log_entries(donor, rows), account, conn)

    # The other side of the board, and the one delivery between the two. The recipient is
    # written after the donors: the delivery needs a kitchen to run from, and that kitchen
    # is one of them.
    recipient = (remember_recipient(TEST_RECIPIENT) if conn is None
                 else store_recipient(conn, TEST_RECIPIENT))
    if recipient is None:
        # Already registered, counted the way a donor already registered is counted, so the
        # report adds up to the accounts that are there.
        skipped.append(TEST_RECIPIENT["email"])

    # The Miami kitchen's own books: the three shelters its deliveries run to, and the five
    # deliveries that have already gone out to them. Written last, because the orders need
    # both a kitchen and a shelter to run between.
    shelters_created = []
    for shelter in fixture_shelters():
        account = (remember_recipient(shelter) if conn is None
                   else store_recipient(conn, shelter))
        if account is None:
            skipped.append(shelter["email"])
        else:
            shelters_created.append(shelter["email"])

    return {
        "created": created,
        "recipient_created": TEST_RECIPIENT["email"] if recipient else None,
        "shelters_created": shelters_created,
        "orders_written": seed_ortega_orders(conn),
        "orders_for": TEST_KITCHEN_EMAIL,
        "skipped": skipped,
        "days_written": days_written,
        "log_written": log_written,
        "delivery": seed_delivery(conn),
        "delivery_from": TEST_DELIVERY["donor_email"],
        "delivery_to": TEST_RECIPIENT["email"],
        "weather_from_archive": archived,
        "weather_from_pattern": patterned,
        "password": TEST_PASSWORD,
    }


def describe_test_accounts():
    """The fixture accounts as the README lists them: who they are and where they are."""
    return [
        {
            "email": donor["email"],
            "business": donor["business_name"],
            "address": account_address(donor),
            "customers_per_day": donor["customers"],
        }
        for donor in FIXTURE_DONORS
    ]
