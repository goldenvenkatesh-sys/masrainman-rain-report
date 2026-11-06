"""
MasRainman · "Past 24 Hours Observed Rainfall" for KTCC only.

KTCC = Kancheepuram, Tiruvallur, Chennai, Chengalpattu. Same 1080x1350, 36.5 s video as
`python rain_report.py live24`, but the map, the leaders, the peak-hour card, the station count,
the toppers, the rain clock and the model scorecard all use only the gauges inside those four districts.

Gauges come from two sources, added together:
  * TN-SMART hourly JSON (tnsmart_fetch.py), kept when the gauge's district is one of the four
  * IMD hourly gauges (AWS / ARG) from a file you pass with --imd (CSV or JSON, format below);
    an IMD gauge within 2 km of a TN-SMART gauge is treated as the same gauge and skipped, the rest
    are added if they fall inside the four districts

  python ktcc.py                          # latest complete rain-day (24 h ending 08:30 IST)
  python ktcc.py --date 2026-11-05
  python ktcc.py --date 2026-11-05 --imd imd_2026-11-05.csv
  python ktcc.py live                     # "rain so far" picture for the running day (hourly, like Tamil Nadu)
  python ktcc.py live --always            # ...even if no new rain since the last check

IMD file format
  CSV  : station,district,lat,lon,h01,h02,...,h24      (district may be blank: found from lat/lon)
         h01 = rain in 08:30-09:30 IST, h24 = rain in 07:30-08:30 IST (same order as TN-SMART), mm
  JSON : [{"station":..., "district":..., "lat":..., "lon":..., "hours":[24 numbers]}, ...]

Needs the same folder as rain_report.py (it reuses its fonts, terrain, colour ramp and logo).
"""
import argparse
import csv
import json
import math
import os
import sys
from datetime import date, timedelta

import numpy as np
from shapely.geometry import Point, shape
from shapely.ops import unary_union

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import tn_names                      # noqa: E402
import tnsmart_fetch as tf           # noqa: E402
import rain_report as rr             # noqa: E402
import live24                        # noqa: E402

KTCC = ["Kancheepuram", "Tiruvallur", "Chennai", "Chengalpattu"]      # TN-SMART spellings
GEO_FILE = "ktcc_districts.geojson"
SAME_GAUGE_KM = 2.0


# ----------------------------------------------------------------- boundary
def _build_geojson():
    """data/ktcc_districts.geojson = the four districts cut out of data/tn_districts.geojson."""
    out = os.path.join(HERE, "data", GEO_FILE)
    with open(os.path.join(HERE, "data", "tn_districts.geojson"), encoding="utf-8") as f:
        gj = json.load(f)
    keep = [ft for ft in gj["features"] if tn_names.canon(ft["properties"]["district"]) in KTCC]
    found = sorted(tn_names.canon(ft["properties"]["district"]) for ft in keep)
    if found != sorted(KTCC):
        raise SystemExit(f"tn_districts.geojson is missing districts: have {found}")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection", "features": keep}, f)
    return out


# ----------------------------------------------------------------- data
def _tn_smart(d):
    """TN-SMART hourly gauges of the four districts: (stations, 24 x n hours)."""
    stations, hours = tf.hourly_stations(d)
    idx = [i for i, s in enumerate(stations) if tn_names.canon(s["district"]) in KTCC]
    st = [dict(stations[i], district=tn_names.canon(stations[i]["district"]), src="TN-SMART") for i in idx]
    hr = [[hours[h][i] for i in idx] for h in range(24)] if idx else [[] for _ in range(24)]
    return st, hr


