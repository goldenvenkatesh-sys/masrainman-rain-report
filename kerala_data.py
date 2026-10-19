"""
Kerala station rainfall from IMD Thiruvananthapuram's daily "Rainfall statement" PDF.

  page 1 : IMD rain gauges, two district/station/rainfall column groups
  page 2 : AWS/ARG stations (district, station, rainfall); IMD lists only stations with rain

The PDF dated D covers the 24 hours ending 08:30 IST on D.
Station coordinates come from data/kerala_stations.csv (IMD network list + a few approximate ones).

  python kerala_data.py            # parse every archived PDF into data/kerala/daily/YYYY-MM-DD.json
"""
import csv
import glob
import json
import os
import re
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "data", "kerala", "raw")
DAILY = os.path.join(HERE, "data", "kerala", "daily")
STATIONS_CSV = os.path.join(HERE, "data", "kerala_stations.csv")
UNMAPPED = os.path.join(HERE, "data", "kerala", "unmapped_stations.txt")

DISTRICTS = ["Thiruvananthapuram", "Kollam", "Pathanamthitta", "Alappuzha", "Kottayam", "Idukki", "Ernakulam",
             "Thrissur", "Palakkad", "Malappuram", "Kozhikode", "Wayanad", "Kannur", "Kasaragod"]
_DKEY = {re.sub(r"[^A-Z]", "", d.upper()): d for d in DISTRICTS}
_DALIAS = {"KASARAGODE": "Kasaragod", "KASARGOD": "Kasaragod", "TRIVANDRUM": "Thiruvananthapuram",
           "ALLEPPEY": "Alappuzha", "CALICUT": "Kozhikode", "TRICHUR": "Thrissur", "PALGHAT": "Palakkad",
           "QUILON": "Kollam", "CANNANORE": "Kannur", "COCHIN": "Ernakulam"}
# PDF spelling -> name in kerala_stations.csv (only where they differ)
_SALIAS = {
    "TRIVANDRUM AP": "THIRUVANANTHAPURAM AIRPORT", "TRIVANDRUM CITY": "THIRUVANANTHAPURAM",
    "CIAL KOCHI": "CIAL KOCHI", "C I A L KOCHI": "CIAL KOCHI", "KARIPUR AP": "KARIPUR AP",
    "AIRPORT": "AIRPORT CHAKKA", "MUNNAR KSEB": "MUNNAR", "PEERMADE TO": "PEERMADE",
}


def district(name):
    k = re.sub(r"[^A-Z]", "", (name or "").upper().split("(")[0])
    if not k:
        return None
    if k in _DKEY:
        return _DKEY[k]
    if k in _DALIAS:
        return _DALIAS[k]
    for kk, d in _DKEY.items():          # truncated cells like "HIRUVANANTHAPURAM"
        if len(k) >= 5 and (k in kk or kk in k):
            return d
    return None


