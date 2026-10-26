"""
Andhra Pradesh live rain feed: the DES / APSDPS real-time weather network (about 2,200 AWS and rain gauges).

The station map on desweather.ap.gov.in (Livemap/Apsdpsmapsv1.html) reads its markers from
    http://desweather.ap.gov.in/webservice/rest/json1
a JSON list with one record per station:
    clientid, dmcode, dcode, latitude, longitude, location, mandal, district, rain, temp, wind, ..., ldate
`rain` is the rain since 08:30 IST of the running rain-day (the same numbers as Realtime/Rainfall.jsp),
-1 means the station is not reporting, and `ldate` ("25/10/2026 06:00:00 AM") is its last report.
Every station is listed, dry ones with 0.

Finished rain-days come from http://desweather.ap.gov.in/Realtime/Rainfall24.jsp, a table of the last full
24 hours (08:30 to 08:30) by station id, district, mandal and location, without coordinates. update_daily()
takes the positions from the live feed and keeps every station in data/ap/stations.json (so a station that
drops out of the feed still has one), and saves the day's rain by station id to data/ap/daily/<rain-day>.json
for the daily, multi-day and monthly reports. Only the latest day is on the site, so the archive starts on 2026-10-24.

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
DAY_URL = "http://desweather.ap.gov.in/Realtime/Rainfall24.jsp"
HERE = os.path.dirname(os.path.abspath(__file__))
AP_DIR = os.path.join(HERE, "data", "ap")
DAILY = os.path.join(AP_DIR, "daily")
STATIONS = os.path.join(AP_DIR, "stations.json")
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


def _get(url, retries=3):
    last = None
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=(20, 120))
            r.raise_for_status()
            return r.text
        except Exception as e:
            last = e
            time.sleep(10 * (attempt + 1))
    raise RuntimeError(f"DES Andhra Pradesh {url.rsplit('/', 1)[-1]} failed: {last}")


def parse_day_table(html):
    """Rainfall24.jsp -> (rain-day, [(id, district, mandal, location, rain)])."""
    m = re.search(r"Data from\s*(\d+/\d+/\d{4})\s*08:30\s*AM\s*to\s*(\d+/\d+/\d{4})", html)
    if not m:
        raise ValueError("Rainfall24.jsp: no date range found")
    day = datetime.strptime(m.group(2), "%d/%m/%Y").date()
    body = re.sub(r"<!--.*?-->", "", html[html.find("<tbody"):], flags=re.S)
    rows = []
    for tr in re.split(r"<tr[^>]*>", body)[1:]:
        c = [re.sub(r"<[^>]+>", "", x).strip() for x in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        if len(c) >= 6 and c[1].isdigit():
            try:
                rain = float(c[5])
            except ValueError:
                rain = None
            rows.append((c[1], c[2], c[3], c[4], rain if rain is not None and 0 <= rain <= 1500 else None))
    return day, rows


def _positions(live_rows):
    """Station id -> {lat, lon, district, name}, from the live feed plus the saved list."""
    known = {}
    if os.path.exists(STATIONS):
        with open(STATIONS, encoding="utf-8") as f:
            known = json.load(f)
    for r in live_rows:
        try:
            lat, lon = float(r["latitude"]), float(r["longitude"])
        except (KeyError, TypeError, ValueError):
            continue
        if 12 < lat < 20 and 76 < lon < 85.5:
            known[str(r["clientid"])] = {"lat": round(lat, 5), "lon": round(lon, 5), "district": canon(r.get("district")),
                                         "name": str(r.get("location") or r.get("mandal")).replace("_", " ").strip(),
                                         "mandal": r.get("mandal")}
    return known


def update_daily(want=None, verbose=True, wait=True):
    """Save the last finished rain-day from Rainfall24.jsp to data/ap/daily/<day>.json.
    With `want`, wait (up to ~40 min) for the page to move on to that rain-day.
    With wait=False (the hourly live backup), look once and save nothing if the page has not moved on yet,
    so the live picture is not held up past the job's time limit."""
    for attempt in range(9 if wait else 1):
        if os.environ.get("APSDPS_DAY_SAMPLE"):
            with open(os.environ["APSDPS_DAY_SAMPLE"], encoding="utf-8", errors="replace") as f:
                html = f.read()
        else:
            html = _get(DAY_URL)
        day, rows = parse_day_table(html)
        if want is None or day >= want or os.environ.get("APSDPS_DAY_SAMPLE"):
            break
        if not wait:
            print(f"  Rainfall24.jsp still shows {day}, not {want} yet; leaving it for a later run")
            return None
        print(f"  Rainfall24.jsp still shows {day}; waiting for {want}")
        time.sleep(300)
    try:
        live = fetch_raw()
    except RuntimeError as e:  # positions then come from the saved list only
        print(f"  {e}")
        live = []
    known = _positions(live)
    os.makedirs(DAILY, exist_ok=True)
    nopos = 0
    for sid, dist, mandal, loc, rain in rows:
        g = known.setdefault(sid, {"lat": None, "lon": None})
        nopos += g["lat"] is None
        g.update(district=canon(dist), name=loc.replace("_", " ").strip() or mandal, mandal=mandal)
    with open(STATIONS, "w", encoding="utf-8") as f:              # one station per line, for readable diffs
        f.write("{\n" + ",\n".join(f"{json.dumps(k)}:{json.dumps(v, ensure_ascii=False)}"
                                    for k, v in sorted(known.items())) + "\n}\n")
    rain = {sid: r for sid, _, _, _, r in rows}
    with open(os.path.join(DAILY, f"{day.isoformat()}.json"), "w", encoding="utf-8") as f:
        json.dump({"rain_day": day.isoformat(), "rain": rain}, f, separators=(",", ":"))
    if verbose:
        rep = sum(1 for r in rain.values() if r is not None)
        print(f"  DES AP rain-day {day}: {rep} stations, {sum(1 for r in rain.values() if r)} with rain, "
              f"{nopos} without a position")
    return day


