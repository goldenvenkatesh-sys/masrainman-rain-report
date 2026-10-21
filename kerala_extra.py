"""
Extra Kerala rain gauges beyond IMD Thiruvananthapuram's daily PDF.

1. Kerala WRIS (wris.kerala.gov.in), Water Resources Department "Kerala SW" network: about 360 manual
   rain gauges. Values for a day are typed in over the next few days, so each run re-reads the last
   LOOKBACK days and keeps whatever has arrived. The WRIS day D is the 24 hours ending 08:30 IST on D
   (same as IMD; checked against IMD's own stations, which WRIS also lists).
2. KSEB Dam Safety Organisation (dams.kseb.in): daily reservoir post with rainfall at its 18 dams.
   A post titled DD.MM.YYYY is treated as the 24 hours ending that morning.

Saved as data/kerala/wris/<date>.json and data/kerala/kseb/<date>.json, which kerala_data.py merges
with IMD's stations. Station positions come from data/kerala_stations.csv (types "sw" and "dam").

  python kerala_extra.py            # fetch both, last LOOKBACK days
"""
import html
import json
import os
import re
from datetime import date, datetime, timedelta, timezone

import requests

IST = timezone(timedelta(hours=5, minutes=30))
HERE = os.path.dirname(os.path.abspath(__file__))
OUT_WRIS = os.path.join(HERE, "data", "kerala", "wris")
OUT_KSEB = os.path.join(HERE, "data", "kerala", "kseb")
WRIS_POS = os.path.join(HERE, "data", "kerala", "wris_stations.json")
UA = {"User-Agent": "Mozilla/5.0 (MasRainman rainfall archive)", "Content-Type": "application/json"}
LOOKBACK = 12

WRIS = "https://wris.kerala.gov.in"
KERALA_UUID = "b3a3109c-1d8d-11ea-9f34-04ea56f9ded6"
WRIS_SRC = "Kerala SW"
KSEB = "https://dams.kseb.in"
DIST_CODE = {"TVM": "Thiruvananthapuram", "KLM": "Kollam", "PTA": "Pathanamthitta", "PTTA": "Pathanamthitta",
             "ALP": "Alappuzha", "KTM": "Kottayam", "IDK": "Idukki", "EKM": "Ernakulam", "TSR": "Thrissur",
             "PKD": "Palakkad", "MLP": "Malappuram", "KKD": "Kozhikode", "WND": "Wayanad", "KNR": "Kannur",
             "KSD": "Kasaragod"}


def _write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=0)
    os.replace(tmp, path)


# ----------------------------------------------------------------- WRIS
def _rf_body(day, ctype, ptype, loc, loc_name):
    d = day.strftime("%Y%m%d")
    return {"src": WRIS_SRC, "view": "ADMIN", "type": "ACTUAL", "locName": loc_name, "aggr": "SUM",
            "format": "yyyyMMdd", "sDate": d, "eDate": d, "ytd": "2019", "cType": ctype,
            "cumStartYear": "1969", "cumEndYear": str(day.year), "isFilter": True,
            "pUUID": loc, "locuuid": loc, "locUUID": loc, "lUUID": loc, "luuid": loc,
            "lType": ptype, "pType": ptype, "loctype": ptype, "component": "rainfall", "sUUID": KERALA_UUID}


def _rf_table(session, body):
    r = session.post(f"{WRIS}/api/rf/rfTable", data=json.dumps(body), headers=UA, timeout=60)
    r.raise_for_status()
    return [x for x in r.json() if x.get("uuid") != "TOTAL"]


def fetch_wris(today=None, lookback=LOOKBACK):
    today = today or datetime.now(IST).date()
    s = requests.Session()
    # station -> district, from one query per district (the statewide list has no district)
    ref = today - timedelta(days=1)
    dists = _rf_table(s, _rf_body(ref, "DISTRICT", "STATE", KERALA_UUID, "Kerala"))
    where = {}
    for d in dists:
        for st in _rf_table(s, _rf_body(ref, "STATION", "DISTRICT", d["uuid"], d["name"])):
            where[st["uuid"]] = d["name"]
    saved = {}
    for i in range(lookback, -1, -1):
        day = today - timedelta(days=i)
        rows = _rf_table(s, _rf_body(day, "STATION", "STATE", KERALA_UUID, "Kerala"))
        st = [{"station": r["name"], "uuid": r["uuid"], "district": where.get(r["uuid"]),
               "rain": float(r["actual"])}
              for r in rows if r.get("actual") is not None]
        if not st:
            continue
        path = os.path.join(OUT_WRIS, f"{day.isoformat()}.json")
        old = None
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                old = json.load(f).get("stations")
        if old != st:
            _write(path, {"date": day.isoformat(), "source": f"{WRIS} rf/rfTable src={WRIS_SRC}",
                          "fetched": datetime.now(IST).strftime("%Y-%m-%dT%H:%M%z"), "stations": st})
        saved[day.isoformat()] = len(st)
    return saved


