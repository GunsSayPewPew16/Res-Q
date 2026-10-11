"""Res-Q's rationing pass: who gets what, and how much of it.

A rationing pass reads the claims on the board and answers one question — when several
shelters reach for the same surplus post, how much of it does each of them get? It has
four steps, and only the first of them wants a model:

1. **Normalise the quantity.** A post's `quantity` is text — "3 crates of tomatoes, 2 trays
   of pastries and a crate of milk" — because a shop logs surplus the way it counts it
   rather than the way it weighs it. The pass turns that into kilograms, and a model reads
   it first when a key is configured (`llm.py` reads the words), with a table of pack
   weights reading the same text when one is not.
2. **Score every claimant of the post** with the project's own formula, out of
   `config.RATION_WEIGHTS`. Each of the three inputs is a fraction of one, so the three
   terms are only comparable because they are all on the same scale.
3. **Split the post in proportion to those scores.** A claimant's share of the item is its
   score over the sum of the post's scores, and the ration is that share of the weight — so
   a shelter does not get the whole item because it asked first, and does not get nothing
   because it asked second. A claim that nobody is competing with is a hundred percent.
4. **Rank the pairs, highest first**, which is the queue: two shelters' claims on the same
   post become two entries in it, and the one that is read first is the one whose need, and
   the goods' urgency, weigh most.

The model is asked for the qualitative half of each claim and nothing else:

- **the quantity**, in kilograms, which is the part that is written in words;
- **need_score**, read from what little a claim carries about a shelter — its name, where
  it is, and what it reached for;
- **expiry_urgency_multiplier**, read from the item itself: what it is, and how long it
  has been sitting.

`distance_score` is geometry, so the pass computes it: the haversine distance between the
donor's pin and the claimant's, scoring one at the donor's own door and zero at the edge
of the radius a match is measured inside. The model is asked for a distance too — a claim
whose donor cannot be placed has no distance to compute, and then the model's reading of
the two written locations is the only reading there is — but a distance the backend can
measure is the one that is used, because a number that can be calculated should not be
guessed at.

**Privacy.** A recipient is answered with its own ration and its own share, never another
shelter's name or amount: the queue the answer carries names every other claimant by count
alone, which is what lets a dashboard show "43% of the post, shared with 3 shelters"
without handing one shelter another's business.

The one reader who does see the whole split is the donor of the post. The item is theirs,
the shelters are asking *them* for it, and where it went is the donor's own business rather
than another claimant's: a pass told `owner_name` answers with `owned` — one entry per post
that name has on the board, each carrying every claimant's name, ration and share. A pass
nobody named an owner for, or one whose owner has no claimed posts, carries an empty list,
so a shelter reading its own ration is never handed another's.

**Where the claims come from.** A shelter's claim is recorded by the API itself
(`claims.py`), so a deployment keeps claims the way it keeps its accounts: in the process in
demo mode, in the `claims` table with persistence on, and in `claim_records.json` only when
the standalone recorder was the one that wrote them. A deployment nobody has used yet has no
claims at all, and the fixture answers that: `seed.py` carries one claimed post with three
shelters on it — the fixture's own recipient among them — so a console still has a queue to
read and a share that is not a hundred percent. It is added only when the signed-in account
is the fixture's own recipient, so a run with its own claims never has the fixture's mixed
into them.
"""

import datetime
import json
import re

import llm
import seed
from accounts import account_by_business
from claims import stored_records
from config import (
    LLM_TIMEOUT_SECONDS,
    METRIC_DECIMALS,
    RATION_DISTANCE_RADIUS_KM,
    RATION_FORMULA,
    RATION_MAX_PAIRS,
    RATION_MAX_POST_KG,
    RATION_PERCENT_DECIMALS,
    RATION_WEIGHTS,
)
from geo import haversine_km
from helpers import account_key, decode_list, display_name

# ----------------------------------------------------------------------------- the text
# What spoils, and how soon, as the pass's own reading of the six categories the board
# files by. A model reads the *item* better than a table can — "yesterday's bread" is more
# urgent than "packaged goods", and both are the same category on paper — which is exactly
# why the table is the fallback rather than the answer.
PERISHABILITY = {
    "prepared_meals": 0.95,
    "dairy_beverages": 0.8,
    "fresh_produce": 0.75,
    "bakery_items": 0.7,
    "packaged_goods": 0.35,
    "household_essentials": 0.1,
}

