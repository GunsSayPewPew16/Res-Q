"""Res-Q backend surplus log.

One row per donation out of a kitchen: the board post it came from, or the logged day it
stands for, what the goods were, and what they came to in kilos. It is what the analyser's
category mix is read from rather than the board itself, because the board is a marketplace
while a log is a record: a post that has been claimed, or cleared off the board entirely,
is still something the kitchen gave away, and a month's chart cannot go blank because the
shelf beside it did.

A row's `source` says how its kilos were arrived at, and there are two readings:

- a **board row** the page handed over, weighed by the pass's own reading of the words its
  donor wrote — `text` when a pack was named, `assumed` when the pack table had to stand
  in for one that was not;
- a **logged day** the fixture seeds, whose kilos are the kitchen's own logged surplus
  split across the categories that kitchen handles — `logged`.

Every row is filed once and only once. `entry_key` is what a row is filed under — the
post's id, or a seeded day and category — so reading the same board again adds nothing,
and a seed that runs again leaves a kitchen's month alone. Only the account's own rows are
ever filed. In demo mode the rows live in the process's own store, keyed by contact the
way the other demo stores are; with persistence they are the `surplus_log` table's own.
"""

import datetime

from config import DEMO_SURPLUS_LOG, SURPLUS_LOG_FIELDS
from helpers import contact_key
from history import as_day

# The longest a stored item name is kept. A board row's name is the shop's own words and
# the log is a record of them, not a place for a paragraph.
MAX_ITEM_CHARS = 80


def entry_key_post(row):
    """The key a board row is filed under: its own id, or failing that its own words.

    The id is the board's own, and one post is one donation however often a page reads the
    table — but a row that arrived without one is still filed, under the words that make it
    what it is, so the same row read twice is still one entry.
    """
    post_id = str(row.get("id") or "").strip()
    if post_id:
        return "post:" + post_id
    return "post:%s|%s|%s" % (
        str(row.get("created_at") or "").strip(),
        str(row.get("item_name") or "").strip().lower(),
        str(row.get("quantity") or "").strip().lower(),
    )


def entry_key_day(day, goods_type):
    """The key a seeded day is filed under: the day, and the category it was split to."""
    return "day:%s|%s" % (str(day), goods_type or "")


def entry_from_post(row, kg, source, today=None):
    """One entry, read out of a board row the page handed over.

    `kg` and `source` are the pass's own reading of the row's written quantity, handed in
    rather than read again here, so the log and a split cannot disagree about what an item
    was worth. The day is the row's own created date; a board row that carries no readable
    date is filed under today, because a donation the board did not stamp is no reason to
    drop it out of the account's month.
    """
    goods_type = (row.get("goods_type") or "").strip().lower() or None
    day = as_day(row.get("created_at")) or (today or datetime.date.today())
    return {
        "entry_key": entry_key_post(row),
        "logged_on": day.isoformat(),
        "goods_type": goods_type,
        "item_name": (row.get("item_name") or "").strip()[:MAX_ITEM_CHARS] or None,
        "kg": kg,
        "source": source,
    }


def demo_log_key(user):
    """The key a demo account's entries are filed under: its email, or failing that its phone.

    The same key every other demo store files an account's own things by — its logged days,
    its orders, its claims — which is what lets a process with no database read back the
    month it just filed without a second notion of who that account is.
    """
    return contact_key(user)


def read_entries(user, conn=None):
    """Every stored entry for this account, oldest first.

    `conn` is what tells the modes apart, the way it does for the logged days: demo mode
    passes none and the rows come from the process's own store, while persistence passes
    the connection and the rows are that account's own rows in the table.
    """
    if not user:
        return []
    if conn is None:
        key = demo_log_key(user)
        return [dict(entry) for entry in DEMO_SURPLUS_LOG.get(key, [])]
    rows = conn.execute(
        "SELECT %s FROM surplus_log WHERE user_id = ? ORDER BY logged_on, entry_key"
        % ", ".join(SURPLUS_LOG_FIELDS),
        (user["id"],),
    ).fetchall()
    return [dict(row) for row in rows]


def write_entries(entries, user, conn=None):
    """File a kitchen's entries, skipping any already stored. Returns how many were written.

    An entry already in the log is left alone rather than overwritten: what a kitchen gave
    away does not change because a screen was reopened or a seed ran again. Only the fields
    the log knows are kept, so a caller cannot write a shape the table would not hold.
    """
    if not user or not entries:
        return 0
    clean = [{field: entry.get(field) for field in SURPLUS_LOG_FIELDS} for entry in entries]
    if conn is None:
        key = demo_log_key(user)
        if not key:
            return 0
        stored = DEMO_SURPLUS_LOG.setdefault(key, [])
        known = {row["entry_key"] for row in stored}
        fresh = [entry for entry in clean if entry["entry_key"] not in known]
        stored.extend(fresh)
        stored.sort(key=lambda row: (str(row["logged_on"]), str(row["entry_key"])))
        return len(fresh)

    written = 0
    for entry in clean:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO surplus_log (user_id, %s) VALUES (?, %s)"
            % (", ".join(SURPLUS_LOG_FIELDS),
               ", ".join("?" for _ in SURPLUS_LOG_FIELDS)),
            (user["id"],) + tuple(entry.get(field) for field in SURPLUS_LOG_FIELDS),
        )
        written += cursor.rowcount
    conn.commit()
    return written


def within_days(entries, days, today=None):
    """The entries logged in the last `days` days, oldest first.

    A calendar window rather than an hours-older-than test: the log is filed by day, and a
    day is a day whatever hour it was written in. An entry whose date cannot be read is
    counted rather than dropped — what is being read is what the kitchen gave away, and a
    date the log did not write is no reason to hide it.
    """
    today = today or datetime.date.today()
    oldest = today - datetime.timedelta(days=days - 1)
    window = []
    for entry in entries:
        day = as_day(entry.get("logged_on"))
        if day is None or day >= oldest:
            window.append(entry)
    window.sort(key=lambda row: (str(row.get("logged_on")), str(row.get("entry_key"))))
    return window
