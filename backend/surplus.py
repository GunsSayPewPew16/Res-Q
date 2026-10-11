"""Res-Q backend surplus calculator.

The regression pipeline the site's Surplus Analyser screen predicts with: a
random-forest regressor over the six features the analyser's forecast is made from —
the day's own name, the customers a kitchen expects, the sky over it and the three
surplus lags — fitted through the same impute → scale / one-hot → forest pipeline the
original notebook script used, and scored with its mean absolute error.

What it is fitted *on* is the logged days rather than an invented run of them. The
notebook built 200 synthetic days with `np.random`; here the training table is the
store's own history — one row per kitchen per day, the sky over it read from
Open-Meteo's archive and written by `history.py` — so the forest learns the same
record the analyser's forecast is read against, and the error it reports is an error
on a kitchen's real past instead of on a made-up one.

The features are *derived* from those rows rather than trusted to the columns beside
them, with the original script's own shifts: yesterday's surplus is the previous logged
day's, the week-old one is seven days back in the same log, the fourteen-day window is
the mean of the rows before the day, and the expected customers are the count that
weekday has actually run at. On a log with no gaps in it — the seeded kitchens' case —
those are the previous calendar day and the same weekday a week ago, which is exactly
what `history.features_from_history` reads back out for a forecast. A row that cannot
answer one of them — a kitchen's first fortnight, a day whose surplus or customer
count was never written down — is dropped rather than imputed as if it were a
measurement, which is the same line the original drew when it dropped the rows its
own shifts left empty.

Two calls are exposed:

    train_surplus_model(rows)   the fitted pipeline, its MAE and what it was fitted on
    forecast_surplus(**row)     the call a request reaches, trained on the first use

`python3 backend/surplus.py` trains on the seeded fixture's own logged days and prints
the same two figures the original script printed.
"""

import threading

# The features the pipeline is fitted on, in the order the training table builds them,
# and the window the rolling average looks back over. `history.py` derives its numbers
# with these rather than with a second copy of the same week, so the log and the model
# cannot drift apart about what "surplus_rolling_14" means.
FEATURES = ("day_of_week", "expected_customers", "weather", "surplus_yesterday",
            "surplus_last_week", "surplus_rolling_14")

ROLLING_WINDOW_DAYS = 14

# The three words for the sky the model was fitted on. A row whose sky is not one of
# them — a day the archive could not answer for, a log written before the vocabulary
# existed — cannot teach the model's weather term, so `usable_day` leaves it out.
SURPLUS_WEATHERS = ("Sunny", "Cloudy", "Rainy")

# A training row is a day with a fortnight behind it: the rolling window has to be full
# and the week-old lag has to exist. A month of such days is the floor, because the
# held-out fifth the error is measured on has to be more than a handful of days for the
# figure to mean anything.
MIN_TRAINING_DAYS = 2 * ROLLING_WINDOW_DAYS

MODEL_CACHE = {}
MODEL_LOCK = threading.Lock()
TRAIN_MEMORY_ERROR = (
    "The surplus model needs pandas and scikit-learn, which are not installed on"
    " this server. Install them (pip install pandas scikit-learn) and restart it."
)


class NoTrainingHistory(Exception):
    """There are no logged days to fit the model to.

    Raised instead of forecasting from a model that was never trained: the forest is
    fitted to the store's own history, so a store with no history in it has nothing to
    learn from rather than a fabricated past to fall back on.
    """


def usable_day(row):
    """Whether one logged day can teach the model anything.

    It needs a readable date, a surplus and a customer count — the three numbers every
    lesson is built from — a sky spelled the way the model knows it, and the kitchen it
    belongs to. A day missing any of them is a gap in the log rather than a reading, so it
    is left out of the training table instead of being filled in as if somebody had
    measured it; a day that does not say which kitchen it came from is left out for the
    same reason, because its lags could only be taken from somebody else's past.
    """
    if not row:
        return False
    if not weekday_name(row.get("date")):
        return False
    if not str(row.get("kitchen") or row.get("user_id") or "").strip():
        return False
    for name in ("surplus_kg", "customers_served"):
        try:
            if row.get(name) is None or float(row[name]) != float(row[name]):
                return False
        except (TypeError, ValueError):
            return False
    return str(row.get("weather") or "").strip() in SURPLUS_WEATHERS


