"""Res-Q backend surplus calculator.

The regression pipeline the site's Surplus Analyser screen predicts with, kept
exactly as it was built and pasted in from the surplus.py notebook script:
synthetic daily history from the shape a kitchen actually sees — a weekday
rhythm, a weather row, a weekend bump — trained into a random-forest regressor
through an impute → scale / one-hot → forest pipeline, scored with its mean
absolute error, then run against the latest conditions to forecast today.

`train_surplus_model` is the pasted file, turned into one function: same seed,
same features, same split, so the score it reports here is the score the
original prints. `forecast_surplus` is the part a request actually needs — the
trained pipeline and its MAE, cached after the first call so a busy session
does not retrain the forest on every forecast.
"""

import threading

MODEL_CACHE = {}
MODEL_LOCK = threading.Lock()
TRAIN_MEMORY_ERROR = (
    "The surplus model needs pandas and scikit-learn, which are not installed on"
    " this server. Install them (pip install pandas scikit-learn) and restart it."
)


def train_surplus_model(verbose=False):
    """Train the pasted pipeline once; return (pipeline, mae).

    The body is the original script verbatim from `np.random.seed(42)` down to
    `predicted_surplus`, with `print` swapped for the values it printed, so
    `surplus.py` and this file stay auditable line against line.
    """
    import numpy as np
    import pandas as pd
    from sklearn.ensemble import RandomForestRegressor
    from sklearn.compose import ColumnTransformer
    from sklearn.impute import SimpleImputer
    from sklearn.metrics import mean_absolute_error
    from sklearn.model_selection import train_test_split
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import OneHotEncoder, StandardScaler

    np.random.seed(42)
    dates = pd.date_range(start='2025-01-01', periods=200, freq='D')
    days_of_week = dates.day_name()

    data = pd.DataFrame({
        'date': dates,
        'day_of_week': days_of_week,
        'customers_served': np.random.randint(100, 300, size=200),
        'weather': np.random.choice(['Sunny', 'Rainy', 'Cloudy'], size=200)
    })

    data['expected_customers'] = data.groupby('day_of_week')['customers_served'].transform(
        lambda x: x.shift(1).expanding().mean())
    data['expected_customers'] = data['expected_customers'].fillna(200)

    base_surplus = data['customers_served'] * 0.15
    weekend_multiplier = data['day_of_week'].isin(['Saturday', 'Sunday']).astype(int) * 4
    data['surplus_kg'] = base_surplus + weekend_multiplier + np.random.normal(0, 2.5, size=200)
    data['surplus_kg'] = data['surplus_kg'].clip(lower=0)

    data['surplus_yesterday'] = data['surplus_kg'].shift(1)
    data['surplus_last_week'] = data['surplus_kg'].shift(7)
    data['surplus_rolling_14'] = data['surplus_kg'].shift(1).rolling(window=14).mean()
    data = data.dropna().reset_index(drop=True)

    features = ['day_of_week', 'expected_customers', 'weather', 'surplus_yesterday',
                'surplus_last_week', 'surplus_rolling_14']
    X = data[features]
    y = data['surplus_kg']

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
    return pipeline, float(mae)


def forecast_surplus(day_of_week=None, weather=None, expected_customers=None,
                     surplus_yesterday=None, surplus_last_week=None,
                     surplus_rolling_14=None):
    """Today's surplus from the trained pipeline, in the shape the API returns.

    The row being forecast is today's, which is why the day defaults to today's own
    date: the analyser asks about the surplus a kitchen is holding now, not tomorrow's.

    Every feature the model was trained on can be passed; anything left out is
    filled with a usable default — the day with today's, the sky with a clear one,
    the numbers with the medians the training imputer would have chosen. The clear
    sky is a fallback and not a reading: the endpoint's caller reads the real
    weather at the account's delivery address through `/api/weather` and passes the
    label in, so a request that omits it is one that had no address to read.

    Missing pandas or scikit-learn raises the message the endpoint answers with, so a
    server without them says plainly what to install instead of a bare import
    traceback.
    """
    try:
        import numpy as np
        import pandas as pd
    except ImportError:
        raise ImportError(TRAIN_MEMORY_ERROR)

    with MODEL_LOCK:
        if "pipeline" not in MODEL_CACHE:
            pipeline, mae = train_surplus_model()
            MODEL_CACHE["pipeline"] = pipeline
            MODEL_CACHE["mae"] = mae
        pipeline = MODEL_CACHE["pipeline"]
        mae = MODEL_CACHE["mae"]

    import datetime
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
        "features": ["day_of_week", "expected_customers", "weather", "surplus_yesterday",
                     "surplus_last_week", "surplus_rolling_14"],
    }


if __name__ == "__main__":
    pipeline, mae = train_surplus_model(verbose=True)
