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


def live_totals(d: date, verbose=True, fetch=None, tag="", upto=None):
    """Rain since 08:30 IST for the running rain-day d, added up over every check.

    TN-SMART's running file holds each station's total so far. Each check takes the fresh file and
    keeps, station by station, the larger of the saved total and the new one: rain a station
    reported at an earlier check is never lost if a later file drops it or shows the station as
    blank, and stations that start reporting later in the day are added when they appear.
    The saved totals are in data/raw/live/<tag><d>.json. `fetch(d)` gives the fresh station list
    (default: TN-SMART's running file); Karnataka passes ksndmc_fetch.day_stations with tag "ka_".
    Returns (station list, changed) where changed is False when no station has new rain or
    newly reported since the last check.

    For the other states (tag set) each check also adds a snapshot of the totals, timed `upto`, to
    data/raw/live/<tag>snaps_<d>.json; snapshot_hours() turns those into the hour-by-hour rain of the
    past-24-hours video (live24), since KSNDMC, DES and TGDPS only give the total so far."""
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
    if tag:
        _snapshot(d, tag, stations, upto or datetime.now(IST))
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


def _skey(s):
    return [s["district"], s["station"], round(s["lat"], 3), round(s["lon"], 3)]


def _snapshot(d, tag, stations, upto):
    """Add the running totals at `upto` to data/raw/live/<tag>snaps_<d>.json:
    {"rain_day", "st": [[district, station, lat, lon], ...], "snaps": [{"t": time, "v": {index: mm}}]}.
    Only stations that have reported are listed in st; v holds the ones with rain (others are 0)."""
    path = os.path.join(LIVE_DIR, f"{tag}snaps_{d.isoformat()}.json")
    j = {"rain_day": d.isoformat(), "st": [], "snaps": []}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                j = json.load(f)
        except ValueError:
            pass
    idx = {tuple(k): i for i, k in enumerate(j["st"])}
    v = {}
    for s in stations:
        if s["rain"] is None:
            continue
        k = tuple(_skey(s))
        if k not in idx:
            idx[k] = len(j["st"])
            j["st"].append(list(k))
        if s["rain"] > 0:
            v[str(idx[k])] = round(float(s["rain"]), 1)
    end = datetime(d.year, d.month, d.day, 8, 30, tzinfo=IST)
    t = min(upto, end).isoformat(timespec="minutes")
    j["snaps"] = sorted([x for x in j["snaps"] if x["t"] != t] + [{"t": t, "v": v}], key=lambda x: x["t"])
    with open(path, "w", encoding="utf-8") as f:
        json.dump(j, f, ensure_ascii=False, separators=(",", ":"))


def snapshot_hours(d: date, tag, final=None, min_snaps=6):
    """Hour-by-hour rain of rain-day d from the snapshots saved by live_totals(): (stations, hours) as
    hourly_stations() gives for TN-SMART. Between two snapshots the rain is spread evenly over the time
    between them (a missed hourly check makes that rain look steady across the gap). `final`, the station
    list of the finished day when the state publishes one (DES, TGDPS), sets each station's 08:30 total.
    Raises FileNotFoundError when there are too few snapshots (the checks had not started yet)."""
    import numpy as np
    path = os.path.join(LIVE_DIR, f"{tag}snaps_{d.isoformat()}.json")
    if not os.path.exists(path):
        raise FileNotFoundError(f"no hourly snapshots for {d} ({path})")
    with open(path, encoding="utf-8") as f:
        j = json.load(f)
    snaps, st = j["snaps"], j["st"]
    t0 = datetime(d.year, d.month, d.day, 8, 30, tzinfo=IST) - timedelta(days=1)
    hrs = [(datetime.fromisoformat(x["t"]) - t0).total_seconds() / 3600 for x in snaps]
    keep = [i for i, h in enumerate(hrs) if 0 < h <= 24]
    if len(keep) < min_snaps or not st:
        raise FileNotFoundError(f"only {len(keep)} hourly snapshots for {d}; need {min_snaps}")
    n = len(st)
    xs = [0.0] + [hrs[i] for i in keep]
    Y = np.zeros((len(xs), n))
    for r, i in enumerate(keep, 1):
        for k, v in snaps[i]["v"].items():
            Y[r, int(k)] = v
    Y = np.maximum.accumulate(Y, axis=0)            # totals never go down
    matched = 0
    if final:
        by = {tuple(k): i for i, k in enumerate(st)}
        loose = {(k[1], round(k[2], 2), round(k[3], 2)): i for i, k in enumerate(st)}
        F = Y[-1].copy()
        for s in final:
            if s.get("rain") is None:
                continue
            i = by.get(tuple(_skey(s)))
            if i is None:
                i = loose.get((s["station"], round(s["lat"], 2), round(s["lon"], 2)))
            if i is not None:
                F[i] = max(F[i], float(s["rain"]))
                matched += 1
        if xs[-1] < 24:
            xs.append(24.0)
            Y = np.vstack([Y, F])
        else:
            Y[-1] = F
    grid = np.arange(25, dtype=float)
    cum = np.empty((25, n))
    for i in range(n):
        cum[:, i] = np.interp(grid, xs, Y[:, i])
    hours = np.maximum(np.diff(cum, axis=0), 0)
    stations = [{"district": k[0], "station": k[1], "lat": k[2], "lon": k[3], "rain": float(cum[-1, i])}
                for i, k in enumerate(st)]
    print(f"  {len(keep)} hourly snapshots ({snaps[keep[0]]['t'][11:16]} to {snaps[keep[-1]]['t'][11:16]})"
          + (f", finished-day totals for {matched} stations" if final else ""))
    return stations, hours.tolist()


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