def _read_imd(path):
    rows = []
    if path.lower().endswith(".json"):
        with open(path, encoding="utf-8") as f:
            for r in json.load(f):
                rows.append((r.get("station", ""), r.get("district", ""), r["lat"], r["lon"], r["hours"]))
    else:
        with open(path, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                hrs = [r.get(f"h{h:02d}") for h in range(1, 25)]
                rows.append((r.get("station", ""), r.get("district", ""), r["lat"], r["lon"], hrs))
    out = []
    for name, dist, lat, lon, hrs in rows:
        try:
            lat, lon = float(lat), float(lon)
            hrs = [max(0.0, float(v)) if v not in (None, "", "NA", "-") else 0.0 for v in hrs]
        except (TypeError, ValueError):
            continue
        if len(hrs) != 24 or max(hrs) > 500:
            continue
        out.append((str(name).strip(), str(dist).strip(), lat, lon, hrs))
    return out


def _district_at(geo, lat, lon):
    p = Point(lon, lat)
    for name, g in geo.districts.items():
        if g.buffer(0.002).contains(p):
            return name
    return None


def _merge_imd(stations, hours, imd, geo):
    """Add IMD gauges inside the four districts that are not already a TN-SMART gauge."""
    if not imd:
        return stations, hours, 0
    have = geo.km([s["lon"] for s in stations], [s["lat"] for s in stations]) if stations else np.zeros((0, 2))
    added = []
    for name, dist, lat, lon, hrs in imd:
        d = _district_at(geo, lat, lon)
        if d is None:
            continue                                      # outside KTCC
        xy = geo.km([lon], [lat])[0]
        if len(have) and np.min(np.hypot(*(have - xy).T)) < SAME_GAUGE_KM:
            continue                                      # same gauge as a TN-SMART one
        added.append((name or "IMD gauge", d, lat, lon, hrs))
        have = np.vstack([have, xy])
    for name, d, lat, lon, hrs in added:
        stations.append({"district": d, "station": name, "lat": lat, "lon": lon, "rain": float(sum(hrs)),
                         "src": "IMD"})
    cols = [[*hours[h], *[a[4][h] for a in added]] for h in range(24)]
    return stations, cols, len(added)


def make_hourly(imd_path=None):
    geo = rr.Geo(rr.REGIONS["ktcc"])

    def hourly(d):
        stations, hours = _tn_smart(d)
        n_tn = len(stations)
        n_imd = 0
        if imd_path:
            stations, hours, n_imd = _merge_imd(stations, hours, _read_imd(imd_path), geo)
        print(f"  KTCC gauges: {n_tn} TN-SMART + {n_imd} IMD = {len(stations)}")
        return stations, hours
    return hourly


def _live_fetch(d):
    """TN-SMART's running file for rain-day d, only the gauges of the four districts."""
    out = []
    for s in tf.station_totals(tf.download_day(d, force=not tf.OFFLINE)):
        c = tn_names.canon(s["district"])
        if c in KTCC:
            out.append(dict(s, district=c))
    return out


# ----------------------------------------------------------------- registration
def register(imd_path=None):
    _build_geojson()
    rr.REGIONS["ktcc"] = {
        "name": "Chennai Region", "geo": GEO_FILE, "canon": tn_names.canon, "langs": ["ta"],
        "radius": 18,                 # dense gauges over a small area: keep the rain field local
        "source": "TN-SMART (TNSDMA)" + (" + IMD" if imd_path else ""), "pill": {"ta": "KTCC"},
        "load": None, "live": _live_fetch,
    }
    live24.REGIONS24["ktcc"] = {
        "native": "KTCC", "native_font": "MuktaMalar-ExtraBold.ttf", "hourly": make_hourly(imd_path),
        "source": "TN-SMART (TNSDMA)" + (" + IMD" if imd_path else ""), "network": "Rain gauge",
        "dem_zoom": 10,
        "sub": "Kancheepuram · Tiruvallur · Chennai · Chengalpattu  ·  hourly rain gauges",
        "zf": 2.0,
        "peaks": [],
    }
    live24.ZF = 2.0                   # the area is small: follow the heaviest rain at a gentler zoom


def main():
    ap = argparse.ArgumentParser(description="KTCC rainfall videos")
    ap.add_argument("mode", nargs="?", choices=["live24", "live"], default="live24",
                    help="live24 = past 24 hours video (default); live = rain so far in the running day")
    ap.add_argument("--always", action="store_true", help="live: make the picture even if nothing changed")
    ap.add_argument("--date", help="rain-day (24 h ending 08:30 IST on this date); default: latest complete")
    ap.add_argument("--imd", help="IMD hourly gauge file (CSV or JSON, see top of this file)")
    ap.add_argument("--out", default=rr.OUT_DIR)
    a = ap.parse_args()
    if a.mode == "live":
        register(None)
        sys.argv = ["rain_report.py", "live", "--region", "ktcc"] + (["--always"] if a.always else [])
        return rr.main()
    today = tf.today_ist()
    latest = today if tf.rain_day_is_complete(today) else today - timedelta(days=1)
    day = date.fromisoformat(a.date) if a.date else latest
    register(a.imd)
    os.makedirs(a.out, exist_ok=True)
    live24.run("ktcc", day, a.out)


if __name__ == "__main__":
    main()