def fetch_wris_positions():
    """Station positions from WRIS's map server (it was down when this was written, returning 502).
    Saved once as data/kerala/wris_stations.json: {uuid or name: [lat, lon]}."""
    if os.path.exists(WRIS_POS):
        return "already saved"
    url = (f"{WRIS}/geoserver/KWRIS/ows?service=WFS&version=1.0.0&request=GetFeature"
           f"&typeName=KWRIS:kerala_rf_stations&maxFeatures=5000&outputFormat=application/json")
    r = requests.get(url, headers={"User-Agent": UA["User-Agent"]}, timeout=90)
    if r.status_code != 200 or not r.text.lstrip().startswith("{"):
        return f"map server not available (HTTP {r.status_code})"
    pos = {}
    for f in r.json().get("features", []):
        g = f.get("geometry") or {}
        if g.get("type") != "Point":
            continue
        lon, lat = g["coordinates"][:2]
        for v in (f.get("properties") or {}).values():   # index by every text property (uuid, name, ...)
            if isinstance(v, str) and v.strip():
                pos.setdefault(v.strip(), [round(lat, 5), round(lon, 5)])
    if pos:
        _write(WRIS_POS, pos)
    return f"{len(pos)} keys saved"


# ----------------------------------------------------------------- KSEB dams
def _cells(tr):
    out = []
    for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, flags=re.S | re.I):
        c = re.sub(r"<[^>]+>", " ", c)
        out.append(re.sub(r"\s+", " ", html.unescape(c)).strip())
    return out


def parse_kseb(content):
    """Rows of the dam table -> [{station, district, rain}]. Finds the rainfall column by its header."""
    rows = [_cells(tr) for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", content, flags=re.S | re.I)]
    head = next((r for r in rows if any("rain" in c.lower() for c in r)), None)
    if not head:
        return []
    ri = next(i for i, c in enumerate(head) if "rain" in c.lower())
    ni = next((i for i, c in enumerate(head) if "reservoir" in c.lower() or "name" in c.lower()), 1)
    di = next((i for i, c in enumerate(head) if "district" in c.lower()), None)
    out = []
    for r in rows:
        if len(r) <= ri or not r[0].strip().isdigit() or not r[ni].strip() or r[ni].strip().isdigit():
            continue
        v = r[ri].replace(",", "").strip()
        rain = float(v) if re.fullmatch(r"\d+(\.\d+)?", v) else None
        name = re.sub(r"\s*[–-]\s*", " - ", r[ni]).strip()
        dist = DIST_CODE.get((r[di] if di is not None else "").strip().upper())
        out.append({"station": name, "district": dist, "rain": rain})
    return out


def fetch_kseb(n_posts=LOOKBACK):
    # (the site's firewall blocks a "_fields" list that includes content, so ask for whole posts)
    r = requests.get(f"{KSEB}/", params={"rest_route": "/wp/v2/posts", "per_page": n_posts},
                     headers={"User-Agent": UA["User-Agent"]}, timeout=60)
    r.raise_for_status()
    saved = {}
    for p in r.json():
        m = re.search(r"(\d{1,2})[./-](\d{1,2})[./-](\d{4})", html.unescape(p["title"]["rendered"]))
        if not m:
            continue
        day = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        st = parse_kseb(p["content"]["rendered"])
        if not any(x["rain"] is not None for x in st):
            continue
        _write(os.path.join(OUT_KSEB, f"{day.isoformat()}.json"),
               {"date": day.isoformat(), "source": p.get("link"), "posted": p.get("date"), "stations": st})
        saved[day.isoformat()] = sum(x["rain"] is not None for x in st)
    return saved


def main():
    for name, fn in (("Kerala WRIS (Water Resources Dept gauges)", fetch_wris),
                     ("Kerala WRIS station positions", fetch_wris_positions), ("KSEB dams", fetch_kseb)):
        print(name)
        try:
            got = fn()
            if isinstance(got, dict):
                got = ", ".join(f"{d}: {n}" for d, n in sorted(got.items())) or "nothing new"
            print("  " + got)
        except Exception as e:                       # one source down must not stop the others
            print(f"  failed: {e.__class__.__name__}: {str(e)[:200]}")


if __name__ == "__main__":
    main()
