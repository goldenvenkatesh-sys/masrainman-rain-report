"""
Model forecasts of 24-hour rain for the live24 scorecard ("Did the models get it right?").

The window is the rain-day: 08:30 IST on D-1 to 08:30 IST on D (03 UTC to 03 UTC). Each model's forecast
is taken from the run issued at 12 UTC on D-2 (5:30 PM IST, the evening before the window starts).

  ECMWF IFS   open data, 0.25°, 3-hourly steps: tp(39 h) - tp(15 h)
  ECMWF AIFS  open data (aifs-single), 0.25°, 6-hourly steps: steps 15 and 39 are the mean of the
              steps either side
  WeatherNext 3  comes from Google Cloud, which this repository cannot read. The weathernext3 repository's
              model-maps workflow saves the 24-h rain of each run for these windows; it is read from
              there with a GitHub token (WN3_READ_TOKEN, else WN3_DISPATCH_TOKEN) that can read that repo:
              site-data branch, rain24/wn3_<run YYYYMMDD_HH>.json = {"run": ..., "windows": {"<D>":
              {"lat": [...], "lon": [...], "mm": [[...]]}}} (24-h rain, mm, rows = lat).
              A local file data/raw/models/wn3_<D>.json ({"lat", "lon", "mm"}) or WN3_RAIN_URL (with {D})
              is used instead when present.

Only the small "tp" field is downloaded (HTTP range request from the .index file), from
data.ecmwf.int or, for older runs, the AWS open-data mirror. Files are cached in data/raw/models/.
A model whose data is missing is returned as None, so the video shows "no forecast" for it.
"""
import json
import os
from datetime import datetime, timedelta, timezone

import numpy as np
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(os.environ.get("TNRAIN_RAW_DIR") or os.path.join(HERE, "data", "raw"), "models")
MIRRORS = ["https://data.ecmwf.int/forecasts", "https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com"]
ECMWF = {"ifs": ("ifs", "fc"), "aifs": ("aifs-single", "fc")}
NAMES = {"wn3": "WeatherNext 3", "ifs": "ECMWF IFS", "aifs": "ECMWF AIFS"}
BOX = (7.0, 15.0, 74.5, 81.5)          # lat0, lat1, lon0, lon1 kept from each global field


def run_for(day):
    """The run used for rain-day `day`: 12 UTC two days before."""
    d = day - timedelta(days=2)
    return datetime(d.year, d.month, d.day, 12, tzinfo=timezone.utc)


def _grib_tp(model, run, step):
    """Total precipitation (mm, accumulated from the start of the run) at `step` hours: (lat, lon, field)."""
    os.makedirs(CACHE, exist_ok=True)
    folder, typ = ECMWF[model]
    name = f"{run:%Y%m%d%H}0000-{step}h-oper-{typ}"
    path = os.path.join(CACHE, f"{model}_{name}_tp.grib2")
    if not os.path.exists(path):
        data = None
        for base in MIRRORS:
            url = f"{base}/{run:%Y%m%d}/{run:%H}z/{folder}/0p25/oper/{name}"
            try:
                r = requests.get(url + ".index", timeout=30)
                if r.status_code != 200:
                    continue
                ent = next(e for e in map(json.loads, r.text.splitlines()) if e.get("param") == "tp")
                a, n = ent["_offset"], ent["_length"]
                r = requests.get(url + ".grib2", headers={"Range": f"bytes={a}-{a + n - 1}"}, timeout=60)
                if r.status_code in (200, 206) and len(r.content) == n:
                    data = r.content
                    break
            except (requests.RequestException, StopIteration, ValueError) as e:
                print(f"    {url}: {e}")
                continue
        if data is None:
            raise FileNotFoundError(f"{model} {run:%Y-%m-%d %H}z +{step}h not found")
        with open(path, "wb") as f:
            f.write(data)
    import eccodes
    with open(path, "rb") as f:
        h = eccodes.codes_new_from_message(f.read())
    try:
        ni, nj = eccodes.codes_get(h, "Ni"), eccodes.codes_get(h, "Nj")
        lat0 = eccodes.codes_get(h, "latitudeOfFirstGridPointInDegrees")
        lat1 = eccodes.codes_get(h, "latitudeOfLastGridPointInDegrees")
        lon0 = eccodes.codes_get(h, "longitudeOfFirstGridPointInDegrees")
        vals = eccodes.codes_get_values(h).reshape(nj, ni)
        if eccodes.codes_get(h, "units") == "m":     # IFS gives metres, AIFS kg m-2 (= mm)
            vals = vals * 1000.0
    finally:
        eccodes.codes_release(h)
    lats = np.linspace(lat0, lat1, nj)
    lons = (lon0 + np.arange(ni) * 360.0 / ni + 180) % 360 - 180
    o = np.argsort(lons)
    lons, vals = lons[o], vals[:, o]
    la = (lats >= BOX[0]) & (lats <= BOX[1])
    lo = (lons >= BOX[2]) & (lons <= BOX[3])
    return lats[la], lons[lo], vals[np.ix_(la, lo)]


