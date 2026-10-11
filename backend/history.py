"""Res-Q backend surplus history.

One row per kitchen per day: what that kitchen served, what the sky over it was, and how
much surplus came out of the day. It is the donor's own log — the record a device never
has to keep twice, because the same rows are what the analyser's model reads its past
from. The three surplus lags and the expected customer count a forecast needs are
*derived* from these rows, so the model's own defaults are what a kitchen with no history
falls back to rather than what every kitchen is answered with.

Rows are read oldest first, because that is the order they happened in and the order the
derivation walks them in; the API answers newest first, the way every other list here
does. Dates are plain ISO days — a day is a day, whatever timezone it was logged in — and
every lookup is by date, so a gap in the log is a gap rather than a shift.
"""

import datetime

from config import DEMO_HISTORY, HISTORY_FIELDS
from helpers import contact_key
from surplus import ROLLING_WINDOW_DAYS


def demo_history_key(user):
    """The key a demo account's rows are filed under: its email, or failing that its phone.

    The same key the rest of the demo stores file an account's own things by — its logged
    days, and the deliveries it is one side of — which is what lets a second process-less
    store be read by the same account without a second notion of who that account is.
    """
    return contact_key(user)


def read_history(user, conn=None):
    """Every stored day for this account, oldest first.

    `conn` is what tells the modes apart: demo mode passes none and the rows come from the
    process's own store, while persistence passes the connection the account was read with
    and the rows are that account's own rows in the table.
    """
    if not user:
        return []
    if conn is None:
        key = demo_history_key(user)
        return [dict(row) for row in DEMO_HISTORY.get(key, [])]
    rows = conn.execute(
        "SELECT %s FROM surplus_history WHERE user_id = ? ORDER BY date"
        % ", ".join(HISTORY_FIELDS),
        (user["id"],),
    ).fetchall()
    return [dict(row) for row in rows]


def write_history(days, user, conn=None):
    """Store a kitchen's days, oldest first, skipping any date already logged.

    Returns how many rows were written. A day already in the log is left alone rather than
    overwritten, which is what makes seeding a second time a no-op: a kitchen's past is a
    record, and a record does not change because a seed ran again.
    """
    if not user or not days:
        return 0
    if conn is None:
        key = demo_history_key(user)
        if not key:
            return 0
        stored = DEMO_HISTORY.setdefault(key, [])
        known = {row["date"] for row in stored}
        fresh = [row for row in days if row["date"] not in known]
        stored.extend(fresh)
        stored.sort(key=lambda row: row["date"])
        return len(fresh)

    columns = ", ".join(HISTORY_FIELDS)
    placeholders = ", ".join("?" for _ in HISTORY_FIELDS)
    written = 0
    for day in days:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO surplus_history (user_id, %s) VALUES (?, %s)"
            % (columns, placeholders),
            (user["id"],) + tuple(day.get(field) for field in HISTORY_FIELDS),
        )
        written += cursor.rowcount
    conn.commit()
    return written


def as_day(value):
    """An ISO day, however the row carries it; None when it is not one."""
    if isinstance(value, datetime.date):
        return value
    try:
        return datetime.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def features_from_history(days, on_date=None):
    """The model's own past, read out of a kitchen's logged days.

    Every feature is looked up **before** the day being forecast, which is the window the
    training data built them in: yesterday's surplus, the same weekday's surplus a week
    ago, the mean of the fourteen days before it, and the mean customer count that
    weekday has actually run at. A feature the log cannot answer comes back as None — a
    day that has not happened yet has no yesterday to read, and a gap in the log is a
    gap — so the caller can leave that one to the model's imputer instead of feeding it a
    zero dressed up as a reading.
    """
    target = on_date or datetime.date.today()
    by_date = {}
    for row in days:
        day = as_day(row.get("date"))
        if day is not None:
            by_date[day] = row

    def surplus_on(day):
        row = by_date.get(day)
        if row is None:
            return None
        value = row.get("surplus_kg")
        return None if value is None else round(float(value), 2)

    window = [surplus_on(target - datetime.timedelta(days=back))
              for back in range(1, ROLLING_WINDOW_DAYS + 1)]
    window = [value for value in window if value is not None]

    same_weekday = [
        float(row["customers_served"])
        for day, row in by_date.items()
        if day < target and day.weekday() == target.weekday() and row.get("customers_served") is not None
    ]

    return {
        "expected_customers": round(sum(same_weekday) / len(same_weekday), 1) if same_weekday else None,
        "surplus_yesterday": surplus_on(target - datetime.timedelta(days=1)),
        "surplus_last_week": surplus_on(target - datetime.timedelta(days=7)),
        "surplus_rolling_14": round(sum(window) / len(window), 2) if window else None,
    }


def history_summary(days):
    """What a log holds, in the figures the endpoint answers with beside the rows."""
    if not days:
        return {"days": 0, "first_date": None, "last_date": None,
                "average_surplus_kg": None, "average_customers": None, "weather": {}}
    surplus = [float(row["surplus_kg"]) for row in days if row.get("surplus_kg") is not None]
    customers = [float(row["customers_served"]) for row in days
                 if row.get("customers_served") is not None]
    weather = {}
    for row in days:
        label = row.get("weather")
        if label:
            weather[label] = weather.get(label, 0) + 1
    return {
        "days": len(days),
        "first_date": days[0].get("date"),
        "last_date": days[-1].get("date"),
        "average_surplus_kg": round(sum(surplus) / len(surplus), 2) if surplus else None,
        "average_customers": round(sum(customers) / len(customers), 1) if customers else None,
        "weather": weather,
    }
