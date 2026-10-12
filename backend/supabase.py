"""Publishing a ration back to the board.

The board is Supabase's and this backend is not its owner: the pages read it, log posts to
it and claim them directly with the publishable key. The one thing the API adds to it is the
allocation the rationing pass works out — how much of an item a shelter will actually get —
and publishing that is what puts the number in front of both sides of the delivery, on the
row they are already looking at, instead of only inside the console that asked for it.

The write is a single PATCH against one row, which is small enough that it wants nothing but
`urllib`: no SDK, no new dependency in a backend whose only requirements are the server it
runs on and the surplus calculator's two libraries.

Nothing here runs unless the project is configured — `RESQ_SUPABASE_URL` and
`RESQ_SUPABASE_KEY` — and nothing raises. A board that cannot be written to answers
`(False, reason)`, and the endpoint that called it reports that per post rather than failing
the whole request, because a ration that was computed and not published is still a ration
the console can show. The key travels in one header, is never logged or echoed, and a
failure reports the board's own message with the key stripped out of it.
"""

import json
import urllib.error
import urllib.parse
import urllib.request

from config import (
    SUPABASE_KEY,
    SUPABASE_TABLE,
    SUPABASE_TIMEOUT_SECONDS,
    SUPABASE_URL,
)


def configured():
    """Whether a project is set up to be written to at all."""
    return bool(SUPABASE_URL and SUPABASE_KEY)


def describe():
    """How the board was reached, for an answer to say out loud."""
    return {
        "mode": "supabase" if configured() else "unconfigured",
        "project": SUPABASE_URL or None,
        "table": SUPABASE_TABLE,
    }


# The migration that gives the board the columns this writes, named where it is needed:
# the one failure whose fix is a file rather than a setting should say which file.
MIGRATION = "supabase/migrations/20261011000000_add_allocation_to_surplus_posts.sql"

# The migration that gives the board the column a closed post carries, named for the same
# reason and kept apart from the one above: closing a post and publishing a share to it are
# two different writes, and a project can have run either one without the other.
CLOSED_MIGRATION = "supabase/migrations/20261013000000_add_closed_at_to_surplus_posts.sql"

# How a board that has not been given the columns says so. It says it two ways, and which
# one it is depends on where the write stopped: Postgres answers `42703` with "column … does
# not exist" when it is the select or the statement that fails, while the REST layer's own
# schema cache answers `PGRST204` with "Could not find the '…' column … in the schema
# cache" before the statement is ever sent. Both mean the migration has not been applied.
MISSING_COLUMN_HINTS = ("does not exist", "in the schema cache")


def missing_column(message):
    """Whether a board message is the board saying it lacks the allocation columns."""
    return any(hint in message for hint in MISSING_COLUMN_HINTS)


# Only a value long enough to be a key is ever scrubbed. Replacing a short one rewrites
# innocent words — a one-character key turns "allocated_kg" into nonsense, and a *worse*
# outcome than the message it was protecting is one nobody can read. No real key is this
# short: a Supabase key is a long opaque string, publishable or secret alike.
MIN_REDACTABLE_KEY = 12


def redact(text):
    """A message from the board with the key taken out of it, whatever it says."""
    message = str(text or "").strip()
    if not message:
        return ""
    if len(SUPABASE_KEY) >= MIN_REDACTABLE_KEY and SUPABASE_KEY in message:
        message = message.replace(SUPABASE_KEY, "«key»")
    return message[:300]


def publish_allocation(post_id, fields):
    """Write one allocation onto one board row: `(ok, detail)`.

    One row is named by its own id, and only the allocation columns are sent — never the
    status, the donor or the item — so the trigger that freezes a post's identity has nothing
    to refuse and a misread id can only ever change the allocation of the row it named.

    The detail is the row as the board returned it on success, and the board's own message on
    failure. Neither carries the key. What a row with no allocation on it means is the one
    thing this adds to `patch_row`: an allocation is written for a post the board holds, so a
    write that matched no row is a ration nobody can see rather than a quiet success.
    """
    missing = [name for name in fields if fields[name] is None]
    if missing:
        return False, "nothing to publish for " + ", ".join(missing)
    return patch_row(post_id, fields, MIGRATION)


