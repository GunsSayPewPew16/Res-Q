"""Res-Q claims: which shelter reached for which surplus post.

A claim is the board's own record of an item being spoken for. The board keeps the thing
the feed needs — a post's status, so it stops being claimable — and nothing about *who*
reached for it, which is what the rationing pass needs when more than one shelter wanted the
same item, and what a shelter needs to see its own ration of it. So the claim is recorded
here, in the API, where the rest of this project's state already lives:

    POST /api/claims     record the caller's claim on one board row
    GET  /api/claims     the caller's own claims, newest first

One record per claim, in **one shape** — the board row that was claimed, and the shelter
that claimed it — kept whole rather than spread across columns, because four readers share
it: the API that writes it, the standalone recorder that used to, the rationing pass that
reads it, and the dashboard that shows what is on the books. What the store adds is what a
query needs: the post it joins on, when it happened, and the claimant, so one shelter
cannot claim one post twice.

In demo mode the records live for the process (`config.DEMO_CLAIMS`); with persistence on
they are the `claims` table's own rows, which is also where that one-claim-per-post rule is
enforced. `claim_records.json` is still read, so a run that used the standalone recorder
keeps the claims it recorded before the API took the job over — and now that the API can be
the one recording them, a deployment records claims that survive as long as its accounts do.
"""

import datetime
import json
import uuid

from config import (
    CLAIM_POST_FIELDS,
    CLAIM_RECIPIENT_FIELDS,
    CLAIM_RECORDS_PATH,
    DEMO_CLAIMS,
)
from helpers import account_key


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc)


def format_time(moment):
    """A moment as a claim writes it: UTC, to the second, with a `Z`."""
    return moment.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def stamp():
    """Now, in the shape a claim carries it: what a claim made this second is stamped with."""
    return format_time(utcnow())


def pick(source, allowed):
    """The listed fields of a row, and nothing else: a claim carries what it may carry."""
    row = source if isinstance(source, dict) else {}
    return {field: row.get(field) for field in allowed if field in row}


def record_from_post(post, recipient, claimed_at=None):
    """One claim, in the shape every reader of a claim shares.

    The post arrives from the board — the page sends the row it claimed — and the shelter is
    the signed-in account, copied field by field from its own profile rather than taken from
    the request, so a claim cannot be filed against somebody else's name.
    """
    return {
        "record_id": uuid.uuid4().hex,
        "claimed_at": claimed_at or format_time(utcnow()),
        "post": pick(post, CLAIM_POST_FIELDS),
        "recipient": pick(recipient, CLAIM_RECIPIENT_FIELDS) or None,
    }


def held_already(post_id, claimant_key, conn=None):
    """The claim this shelter already has on this post, or None."""
    if not post_id or not claimant_key:
        return None
    if conn is None:
        for held in DEMO_CLAIMS:
            if (held["post"]["id"] == post_id
                    and account_key(held.get("recipient")) == claimant_key):
                return held
        return None
    found = conn.execute(
        "SELECT payload FROM claims WHERE post_id = ? AND claimant_key = ?",
        (post_id, claimant_key),
    ).fetchone()
    return decode_payload(found["payload"]) if found else None


def remember(record, conn=None):
    """File one claim, unless that shelter has already claimed that post.

    The replay answers with the record already saved rather than a second one: a claim is a
    thing that happened once, and a second copy of it would double a claimant's share of the
    post the rationing pass is about to split.
    """
    claimant_key = account_key(record.get("recipient"))
    existing = held_already((record.get("post") or {}).get("id"), claimant_key, conn)
    if existing is not None:
        return existing
    if conn is None:
        DEMO_CLAIMS.append(record)
        return record

    # `INSERT OR IGNORE` against the table's own unique key: two requests arriving at once
    # cannot both win, and the loser reads back the winner's record.
    conn.execute(
        "INSERT OR IGNORE INTO claims"
        " (record_id, post_id, claimant_key, claimed_at, payload)"
        " VALUES (?, ?, ?, ?, ?)",
        (
            record["record_id"],
            record["post"]["id"],
            claimant_key,
            record["claimed_at"],
            json.dumps(record, ensure_ascii=False),
        ),
    )
    conn.commit()
    return held_already(record["post"]["id"], claimant_key, conn) or record


def decode_payload(text):
    """One stored record back out of its JSON, or None when it is not one."""
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def file_records(path=None):
    """The claims the standalone recorder wrote, oldest first, or none when there is no file.

    Kept as a source rather than replaced by the table: a run that recorded claims before the
    API could keeps them, which is the difference between moving where claims are recorded
    and losing the ones already recorded.
    """
    try:
        with open(path or CLAIM_RECORDS_PATH, "r", encoding="utf-8") as handle:
            parsed = json.load(handle)
    except (OSError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    return [record for record in parsed if isinstance(record, dict)]


def stored_records(conn=None):
    """Every claim this run knows about, oldest first, from the store the mode keeps."""
    if conn is None:
        return list(DEMO_CLAIMS) + file_records()

    rows = conn.execute(
        "SELECT payload FROM claims ORDER BY created_at, rowid"
    ).fetchall()
    records = []
    for row in rows:
        record = decode_payload(row["payload"])
        if record is not None:
            records.append(record)
    return records


def caller_claims(caller, conn=None):
    """The claims one account made, oldest first, found by the key a claim is filed under."""
    key = account_key(caller)
    if not key:
        return []
    return [record for record in stored_records(conn)
            if account_key(record.get("recipient")) == key]
