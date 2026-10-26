"""
Terrain for the maps: elevation from the AWS Terrain Tiles (SRTM and others, "terrarium" PNG tiles)
turned into a hillshade.

  https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png
  elevation (m) = R * 256 + G + B / 256 - 32768

Tiles are cached in data/raw/terrain/ (the workflows already cache data/raw).
"""
import io
import math
import os
import time

import numpy as np
import requests
from PIL import Image

TILE = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(os.environ.get("TNRAIN_RAW_DIR") or os.path.join(HERE, "data", "raw"), "terrain")


def _tile_xy(lon, lat, z):
    n = 2 ** z
    x = (lon + 180) / 360 * n
    y = (1 - np.arcsinh(np.tan(np.radians(lat))) / math.pi) / 2 * n
    return x, y


def _tile(z, x, y):
    path = os.path.join(CACHE, f"{z}_{x}_{y}.png")
    if os.path.exists(path):
        return np.asarray(Image.open(path).convert("RGB"))
    os.makedirs(CACHE, exist_ok=True)
    for attempt in range(3):
        try:
            r = requests.get(TILE.format(z=z, x=x, y=y), timeout=30)
            r.raise_for_status()
            with open(path, "wb") as f:
                f.write(r.content)
            return np.asarray(Image.open(io.BytesIO(r.content)).convert("RGB"))
        except Exception as e:  # network hiccup: retry
            err = e
            time.sleep(2 * (attempt + 1))
    print(f"  terrain tile {z}/{x}/{y} failed ({err}); flat there")
    return np.full((256, 256, 3), (128, 0, 0), np.uint8)       # = 0 m


def elevation(lon, lat, z=9):
    """Elevation (m) at the lon/lat grids (any shape), bilinear from zoom-z tiles."""
    lon, lat = np.asarray(lon, float), np.asarray(lat, float)
    x0, y1 = _tile_xy(lon.min(), lat.min(), z)
    x1, y0 = _tile_xy(lon.max(), lat.max(), z)
    tx0, ty0, tx1, ty1 = int(x0), int(y0), int(x1), int(y1)
    rows = []
    for ty in range(ty0, ty1 + 1):
        rows.append(np.hstack([_tile(z, tx, ty) for tx in range(tx0, tx1 + 1)]))
    rgb = np.vstack(rows).astype(np.float32)
    dem = rgb[..., 0] * 256 + rgb[..., 1] + rgb[..., 2] / 256 - 32768
    dem = np.maximum(dem, 0)                                    # sea = 0
    px, py = _tile_xy(lon, lat, z)
    import cv2
    return cv2.remap(dem, ((px - tx0) * 256 - 0.5).astype(np.float32), ((py - ty0) * 256 - 0.5).astype(np.float32),
                     cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


def hillshade(dem, cell_m, azimuth=315, altitude=40, exaggerate=2.5):
    """0..1 shade, 1 = facing the light. cell_m = (dy, dx) of a grid cell in metres."""
    dy, dx = cell_m
    gy, gx = np.gradient(dem * exaggerate, dy, dx)
    slope = np.arctan(np.hypot(gx, gy))
    aspect = np.arctan2(-gx, gy)
    az, alt = math.radians(azimuth), math.radians(altitude)
    s = np.sin(alt) * np.cos(slope) + np.cos(alt) * np.sin(slope) * np.cos(az - aspect)
    return np.clip(s, 0, 1).astype(np.float32)
