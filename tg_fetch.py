"""
Telangana rain: the TGDPS (Telangana Development Planning Society) AWS / rain-gauge network.

TGDPS's website does not answer from outside India, so GitHub's runners cannot read it. The work is split:

  On the PC in India (tg_pc.py, every hour at :25 from Windows Task Scheduler):
      python tg_fetch.py   -> data/tg/live/<rain-day>.json   rain since 08:30 so far, every station
                              data/tg/daily/<rain-day>.json  the run between 07:30 and 08:30 also saves the
                                                             finished 24 hours (missing at most its last minutes)
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
#
# liverain.jsp   "Today's Rainfall (mm) from DD/MM/YYYY 08:30Hrs to DD/MM/YYYY HH:MMHrs": one row per station
#                WITH rain (S.No, Aws Id, District, Mandal, Location, Rainfall); dry stations are not listed.
# values.jsp?s1=<id>   one station: location, mandal, latitude, longitude.
# livejsp/<District>.jsp   district maps linking every station id (about 1,100 in all).
BASE = "https://tgdps.telangana.gov.in/"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130 Safari/537.36 "
                         "(MasRainman rainfall report)"}


# On GitHub (outside India) TGDPS is read through the relay on Vercel's Mumbai server (relay/), set by
# TGDPS_RELAY (its /api/tgdps address) and TGDPS_KEY. On the PC in India neither is set and TGDPS is read directly.
RELAY = os.environ.get("TGDPS_RELAY", "").strip()
RELAY_KEY = os.environ.get("TGDPS_KEY", "").strip()


def _get(path, retries=3):
    import requests
    if RELAY:
        page, _, s1 = path.partition("?s1=")
        url, params = RELAY, {"k": RELAY_KEY, "p": page, **({"s1": s1} if s1 else {})}
    else:
        url, params = BASE + path, None
    last = None
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=(20, 90))
            r.raise_for_status()
            return r.text
        except Exception as e:
            last = e
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"TGDPS {path} failed: {last}")


def _text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).replace("&nbsp;", " ").strip()


def fetch_live():
    """(end of period as datetime IST, [(id, district, mandal, location, rain)]) from liverain.jsp."""
    html = _get("liverain.jsp")
    # after midnight TGDPS drops the minutes: "to 05/11/2026 06Hrs" instead of "to 04/11/2026 23:00Hrs"
    m = re.search(r"from\s*(\d+/\d+/\d{4})\s*08:30\s*Hrs\s*to\s*(\d+/\d+/\d{4})\s*(\d+)(?:[:.](\d+))?\s*Hrs", _text(html))
    if not m:
        raise ValueError("liverain.jsp: no time period found")
    end = datetime.strptime(f"{m.group(2)} {m.group(3)}:{m.group(4) or '00'}", "%d/%m/%Y %H:%M").replace(tzinfo=tf.IST)
    rows = []
    for tr in re.split(r"<tr[^>]*>", html)[1:]:
        c = [_text(x) for x in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        if len(c) >= 6 and c[0].isdigit() and c[1].isdigit():
            try:
                rain = float(c[5])
            except ValueError:
                continue
            rows.append((c[1], c[2], c[3], c[4], rain))
    return end, rows


def all_station_ids():
    """Every station id on TGDPS's district maps."""
    ids = set()
    for page in sorted(set(re.findall(r"livejsp/[A-Za-z_]+\.jsp", _get("aws.jsp")))):
        ids.update(re.findall(r"\.jsp\?s1=(\d+)", _get(page)))
    return ids


def station_info(sid):
    """{lat, lon, name, mandal} from values.jsp, or None."""
    t = _text(_get(f"values.jsp?s1={sid}"))
    g = lambda pat: (re.search(pat, t) or [None, None])[1]
    try:
        lat, lon = float(g(r"Latitude\s+([\d.]+)")), float(g(r"Longitude\s+([\d.]+)"))
    except (TypeError, ValueError):
        return None
    return {"lat": round(lat, 5), "lon": round(lon, 5), "name": (g(r"AWS Location\s+(.*?)\s+Mandal Name") or "").strip(),
            "mandal": (g(r"Mandal Name\s+(.*?)\s+Latitude") or "").strip()}


def _district_of(lat, lon):
    """District containing a point, from data/tg_districts.geojson."""
    global _DIST
    from shapely.geometry import Point, shape
    if "_DIST" not in globals():
        with open(os.path.join(HERE, "data", "tg_districts.geojson"), encoding="utf-8") as f:
            _DIST = [(ft["properties"]["district"], shape(ft["geometry"])) for ft in json.load(f)["features"]]
    p = Point(lon, lat)
    best = min(_DIST, key=lambda dg: dg[1].distance(p))
    return best[0]


