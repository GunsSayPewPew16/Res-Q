"""Res-Q backend order shape.

One order in the shape it is stored in and the dashboards read it back, so
demo mode and persistent mode hand over the same thing.

The demo store has one extra thing to keep: *who* the order's two sides are. A row in the
table answers that with two ids, but a demo account carries id 0 — there is no table
handing out distinct ones — so a demo order records a key for each side instead, and that
key is what an account finds its own deliveries by. The three functions below are the whole
of that store: what it holds, and how one side of it is read back.
"""

from config import DEMO_ORDERS, METRIC_DECIMALS
from helpers import account_key, display_name


def order_row(order_id, donor, recipient, category, scheduled_for, distance_km, created_at):
    """One order in the shape it is stored in, so both modes hand back the same thing."""
    return {
        "id": order_id,
        "donor_id": donor["id"],
        "recipient_id": recipient["id"],
        "category": category,
        "status": "proposed",
        "scheduled_for": scheduled_for,
        "pickup_address": donor.get("delivery_address"),
        "pickup_lat": donor.get("delivery_lat"),
        "pickup_lng": donor.get("delivery_lng"),
        "dropoff_address": recipient.get("delivery_address"),
        "dropoff_lat": recipient.get("delivery_lat"),
        "dropoff_lng": recipient.get("delivery_lng"),
        "distance_km": distance_km,
        "created_at": created_at,
    }


def order_payload(order, donor, recipient, viewer_side=None):
    """One order as a dashboard reads it: who is on each side, and where.

    This is the one shape both modes answer with — a demo order is stored in it and a
    table row is read into it — so a dashboard never has two shapes to know about.

    `viewer_side` is which side of it the caller is on, the donor's pickup or the
    recipient's drop-off, because that is what decides whether the delivery is one they
    are sending or one being sent to them. A dashboard could not work it out from the two
    account ids: a demo account has no id of its own.
    """
    distance = order.get("distance_km")
    return {
        "id": order["id"],
        "status": order["status"],
        "viewer_side": viewer_side,
        "category": order["category"],
        "scheduled_for": order["scheduled_for"],
        "distance_km": (round(float(distance), METRIC_DECIMALS)
                        if distance is not None else None),
        "created_at": order["created_at"],
        "donor": {
            "user_id": order["donor_id"],
            "name": display_name(donor),
            "address": order["pickup_address"],
            "lat": order["pickup_lat"],
            "lng": order["pickup_lng"],
        },
        "recipient": {
            "user_id": order["recipient_id"],
            "name": display_name(recipient),
            "address": order["dropoff_address"],
            "lat": order["dropoff_lat"],
            "lng": order["dropoff_lng"],
        },
    }


def remember_demo_order(order, donor, recipient):
    """File one order process-wide, with a key for each of its two sides beside it.

    Process-wide rather than per session, because an order belongs to the two accounts on
    it: the side that did not propose the delivery still has to read it back.
    """
    DEMO_ORDERS.append({
        "order": order,
        "donor_key": account_key(donor),
        "recipient_key": account_key(recipient),
    })
    return order


def demo_order_side(entry, account):
    """Which side of a stored demo order this account is on, or None when it is neither."""
    if account is None:
        return None
    if entry.get("donor_key") == account:
        return "donor"
    if entry.get("recipient_key") == account:
        return "recipient"
    return None


def demo_order_pair(entry):
    """The two sides of a stored demo order, as the set a duplicate is judged against."""
    return {entry.get("donor_key"), entry.get("recipient_key")}
