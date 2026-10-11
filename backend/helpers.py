"""Res-Q backend request cleaning and naming.

The small functions a handler runs *before* an answer is written: category
selections, stored JSON lists, contact strings, ids and bounded numbers arrive
shaped however the caller shaped them, and these take them apart safely. The
naming helpers sit here too, because what an account is called on the dashboard
is decided the same way everywhere.
"""

import json
import re

from config import ALLOWED_ORIGINS, MAX_PICKS

def clean_selection(value, allowed, limit=MAX_PICKS):
    """Normalise a 1..limit multi-select answer. None means it is not a valid answer.

    A bare string is accepted so a caller that still sends a single value keeps working.
    """
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return None
    picked = []
    for item in value:
        if not isinstance(item, str):
            return None
        key = item.strip().lower()
        if not key or key in picked:
            continue
        if key not in allowed:
            return None
        picked.append(key)
    if not picked or len(picked) > limit:
        return None
    return picked

def decode_list(value):
    """Read a stored JSON list back as a list of strings."""
    if isinstance(value, list):
        return value
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, str)]

def contact_key(user):
    """The contact a demo account is filed and found under: its email, or failing that its phone.

    Demo users all carry id 0 — there is no table to key them by — so a contact is what a
    process without a database has to file anything of an account's under, whether that is
    a kitchen's logged days or the deliveries it is one side of. Email wins over phone so
    an account that has both keeps one key rather than two.
    """
    if not user:
        return None
    return clean_email(user.get("email")) or clean_phone(user.get("phone")) or None


def account_key(user):
    """What tells one account apart from another inside a demo run: its contact, or its id.

    A demo account is its contact, because every one of them carries id 0 — there is no
    table handing out distinct ones. The fabricated peers a demo match ranks against have
    no contact at all, so their id is the only thing left to tell them apart by, and it is
    what this falls back to.
    """
    return contact_key(user) or ("#" + str((user or {}).get("id")))


def origin_allowed(origin):
    """Whether this Origin may read the API, by the allowlist in config.

    An entry is either an exact origin (`https://res-q.vercel.app`) or a wildcard for one
    domain's subdomains (`*.vercel.app`) — which is what preview deployments need, since
    Vercel gives every branch its own hostname — and `*` is honoured only when it is asked
    for outright. An empty list, which is the default, allows nothing: a deployment nobody
    configured stays same-origin only. A request with no Origin at all (a server-side
    call, or curl) is never allowed, because there is no browser origin to answer.
    """
    if not origin:
        return False
    origin = origin.strip().rstrip("/").lower()
    host = origin.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0]
    for entry in ALLOWED_ORIGINS:
        if entry == "*":
            return True
        if entry.startswith("*."):
            # The dot matters: *.vercel.app matches res-q.vercel.app and every preview
            # hostname under it, and never a host that merely ends in similar letters.
            base = entry[2:]
            if host == base or host.endswith("." + base):
                return True
        elif origin == entry:
            return True
    return False


def demo_display_name(contact):
    """Turn an unknown email or phone into something friendly to greet."""
    if contact and "@" in contact:
        local = contact.split("@")[0]
        parts = [p for p in re.split(r"[._-]+", local) if p]
        if parts:
            return parts[0].capitalize()
    return "Res-Q user"

def password_problem(password):
    if len(password) < 8:
        return "Please use at least 8 characters for your password."
    if not re.search(r"[A-Z]", password):
        return "Please include at least 1 uppercase letter in your password."
    if not re.search(r"\d", password):
        return "Please include at least 1 number in your password."
    return None

def clean_email(value):
    return (value or "").strip().lower()

def clean_phone(value):
    return re.sub(r"[^\d]", "", value or "")

def display_name(user):
    """What to call an account in a match or an order: the business, if there is one."""
    if not user:
        return "Res-Q user"
    business = (user.get("business_name") or "").strip()
    if business:
        return business
    person = ("%s %s" % (user.get("first_name") or "", user.get("last_name") or "")).strip()
    return person or "Res-Q user"

def clean_id(value):
    """A user id as it arrives over JSON: a whole number, a numeric string, or None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    if isinstance(value, str):
        text = value.strip()
        if text.lstrip("+-").isdigit():
            return int(text)
    return None

def query_number(query, name, default, minimum, maximum):
    """Read a bounded numeric query parameter; None when it is not a number at all.

    A value outside the range is pulled to the nearest end rather than refused, so a
    request that asks for everything simply gets the largest answer allowed.
    """
    raw = (query.get(name) or [""])[0].strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return None
    if value != value:
        return None
    return max(minimum, min(maximum, value))
