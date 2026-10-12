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
    DEMO_CLOSURES,
    METRIC_DECIMALS,
)
from geo import haversine_km
from helpers import account_key, display_name


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


# ---------------------------------------------------------------------------
# Closing a post
# ---------------------------------------------------------------------------
# A claim says "this shelter wants this item". It deliberately does not say "and here is
# their share of it": a share is a fraction of what *every* claimant asked for, and it is
# the donor's own hand that ends the asking. Closing the post is that hand — the moment the
# claims on an item stop being open to more shelters and start being the ration the pass
# works out — so the closure is filed here, beside the claims it closes, and the rationing
# pass reads the two together.


def closure_from_post(post, donor, closed_at=None):
    """One closed post, in the shape every reader of a closure shares.

    The post id is what a closure is joined on; the name is the account that closed it, and
    it is the donor's own business name rather than anything the caller sent, so a closure
    cannot be filed as somebody else's.
    """
    row = post if isinstance(post, dict) else {}
    return {
        "post_id": str(row.get("id") or "").strip(),
        "item_name": row.get("item_name"),
        "donor_name": donor,
        "closed_at": closed_at or format_time(utcnow()),
    }


def closure_for(post_id, conn=None):
    """The closure on one post, or None while the post is still open."""
    wanted = str(post_id or "").strip()
    if not wanted:
        return None
    if conn is None:
        for held in DEMO_CLOSURES:
            if held.get("post_id") == wanted:
                return held
        return None
    found = conn.execute(
        "SELECT payload FROM closures WHERE post_id = ?", (wanted,)
    ).fetchone()
    return decode_payload(found["payload"]) if found else None


def remember_closure(record, conn=None):
    """File one closure, unless that post is closed already.

    The replay answers with the closure already saved rather than a second one: a post
    closes once, and a second close would rewrite the moment the ration was worked out —
    which is the one thing about it a shelter reads back.
    """
    existing = closure_for(record.get("post_id"), conn)
    if existing is not None:
        return existing
    if conn is None:
        DEMO_CLOSURES.append(record)
        return record

    # `INSERT OR IGNORE` against the table's own key: two closes arriving at once cannot
    # both win, and the loser reads back the winner's closure.
    conn.execute(
        "INSERT OR IGNORE INTO closures (post_id, donor_name, closed_at, payload)"
        " VALUES (?, ?, ?, ?)",
        (
            record["post_id"],
            record.get("donor_name"),
            record["closed_at"],
            json.dumps(record, ensure_ascii=False),
        ),
    )
    conn.commit()
    return closure_for(record["post_id"], conn) or record


def stored_closures(conn=None):
    """Every closure this run knows about, oldest first, from the store the mode keeps."""
    if conn is None:
        return list(DEMO_CLOSURES)
    rows = conn.execute(
        "SELECT payload FROM closures ORDER BY closed_at, rowid"
    ).fetchall()
    closures = []
    for row in rows:
        record = decode_payload(row["payload"])
        if record is not None:
            closures.append(record)
    return closures


def closures_by_post(conn=None):
    """The closures as a mapping from post id to the moment it closed.

    The shape the rationing pass reads: it has the claims, and this is what tells it which
    of the posts those claims are on have been rationed already.
    """
    return {closure["post_id"]: closure.get("closed_at")
            for closure in stored_closures(conn)
            if closure.get("post_id")}


def is_closed(post_id, conn=None):
    """Whether this post has been closed by its donor."""
    return closure_for(post_id, conn) is not None


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


def claims_on_posts(donor_name, conn=None):
    """The claims other shelters have made on the posts one donor logged, oldest first.

    The other side of `caller_claims`, and it is found the other way round: a shelter is
    the key its own claims are filed under, while a post is identified by the name on it.
    That is the only join there is — the board is Supabase's and keeps no donor id, so the
    `donor_name` a claim carries on the post row it recorded is the one thing tying a
    claimed item back to the kitchen that posted it. A donor's claimants are therefore
    found by the same name its own posts are, which is how the board's own *My posts* read
    finds them too.
    """
    wanted = str(donor_name or "").strip()
    if not wanted:
        return []
    return [record for record in stored_records(conn)
            if str((record.get("post") or {}).get("donor_name") or "").strip() == wanted]


def delivery_point(user):
    """An account's own confirmed pin as a pair of numbers, or None when it has none."""
    account = user or {}
    try:
        return (float(account.get("delivery_lat")), float(account.get("delivery_lng")))
    except (TypeError, ValueError):
        return None


def claimed_posts(records, donor):
    """The claims on one donor's posts, gathered item by item, newest item first.

    One entry per claimed post, each carrying the shelters that reached for it in the order
    a round would serve them — nearest first, because this is read beside a map and the
    nearest shelter is the one a donor is most likely to look at. A claimant that has
    confirmed no location is kept rather than dropped: it is still a shelter holding a
    claim, so it stays in the list with no pin and no distance for the map to draw.

    The distance is measured between the two pins the delivery really runs between — the
    caller's own pin and the claimant's — and is left unread rather than guessed at when
    either end has no coordinates.
    """
    pin = delivery_point(donor)
    posts = []
    index = {}
    for record in records:
        post = record.get("post") or {}
        post_id = str(post.get("id") or "").strip()
        if not post_id:
            continue
        entry = index.get(post_id)
        if entry is None:
            entry = {
                "post_id": post_id,
                "item_name": post.get("item_name"),
                "quantity": post.get("quantity"),
                "location": post.get("location"),
                "goods_type": post.get("goods_type"),
                "created_at": post.get("created_at"),
                "claimants": [],
            }
            index[post_id] = entry
            posts.append(entry)

        claimant = record.get("recipient") or {}
        at = delivery_point(claimant)
        entry["claimants"].append({
            "name": display_name(claimant) if claimant else None,
            "address": claimant.get("delivery_address"),
            "city": claimant.get("city"),
            "lat": at[0] if at else None,
            "lng": at[1] if at else None,
            "claimed_at": record.get("claimed_at"),
            "distance_km": (round(haversine_km(pin[0], pin[1], at[0], at[1]), METRIC_DECIMALS)
                            if (pin and at) else None),
        })

    posts.sort(key=lambda entry: str(entry.get("created_at") or ""), reverse=True)
    for entry in posts:
        entry["claimants"].sort(key=lambda claim: (
            claim["distance_km"] is None,
            claim["distance_km"] or 0.0,
            str(claim["claimed_at"] or ""),
        ))
    return posts
