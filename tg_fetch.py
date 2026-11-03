"""
Telangana rain: the TGDPS (Telangana Development Planning Society) AWS / rain-gauge network.

TGDPS's website does not answer from outside India, so GitHub's runners cannot read it. The work is split:

  On the PC in India (tg_pc.py, every hour from Windows Task Scheduler):
      python tg_fetch.py live      -> data/tg/live/<rain-day>.json   rain since 08:30 so far, every station
      python tg_fetch.py daily     -> data/tg/daily/<rain-day>.json  the finished 24 hours (08:30 to 08:30)
  and pushes data/tg to GitHub.

  On GitHub (tg-report.yml, started by that push):
      rain_report.py live  --region tg   reads data/tg/live/<rain-day>.json  (day_stations)
      rain_report.py daily --region tg   reads data/tg/daily/               (load_days)

Station records use the same shape as the other states: {district, station, lat, lon, rain}, rain in mm,
None = not reporting. Station positions are kept in data/tg/stations.json so a day saved without
coordinates can still be mapped.
"""
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta

import tnsmart_fetch as tf

HERE = os.path.dirname(os.path.abspath(__file__))
TG_DIR = os.path.join(HERE, "data", "tg")
LIVE = os.path.join(TG_DIR, "live")
DAILY = os.path.join(TG_DIR, "daily")
STATIONS = os.path.join(TG_DIR, "stations.json")
BOX = (15.7, 77.1, 19.95, 81.4)            # lat0, lon0, lat1, lon1 around Telangana

# TGDPS spelling -> name used in data/tg_districts.geojson
_ALIAS = {"rangareddy": "Ranga Reddy", "rrdistrict": "Ranga Reddy",
          "medchal": "Medchal Malkajgiri", "medchalmalkajgiri": "Medchal Malkajgiri",
          "kumurambheem": "Kumuram Bheem Asifabad", "komarambheem": "Kumuram Bheem Asifabad",
          "kumurambheemasifabad": "Kumuram Bheem Asifabad", "komarambheemasifabad": "Kumuram Bheem Asifabad",
          "asifabad": "Kumuram Bheem Asifabad",
          "warangalurban": "Hanumakonda", "hanamkonda": "Hanumakonda", "hanumakonda": "Hanumakonda",
          "warangalrural": "Warangal", "warangal": "Warangal",
          "jayashankarbhupalpally": "Jayashankar Bhupalapally", "jayashankar": "Jayashankar Bhupalapally",
          "bhupalpally": "Jayashankar Bhupalapally", "bhupalapally": "Jayashankar Bhupalapally",
          "jogulambagadwal": "Jogulamba Gadwal", "gadwal": "Jogulamba Gadwal",
          "bhadradrikothagudem": "Bhadradri Kothagudem", "kothagudem": "Bhadradri Kothagudem",
          "yadadribhuvanagiri": "Yadadri Bhuvanagiri", "yadadri": "Yadadri Bhuvanagiri",
          "bhuvanagiri": "Yadadri Bhuvanagiri",
          "rajannasircilla": "Rajanna Sircilla", "sircilla": "Rajanna Sircilla",
          "jangaon": "Jangaon", "jangoan": "Jangaon", "jagtial": "Jagtial", "jagityal": "Jagtial",
          "mahabubnagar": "Mahabubnagar", "mahbubnagar": "Mahabubnagar", "mahabubabad": "Mahabubabad",
          "mahbubabad": "Mahabubabad", "nagarkurnool": "Nagarkurnool", "peddapalli": "Peddapalli",
          "peddapally": "Peddapalli"}


def canon(d):
    d = (d or "").strip()
    key = re.sub(r"[^a-z]", "", d.lower())
    return _ALIAS.get(key) or " ".join(w.capitalize() for w in re.sub(r"\s+", " ", d).split())


# ------------------------------------------------------------------ PC side: reading TGDPS
def fetch_live():
    """[(station id, district, mandal, name, lat, lon, rain since 08:30)] from TGDPS, now.

    Filled in once TGDPS's page format has been checked from the PC (it cannot be seen from outside India)."""
    raise NotImplementedError("TGDPS reader not written yet: run from the PC so its page format can be checked")


def fetch_day():
    """(rain-day, [(station id, district, mandal, name, rain)]) for the last finished 24 hours."""
    raise NotImplementedError("TGDPS reader not written yet: run from the PC so its page format can be checked")


