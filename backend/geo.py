"""Res-Q backend map arithmetic.

Distance, a coarse pre-filter box and an offset: the three pieces of geometry
the matching and demo data need. Degrees, not radians, at the API boundary —
the database stores degrees and the dashboards speak degrees.
"""

import math

from config import EARTH_RADIUS_KM, KM_PER_DEGREE

def haversine_km(lat1, lng1, lat2, lng2):
    """Great-circle distance between two points on the map, in kilometres."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lng2 - lng1)
    a = (math.sin(d_phi / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2)
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))

def bounding_box(lat, lng, radius_km):
    """A coarse box around a point, so the database discards the far away first.

    Every candidate still gets measured properly; the box only keeps the rows the query
    has to look at to those that could possibly be inside the radius. A degree of
    longitude shrinks towards the poles, hence the cosine.
    """
    lat_delta = radius_km / KM_PER_DEGREE
    width = KM_PER_DEGREE * max(math.cos(math.radians(lat)), 0.01)
    lng_delta = min(radius_km / width, 180.0)
    return lat - lat_delta, lat + lat_delta, lng - lng_delta, lng + lng_delta

def offset_point(lat, lng, distance_km, bearing_deg):
    """A point at a bearing, distance_km from another: flat enough for demo data."""
    angle = math.radians(bearing_deg)
    width = KM_PER_DEGREE * max(math.cos(math.radians(lat)), 0.01)
    north = distance_km * math.cos(angle) / KM_PER_DEGREE
    east = distance_km * math.sin(angle) / width
    return lat + north, lng + east