def training_frame(rows):
    """The logged days as the table the pipeline is fitted on.

    Every kitchen's rows are turned into features on their own, in date order, so a
    surplus lag or a rolling mean is that kitchen's own past rather than one kitchen's
    numbers following another's day. A lag is the previous *logged* day, as the original
    script's shifts were: a kitchen that skipped a day still has yesterday's figure to
    learn from, which is closer to what its own log can answer than a hole would be. The
    kitchens are then put back together in date order, which is what makes the held-out
    tail of the split the newest days across the whole store — the same "score it on the
    days it has not seen" the original script's shuffled-off test set was for.

    Returns (frame, kitchens); raises `NoTrainingHistory` when the store has no usable
    days at all, and answers a frame shorter than `MIN_TRAINING_DAYS` as the caller's
    problem rather than here.
    """
    import pandas as pd

    usable = [row for row in (rows or []) if usable_day(row)]
    if not usable:
        raise NoTrainingHistory(
            "The surplus model is fitted to logged days and there are none to fit it"
            " to. Seed the test kitchens (RESQ_SEED=on) or log a kitchen's days first."
        )

    frame = pd.DataFrame(
        [
            {
                "kitchen": str(row.get("kitchen") or row.get("user_id")),
                "date": str(row["date"])[:10],
                "day_of_week": weekday_name(row["date"]),
                "weather": str(row["weather"]).strip(),
                "customers_served": float(row["customers_served"]),
                "surplus_kg": float(row["surplus_kg"]),
            }
            for row in usable
        ]
    )

    blocks = []
    for _, block in frame.groupby("kitchen", sort=False):
        block = block.sort_values("date")
        surplus = block["surplus_kg"]
        # The three lags, built the way the notebook built them and the way
        # `history.features_from_history` reads them back out of one log.
        block["surplus_yesterday"] = surplus.shift(1)
        block["surplus_last_week"] = surplus.shift(7)
        block["surplus_rolling_14"] = surplus.shift(1).rolling(window=ROLLING_WINDOW_DAYS).mean()
        # The customer count that weekday has actually run at, from the days before this
        # one: the mean of the same weekday's previous customers, which is the number a
        # kitchen could have written down that morning.
        block["expected_customers"] = block.groupby("day_of_week")["customers_served"].transform(
            lambda served: served.expanding().mean().shift(1)
        )
        blocks.append(block)

    frame = pd.concat(blocks, ignore_index=True)
    # A day the log cannot answer for is dropped rather than imputed as a measurement:
    # the first fortnight of a kitchen's log has no yesterday, no last week and no full
    # rolling window to be scored against.
    frame = frame.dropna(subset=list(FEATURES))
    frame = frame.sort_values(["date", "kitchen"]).reset_index(drop=True)
    return frame, int(frame["kitchen"].nunique()) if len(frame) else 0


def weekday_name(value):
    """A logged day's own weekday, in the model's vocabulary.

    Days are stored as plain ISO dates, so a day is a day whatever timezone it was
    written in; a date this server cannot read is carried as an empty day name, which
    `training_frame`'s drop of unanswerable rows keeps out of the fit.
    """
    import datetime

    try:
        return datetime.date.fromisoformat(str(value)[:10]).strftime("%A")
    except ValueError:
        return ""


def trained_on(frame, kitchens):
    """What the forest was fitted to, so an answer can say so rather than implying it.

    The figures are the usable days — the ones with a fortnight behind them — and the
    date range they cover, which is the past every forecast is standing on.
    """
    if not len(frame):
        return {"kitchens": 0, "days": 0, "first_date": None, "last_date": None}
    return {
        "kitchens": kitchens,
        "days": int(len(frame)),
        "first_date": frame["date"].iloc[0],
        "last_date": frame["date"].iloc[-1],
    }


def train_surplus_model(rows, verbose=False):
    """Fit the pipeline to the logged days; return (pipeline, mae, trained_on).

    The split, the preprocessing and the forest are the original script's, unchanged:
    the last fifth of the days — the newest, since the frame is in date order — is held
    out and never fitted on, so the mean absolute error is this model's error on days
    it did not see rather than on the days it memorised. The one seed left is the
    forest's own, which is what makes the same log always produce the same forest.
    """
    try:
        import pandas as pd
        from sklearn.ensemble import RandomForestRegressor
        from sklearn.compose import ColumnTransformer
        from sklearn.impute import SimpleImputer
        from sklearn.metrics import mean_absolute_error
        from sklearn.model_selection import train_test_split
        from sklearn.pipeline import Pipeline
        from sklearn.preprocessing import OneHotEncoder, StandardScaler
    except ImportError:
        raise ImportError(TRAIN_MEMORY_ERROR)

    frame, kitchens = training_frame(rows)
    if len(frame) < MIN_TRAINING_DAYS:
        raise NoTrainingHistory(
            "The surplus model needs at least %d logged days with a fortnight behind"
            " them and only %d are in the store. Log more days, or seed the test"
            " kitchens with RESQ_SEED=on." % (MIN_TRAINING_DAYS, len(frame))
        )

    X = frame[list(FEATURES)]
    y = frame["surplus_kg"]

    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, shuffle=False)

    numeric_features = ['expected_customers', 'surplus_yesterday', 'surplus_last_week',
                        'surplus_rolling_14']
    numeric_transformer = Pipeline(steps=[
        ('imputer', SimpleImputer(strategy='median')),
        ('scaler', StandardScaler())
    ])

    categorical_features = ['day_of_week', 'weather']
    categorical_transformer = Pipeline(steps=[
        ('imputer', SimpleImputer(strategy='constant', fill_value='missing')),
        ('onehot', OneHotEncoder(handle_unknown='ignore'))
    ])

    preprocessor = ColumnTransformer(
        transformers=[
            ('num', numeric_transformer, numeric_features),
            ('cat', categorical_transformer, categorical_features)
        ])

    pipeline = Pipeline(steps=[
        ('preprocessor', preprocessor),
        ('regressor', RandomForestRegressor(n_estimators=100, random_state=42))
    ])

    pipeline.fit(X_train, y_train)

    y_pred = pipeline.predict(X_test)
    mae = mean_absolute_error(y_test, y_pred)
    if verbose:
        print("Mean Absolute Error: %.2f kg" % mae)
        latest_conditions = X.tail(1)
        predicted_surplus = pipeline.predict(latest_conditions)
        print("Estimated Surplus Food Available for Donation: %.2f kg" % predicted_surplus[0])
    return pipeline, float(mae), trained_on(frame, kitchens)