# A post filed under nothing, or under a category this build does not know: unreadable is
# not the same as fresh, so it sits in the middle.
PERISHABILITY_UNKNOWN = 0.5

# What one pack of something weighs, in kilograms. These are the words a kitchen actually
# writes when it logs surplus, and the weights are the retail packs those words name —
# a produce crate is heavier than a bakery tray, and a pallet is a pallet.
PACK_KG = {
    "kg": 1.0, "lb": 0.45, "g": 0.001,
    "litre": 1.0,
    "crate": 12.0, "box": 8.0, "carton": 9.0, "case": 10.0, "pallet": 400.0,
    "tray": 6.0, "basket": 4.0, "punnet": 0.5, "bag": 5.0, "sack": 25.0,
    "pack": 2.0, "tin": 0.4, "bottle": 0.75, "loaf": 0.8, "dozen": 6.0,
    "each": 0.5,
}

# The words that name each of those, singular and plural, plus the spellings people use.
UNIT_WORDS = {
    "crate": "crate", "crates": "crate",
    "box": "box", "boxes": "box",
    "carton": "carton", "cartons": "carton",
    "case": "case", "cases": "case",
    "pallet": "pallet", "pallets": "pallet",
    "tray": "tray", "trays": "tray",
    "basket": "basket", "baskets": "basket",
    "punnet": "punnet", "punnets": "punnet",
    "bag": "bag", "bags": "bag",
    "sack": "sack", "sacks": "sack",
    "pack": "pack", "packs": "pack", "packet": "pack", "packets": "pack",
    "tin": "tin", "tins": "tin", "can": "tin", "cans": "tin",
    "bottle": "bottle", "bottles": "bottle",
    "loaf": "loaf", "loaves": "loaf",
    "dozen": "dozen", "dozens": "dozen",
    "kg": "kg", "kgs": "kg", "kilo": "kg", "kilos": "kg",
    "kilogram": "kg", "kilograms": "kg",
    "lb": "lb", "lbs": "lb", "pound": "lb", "pounds": "lb",
    "g": "g", "gram": "g", "grams": "g", "gr": "g",
    "litre": "litre", "litres": "litre", "liter": "litre", "liters": "litre",
    "each": "each", "unit": "each", "units": "each", "item": "each", "items": "each",
    "meal": "each", "meals": "each", "portion": "each", "portions": "each",
    "head": "each", "heads": "each", "piece": "each", "pieces": "each",
}

# The counts people write as words. "half" is here rather than in a unit, because "half a
# crate" is a count of crates and not a crate of halves.
NUMBER_WORDS = {
    "a": 1.0, "an": 1.0, "half": 0.5, "one": 1.0, "two": 2.0, "three": 3.0, "four": 4.0,
    "five": 5.0, "six": 6.0, "seven": 7.0, "eight": 8.0, "nine": 9.0, "ten": 10.0,
    "eleven": 11.0, "twelve": 12.0, "fifteen": 15.0, "twenty": 20.0, "thirty": 30.0,
    "forty": 40.0, "fifty": 50.0, "hundred": 100.0,
}

TOKEN_RE = re.compile(r"[a-z]+|\d+(?:[.,]\d+)?")

# What a post weighs when the text names no amount at all: one pack of the category, at
# the size that category's packs come in. It is an assumption, and the answer says so —
# `quantity_source` reads "assumed" whenever one of these stood in for a written amount.
ASSUMED_PACK_KG = {
    "prepared_meals": 8.0,
    "fresh_produce": 12.0,
    "bakery_items": 6.0,
    "packaged_goods": 10.0,
    "dairy_beverages": 8.0,
    "household_essentials": 15.0,
}

ASSUMED_PACK_UNKNOWN_KG = 10.0

# A bare number with no unit beside it — "40" of something — counted as this much each.
PIECE_KG = 0.5

# What "this is going today" sounds like when a kitchen writes it. The model hears these
# far better than a table does; a table is what reads them when there is no model.
URGENT_PHRASES = (
    "tonight", "today", "today's", "this evening", "expires", "expiring", "best before",
    "use by", "going off", "must go", "end of day", "short shelf", "yesterday's",
)