def update_stations(extra_ids=(), verbose=True):
    """Add positions for stations not yet in data/tg/stations.json (all of them the first time; once a day
    the district maps are checked for new ones). Returns the station dict."""
    known = _load_known()
    ids = set(extra_ids)
    stamp = os.path.join(TG_DIR, ".stations_checked")
    today = tf.today_ist().isoformat()
    if not known or not os.path.exists(stamp) or open(stamp).read().strip() != today:
        try:
            ids |= all_station_ids()
            os.makedirs(TG_DIR, exist_ok=True)
            with open(stamp, "w") as f:
                f.write(today)
        except RuntimeError as e:
            print(f"  {e}")
    new = [i for i in sorted(ids) if i not in known]
    for i, sid in enumerate(new):
        try:
            info = station_info(sid)
        except RuntimeError as e:
            print(f"  {e}")
            continue
        if info and _inside(info["lat"], info["lon"]):
            info["district"] = _district_of(info["lat"], info["lon"])
            known[sid] = info
        if verbose and (i + 1) % 100 == 0:
            print(f"  positions: {i + 1}/{len(new)}")
    if new:
        _save_known(known)
        if verbose:
            print(f"  TGDPS: {len(known)} stations with a position ({len(new)} looked up)")
    return known


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


def _rain_day(end):
    """Rain-day (24 h ending 08:30 on that date) of a period ending at `end`."""
    return end.date() if (end.hour, end.minute) <= (8, 30) else end.date() + timedelta(days=1)


def save_live(verbose=True):
    """PC: save rain since 08:30 so far to data/tg/live/<rain-day>.json. TGDPS lists only stations with rain,
    so every other known station is saved as 0 mm. In the last hour of the rain-day (after 07:30) the same
    numbers are also saved as the finished day, data/tg/daily/<rain-day>.json."""
    end, rows = fetch_live()
    known = update_stations(r[0] for r in rows)
    day = _rain_day(end)
    rain = {sid: 0.0 for sid in known}
    for sid, dist, mandal, loc, r in rows:
        if sid in known and 0 <= r <= 1500:
            rain[sid] = r
    out = [{"district": g["district"], "station": g["name"], "lat": g["lat"], "lon": g["lon"], "rain": rain[sid]}
           for sid, g in sorted(known.items())]
    os.makedirs(LIVE, exist_ok=True)
    with open(os.path.join(LIVE, f"{day.isoformat()}.json"), "w", encoding="utf-8") as f:
        json.dump({"rain_day": day.isoformat(), "upto": end.isoformat(timespec="minutes"), "stations": out},
                  f, ensure_ascii=False, separators=(",", ":"))
    for old in sorted(os.listdir(LIVE))[:-3]:                     # keep the last 3 running days
        os.remove(os.path.join(LIVE, old))
    if verbose:
        print(f"  TGDPS live {day} (to {end:%H:%M}): {len(out)} stations, {sum(1 for s in out if s['rain'])} with rain"
              f"{'' if len(rows) == sum(1 for r in rows if r[0] in known) else ' (some without a position)'}")
    if (end.hour, end.minute) >= (7, 30) and (end.hour, end.minute) <= (8, 30):
        os.makedirs(DAILY, exist_ok=True)
        with open(os.path.join(DAILY, f"{day.isoformat()}.json"), "w", encoding="utf-8") as f:
            json.dump({"rain_day": day.isoformat(), "upto": end.isoformat(timespec="minutes"), "rain": rain},
                      f, separators=(",", ":"))
        if verbose:
            print(f"  TGDPS rain-day {day} saved as finished (to {end:%H:%M})")
    return day


# ------------------------------------------------------------------ PC side: finished day from mandal pages
# mandaldata.jsp?s1=1..33 (one page per district, in this order) lists every mandal's official rainfall for
# the last finished rain-day ("Today's Rainfall", 24 h ending 08:30 on the date shown) and stays up all day,
# so the 24-hour report can be made whenever the PC is switched on, not only at 08:25. Each mandal is placed
# at the middle of its stations (station mandal names from values.jsp, matched loosely).
MANDAL_DISTRICTS = ["Adilabad", "Kumuram Bheem Asifabad", "Mancherial", "Nirmal", "Nizamabad", "Jagtial",
                    "Peddapalli", "Jayashankar Bhupalapally", "Bhadradri Kothagudem", "Mahabubabad", "Warangal",
                    "Hanumakonda", "Karimnagar", "Rajanna Sircilla", "Kamareddy", "Sangareddy", "Medak", "Siddipet",
                    "Jangaon", "Yadadri Bhuvanagiri", "Medchal Malkajgiri", "Hyderabad", "Ranga Reddy", "Vikarabad",
                    "Mahabubnagar", "Jogulamba Gadwal", "Wanaparthy", "Nagarkurnool", "Nalgonda", "Suryapet",
                    "Khammam", "Mulugu", "Narayanpet"]
# mandal page spelling -> station (values.jsp) spelling, where the loose match misses
# (keys are _mkey() of the mandal page's name)
_MANDAL_ALIAS = {"nennal": "Nennel", "mutarammantani": "Mutharam(MNT)", "ganagavaram": "Gangaram",
                 "inole": "Inavole", "naikal": "Nyalkal", "ielal": "Yalal", "aiza": "Ieeja"}


