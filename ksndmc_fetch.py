"""
KSNDMC (Karnataka) live rain-gauge feed.

The weather dashboard at ksndmc.org/default.aspx/WebDashboard shows a map from https://ksndmc.org:122/.
That page asks its own server for the 15-minute rainfall with an ASP.NET callback: it posts the page's
hidden form fields back with __CALLBACKID=__Page and __CALLBACKPARAM=showRain15Q. The reply is
    0|showRain15$ RAINFALL (mm)${"gisQuery": ..., "dtTable": [...], "dtTableSymbology": ...}
and dtTable has one row per telemetric rain gauge with rain since 08:30 IST so far:
    RAINGAUGE, RAIN, RAINTIME, DISTRICT, TALUKNAME, HOBLI, HOBLICODE, RAINDATEs
Only gauges with rain (0.5 mm or more) are listed, and there are no coordinates.

Gauge positions are in data/ka_gauges.csv (gram panchayat / hobli names matched to village centroids,
see README). A gauge that is not in that file is placed at the middle of the known gauges of the same
hobli (first 6 digits of HOBLICODE) or taluk (first 4), and remembered in data/raw/ka_gauges_new.json.
Known gauges that are not in the reply had no rain, so they count as 0 mm.
"""
import csv
import json
import os
import re
import time
from datetime import date

import requests

import tnsmart_fetch as tf

URL = "https://ksndmc.org:122/"
HERE = os.path.dirname(os.path.abspath(__file__))
GAUGES = os.path.join(HERE, "data", "ka_gauges.csv")
NEW_GAUGES = os.path.join(tf.RAW_DIR, "ka_gauges_new.json")
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130 Safari/537.36 "
                         "(MasRainman rainfall report)"}


def fetch_raw(retries=3):
    """Rows of the dashboard's 15-minute rain table (rain since 08:30 IST)."""
    if os.environ.get("KSNDMC_SAMPLE"):  # a saved table, for testing offline
        with open(os.environ["KSNDMC_SAMPLE"], encoding="utf-8") as f:
            return json.load(f)
    last = None
    for attempt in range(retries):
        try:
            s = requests.Session()
            s.headers.update(HEADERS)
            page = s.get(URL, timeout=90).text
            form = dict(re.findall(r'<input type="hidden" name="([^"]+)" id="[^"]*" value="([^"]*)"', page))
            form.update(__CALLBACKID="__Page", __CALLBACKPARAM="showRain15Q")
            r = s.post(URL, data=form, timeout=240, headers={"Referer": URL})
            r.raise_for_status()
            parts = r.text.split("$", 2)
            if len(parts) < 3 or "showRain15" not in parts[0]:
                raise ValueError(f"unexpected reply: {r.text[:120]!r}")
            table = json.loads(parts[2])["dtTable"]
            return json.loads(table) if isinstance(table, str) else table
        except Exception as e:  # network hiccup or a slow server: retry
            last = e
            time.sleep(10 * (attempt + 1))
    raise RuntimeError(f"KSNDMC live feed failed: {last}")


def _gauges():
    known = {}
    with open(GAUGES, encoding="utf-8") as f:
        for g in csv.DictReader(f):
            if g["lat"]:
                known[f'{g["code"]}|{g["gauge"]}'] = dict(g, lat=float(g["lat"]), lon=float(g["lon"]))
    if os.path.exists(NEW_GAUGES):
        with open(NEW_GAUGES, encoding="utf-8") as f:
            for g in json.load(f):
                known.setdefault(f'{g["code"]}|{g["gauge"]}', g)
    return known


def _place(code, known):
    for n in (6, 4):
        sib = [g for g in known.values() if g["code"][:n] == code[:n]]
        if sib:
            return (round(sum(g["lat"] for g in sib) / len(sib), 4),
                    round(sum(g["lon"] for g in sib) / len(sib), 4))
    return None


def stations(rows=None, verbose=True):
    """Station list in the shape tnsmart_fetch uses: district, station, lat, lon, rain."""
    rows = fetch_raw() if rows is None else rows
    known = _gauges()
    seen, new = {}, []
    for r in rows:
        code = str(r.get("HOBLICODE") or "").strip()
        key = f'{code}|{r.get("RAINGAUGE")}'
        try:
            rain = float(r.get("RAIN"))
        except (TypeError, ValueError):
            continue
        if not code or not 0 <= rain <= 1500:
            continue
        g = known.get(key)
        if g is None:
            xy = _place(code, known)
            if xy is None:
                continue
            g = {"code": code, "gauge": str(r.get("RAINGAUGE")), "district": r.get("DISTRICT", ""), "taluk": r.get("TALUKNAME", ""),
                 "hobli": r.get("HOBLI", ""), "lat": xy[0], "lon": xy[1]}
            known[key] = g
            new.append(g)
        seen[key] = max(rain, seen.get(key, 0.0))
    if new:
        old = []
        if os.path.exists(NEW_GAUGES):
            with open(NEW_GAUGES, encoding="utf-8") as f:
                old = json.load(f)
        os.makedirs(tf.RAW_DIR, exist_ok=True)
        with open(NEW_GAUGES, "w", encoding="utf-8") as f:
            json.dump(old + new, f, ensure_ascii=False)
    out = [{"district": g["district"], "station": g["hobli"].replace("_", " "), "lat": g["lat"], "lon": g["lon"],
            "rain": seen.get(key, 0.0)} for key, g in known.items()]
    if verbose:
        print(f"  KSNDMC: {len(rows)} gauges with rain, {len(new)} new gauge(s); {len(out)} gauges on the map")
    return out


def day_stations(d: date):
    """For tnsmart_fetch.live_totals: the running day's totals so far (d is the running rain-day)."""
    return stations()


if __name__ == "__main__":
    st = stations()
    st.sort(key=lambda s: -s["rain"])
    for s in st[:10]:
        print(f"  {s['rain']:7.1f}  {s['station']} ({s['district']})")
