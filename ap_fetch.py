"""
Andhra Pradesh live rain feed: the DES / APSDPS real-time weather network (about 2,200 AWS and rain gauges).

The station map on desweather.ap.gov.in (Livemap/Apsdpsmapsv1.html) reads its markers from
    http://desweather.ap.gov.in/webservice/rest/json1
a JSON list with one record per station:
    clientid, dmcode, dcode, latitude, longitude, location, mandal, district, rain, temp, wind, ..., ldate
`rain` is the rain since 08:30 IST of the running rain-day (the same numbers as Realtime/Rainfall.jsp),
-1 means the station is not reporting, and `ldate` ("25/10/2026 06:00:00 AM") is its last report.
Every station is listed, dry ones with 0.

The https site has a self-signed certificate, so the feed is read over plain http. apsdps.ap.gov.in
itself does not answer from outside India, but desweather.ap.gov.in does (tested from GitHub's runners).
"""
import json
import os
import re
import time
from datetime import date, datetime, timedelta

import requests

import tnsmart_fetch as tf

URL = "http://desweather.ap.gov.in/webservice/rest/json1"
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130 Safari/537.36 "
                         "(MasRainman rainfall report)",
           "Referer": "http://desweather.ap.gov.in/Livemap/Apsdpsmapsv1.html"}

# feed spelling -> name used in data/ap_districts.geojson
_ALIAS = {"ysrkadapa": "YSR Kadapa", "ysr": "YSR Kadapa", "kadapa": "YSR Kadapa",
          "ananthapuramu": "Anantapuramu", "anantapuramu": "Anantapuramu", "anantapur": "Anantapuramu",
          "srisathyasai": "Sri Sathya Sai",
          "drbrambedkarkonaseema": "Konaseema", "konaseema": "Konaseema",
          "spsnellore": "Nellore", "sripottisriramulunellore": "Nellore", "nellore": "Nellore",
          "ntr": "NTR", "allurisitharamaraju": "Alluri Sitharama Raju", "asr": "Alluri Sitharama Raju",
          "parvathipurammanyam": "Parvathipuram Manyam"}


def canon(d):
    d = (d or "").strip()
    key = re.sub(r"[^a-z]", "", d.lower())
    return _ALIAS.get(key) or " ".join(w.capitalize() for w in re.sub(r"\s+", " ", d).split())


def fetch_raw(retries=3):
    """The feed's station list."""
    if os.environ.get("APSDPS_SAMPLE"):  # a saved reply, for testing offline
        with open(os.environ["APSDPS_SAMPLE"], encoding="utf-8") as f:
            return json.load(f)
    last = None
    for attempt in range(retries):
        try:
            r = requests.get(URL, headers=HEADERS, timeout=(20, 120))
            r.raise_for_status()
            rows = r.json()
            if not isinstance(rows, list) or not rows:
                raise ValueError(f"unexpected reply: {r.text[:120]!r}")
            return rows
        except Exception as e:  # network hiccup or a slow server: retry
            last = e
            time.sleep(10 * (attempt + 1))
    raise RuntimeError(f"DES Andhra Pradesh live feed failed: {last}")


def _when(s):
    try:
        return datetime.strptime(s.strip(), "%d/%m/%Y %I:%M:%S %p").replace(tzinfo=tf.IST)
    except (AttributeError, ValueError):
        return None


def stations(d: date, rows=None, verbose=True):
    """Station list in the shape tnsmart_fetch uses (district, station, lat, lon, rain) for rain-day d.
    A station whose last report is from before the rain-day began (08:30 on d-1) is counted as not reporting."""
    rows = fetch_raw() if rows is None else rows
    start = datetime(d.year, d.month, d.day, 8, 30, tzinfo=tf.IST) - timedelta(days=1)
    out, stale = [], 0
    for r in rows:
        try:
            lat, lon = float(r["latitude"]), float(r["longitude"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (12 < lat < 20 and 76 < lon < 85.5):
            continue
        try:
            rain = float(r.get("rain"))
        except (TypeError, ValueError):
            rain = None
        if rain is not None and not 0 <= rain <= 1500:
            rain = None
        t = _when(r.get("ldate"))
        if rain is not None and (t is None or t <= start):
            rain, stale = None, stale + 1
        name = str(r.get("location") or r.get("mandal") or r.get("clientid")).replace("_", " ").strip()
        out.append({"district": canon(r.get("district")), "station": name,
                    "lat": round(lat, 5), "lon": round(lon, 5), "rain": rain})
    if verbose:
        rep = sum(1 for s in out if s["rain"] is not None)
        wet = sum(1 for s in out if s["rain"])
        print(f"  DES AP: {len(out)} stations, {rep} reporting this rain-day ({stale} stale), {wet} with rain")
    return out


def day_stations(d: date):
    """For tnsmart_fetch.live_totals: the running day's totals so far (d is the running rain-day)."""
    return stations(d)


if __name__ == "__main__":
    st = stations(tf.running_day(datetime.now(tf.IST)))
    st.sort(key=lambda s: -(s["rain"] or 0))
    for s in st[:10]:
        print(f"  {s['rain']:7.1f}  {s['station']} ({s['district']})")