def _mkey(s):
    s = (s or "").lower().replace("(u)", " urban").replace("(r)", " rural")
    s = re.sub(r"[^a-z]", "", s)
    for a, b in [("pally", "palle"), ("palli", "palle"), ("oo", "u"), ("ee", "i"), ("th", "t"), ("dh", "d"),
                 ("bh", "b"), ("kh", "k"), ("gh", "g"), ("w", "v"), ("aa", "a"), ("y", "i")]:
        s = s.replace(a, b)
    return s


def fetch_mandal_day():
    """(rain-day, [(district, mandal, rain)]) from the 33 mandaldata.jsp pages."""
    day, rows = None, []
    for i, dist in enumerate(MANDAL_DISTRICTS, 1):
        html = _get(f"mandaldata.jsp?s1={i}")
        m = re.search(r"Rainfall\s*\(mm\)\s*on\s*(\d+/\d+/\d{4})", _text(html))
        if m:
            d = datetime.strptime(m.group(1), "%d/%m/%Y").date()
            if day and d != day:
                raise ValueError(f"mandaldata.jsp pages show different dates ({day}, {d}); try later")
            day = d
        for tr in re.split(r"<tr[^>]*>", html)[1:]:
            c = [_text(x) for x in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
            if len(c) >= 3 and c[0].isdigit():
                try:
                    rows.append((dist, c[1], float(c[2])))
                except ValueError:
                    rows.append((dist, c[1], None))
    if not day or not rows:
        raise ValueError("mandaldata.jsp: no date or no mandals found")
    return day, rows


def mandal_points(rows, known, verbose=True):
    """Place each mandal at the middle of its stations: [{district, station, lat, lon, rain}]."""
    import difflib
    from shapely.geometry import Point
    _district_of(17.4, 78.5)                                     # loads _DIST
    shapes = dict(_DIST)
    by = {}
    for g in known.values():
        by.setdefault(_mkey(g.get("mandal")), []).append(g)
    out, miss = [], []
    for dist, mandal, rain in rows:
        k = _mkey(_MANDAL_ALIAS.get(_mkey(mandal), mandal))
        c = by.get(k)
        if not c:
            close = difflib.get_close_matches(k, list(by), 1, 0.85)
            c = by.get(close[0]) if close else None
        if not c:
            miss.append(f"{mandal} ({dist})")
            continue
        poly = shapes.get(dist)
        if poly is not None:                                       # same-name mandals in other districts: keep the near ones
            near = [g for g in c if poly.distance(Point(g["lon"], g["lat"])) < 0.15]
            c = near or c
        lat = sum(g["lat"] for g in c) / len(c)
        lon = sum(g["lon"] for g in c) / len(c)
        out.append({"district": dist, "station": mandal.replace("_", " ").strip(), "lat": round(lat, 5),
                    "lon": round(lon, 5), "rain": rain if rain is None or 0 <= rain <= 1500 else None})
    if verbose:
        print(f"  TGDPS mandals: {len(out)} placed" + (f", {len(miss)} not found: {', '.join(miss)}" if miss else ""))
    return out


def save_mandal_day(verbose=True):
    """PC: save the last finished rain-day from the mandal pages, unless the 08:25 run already saved that day
    station by station. Re-saved only when TGDPS changes a number. Returns the rain-day or None."""
    day, rows = fetch_mandal_day()
    path = os.path.join(DAILY, f"{day.isoformat()}.json")
    old = None
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            old = json.load(f)
        if "points" not in old:
            if verbose:
                print(f"  rain-day {day} already saved station by station")
            return None
    pts = mandal_points(rows, _load_known(), verbose)
    if old and old["points"] == pts:
        if verbose:
            print(f"  rain-day {day} (mandals) unchanged")
        return None
    os.makedirs(DAILY, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"rain_day": day.isoformat(), "source": "TGDPS mandal-wise rainfall", "points": pts},
                  f, ensure_ascii=False, separators=(",", ":"))
    if verbose:
        print(f"  TGDPS rain-day {day} saved from {len(pts)} mandals")
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
    print(f"  TGDPS rain to {snap.get('upto')}: {len(snap['stations'])} stations")
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
                saved = json.load(f)
            if "points" in saved:                                  # mandal-wise day (PC was off at 08:25)
                days[d] = saved["points"]
            else:
                days[d] = [{"district": g["district"], "station": g["name"], "lat": g["lat"], "lon": g["lon"],
                            "rain": r}
                           for sid, r in saved["rain"].items() for g in [known.get(sid)] if g and g.get("lat") is not None]
            if verbose:
                print(f"  {d}  {sum(1 for r in days[d] if r['rain'] is not None)} stations")
        elif verbose:
            print(f"  {d}  no TGDPS Telangana day saved")
        d += timedelta(days=1)
    return days


if __name__ == "__main__":
    # python tg_fetch.py           save rain so far (and, after 07:30, the finished rain-day)
    # python tg_fetch.py day       save the last finished rain-day from the mandal pages (any time of day)
    # python tg_fetch.py stations  look up every station's position (first run; later runs do this by themselves)
    if sys.argv[1:2] == ["stations"]:
        update_stations()
    elif sys.argv[1:2] == ["day"]:
        save_mandal_day()
    else:
        save_live()