def _load_known():
    if os.path.exists(STATIONS):
        with open(STATIONS, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_known(known):
    os.makedirs(TG_DIR, exist_ok=True)
    with open(STATIONS, "w", encoding="utf-8") as f:              # one station per line, for readable diffs
        f.write("{\n" + ",\n".join(f"{json.dumps(k)}:{json.dumps(v, ensure_ascii=False)}"
                                    for k, v in sorted(known.items())) + "\n}\n")


def _inside(lat, lon):
    return lat is not None and lon is not None and BOX[0] < lat < BOX[2] and BOX[1] < lon < BOX[3]


def save_live(verbose=True):
    """PC: save rain since 08:30 so far to data/tg/live/<rain-day>.json (stations without a position are left out)."""
    now = datetime.now(tf.IST)
    day = tf.running_day(now)
    known = _load_known()
    out = []
    for sid, dist, mandal, name, lat, lon, rain in fetch_live():
        sid = str(sid)
        if _inside(lat, lon):
            known[sid] = {"lat": round(lat, 5), "lon": round(lon, 5), "district": canon(dist),
                          "name": name, "mandal": mandal}
        g = known.get(sid)
        if not g:
            continue
        if rain is not None and not 0 <= rain <= 1500:
            rain = None
        out.append({"district": g["district"], "station": g["name"], "lat": g["lat"], "lon": g["lon"], "rain": rain})
    _save_known(known)
    os.makedirs(LIVE, exist_ok=True)
    with open(os.path.join(LIVE, f"{day.isoformat()}.json"), "w", encoding="utf-8") as f:
        json.dump({"rain_day": day.isoformat(), "fetched": now.isoformat(timespec="minutes"), "stations": out},
                  f, ensure_ascii=False, separators=(",", ":"))
    for old in sorted(os.listdir(LIVE))[:-3]:                     # keep the last 3 running days
        os.remove(os.path.join(LIVE, old))
    if verbose:
        print(f"  TGDPS live {day}: {len(out)} stations, {sum(1 for s in out if s['rain'])} with rain")
    return day


def save_daily(verbose=True):
    """PC: save the finished rain-day to data/tg/daily/<rain-day>.json (rain by station id)."""
    day, rows = fetch_day()
    known = _load_known()
    for sid, dist, mandal, name, _ in rows:
        g = known.setdefault(str(sid), {"lat": None, "lon": None})
        g.update(district=canon(dist), name=name or mandal, mandal=mandal)
    _save_known(known)
    os.makedirs(DAILY, exist_ok=True)
    rain = {str(sid): (r if r is not None and 0 <= r <= 1500 else None) for sid, _, _, _, r in rows}
    with open(os.path.join(DAILY, f"{day.isoformat()}.json"), "w", encoding="utf-8") as f:
        json.dump({"rain_day": day.isoformat(), "rain": rain}, f, separators=(",", ":"))
    if verbose:
        nopos = sum(1 for sid in rain if known.get(sid, {}).get("lat") is None)
        print(f"  TGDPS rain-day {day}: {sum(1 for r in rain.values() if r is not None)} stations, "
              f"{sum(1 for r in rain.values() if r)} with rain, {nopos} without a position")
    return day


# ------------------------------------------------------------------ GitHub side: reading the saved files
def day_stations(d: date):
    """For tnsmart_fetch.live_totals: the PC's latest snapshot of rain-day d (empty if it has not sent one)."""
    p = os.path.join(LIVE, f"{d.isoformat()}.json")
    if not os.path.exists(p):
        print(f"  no Telangana snapshot for {d} yet (data/tg/live)")
        return []
    with open(p, encoding="utf-8") as f:
        snap = json.load(f)
    print(f"  TGDPS snapshot from {snap.get('fetched')}: {len(snap['stations'])} stations")
    return snap["stations"]


def load_days(start, end, verbose=True):
    """Same shape as tnsmart_fetch.load_days: {date: [ {district, station, lat, lon, rain} ]}."""
    known = _load_known()
    days = {}
    d = start
    while d <= end:
        p = os.path.join(DAILY, f"{d.isoformat()}.json")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                rain = json.load(f)["rain"]
            days[d] = [{"district": g["district"], "station": g["name"], "lat": g["lat"], "lon": g["lon"], "rain": r}
                       for sid, r in rain.items() for g in [known.get(sid)] if g and g.get("lat") is not None]
            if verbose:
                print(f"  {d}  {sum(1 for r in days[d] if r['rain'] is not None)} stations")
        elif verbose:
            print(f"  {d}  no TGDPS Telangana day saved")
        d += timedelta(days=1)
    return days


if __name__ == "__main__":
    what = sys.argv[1:2] or ["live"]
    if what == ["live"]:
        save_live()
    elif what == ["daily"]:
        save_daily()
    else:
        raise SystemExit("usage: python tg_fetch.py live|daily")
