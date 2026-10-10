"""Res-Q backend order shape.

One order in the shape it is stored in and the dashboards read it back, so
demo mode and persistent mode hand over the same thing.
"""

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