# How a written timestamp is read. The claim recorder writes UTC with a `Z`, the orders
# and the fixture write SQLite's own shape, and anything else is simply not a time.
TIME_FORMATS = ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S")


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc)


def format_time(moment):
    """A moment as the claim recorder would have written it."""
    return moment.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_time(value):
    """A written timestamp as a moment, or None when it is not one."""
    text = (value or "").strip()
    if not text:
        return None
    try:
        return datetime.datetime.strptime(text, TIME_FORMATS[0]).replace(
            tzinfo=datetime.timezone.utc)
    except ValueError:
        pass
    if text.endswith("Z"):
        text = text[:-1]
    for shape in TIME_FORMATS[1:]:
        try:
            return datetime.datetime.strptime(text, shape).replace(
                tzinfo=datetime.timezone.utc)
        except ValueError:
            continue
    # The board writes its own timestamps the way Postgres does — an offset and fractional
    # seconds, `2026-10-11T05:06:59.245346+00:00` — which none of the shapes above cover.
    # A live claim's post was written in that shape, so the one writer this project does
    # not control is the one it would otherwise read as no time at all: a post's age, and
    # with it how long a shelter waited for it, is read from here.
    try:
        parsed = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.astimezone(datetime.timezone.utc)


def hours_since(value, now=None):
    """How long ago a written timestamp was, in hours, or None when it cannot be read."""
    moment = parse_time(value)
    if moment is None:
        return None
    delta = (now or utcnow()) - moment
    return max(0.0, delta.total_seconds() / 3600.0)


def clamp01(value):
    """A score as the formula takes it: a fraction of one, never outside [0, 1]."""
    return max(0.0, min(1.0, value))


def as_score(value):
    """A number a model sent back as a score, or None when it did not send one.

    Every field the model is asked for is optional: a reply that leaves one out, or sends
    a string, or sends something outside the range, refines the pair by nothing rather
    than by a number nobody can defend.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return clamp01(float(value))


# ------------------------------------------------------------------------ the quantity
def assumed_kg(goods_type):
    """What one pack of this category weighs, when the text names no amount at all."""
    return ASSUMED_PACK_KG.get(goods_type, ASSUMED_PACK_UNKNOWN_KG)


def parse_quantity(text, goods_type=None):
    """A post's own words for its quantity, in kilograms: `(kg, source)`.

    The words are read as a sequence of amounts — a count, then the pack it counts — so
    "3 crates of tomatoes, 2 trays of pastries and a crate of milk" comes to
    3x12 + 2x6 + 1x12. A number with no unit after it is counted as so many pieces, and a
    text with no number in it falls back to one pack of the category. `source` says which
    of the three happened, because two of them are the pass's own arithmetic and the third
    is an assumption, and an answer should be able to tell a reader which it got.
    """
    words = (text or "").strip().lower()
    if not words:
        return round(assumed_kg(goods_type), METRIC_DECIMALS), "assumed"

    total = 0.0
    counted = False
    number = None
    for token in TOKEN_RE.findall(words):
        if token in NUMBER_WORDS:
            # "two" and "2" are the same count, so a number already standing is folded
            # rather than replaced: "two hundred" is two hundred, not two.
            number = NUMBER_WORDS[token] if number is None else number * NUMBER_WORDS[token]
            continue
        try:
            amount = float(token.replace(",", "."))
        except ValueError:
            amount = None
        if amount is not None:
            number = amount if number is None else number * amount
            continue
        unit = UNIT_WORDS.get(token)
        if unit is None:
            continue  # a word that is neither a count nor a pack: "of", "and", "tomatoes"
        total += (number if number is not None else 1.0) * PACK_KG[unit]
        counted = True
        number = None

    if counted:
        return round(total, METRIC_DECIMALS), "text"
    if number is not None:
        # A count of something with no pack named after it, counted as pieces.
        return round(number * PIECE_KG, METRIC_DECIMALS), "assumed"
    return round(assumed_kg(goods_type), METRIC_DECIMALS), "assumed"


def plausible_kg(kg, goods_type=None):
    """A weight the pass is willing to put on a post, or None when it cannot believe it.

    A reply carrying "heavy" instead of a number, a negative one, a zero or a
    pallet-sized accident is refused here rather than a few lines later, because the
    weight of a post is the one figure every claimant's share is a fraction of: a wrong
    total is wrong for all of them at once.
    """
    if isinstance(kg, bool) or not isinstance(kg, (int, float)):
        return None
    if kg != kg or kg <= 0 or kg > RATION_MAX_POST_KG:
        return None
    return round(float(kg), METRIC_DECIMALS)


# --------------------------------------------------------------------------- the score
def score_total(distance_score, need_score, urgency):
    """The project's own weighted score, from three fractions of one.

    `config.RATION_WEIGHTS` holds the coefficients and `RATION_FORMULA` the same formula
    as the line an answer prints, so this function and that string cannot disagree.
    """
    return (distance_score * RATION_WEIGHTS["distance_score"]
            + need_score * RATION_WEIGHTS["need_score"]
            + urgency * RATION_WEIGHTS["expiry_urgency_multiplier"])


def score_terms(distance_score, need_score, urgency):
    """Each term's own contribution, so an answer can show how the total was reached."""
    return {
        "distance": round(distance_score * RATION_WEIGHTS["distance_score"], METRIC_DECIMALS),
        "need": round(need_score * RATION_WEIGHTS["need_score"], METRIC_DECIMALS),
        "urgency": round(urgency * RATION_WEIGHTS["expiry_urgency_multiplier"],
                         METRIC_DECIMALS),
    }