def forecast_surplus(rows=None, day_of_week=None, weather=None, expected_customers=None,
                     surplus_yesterday=None, surplus_last_week=None,
                     surplus_rolling_14=None):
    """Today's surplus from the pipeline, in the shape the API returns.

    The row being forecast is today's, which is why the day defaults to today's own
    date: the analyser asks about the surplus a kitchen is holding now, not tomorrow's.

    `rows` is the logged days the model is fitted to, and it is read on the first call
    only — the fitted forest is cached for the life of the process, so a busy session
    does not refit it per request and every answer is made by one model rather than by
    whichever request happened to arrive first. A call that arrives with nothing to fit
    to and nothing fitted yet is refused plainly instead of forecasting from a guess.

    Every feature the model was trained on can be passed; anything left out is
    filled with a usable default — the day with today's, the sky with a clear one,
    the numbers with the medians the imputer would have chosen. The clear sky is a
    fallback and not a reading: the endpoint's caller reads the real weather at the
    account's delivery address through `/api/weather` and passes the label in, so a
    request that omits it is one that had no address to read.

    Missing pandas or scikit-learn raises the message the endpoint answers with, so a
    server without them says plainly what to install instead of a bare import
    traceback.
    """
    with MODEL_LOCK:
        if "pipeline" not in MODEL_CACHE:
            pipeline, mae, fitted_on = train_surplus_model(rows or [])
            MODEL_CACHE["pipeline"] = pipeline
            MODEL_CACHE["mae"] = mae
            MODEL_CACHE["trained_on"] = fitted_on
        pipeline = MODEL_CACHE["pipeline"]
        mae = MODEL_CACHE["mae"]
        fitted_on = MODEL_CACHE["trained_on"]

    import datetime
    try:
        import pandas as pd
    except ImportError:
        raise ImportError(TRAIN_MEMORY_ERROR)

    today = datetime.date.today()
    dow = day_of_week or today.strftime("%A")
    weather_value = weather or "Sunny"

    row = pd.DataFrame({
        "day_of_week": [dow],
        "expected_customers": [float(expected_customers) if expected_customers is not None else 200.0],
        "weather": [weather_value],
        "surplus_yesterday": [float(surplus_yesterday) if surplus_yesterday is not None else 15.0],
        "surplus_last_week": [float(surplus_last_week) if surplus_last_week is not None else 15.0],
        "surplus_rolling_14": [float(surplus_rolling_14) if surplus_rolling_14 is not None else 15.0],
    })

    predicted = float(pipeline.predict(row)[0])
    return {
        "day_of_week": dow,
        "weather": weather_value,
        "predicted_surplus_kg": round(predicted, 2),
        "mae_kg": round(mae, 2),
        "model": "RandomForestRegressor(n_estimators=100, random_state=42)",
        "features": list(FEATURES),
        "trained_on": dict(fitted_on),
    }


def fixture_rows():
    """The seeded kitchens' own logged days, for a run of this file on its own.

    `python3 backend/surplus.py` trains what the server trains, from the same fixture
    the server seeds, so the figures it prints are the ones the analyser will answer
    with. The import is made here rather than at the top because `seed` reaches back
    into this module for the window the log is built with.
    """
    from seed import FIXTURE_DONORS, donor_days

    rows = []
    for donor in FIXTURE_DONORS:
        days = donor.get("history_days")
        for day in donor_days(donor, days=days) if days else donor_days(donor):
            rows.append(dict(day, kitchen=donor["email"]))
    return rows


if __name__ == "__main__":
    print("Fitting the surplus model to the seeded kitchens' logged days.")
    pipeline, mae, fitted_on = train_surplus_model(fixture_rows(), verbose=True)
    print("Fitted on %d days from %d kitchens (%s to %s)."
          % (fitted_on["days"], fitted_on["kitchens"],
             fitted_on["first_date"], fitted_on["last_date"]))
