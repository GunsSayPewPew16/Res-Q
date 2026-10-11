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


def redact(text):
    """A message from the board with the key taken out of it, whatever it says."""
    message = str(text or "").strip()
    if not message:
        return ""
    if SUPABASE_KEY and SUPABASE_KEY in message:
        message = message.replace(SUPABASE_KEY, "«key»")
    return message[:300]


def publish_allocation(post_id, fields):
    """Write one allocation onto one board row: `(ok, detail)`.

    One row is named by its own id, and only the allocation columns are sent — never the
    status, the donor or the item — so the trigger that freezes a post's identity has nothing
    to refuse and a misread id can only ever change the allocation of the row it named.

    The detail is the row as the board returned it on success, and the board's own message on
    failure. Neither carries the key.
    """
    if not configured():
        return False, "no board configured"
    if not post_id:
        return False, "no post to publish to"

    missing = [name for name in fields if fields[name] is None]
    if missing:
        return False, "nothing to publish for " + ", ".join(missing)

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
        if error.code == 404 or "does not exist" in message:
            message += (" · apply supabase/migrations/"
                        "20261011000000_add_allocation_to_surplus_posts.sql first")
        return False, message
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as error:
        return False, redact(error)

    if isinstance(payload, list):
        return True, payload[0] if payload else None
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
