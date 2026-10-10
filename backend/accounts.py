"""Res-Q backend accounts and their impact figures.

The donor's three impact figures are seeded and filled here, and so are the
demo-mode stores beyond a session's life: an account registered in a demo run
signs back in after its token is gone, and the fabricated peers a demo match
ranks against are built here, relative to the caller's own pin.
"""

import random

from config import (
    DEMO_ACCOUNTS,
    DEMO_PEERS,
    DEMO_SESSIONS,
    DONOR_METRIC_FIELDS,
    METRIC_DECIMALS,
    NEW_DONOR_MEALS,
    NEW_DONOR_ORDERS,
    NEW_DONOR_SURPLUS_KG,
)
from geo import offset_point
from helpers import clean_email, clean_phone

def seed_donor_metrics():
    """The impact figures a new donor profile starts with."""
    return {
        "surplus_saved": round(random.uniform(*NEW_DONOR_SURPLUS_KG), METRIC_DECIMALS),
        "meals_served": random.randint(*NEW_DONOR_MEALS),
        "orders_completed": random.randint(*NEW_DONOR_ORDERS),
    }

def fill_donor_metrics(user):
    """Give a donor profile the impact figures it is missing, in place.

    Returns True when anything was filled. A profile created before the figures existed
    gets them the first time it is read, so a donor never has to wait for a fresh
    sign-in to see their own numbers. A recipient is left alone: the figures are the
    donor's, and a recipient profile carries none of them.
    """
    if user.get("role") != "donor":
        return False
    missing = [field for field in DONOR_METRIC_FIELDS if user.get(field) is None]
    if not missing:
        return False
    fresh = seed_donor_metrics()
    for field in missing:
        user[field] = fresh[field]
    return True

def demo_lookup(email, phone):
    """Return a demo account registered earlier in this process, if there is one.

    The registered account is looked up first, because it outlives its session: signing
    out gives the token back and leaves the account behind, so the same details sign in
    again as the role they registered with. A session that was never registered — the
    stranger demo mode welcomes — still answers while it lives.
    """
    for key in (email, phone):
        if key and key in DEMO_ACCOUNTS:
            return dict(DEMO_ACCOUNTS[key])
    for user in DEMO_SESSIONS.values():
        if email and user.get("email") == email:
            return dict(user)
        if phone and user.get("phone") == phone:
            return dict(user)
    return None

def remember_demo_account(user):
    """Index a registered account so it can sign in again after its session is gone.

    In demo mode the account is one dict shared by the session, the profile answers and
    the map, so indexing it once keeps every later answer without re-indexing.
    """
    for key in (clean_email(user.get("email")), clean_phone(user.get("phone"))):
        if key:
            DEMO_ACCOUNTS[key] = user

def demo_peer_users(caller, lat, lng):
    """The fabricated counterparts for this caller, keyed by id, other role only.

    Each peer is placed relative to the caller's own confirmed pin, so the ranks and
    distances move with the pin instead of being tied to one city, and the same peer id
    resolves to the same place every time it is asked for.
    """
    peers = {}
    for peer in DEMO_PEERS:
        if peer["role"] == caller.get("role"):
            continue
        peer_lat, peer_lng = offset_point(lat, lng, peer["distance_km"], peer["bearing"])
        first, _, last = peer["contact"].partition(" ")
        place = [part for part in
                 (peer["street"], caller.get("city"), caller.get("province")) if part]
        peers[peer["id"]] = {
            "id": peer["id"],
            "role": peer["role"],
            "first_name": first,
            "last_name": last,
            "business_name": peer["business_name"],
            "email": None,
            "phone": None,
            "dial_code": None,
            "street": peer["street"],
            "sub_locality": None,
            "locality": None,
            "province": caller.get("province"),
            "city": caller.get("city"),
            "postal": None,
            "firm_type": peer.get("firm_type"),
            "delivery_days": list(peer["delivery_days"]),
            "surplus_types": list(peer["surplus_types"]),
            "delivery_address": ", ".join(place) or None,
            "delivery_lat": peer_lat,
            "delivery_lng": peer_lng,
            "delivery_confirmed_at": None,
            "demo": True,
        }
    return peers