def _ecmwf_24h(model, day):
    run = run_for(day)
    if model == "ifs":
        get = lambda s: _grib_tp(model, run, s)
        (la, lo, a), (_, _, b) = get(15), get(39)
    else:                                   # 6-hourly: interpolate steps 15 and 39
        f = {s: _grib_tp(model, run, s)[2] for s in (12, 18, 36, 42)}
        la, lo, _ = _grib_tp(model, run, 12)
        a, b = (f[12] + f[18]) / 2, (f[36] + f[42]) / 2
    return la, lo, np.maximum(b - a, 0)


WN3_REPO = "goldenvenkatesh-sys/masrainman-weathernext3"


def _wn3_github(day, path):
    """Fetch this window from the weathernext3 repo's site-data branch into `path`."""
    token = os.environ.get("WN3_READ_TOKEN") or os.environ.get("WN3_DISPATCH_TOKEN")
    if not token:
        raise FileNotFoundError("WeatherNext 3: no GitHub token to read the weathernext3 repo")
    api = f"https://api.github.com/repos/{WN3_REPO}/contents"
    hdr = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github.raw+json"}
    r = requests.get(f"{api}/README.md", headers=hdr, timeout=30)
    if r.status_code != 200:
        raise PermissionError(f"WeatherNext 3: the token cannot read {WN3_REPO} contents (HTTP {r.status_code}); "
                              f"it needs Contents: read on that repository")
    run = run_for(day)
    r = requests.get(f"{api}/rain24/wn3_{run:%Y%m%d_%H}.json", params={"ref": "site-data"}, headers=hdr, timeout=60)
    if r.status_code != 200:
        raise FileNotFoundError(f"WeatherNext 3: no rain24 file for the {run:%Y-%m-%d %H}z run (HTTP {r.status_code})")
    win = r.json().get("windows", {}).get(day.isoformat())
    if not win:
        raise FileNotFoundError(f"WeatherNext 3: the {run:%Y-%m-%d %H}z run has no window ending {day}")
    os.makedirs(CACHE, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(win, f)


def _wn3_24h(day):
    path = os.path.join(CACHE, f"wn3_{day.isoformat()}.json")
    if not os.path.exists(path) and not os.environ.get("WN3_RAIN_URL"):
        _wn3_github(day, path)
    url = os.environ.get("WN3_RAIN_URL")
    if not os.path.exists(path) and url:
        r = requests.get(url.replace("{D}", day.isoformat()), timeout=30)
        if r.status_code != 200:
            raise FileNotFoundError(f"WeatherNext 3: HTTP {r.status_code}")
        os.makedirs(CACHE, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(r.text)
    if not os.path.exists(path):
        raise FileNotFoundError("WeatherNext 3: no file for this day")
    with open(path, encoding="utf-8") as f:
        j = json.load(f)
    return np.asarray(j["lat"], float), np.asarray(j["lon"], float), np.asarray(j["mm"], float)


def district_max(la, lo, mm, geo, step=0.05):
    """Highest forecast 24-h rain (mm) in each district: the field (bilinear) on a 0.05° lattice inside it."""
    from shapely import contains_xy
    order = np.argsort(la)
    la, mm = la[order], mm[order]
    out = {}
    for dn, g in geo.districts.items():
        x0, y0, x1, y1 = g.bounds
        gx, gy = np.meshgrid(np.arange(x0, x1 + step, step), np.arange(y0, y1 + step, step))
        ins = contains_xy(g, gx, gy)
        px, py = (gx[ins], gy[ins]) if ins.any() else (np.array([g.centroid.x]), np.array([g.centroid.y]))
        fi = np.interp(py, la, np.arange(len(la)))
        fj = np.interp(px, lo, np.arange(len(lo)))
        i0, j0 = np.floor(fi).astype(int), np.floor(fj).astype(int)
        i1, j1 = np.minimum(i0 + 1, len(la) - 1), np.minimum(j0 + 1, len(lo) - 1)
        di, dj = fi - i0, fj - j0
        v = (mm[i0, j0] * (1 - di) * (1 - dj) + mm[i1, j0] * di * (1 - dj) + mm[i0, j1] * (1 - di) * dj
             + mm[i1, j1] * di * dj)
        out[dn] = float(v.max())
    return out


def forecasts(day, geo, verbose=True):
    """{model key: {district: max mm} or None} for wn3, ifs and aifs."""
    out = {}
    for key, fn in (("wn3", lambda: _wn3_24h(day)), ("ifs", lambda: _ecmwf_24h("ifs", day)),
                    ("aifs", lambda: _ecmwf_24h("aifs", day))):
        try:
            out[key] = district_max(*fn(), geo)
            if verbose:
                top = max(out[key].items(), key=lambda kv: kv[1])
                print(f"  {NAMES[key]}: highest district {top[0]} {top[1]:.0f} mm")
        except Exception as e:      # missing data never stops the video
            out[key] = None
            if verbose:
                print(f"  {NAMES[key]}: no forecast ({e})")
    return out


def score(fc, observed_heavy, thr):
    """Heavy-rain districts caught / missed and false alarms for one model's district maxima."""
    if fc is None:
        return None
    hit = [d for d in observed_heavy if fc.get(d, 0) >= thr]
    false = sorted(d for d, v in fc.items() if v >= thr and d not in observed_heavy)
    return {"caught": hit, "missed": [d for d in observed_heavy if d not in hit], "false": false}