def distance_score(distance_km):
    """Distance as a score: one at the donor's door, zero at the edge of the radius.

    The donor's own door is the best a delivery can be — the goods do not travel — and the
    radius a match is measured inside is the worst one worth making at all, which is what
    makes this a straight line between the two rather than a curve fitted to nothing.
    """
    if distance_km is None:
        return None
    span = RATION_DISTANCE_RADIUS_KM or 1.0
    return clamp01(1.0 - (float(distance_km) / span))


def local_need_score(post, claim, now=None):
    """How pressing a claim's need looks from the claim itself, without a model.

    Four signals, and what a claim does not carry contributes the neutral middle rather
    than nothing: an unread shelter is assumed to need the item moderately, not to need it
    least. The signals are the ones a claim can have —

    - **how many are waiting** — households or occupants, when the shelter reports them;
    - **how long since their last delivery**, when anything knows it;
    - **whether the post is something they asked for** — the goods profile's own needs;
    - **how promptly they claimed**, which is the one signal every claim has: a shelter
      that reached for a post within the hour wanted it more than one that came to it two
      days later.
    """
    metrics = claim.get("metrics") or {}
    waiting = metrics.get("households_waiting") or metrics.get("occupants")
    waiting_score = 0.5
    if isinstance(waiting, (int, float)) and not isinstance(waiting, bool) and waiting > 0:
        # A shelter feeding a hundred is more pressed than one feeding ten, and the curve
        # flattens past a couple of hundred because past that it is a large institution
        # either way.
        waiting_score = clamp01(float(waiting) / 250.0)

    days = metrics.get("days_since_last_delivery")
    gap_score = 0.5
    if isinstance(days, (int, float)) and not isinstance(days, bool) and days >= 0:
        # A fortnight without a delivery is at the far end of what this measures.
        gap_score = clamp01(float(days) / 14.0)

    needs = decode_list((claim.get("recipient") or {}).get("surplus_types"))
    goods_type = post.get("goods_type")
    wanted_score = 0.5
    if needs and goods_type:
        wanted_score = 1.0 if goods_type in needs else 0.0

    prompt_score = 0.5
    posted = hours_since(post.get("created_at"), now)
    claimed = hours_since(claim.get("claimed_at"), now)
    if posted is not None and claimed is not None:
        waited = max(0.0, posted - claimed)
        # Two days after it was posted is the far end of "they were quick about it".
        prompt_score = clamp01(1.0 - (waited / 48.0))

    return clamp01(0.30
                   + 0.25 * waiting_score
                   + 0.20 * gap_score
                   + 0.15 * wanted_score
                   + 0.10 * prompt_score)


