"""Res-Q backend live weather.

The analyser's random forest was trained with a weather column that knows exactly
three words — Sunny, Cloudy and Rainy — so the real sky over a delivery address has
to be translated into that vocabulary before the model can use it. Open-Meteo is
where it is read from: keyless, the way the map's vector tiles and the address
lookups are, so nothing has to be registered to run this server.

The WMO code in Open-Meteo's answer is what carries the condition, and the table in
`condition_label` is the single place that decides which of the three words a code
becomes. Two kinds of weather have no word of their own there: snow is answered as
Rainy and fog as Cloudy, because the vocabulary has neither, and those are the
closest things the model was trained to hold. That is a real loss of information, so
the raw code, its own words and the day's own rainfall travel beside the label for
anything that wants to show the weather rather than feed it to the model.

Two readings come out of the same service: the sky over a point right now, which is
the row a forecast is made under, and the days ahead of it, which is what the
analyser's weekly chart is drawn from. Both are the same call with a different set of
fields, so the request, the answer and every failure they share live once, here.
"""

import datetime
import json
import urllib.error
import urllib.request

from config import (
    WEATHER_API_URL,
    WEATHER_CURRENT_FIELDS,
    WEATHER_DAILY_FIELDS,
    WEATHER_MAX_OUTLOOK_DAYS,
    WEATHER_OUTLOOK_DAYS,
    WEATHER_SOURCE,
    WEATHER_SOURCE_URL,
    WEATHER_TIMEOUT_SECONDS,
)

# Identify the caller rather than arriving as an anonymous bot. Open-Meteo does not
# require it; their terms ask for it, and it is one header.
USER_AGENT = "Res-Q/1.0 (local demo; https://open-meteo.com/)"

# WMO 4677 weather codes, grouped into the model's vocabulary. Every code Open-Meteo
# can answer with is here, so an unmapped one is a code Open-Meteo does not use.
CONDITION_CODES = {
    "Sunny": (0, 1),            # clear sky, mainly clear
    "Cloudy": (2, 3, 45, 48),   # partly cloudy, overcast, fog and depositing rime fog
    "Rainy": (
        51, 53, 55, 56, 57,     # drizzle, freezing drizzle
        61, 63, 65, 66, 67,     # rain, freezing rain
        71, 73, 75, 77,         # snowfall and snow grains: no word for snow in the model
        80, 81, 82,             # rain showers
        85, 86,                 # snow showers
        95, 96, 99,             # thunderstorm, with and without hail
    ),
}

# The code in words, for the conditions panel rather than the model.
CONDITION_SUMMARIES = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Drizzle",
    55: "Heavy drizzle",
    56: "Light freezing drizzle",
    57: "Freezing drizzle",
    61: "Light rain",
    63: "Rain",
    65: "Heavy rain",
    66: "Light freezing rain",
    67: "Freezing rain",
    71: "Light snow",
    73: "Snow",
    75: "Heavy snow",
    77: "Snow grains",
    80: "Light rain showers",
    81: "Rain showers",
    82: "Violent rain showers",
    85: "Light snow showers",
    86: "Snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with light hail",
    99: "Thunderstorm with heavy hail",
}


class WeatherUnavailable(Exception):
    """The sky could not be read: the service was unreachable or answered oddly.

    Carried out of `fetch_weather` so a handler can say so plainly instead of
    answering a forecast built on an invented sky.
    """


def condition_label(code):
    """The model's word for a WMO code, or None for a code it does not know."""
    for label, codes in CONDITION_CODES.items():
        if code in codes:
            return label
    return None


def condition_summary(code):
    """The code in words, for display; the code itself when it has no words here."""
    return CONDITION_SUMMARIES.get(code, "WMO code %s" % code)


def weather_url(lat, lng):
    """The Open-Meteo request for one point, asking for the sky and today's summary."""
    return (
        "%s?latitude=%.6f&longitude=%.6f&current=%s&daily=%s&forecast_days=1&timezone=auto"
        % (WEATHER_API_URL, lat, lng, WEATHER_CURRENT_FIELDS, WEATHER_DAILY_FIELDS)
    )


def outlook_url(lat, lng, days):
    """The Open-Meteo request for a run of days at one point, starting with today."""
    return (
        "%s?latitude=%.6f&longitude=%.6f&daily=%s&forecast_days=%d&timezone=auto"
        % (WEATHER_API_URL, lat, lng, WEATHER_DAILY_FIELDS, days)
    )


def fetch_json(url):
    """One Open-Meteo answer, or WeatherUnavailable.

    Raises WeatherUnavailable when the service cannot be reached or its answer cannot
    be read: a forecast nobody can trace to a reading of the sky is not one worth
    showing, so the failure is carried instead of a default.
    """
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=WEATHER_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as err:
        # HTTPError is a URLError, a timeout is an OSError and an unreadable body is a
        # ValueError; the caller sees one failure either way.
        raise WeatherUnavailable(
            "The weather service could not be reached for that location (%s)." % err
        )


