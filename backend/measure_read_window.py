"""How much does the window the features are read from actually buy?

`python backend/measure_read_window.py`

The served forecast derives `expected_customers` from the caller's most recent
`HISTORY_READ_DAYS` logged days, while `training_frame` fits the model with an *expanding*
same-weekday mean over every prior day — the window is the one place serving and training
disagreed, and this is the measurement behind the width `config.py` settles on.

It fits the real pipeline, then re-scores the same held-out days three times, with the
customer feature derived from the last six weeks of the log, the last year of it, and all
of it. Nothing here is part of the API: it is the evidence for a constant, kept runnable so
the claim in the README can be checked rather than believed.

Needs pandas and scikit-learn, the same two the calculator needs.
"""

import datetime
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from surplus import FEATURES, fixture_rows, train_surplus_model, training_frame  # noqa: E402

import pandas as pd  # noqa: E402

# The windows compared, by the number of logged days they keep. `None` is the whole log,
# which is what the training frame's own expanding mean effectively reads.
WINDOWS = (("six weeks", 42), ("a year", 365), ("the whole log", None))


def split_at(total):
    """Where the module's held-out fifth begins.

    `train_test_split(test_size=0.2, shuffle=False)` keeps the last `ceil(0.2 * n)` rows of
    the date-ordered frame as the test set, so the same figure is worked out here rather
    than guessed at.
    """
    held_back = math.ceil(total * 0.2)
    return total - held_back, held_back


def log_before(rows, kitchen, target, window=None):
    """One kitchen's log as it stood the day before `target`, optionally only its tail."""
    seen = [row for row in rows
            if row["kitchen"] == kitchen and str(row["date"])[:10] < target]
    seen.sort(key=lambda row: str(row["date"]))
    return seen[-window:] if window else seen


def same_weekday_customers(days, target):
    """The mean customer count the target's weekday has run at, over the days given."""
    weekday = datetime.date.fromisoformat(target).weekday()
    seen = [float(row["customers_served"]) for row in days
            if row.get("customers_served") is not None
            and datetime.date.fromisoformat(str(row["date"])[:10]).weekday() == weekday]
    return round(sum(seen) / len(seen), 1) if seen else None


def measure(verbose=True):
    """(label, MAE, days) for each window, measured on the model's own held-out days."""
    rows = fixture_rows()
    frame, kitchens = training_frame(rows)
    cut, held_back = split_at(len(frame))
    test_frame = frame.iloc[cut:]

    pipeline, mae, fitted_on = train_surplus_model(rows)
    if verbose:
        print("The store's log: %d usable days from %d kitchens (%s to %s)."
              % (len(frame), kitchens, fitted_on["first_date"], fitted_on["last_date"]))
        print("Fitted on %d, held out %d days; the model's own MAE is %.2f kg.\n"
              % (cut, held_back, mae))

    scored = []
    for label, window in WINDOWS:
        errors = []
        for _, day in test_frame.iterrows():
            target = str(day["date"])[:10]
            days = log_before(rows, day["kitchen"], target, window)
            customers = same_weekday_customers(days, target)
            if customers is None:
                continue
            probe = {name: day[name] for name in FEATURES}
            probe["expected_customers"] = customers
            predicted = float(pipeline.predict(pd.DataFrame([probe])[list(FEATURES)])[0])
            errors.append(abs(predicted - float(day["surplus_kg"])))
        scored.append((label, sum(errors) / len(errors), len(errors)))

    if verbose:
        print("%-14s %8s %7s" % ("window", "MAE", "days"))
        for label, error, count in scored:
            print("%-14s %8.2f %7d" % (label, error, count))
        print("\nper kitchen, on the same held-out days:")
        for label, window in WINDOWS:
            print("  " + label)
            for kitchen in sorted({row["kitchen"] for row in rows}):
                errors = []
                for _, day in test_frame.iterrows():
                    if day["kitchen"] != kitchen:
                        continue
                    target = str(day["date"])[:10]
                    customers = same_weekday_customers(log_before(rows, kitchen, target, window),
                                                       target)
                    if customers is None:
                        continue
                    probe = {name: day[name] for name in FEATURES}
                    probe["expected_customers"] = customers
                    predicted = float(pipeline.predict(pd.DataFrame([probe])[list(FEATURES)])[0])
                    errors.append(abs(predicted - float(day["surplus_kg"])))
                if errors:
                    print("    %-20s %7.2f   (%d days)"
                          % (kitchen, sum(errors) / len(errors), len(errors)))
    return scored


if __name__ == "__main__":
    measure()