def local_urgency(post, now=None):
    """How urgent the goods are on the pass's own reading: what they are, and how old.

    Perishability carries most of it — prepared meals go first, household essentials keep —
    and the post's own age carries the rest: an item that has been sitting on the board
    since yesterday is closer to going off than one logged an hour ago. A word in the item
    name or the quantity saying so — "tonight", "must go", "best before" — is worth a nudge
    on top, because a kitchen writing that means it.
    """
    perishable = PERISHABILITY.get(post.get("goods_type"), PERISHABILITY_UNKNOWN)
    age_score = 0.5
    posted = hours_since(post.get("created_at"), now)
    if posted is not None:
        # A day on the board is the far end of what the age of a post says.
        age_score = clamp01(posted / 24.0)

    urgency = 0.6 * perishable + 0.4 * age_score
    words = ("%s %s" % (post.get("item_name") or "", post.get("quantity") or "")).lower()
    if any(phrase in words for phrase in URGENT_PHRASES):
        urgency += 0.1
    return clamp01(urgency)


# ---------------------------------------------------------------------------- the model
# What the model is told to do, and what it is given to do it with. The rules are worth
# stating plainly because every one of them is a way the arithmetic could be broken by a
# helpful answer: the scores are fractions of one, the counts stay the counts the pass
# gave it, and anything it is not sure of it leaves out rather than inventing.
MODEL_SYSTEM = (
    "You ration surplus food between shelters. You are given one or more posts, and the "
    "shelters that claimed them. Answer with JSON only, in exactly this shape:\n"
    '{"posts": [{"post_id": "...", "total_kg": 0.0, "note": "how you weighed it"}],'
    ' "pairs": [{"pair_id": "...", "distance_score": 0.0, "need_score": 0.0,'
    ' "expiry_urgency_multiplier": 0.0, "reason": "one line"}]}\n'
    "Rules:\n"
    "- total_kg: the post's quantity in kilograms. Read the written quantity, using these "
    "pack weights: " + ", ".join(
        "%s=%gk" % (unit, PACK_KG[unit]) for unit in
        ("crate", "box", "tray", "bag", "sack", "pallet", "pack", "loaf", "dozen", "each")
    ) + ".\n"
    "- Every score is a fraction of one, between 0 and 1. 1 is best or most urgent.\n"
    "- distance_score: 1 means the shelter is at the donor's door and 0 means it is at the "
    "far end of the useful delivery radius. You are given the measured distance_km where "
    "it could be measured; where it is null, read it from the two written locations.\n"
    "- need_score: how much this shelter needs this item, from what is known about it. "
    "More households waiting, a longer wait since the last delivery, and a claimed item "
    "the shelter had asked for all mean more need. Where a figure is not given, do not "
    "invent one.\n"
    "- expiry_urgency_multiplier: how quickly the goods will be worthless. Prepared food "
    "and fresh produce are urgent; packaged goods and household essentials are not. An "
    "item that has been on the board for a day is more urgent than one logged an hour ago. "
    "Words like 'tonight', 'must go' or 'yesterday's' mean the top of the range.\n"
    "- Values must be numbers, never strings, and must be within 0 and 1.\n"
    "- Echo back every post_id and pair_id you were given, and invent none."
)


def model_input(posts, now=None):
    """The pass's own reading, written out for the model to refine.

    Only the fields a claim actually has are sent: the model is told what is known about a
    shelter, and an absent figure is absent from the request rather than sent as a zero the
    model would read as a real answer.
    """
    shown_posts = []
    shown_pairs = []
    for post_entry in posts:
        post = post_entry["post"]
        shown_posts.append({
            "post_id": post.get("id"),
            "item_name": post.get("item_name"),
            "goods_type": post.get("goods_type"),
            "quantity": post.get("quantity"),
            "donor": post.get("donor_name"),
            "pickup": post.get("location"),
            "posted_hours_ago": _rounded(hours_since(post.get("created_at"), now)),
            "pass_read_kg": post_entry["local_kg"],
        })
        for pair in post_entry["pairs"]:
            metrics = pair["claim"].get("metrics") or {}
            entry = {
                "pair_id": pair["pair_id"],
                "post_id": post.get("id"),
                "shelter": pair["name"],
                "delivery_address": pair["address"],
                "distance_km": pair["distance_km"],
                "claimed_hours_after_posting": _rounded(pair["waited_hours"]),
                "needs": pair["needs"],
            }
            if metrics.get("households_waiting"):
                entry["households_waiting"] = metrics["households_waiting"]
            if metrics.get("occupants"):
                entry["occupants"] = metrics["occupants"]
            if metrics.get("days_since_last_delivery") is not None:
                entry["days_since_last_delivery"] = metrics["days_since_last_delivery"]
            shown_pairs.append(entry)
    return {"posts": shown_posts, "pairs": shown_pairs}


