import numpy as np

M_PER_DEG_LAT = 111_320.0


def to_xy(lat, lon, lat0=46.9, lon0=-97.2):
    """Local equirectangular metres (Casselton ND is ~46.9N, 97.2W)."""
    lat, lon = np.asarray(lat, float), np.asarray(lon, float)
    return (lon - lon0) * M_PER_DEG_LAT * np.cos(np.radians(lat0)), (lat - lat0) * M_PER_DEG_LAT


def haversine_km(lat1, lon1, lat2, lon2):
    p1, p2 = np.radians(lat1), np.radians(lat2)
    a = np.sin((p2 - p1) / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(np.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6371.0 * np.arcsin(np.sqrt(a))
