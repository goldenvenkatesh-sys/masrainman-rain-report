"""
TN-SMART daily station rainfall downloader.

TN-SMART publishes one JSON file per rain-day:
  https://beta-tnsmart.rimes.int/Rainfall_Python_IDW/Hourly_json/hourly_rainfall_YYYY-MM-DD.json
The file for date D covers 09:30 AM on D-1 to 08:30 AM on D (IST), i.e. the
IMD-style "24 hours ending 8:30 am" of date D. Each record has
district_name, station_name, latitude, longitude, the 24 hourly columns and "total".
A station with total = null did not report.
"""
import json
import os
import time
from datetime import date, datetime, timedelta, timezone

import requests

BASE = "https://beta-tnsmart.rimes.int/Rainfall_Python_IDW/Hourly_json/hourly_rainfall_{d}.json"
IST = timezone(timedelta(hours=5, minutes=30))
HERE = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.environ.get("TNRAIN_RAW_DIR") or os.path.join(HERE, "data", "raw")
OFFLINE = os.environ.get("TNRAIN_OFFLINE") == "1"  # use cached files only (testing)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (MasRainman rainfall report)",
    "Referer": "https://beta-tnsmart.rimes.int/index.php/RTDAS/Hourly_data_TNDRRA_c/public",
}


def today_ist():
    return datetime.now(IST).date()


def rain_day_is_complete(d: date) -> bool:
    """The file for date D is final after 08:30 IST on D (allow 45 min for upload)."""
    done_at = datetime(d.year, d.month, d.day, 9, 15, tzinfo=IST)
    return datetime.now(IST) >= done_at


def _cache_path(d: date):
    return os.path.join(RAW_DIR, f"hourly_rainfall_{d.isoformat()}.json")


def download_day(d: date, force=False, retries=3):
    """Return the list of station records for rain-day d (cached on disk once complete)."""
    os.makedirs(RAW_DIR, exist_ok=True)
    path = _cache_path(d)
    if os.path.exists(path) and not force and (OFFLINE or rain_day_is_complete(d)):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    if OFFLINE:
        raise FileNotFoundError(f"no cached file for {d}")

    url = BASE.format(d=d.isoformat()) + f"?t={int(time.time() * 1000)}"
    last_err = None
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=60)
            if r.status_code == 404:
                raise FileNotFoundError(f"TN-SMART has no file for {d} yet")
            r.raise_for_status()
            data = r.json()
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            return data
        except FileNotFoundError:
            raise
        except Exception as e:  # network hiccup: retry
            last_err = e
            time.sleep(3 * (attempt + 1))
    # fall back to an older cached copy if the network failed
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    raise RuntimeError(f"Could not download {d}: {last_err}")


def station_totals(records):
    """Turn raw records into a list of dicts: district, station, lat, lon, rain (None = not reported)."""
    out = []
    for s in records:
        try:
            lat = float(s.get("latitude"))
            lon = float(s.get("longitude"))
        except (TypeError, ValueError):
            continue
        # a few feeds swap lat/lon; Tamil Nadu is lat 8-14, lon 76-81
        if lat > 70 and lon < 20:
            lat, lon = lon, lat
        if not (7.5 <= lat <= 14.0 and 75.8 <= lon <= 80.6):
            continue
        tot = s.get("total")
        rain = None
        if isinstance(tot, (int, float)):
            rain = float(tot)
            if rain < 0 or rain > 1500:  # obvious sensor error
                rain = None
        out.append({
            "district": (s.get("district_name") or "").strip(),
            "station": (s.get("station_name") or "").strip(),
            "lat": lat,
            "lon": lon,
            "rain": rain,
        })
    return out


def hourly_stations(d: date):
    """Hour-by-hour rain for rain-day d: (station list, hours) where hours is a (24 x stations) list of
    mm in each hour, 08:30-09:30 first. Only stations with a 24-hour total are listed; a blank hour
    counts as 0."""
    recs = download_day(d)
    stations, rows = [], []
    for s, r in zip(station_totals(recs), _with_coords(recs)):
        if s["rain"] is None:
            continue
        hrs = []
        for k in _HOUR_KEYS:
            v = r.get(k)
            hrs.append(float(v) if isinstance(v, (int, float)) and 0 <= v <= 500 else 0.0)
        stations.append(s)
        rows.append(hrs)
    return stations, [list(h) for h in zip(*rows)] if rows else [[] for _ in _HOUR_KEYS]