def _rounded(value):
    return None if value is None else round(value, 1)


def model_refinements(posts, now=None):
    """Ask the model to read the claims, and take back only what it read well.

    Returns `(posts_by_id, pairs_by_id, answered)`: the weights it read for each post, the
    two qualitative scores it read for each pair, and whether it answered at all. Anything
    the reply does not carry stays absent, and the pass's own reading stands in for it.
    """
    if not llm.available():
        return {}, {}, False

    pairs_shown = sum(len(entry["pairs"]) for entry in posts)
    if not pairs_shown or pairs_shown > RATION_MAX_PAIRS:
        # Nothing to read, or more than one request should carry: the pass's own reading
        # of the same fields is the answer, and it is the one that scales.
        return {}, {}, False

    payload = json.dumps(model_input(posts, now))
    reply = llm.complete_json(MODEL_SYSTEM, payload, timeout=LLM_TIMEOUT_SECONDS)
    if not reply:
        return {}, {}, False

    posts_by_id = {}
    for entry in reply.get("posts") or []:
        if not isinstance(entry, dict):
            continue
        post_id = entry.get("post_id")
        if isinstance(post_id, str):
            posts_by_id[post_id] = entry

    pairs_by_id = {}
    for entry in reply.get("pairs") or []:
        if not isinstance(entry, dict):
            continue
        pair_id = entry.get("pair_id")
        if isinstance(pair_id, str):
            pairs_by_id[pair_id] = entry
    return posts_by_id, pairs_by_id, True


# ----------------------------------------------------------------------------- the pass
def post_weight(post, refined):
    """A post's weight and where it came from: the model's reading, or the pass's own.

    The model's figure is used when it is a weight the pass is willing to put on an item;
    a quantity of zero, a negative one, or a pallet-sized accident is the pass's own table
    reading the same words, because a share of the wrong total is wrong for every claimant
    on the post and not just the one whose quantity was misread.
    """
    read = plausible_kg(refined.get("total_kg") if refined else None, post.get("goods_type"))
    if read is not None and refined.get("total_kg") is not None:
        return read, "llm"
    local_kg, source = parse_quantity(post.get("quantity"), post.get("goods_type"))
    return local_kg, source


def same_business(name, other):
    """Whether two written business names are the same name, compared as names are.

    A post carries its donor by business name and nothing else, so this is the only way a
    pass can tell whose post it is reading. Case and surrounding space are not part of a
    name: the board writes what the form was given, and the API knows the account it signed
    in, and the two have to meet for a donor to be shown their own split.
    """
    return bool(name) and bool(other) and \
        str(name).strip().lower() == str(other).strip().lower()


def group_claims(claims):
    """The claims gathered into posts, in the order the posts were first seen.

    A post is what is rationed, so the claimants of one post have to be scored against each
    other: the pass reads it post by post, and one post is one split.
    """
    posts = []
    index = {}
    for claim in claims:
        post = claim.get("post") or {}
        post_id = post.get("id")
        if not post_id:
            continue  # a claim with no post id joins to nothing, so it is not one
        if post_id not in index:
            index[post_id] = {"post": post, "claims": []}
            posts.append(index[post_id])
        index[post_id]["claims"].append(claim)
    return posts


def claimant_pair(post_entry, claim, now=None):
    """One claim as the pass scores it, before the model has said anything."""
    recipient = claim.get("recipient") or {}
    distance_km = claim.get("distance_km")
    local_distance = distance_score(distance_km)
    waited_hours = None
    posted = hours_since(post_entry["post"].get("created_at"), now)
    claimed = hours_since(claim.get("claimed_at"), now)
    if posted is not None and claimed is not None:
        waited_hours = max(0.0, posted - claimed)
    return {
        "claim": claim,
        "key": account_key(recipient),
        "name": display_name(recipient),
        "address": recipient.get("delivery_address"),
        "needs": decode_list(recipient.get("surplus_types")),
        "distance_km": distance_km,
        "local_distance": local_distance,
        "local_need": local_need_score(post_entry["post"], claim, now),
        "local_urgency": local_urgency(post_entry["post"], now),
        "waited_hours": waited_hours,
    }


