"""Res-Q backend matching rules.

What makes two accounts deliverable: at least one category in common, and a
confirmed pin on each side. `routing_problem` says which one is missing as a
400 body, so a dashboard can point the donor at the answer.
"""

from helpers import decode_list

def shared_categories(mine, theirs):
    """The categories both sides have, in the caller's own order, without repeats."""
    return [name for name in mine if name in theirs]

def routing_problem(user):
    """Why this account cannot be matched or ordered with yet, as a 400 body, or None.

    Matching measures between two confirmed pins. The address typed at onboarding is not
    one — it was never turned into coordinates — so the dashboard map is the only place a
    location with a point behind it comes from.
    """
    if user.get("delivery_lat") is None or user.get("delivery_lng") is None:
        return {
            "error": "Confirm your delivery location before matching: a match is a"
                     " distance between two pins.",
            "field": "delivery_lat",
            "code": "no_location",
        }
    if not decode_list(user.get("surplus_types")):
        return {
            "error": "Save your goods profile first: matching pairs the categories you"
                     " have or need.",
            "field": "surplus_types",
            "code": "no_categories",
        }
    return None