def load_days(start, end, verbose=True):
    """Same shape as tnsmart_fetch.load_days: {date: [ {district, station, lat, lon, rain} ]}."""
    days = {}
    known = {}
    if os.path.exists(STATIONS):
        with open(STATIONS, encoding="utf-8") as f:
            known = json.load(f)
    d = start
    while d <= end:
        p = os.path.join(DAILY, f"{d.isoformat()}.json")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                rain = json.load(f)["rain"]
            days[d] = [{"district": g["district"], "station": g["name"], "lat": g["lat"], "lon": g["lon"], "rain": r}
                       for sid, r in rain.items() for g in [known.get(sid)] if g]
            if verbose:
                print(f"  {d}  {sum(1 for r in days[d] if r['rain'] is not None)} stations")
        elif verbose:
            print(f"  {d}  no DES Andhra Pradesh day archived")
        d += timedelta(days=1)
    return days


def day_stations(d: date):
    """For tnsmart_fetch.live_totals: the running day's totals so far (d is the running rain-day)."""
    return stations(d)


if __name__ == "__main__":
    import sys
    if sys.argv[1:2] == ["daily"]:
        # daily [--if-missing]: save the finished rain-day; --if-missing does nothing once it is saved
        today = tf.today_ist()
        want = today if tf.rain_day_is_complete(today) else None
        last = want or today - timedelta(days=1)
        if "--if-missing" in sys.argv and os.path.exists(os.path.join(DAILY, f"{last.isoformat()}.json")):
            print(f"  DES AP rain-day {last} already saved")
        else:
            # --if-missing is the hourly backup: never wait for the page, the live picture comes next
            update_daily(want=want, wait="--if-missing" not in sys.argv)
        raise SystemExit
    st = stations(tf.running_day(datetime.now(tf.IST)))
    st.sort(key=lambda s: -(s["rain"] or 0))
    for s in st[:10]:
        print(f"  {s['rain']:7.1f}  {s['station']} ({s['district']})")