_HOUR_KEYS = [f"{(9 + i - 1) % 12 + 1:02d}:30 {'AM' if (9 + i) % 24 < 12 else 'PM'}" for i in range(24)]


def _with_coords(records):
    """The raw records that station_totals() keeps, in the same order."""
    out = []
    for s in records:
        try:
            lat, lon = float(s.get("latitude")), float(s.get("longitude"))
        except (TypeError, ValueError):
            continue
        if lat > 70 and lon < 20:
            lat, lon = lon, lat
        if 7.5 <= lat <= 14.0 and 75.8 <= lon <= 80.6:
            out.append(s)
    return out


LIVE_DIR = os.path.join(RAW_DIR, "live")


def running_day(now=None) -> date:
    """The rain-day (24 h ending 08:30 on that date) that is still filling up at `now`.
    A check that starts late, before 09:30, still counts towards the day that just ended."""
    now = now or datetime.now(IST)
    t = now.hour * 60 + now.minute
    return now.date() + timedelta(days=1) if t >= 9 * 60 + 30 else now.date()


def live_totals(d: date, verbose=True, fetch=None, tag=""):
    """Rain since 08:30 IST for the running rain-day d, added up over every check.

    TN-SMART's running file holds each station's total so far. Each check takes the fresh file and
    keeps, station by station, the larger of the saved total and the new one: rain a station
    reported at an earlier check is never lost if a later file drops it or shows the station as
    blank, and stations that start reporting later in the day are added when they appear.
    The saved totals are in data/raw/live/<tag><d>.json. `fetch(d)` gives the fresh station list
    (default: TN-SMART's running file); Karnataka passes ksndmc_fetch.day_stations with tag "ka_".
    Returns (station list, changed) where changed is False when no station has new rain or
    newly reported since the last check."""
    os.makedirs(LIVE_DIR, exist_ok=True)
    path = os.path.join(LIVE_DIR, f"{tag}{d.isoformat()}.json")
    saved = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for s in json.load(f)["stations"]:
                saved[(s["district"], s["station"], round(s["lat"], 3), round(s["lon"], 3))] = s

    try:
        fresh = fetch(d) if fetch else station_totals(download_day(d, force=not OFFLINE))
    except FileNotFoundError:
        fresh = []
    changed, added, rose = not saved, 0, 0
    for s in fresh:
        key = (s["district"], s["station"], round(s["lat"], 3), round(s["lon"], 3))
        old = saved.get(key)
        if old is None or old["rain"] is None:
            if s["rain"] is not None:
                added += 1
                changed = True
            if old is None or s["rain"] is not None:
                saved[key] = s
        elif s["rain"] is not None and s["rain"] > old["rain"]:
            rose += 1
            changed = True
            saved[key] = dict(old, rain=s["rain"])

    stations = list(saved.values())
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"rain_day": d.isoformat(), "checked": datetime.now(IST).isoformat(timespec="minutes"),
                   "stations": stations}, f, ensure_ascii=False)
    # drop saved totals older than a week
    for name in os.listdir(LIVE_DIR):
        try:
            if date.fromisoformat(name[-15:-5]) < d - timedelta(days=7):
                os.remove(os.path.join(LIVE_DIR, name))
        except ValueError:
            pass
    if verbose:
        rep = sum(1 for s in stations if s["rain"] is not None)
        print(f"  {d} (running)  {rep} stations so far; {added} started reporting, {rose} had more rain")
    return stations, changed


def load_days(start: date, end: date, verbose=True):
    """Download every rain-day from start to end (inclusive). Returns {date: [station dicts]}."""
    days = {}
    d = start
    while d <= end:
        try:
            recs = download_day(d)
            days[d] = station_totals(recs)
            if verbose:
                rep = sum(1 for s in days[d] if s["rain"] is not None)
                print(f"  {d}  {rep} stations reported")
        except FileNotFoundError as e:
            if verbose:
                print(f"  {d}  skipped ({e})")
        d += timedelta(days=1)
    return days


if __name__ == "__main__":
    import sys
    d = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else today_ist()
    st = station_totals(download_day(d))
    rep = [s for s in st if s["rain"] is not None]
    rep.sort(key=lambda s: -s["rain"])
    print(f"{d}: {len(st)} stations, {len(rep)} reported")
    for s in rep[:10]:
        print(f"  {s['rain']:7.1f}  {s['station']} ({s['district']})")