def skey(name):
    s = (name or "").upper().replace("_", " ")
    s = re.sub(r"\(.*?\)", " ", s)        # codes like (HSD) and (ARG*)
    s = re.sub(r"[^A-Z ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return _SALIAS.get(s, s)


def nice(name, kind="rg"):
    s = (name or "").replace("_", " ").strip()
    s = re.sub(r"\(\s*ARG\*?\s*\)", "", s, flags=re.I)
    if kind == "rg":
        s = re.sub(r"\s*\(\s*[A-Z_ ]{2,8}\s*\)\s*$", "", s)  # drop trailing station code
        s = re.sub(r"\s*\(\s*[A-Z_ ]{2,8}\s*\)\s*$", "", s)
    else:
        s = re.sub(r"\s*\(", " (", s)
    s = re.sub(r"\s+", " ", s).strip()
    if s.isupper():
        s = s.title().replace("Kgov", "KGOV").replace("Kvk", "KVK").replace("Afmu", "AFMU").replace("Rars", "RARS")
    return s


def load_coords():
    c = {}
    with open(STATIONS_CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            c[(r["type"], skey(r["station"]))] = (float(r["lat"]), float(r["lon"]), district(r["district"]))
    return c


def _num(s):
    s = (s or "").strip()
    if not s:
        return None
    s = s.replace("TR", "0.0").replace("Tr", "0.0")   # trace
    m = re.match(r"^-?\d+(\.\d+)?$", s)
    return float(s) if m else None


def parse_pdf(path):
    import pdfplumber
    pdf = pdfplumber.open(path)
    text0 = pdf.pages[0].extract_text() or ""
    m = re.search(r"DATED\s*(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})", text0)
    if not m:
        raise ValueError(f"{path}: no date found")
    d = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    out = []
    # page 1: IMD rain gauges (two column groups: district | station | rain)
    for tb in pdf.pages[0].extract_tables():
        cur = [None, None]
        for row in tb:
            row = (row + [None] * 6)[:6]
            for g, (dc, sc, rc) in enumerate([(0, 1, 2), (3, 4, 5)]):
                dn = district((row[dc] or "").replace("\n", " "))
                if dn:
                    cur[g] = dn
                elif row[dc] and re.search(r"LAKSHADWEEP|SPATIAL|ACTIVITY", row[dc], re.I):
                    cur[g] = None
                st = (row[sc] or "").strip()
                if not st or not cur[g] or re.search(r"STATION NAME|KERALAM|^Mahe$", st, re.I):
                    continue
                out.append({"type": "rg", "district": cur[g], "station": nice(st), "key": skey(st),
                            "rain": _num(row[rc])})
    # page 2+: AWS/ARG list (only stations with rain are listed)
    for p in pdf.pages[1:]:
        for tb in p.extract_tables():
            for row in tb:
                row = (row + [None] * 3)[:3]
                dn, st, v = district(row[0]), (row[1] or "").strip(), _num(row[2])
                if dn and st and v is not None:
                    out.append({"type": "aws", "district": dn, "station": nice(st, "aws"), "key": skey(st), "rain": v})
    return d, out


def attach_coords(recs, coords, unmapped):
    for r in recs:
        hit = coords.get((r["type"], r["key"])) or coords.get(("aws" if r["type"] == "rg" else "rg", r["key"]))
        if hit:
            r["lat"], r["lon"] = hit[0], hit[1]
        else:
            r["lat"] = r["lon"] = None
            unmapped.add(f'{r["type"]},{r["district"]},{r["station"]}')
    return recs


def update_daily(verbose=True):
    """Parse every archived PDF (newest copy wins per date) into data/kerala/daily/<date>.json."""
    os.makedirs(DAILY, exist_ok=True)
    coords = load_coords()
    unmapped = set()
    best = {}
    for pdf in sorted(glob.glob(os.path.join(RAW, "*", "imd_station_daily*.pdf"))):
        try:
            d, recs = parse_pdf(pdf)
        except Exception as e:
            print("  skip", pdf, e)
            continue
        best[d] = (pdf, recs)               # later files (sorted by folder/name) replace earlier
    for d, (pdf, recs) in sorted(best.items()):
        attach_coords(recs, coords, unmapped)
        with open(os.path.join(DAILY, f"{d.isoformat()}.json"), "w", encoding="utf-8") as f:
            json.dump({"date": d.isoformat(), "source": os.path.relpath(pdf, HERE), "stations": recs},
                      f, ensure_ascii=False, indent=0)
        if verbose:
            rep = sum(1 for r in recs if r["rain"] is not None)
            print(f"  {d}: {len(recs)} stations, {rep} with values")
    with open(UNMAPPED, "w", encoding="utf-8") as f:
        f.write("# stations with no coordinates yet: add them to data/kerala_stations.csv\n")
        f.write("\n".join(sorted(unmapped)) + "\n")
    if unmapped and verbose:
        print(f"  {len(unmapped)} stations without coordinates (see {os.path.relpath(UNMAPPED, HERE)})")


def load_days(start, end, verbose=True):
    """Same shape as tnsmart_fetch.load_days: {date: [ {district, station, lat, lon, rain} ]}.
    Stations without coordinates are kept (for rankings) with lat/lon None."""
    days = {}
    d = start
    while d <= end:
        p = os.path.join(DAILY, f"{d.isoformat()}.json")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                js = json.load(f)
            days[d] = [{"district": r["district"], "station": r["station"], "lat": r["lat"], "lon": r["lon"],
                        "rain": r["rain"], "type": r["type"]} for r in js["stations"]]
            if verbose:
                print(f"  {d}  {sum(1 for r in days[d] if r['rain'] is not None)} stations")
        elif verbose:
            print(f"  {d}  no IMD Kerala file archived")
        d += timedelta(days=1)
    return days


if __name__ == "__main__":
    update_daily()