def patch_row(post_id, fields, migration):
    """One PATCH against one board row, naming the migration its own failure is fixed by.

    `publish_allocation` and `close_post` write two different sets of columns and fail for
    two different reasons — one column set missing means one migration unapplied — so the
    migration named with the failure is the caller's, not a constant this module would have
    to guess. Everything else is shared: one row by its own id, the key in one header, and
    the board's own message back with the key stripped out of it.
    """
    if not configured():
        return False, "no board configured"
    if not post_id:
        return False, "no post to write to"

    url = "%s/rest/v1/%s?id=eq.%s" % (
        SUPABASE_URL, SUPABASE_TABLE, urllib.parse.quote(str(post_id), safe=""),
    )
    body = json.dumps(fields).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            # Both are sent because Supabase wants the key in `apikey` and the REST layer
            # wants it as a bearer token; they are the same value.
            "apikey": SUPABASE_KEY,
            "Authorization": "Bearer " + SUPABASE_KEY,
            # Ask for the written row back, so the answer can show what the board holds.
            "Prefer": "return=representation",
        },
        method="PATCH",
    )

    try:
        with urllib.request.urlopen(request, timeout=SUPABASE_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8") or "null")
    except urllib.error.HTTPError as error:
        try:
            detail = error.read().decode("utf-8")
        except (OSError, ValueError):
            detail = ""
        message = redact(parsed_message(detail)) or ("the board answered %s" % error.code)
        if missing_column(message) or error.code == 404:
            message += " \u00b7 apply " + migration + " first"
        return False, message
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as error:
        return False, redact(error)

    if isinstance(payload, list):
        # `Prefer: return=representation` is what makes this answer readable: an empty list
        # means the update matched no row at all, which is a failure however cheerfully it
        # arrived — the id named a row nobody can see.
        if not payload:
            return False, "no board row with the id " + str(post_id)
        return True, payload[0]
    return True, payload


def close_post(post_id, closed_at):
    """Mark one board row closed, so no shelter can claim it again: `(ok, detail)`.

    The one column is written and nothing else — not the status, not the donor, not the
    item — so the trigger that freezes a post's identity has nothing to refuse, and the row
    a recipient's page is reading carries the fact without the post becoming a different
    one. A project that has not run the migration that adds the column says so, and the API
    reports it per post rather than failing the close: the closure is recorded either way,
    and what is missing is the marker the other dashboard reads.
    """
    if not closed_at:
        return False, "no moment to close at"
    return patch_row(post_id, {"closed_at": closed_at}, CLOSED_MIGRATION)


def read_post(post_id):
    """One board row, as the board holds it: `(ok, row_or_message)`.

    The board is the only thing that knows what a post says and whose it is, so this is how
    a write to it is checked before it happens — a donor may only close an item the board
    files under their own name, and the name on the row is the board's answer rather than
    the caller's.
    """
    if not configured():
        return False, "no board configured"
    if not post_id:
        return False, "no post to read"
    url = "%s/rest/v1/%s?id=eq.%s&select=*&limit=1" % (
        SUPABASE_URL, SUPABASE_TABLE, urllib.parse.quote(str(post_id), safe=""),
    )
    ok, payload = request(url)
    if not ok:
        return False, payload
    if isinstance(payload, list):
        if not payload:
            return False, "no board row with the id " + str(post_id)
        return True, payload[0]
    return True, payload


def allocation_fields(allocation, shelter_name, written_at):
    """One allocation as the board's own columns name it.

    The five columns `config.ALLOCATION_FIELDS` lists and the migration adds, and nothing
    else: the pass's ration in kilograms, its share of the post as a percentage, the weighted
    score it came out of, who is to receive it, and when it was published. Building the
    mapping here rather than at the call site keeps the columns named in one place — the same
    list the `PATCH` sends and the migration creates.
    """
    score = allocation.get("score") or {}
    return {
        "allocated_kg": allocation.get("ration_kg"),
        "allocation_percent": allocation.get("share_percent"),
        "allocation_score": score.get("total"),
        "allocated_to": shelter_name,
        "allocated_at": written_at,
    }


# ---------------------------------------------------------------------------
# The other half: adding a row that is not there yet
# ---------------------------------------------------------------------------
# Publishing a ration edits a row the board already holds. The demo fixture needs the
# opposite too — the posts and the claims a fresh board has none of — so the same request
# plumbing is exposed for a single-row insert, guarded by a read first. The guard is what
# makes a seeding pass safe to run on every startup: the row is added once, and every
# startup after that finds it and leaves it alone.

# The claims a post carries, added by the migration named here. A project without it is a
# project this write reports and steps around rather than fails on: the board's own claim
# rows are one half of a claim and the API's record is the other, so a board that cannot
# hold them yet still shows the claims a donor's console reads.
CLAIMS_TABLE = "surplus_claims"
CLAIMS_MIGRATION = "supabase/migrations/20261012000000_create_surplus_claims.sql"


def request(url, method="GET", body=None, prefer=None):
    """One request to the board: `(ok, payload_or_message)`. Never raises."""
    if not configured():
        return False, "no board configured"
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "apikey": SUPABASE_KEY,
        "Authorization": "Bearer " + SUPABASE_KEY,
    }
    if prefer:
        headers["Prefer"] = prefer
    data = json.dumps(body).encode("utf-8") if body is not None else None
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url, data=data, headers=headers, method=method),
            timeout=SUPABASE_TIMEOUT_SECONDS,
        ) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        try:
            detail = error.read().decode("utf-8")
        except (OSError, ValueError):
            detail = ""
        message = redact(parsed_message(detail)) or ("the board answered %s" % error.code)
        return False, message
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as error:
        return False, redact(error)
    try:
        return True, json.loads(raw or "null")
    except ValueError:
        return True, None