def fetch_weather(lat, lng):
    """The real conditions at a point, in the shape the API answers with."""
    return weather_from_payload(fetch_json(weather_url(lat, lng)))


def fetch_outlook(lat, lng, days=WEATHER_OUTLOOK_DAYS):
    """The days ahead at a point, today first, each in the model's own words.

    The run is clamped to what the service will answer and to at least one day, so a
    caller cannot ask for a week that does not exist.
    """
    days = max(1, min(WEATHER_MAX_OUTLOOK_DAYS, int(days)))
    return outlook_from_payload(fetch_json(outlook_url(lat, lng, days)))


def outlook_from_payload(payload):
    """One Open-Meteo daily answer, read into one entry per day.

    Each entry carries the day's own date, its name in the model's vocabulary, the
    label the model reads and the reading it came from. A day whose condition has no
    word in that vocabulary, or a date that cannot be read, refuses the whole outlook
    rather than dropping a day from the middle of a week.
    """
    if not isinstance(payload, dict):
        raise WeatherUnavailable("The weather service answered in a shape that cannot be read.")

    daily = payload.get("daily") or {}
    dates = daily.get("time") or []
    if not dates:
        raise WeatherUnavailable("The weather service answered without a daily outlook.")

    days = []
    for index, date in enumerate(dates):
        code = whole_number(value_at(daily.get("weather_code"), index))
        label = condition_label(code)
        if label is None:
            raise WeatherUnavailable(
                "The weather service answered with a condition this model has no word for"
                " on %s (%s)." % (date, code)
            )
        name = weekday_name(date)
        if name is None:
            raise WeatherUnavailable(
                "The weather service answered with a date this server cannot read (%s)." % date
            )
        days.append({
            "date": date,
            "day_of_week": name,
            "label": label,
            "code": code,
            "summary": condition_summary(code),
            "precipitation_mm": round_number(value_at(daily.get("precipitation_sum"), index), 1),
            "high_c": round_number(value_at(daily.get("temperature_2m_max"), index), 1),
            "low_c": round_number(value_at(daily.get("temperature_2m_min"), index), 1),
        })

    return {
        "days": days,
        "timezone": payload.get("timezone"),
        "fetched_at": now_stamp(),
        "source": WEATHER_SOURCE,
        "source_url": WEATHER_SOURCE_URL,
    }


def weather_from_payload(payload):
    """One Open-Meteo answer, read into the shape the API and the dashboard use.

    Today is the first entry of the daily block, and it is what the label comes from:
    the analyser forecasts today's surplus, so today's own sky is the row the model
    is asking about. The current reading is carried beside it because a temperature
    is what makes a forecast readable — it is not what the model uses.
    """
    if not isinstance(payload, dict):
        raise WeatherUnavailable("The weather service answered in a shape that cannot be read.")

    daily = payload.get("daily") or {}
    current = payload.get("current") or {}
    daily_codes = daily.get("weather_code") or []
    if not daily_codes:
        raise WeatherUnavailable("The weather service answered without today's conditions.")

    code = whole_number(daily_codes[0])
    label = condition_label(code)
    if label is None:
        raise WeatherUnavailable(
            "The weather service answered with a condition this model has no word for (%s)." % code
        )

    return {
        "label": label,
        "code": code,
        "summary": condition_summary(code),
        "precipitation_mm": round_number(first(daily.get("precipitation_sum")), 1),
        "high_c": round_number(first(daily.get("temperature_2m_max")), 1),
        "low_c": round_number(first(daily.get("temperature_2m_min")), 1),
        "temperature_c": round_number(current.get("temperature_2m"), 1),
        "wind_kph": round_number(current.get("wind_speed_10m"), 1),
        "local_date": first(daily.get("time")),
        "local_time": current.get("time"),
        "timezone": payload.get("timezone"),
        "fetched_at": now_stamp(),
        "source": WEATHER_SOURCE,
        "source_url": WEATHER_SOURCE_URL,
    }


def now_stamp():
    """When a reading was taken, in UTC, the way every other timestamp here is written."""
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def weekday_name(date):
    """An ISO date's own weekday, in the model's vocabulary; None when it cannot be read."""
    try:
        parsed = datetime.date.fromisoformat(str(date))
    except ValueError:
        return None
    return parsed.strftime("%A")


def first(values):
    """The first entry of one field of Open-Meteo's daily block, when there is one."""
    return value_at(values, 0)


def value_at(values, index):
    """One entry of a field of Open-Meteo's daily block, when that entry exists."""
    if isinstance(values, list) and 0 <= index < len(values):
        return values[index]
    return None


def round_number(value, decimals):
    """A number rounded for reading, or None for anything that is not one.

    The weather service answers null where it has no reading, and a missing reading
    has to stay missing — a zero would read as a real one.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return round(float(value), decimals)


def whole_number(value):
    """An integer-ish reading, or None for anything that is not one."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)