def rationing_pass(claims, caller_key, owner_name=None, now=None):
    """Score every claim, split every post, and answer with the caller's own ration.

    The answer carries the caller's allocations — their own ration and their own share of
    each post they claimed — and the queue those came out of, which names every other
    claimant by count alone. Ranked highest score first, because the order is the point:
    it is the order a delivery round would serve these claimants in.

    `owner_name` is the caller's own business name, and naming it is what lets a donor read
    the split of the posts that are theirs: those posts land in `owned`, one entry each,
    with every claimant named beside the ration it was allotted. It is the caller's own name
    rather than a flag, because the board files a post under the donor's name and that is
    the only thing a claim carries about who logged it.
    """
    now = now or utcnow()
    posts = group_claims(claims)

    # The pass's own reading of every post and every pair comes first, so a model that
    # says nothing — or is not configured, or does not answer — leaves a complete queue
    # behind rather than half of one. `post_entry["local_kg"]` is carried only to show the
    # model what the pass read, so it can disagree with it.
    for post_entry in posts:
        weight, source = parse_quantity(post_entry["post"].get("quantity"),
                                        post_entry["post"].get("goods_type"))
        post_entry["local_kg"] = weight
        post_entry["local_source"] = source
        post_entry["pairs"] = [claimant_pair(post_entry, claim, now)
                              for claim in post_entry["claims"]]
        for index, pair in enumerate(post_entry["pairs"]):
            pair["pair_id"] = "%s#%d" % (post_entry["post"].get("id"), index)

    posts_by_id, pairs_by_id, answered = model_refinements(posts, now)

    allocations = []
    owned_posts = []
    queue = []
    for post_entry in posts:
        post = post_entry["post"]
        refined_post = posts_by_id.get(post.get("id")) or {}
        total_kg, weight_source = post_weight(post, refined_post)
        # Whose post this is: the one reader who may be shown the whole split.
        owned = same_business(post.get("donor_name"), owner_name)
        splits = []

        scored = []
        for pair in post_entry["pairs"]:
            refined = pairs_by_id.get(pair["pair_id"]) or {}
            distance = as_score(refined.get("distance_score"))
            if pair["local_distance"] is not None:
                # Measured twice beats read once: a distance the pass could take from two
                # pins is the distance, whatever the model made of the two addresses.
                distance = pair["local_distance"]
                distance_source = "measured"
            elif distance is not None:
                distance_source = "read from the two addresses"
            else:
                distance = 0.5
                distance_source = "assumed"
            need = as_score(refined.get("need_score"))
            urgency = as_score(refined.get("expiry_urgency_multiplier"))
            used_model = need is not None or urgency is not None
            pair.update({
                "distance": distance,
                "distance_source": distance_source,
                "need": need if need is not None else pair["local_need"],
                "urgency": urgency if urgency is not None else pair["local_urgency"],
                "reason": (refined.get("reason") if isinstance(refined.get("reason"), str)
                           else None),
                "source": "llm" if used_model else "local",
            })
            pair["total"] = score_total(pair["distance"], pair["need"], pair["urgency"])
            scored.append(pair)

        # Highest score first, and ties broken by the claimant's own key so the same claims
        # ration the same way twice: a queue that reshuffled itself between two reads of one
        # post would be a queue nobody could act on.
        scored.sort(key=lambda pair: (-pair["total"], pair["key"] or ""))
        total_score = sum(pair["total"] for pair in scored) or 1.0

        for rank, pair in enumerate(scored, start=1):
            share = pair["total"] / total_score
            ration_kg = round(total_kg * share, METRIC_DECIMALS)
            mine = pair["key"] == caller_key
            entry = {
                "rank": rank,
                "post_id": post.get("id"),
                "item_name": post.get("item_name"),
                "goods_type": post.get("goods_type"),
                "donor": post.get("donor_name"),
                "claimants": len(scored),
                "total_kg": total_kg,
                "quantity": post.get("quantity"),
                "quantity_source": weight_source,
                "score": {
                    "total": round(pair["total"], METRIC_DECIMALS),
                    "distance_score": round(pair["distance"], METRIC_DECIMALS),
                    "need_score": round(pair["need"], METRIC_DECIMALS),
                    "expiry_urgency_multiplier": round(pair["urgency"], METRIC_DECIMALS),
                    "terms": score_terms(pair["distance"], pair["need"], pair["urgency"]),
                    "source": pair["source"],
                    "distance_source": pair["distance_source"],
                },
                "claimant": "you" if mine else "another shelter",
            }
            if mine:
                # A claimant sees its own ration and its own share — and nobody else's.
                entry["ration_kg"] = ration_kg
                entry["share_percent"] = round(share * 100.0, RATION_PERCENT_DECIMALS)
                if pair["reason"]:
                    entry["reason"] = pair["reason"]
                allocations.append(entry)
            if owned:
                # The donor of the item reads the whole split: who is getting how much of
                # it, by name, which is the one view no shelter is ever handed.
                entry["claimant_name"] = pair["name"]
                entry["ration_kg"] = ration_kg
                entry["share_percent"] = round(share * 100.0, RATION_PERCENT_DECIMALS)
                entry["needs"] = list(pair["needs"])
                entry["address"] = pair["address"]
                entry["distance_km"] = pair["distance_km"]
                if pair["reason"]:
                    entry["reason"] = pair["reason"]
                splits.append(entry)
            queue.append(entry)

        if owned:
            owned_posts.append({
                "post_id": post.get("id"),
                "item_name": post.get("item_name"),
                "goods_type": post.get("goods_type"),
                "quantity": post.get("quantity"),
                "quantity_source": weight_source,
                "location": post.get("location"),
                "posted_at": post.get("created_at"),
                "total_kg": total_kg,
                "claimants": len(scored),
                "recipients": splits,
            })

    allocations.sort(key=lambda entry: (-entry["score"]["total"], entry["rank"]))
    owned_posts.sort(key=lambda entry: (-entry["claimants"], entry["item_name"] or ""))
    return {
        "formula": RATION_FORMULA,
        "weights": dict(RATION_WEIGHTS),
        "radius_km": RATION_DISTANCE_RADIUS_KM,
        "normalizer": dict(llm.describe(), answered=answered),
        "count": len(allocations),
        "allocations": allocations,
        "owned": owned_posts,
        "owner": owner_name or None,
        "queue": queue,
    }