def table_url(table, query=""):
    """One table's REST endpoint, with a query string when there is one."""
    return "%s/rest/v1/%s%s" % (SUPABASE_URL, table, query)


def row_url(table, filters, select="id"):
    """A table's endpoint narrowed to one row, written the way PostgREST reads a filter.

    Every filter is separated by `&`, which is the only thing that tells PostgREST where one
    ends and the next begins. Joining them with `?` instead put the rest of the query inside
    the first filter's value: a claim looked up by post and name was asked for as
    `post_id=eq.<uuid>?recipient_name=eq.Ana Cruz`, the board read the whole of that as one
    uuid and refused it with `invalid input syntax for type uuid`. The lookup failed, the
    insert was never reached, and a *claim* was the only row this could happen to — a post is
    looked up by its id alone, which is one filter and one separator either way. That is why
    the demo board held every fixture post and not one fixture claim, and why the feed had no
    "and N more claimed" line to draw on the posts the fixture says were claimed twice over.
    """
    pairs = [(name, value) for name, value in sorted(filters.items()) if value is not None]
    query = "&".join("%s=eq.%s" % (name, urllib.parse.quote(str(value), safe=""))
                     for name, value in pairs)
    return table_url(table, ("?" + query if query else "?") + "&select=" + select + "&limit=1")


def holds_row(table, filters):
    """Whether the board already has that row: `(ok, present_or_message)`."""
    ok, payload = request(row_url(table, filters))
    if not ok:
        return False, payload
    return True, bool(payload)


def missing_table(message):
    """Whether a board message is the board saying a whole table is not there.

    It says it two ways, and which one it is depends on where the request stopped: the REST
    layer's cache answers "Could not find the table … in the schema cache" before the
    statement is sent, while Postgres answers "… does not exist" when it is. Both mean the
    migration that creates the table has not been applied.
    """
    text = str(message or "")
    return "does not exist" in text or "in the schema cache" in text


def name_trouble(table, message):
    """A board message, with the migration named when a whole table is what is missing.

    The one failure whose fix is a file rather than a setting should say which file — the
    same rule the allocation write follows.
    """
    text = str(message or "")
    if table == CLAIMS_TABLE and missing_table(text):
        return text + " · apply " + CLAIMS_MIGRATION + " to let the board hold claims"
    return text


def add_row(table, row, filters):
    """Add one row unless the board already holds it: `(ok, detail)`.

    The detail is the row the board wrote back on a success and its own message on a
    failure, with the key stripped out of either. `filters` names the row the way the board
    can find it again — an id, or the pair that has to be unique — and is what makes a
    second pass over the same fixture add nothing.
    """
    ok, present = holds_row(table, filters)
    if not ok:
        return False, name_trouble(table, present)
    if present:
        return True, {"present": True}
    ok, payload = request(table_url(table), "POST", row, "return=representation")
    if not ok:
        return False, name_trouble(table, payload)
    if isinstance(payload, list):
        return True, (payload[0] if payload else {"written": True})
    return True, payload


def add_post(row):
    """One board post, added unless that id is on the board already."""
    return add_row(SUPABASE_TABLE, row, {"id": row.get("id")})


def add_claim(row):
    """One claim row, added unless that shelter is already on that post."""
    return add_row(CLAIMS_TABLE, row, {"post_id": row.get("post_id"),
                                       "recipient_name": row.get("recipient_name")})


def parsed_message(text):
    """The board's own error message out of its JSON body, or the body itself."""
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return text
    if isinstance(parsed, dict):
        for key in ("message", "error_description", "error", "hint"):
            if isinstance(parsed.get(key), str) and parsed[key].strip():
                return parsed[key]
    return text