# ------------------------------------------------------------------------- the claims
def claim_from_record(record, conn=None):
    """One record the recorder wrote, as the pass reads a claim: with the pins resolved.

    A record carries the donor by *name* and the claimant by its own confirmed pin, so the
    distance a delivery would cover is looked up rather than measured: the donor's pin comes
    off its own account, matched by the business name the post names, and a donor nobody can
    place leaves the distance unread rather than assumed.
    """
    post = record.get("post") or {}
    if not post.get("id"):
        return None
    donor = account_by_business(post.get("donor_name"), conn)
    donor_lat = donor.get("delivery_lat") if donor else None
    donor_lng = donor.get("delivery_lng") if donor else None
    recipient = record.get("recipient") or {}
    return {
        "post": post,
        "recipient": recipient,
        "claimed_at": record.get("claimed_at"),
        "metrics": record.get("metrics") or None,
        "donor_lat": donor_lat,
        "donor_lng": donor_lng,
        "distance_km": measured_distance(donor_lat, donor_lng,
                                         recipient.get("delivery_lat"),
                                         recipient.get("delivery_lng")),
    }


def measured_distance(lat1, lng1, lat2, lng2):
    """How far apart two pins are, or None when either one is missing."""
    try:
        return round(haversine_km(float(lat1), float(lng1), float(lat2), float(lng2)),
                     METRIC_DECIMALS)
    except (TypeError, ValueError):
        return None


def gather_claims(caller, conn=None):
    """Every claim a pass should read for this caller: the recorded ones, and the fixture.

    The pass reads every claim on the board rather than only the caller's own, because a
    share is a fraction of what all of a post's claimants took; the caller's own rows are
    the ones that come back as allocations. The fixture's claimed post is added only when
    the caller is the fixture's own recipient, which is what keeps a deployment with real
    claims from having demo claimants' shares mixed into its own.
    """
    claims = []
    for record in stored_records(conn):
        claim = claim_from_record(record, conn)
        if claim is not None:
            claims.append(claim)
    # The fixture's claimed post is read for the two accounts it was written about: the
    # shelter that claimed it, whose ration it is, and the kitchen whose item it was, which
    # is the account that reads the whole split.
    if seed.is_fixture_recipient(caller, conn) or seed.is_fixture_donor(caller, conn):
        claims.extend(seed.claim_fixture(conn))
    return claims
