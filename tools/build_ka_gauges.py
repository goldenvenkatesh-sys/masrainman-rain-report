"""
Rebuild data/ka_gauges.csv: positions for KSNDMC rain gauges, which the live feed lists without coordinates.

    python tools/build_ka_gauges.py rows.json ka.geojson karnataka_districts.geojson > data/ka_gauges.csv

rows.json   : list of [HOBLICODE, RAINGAUGE, DISTRICT, TALUKNAME, HOBLI, ...] from the KSNDMC feed
ka.geojson  : DataMeet Indian Village Boundaries, Karnataka (github.com/datameet/indian_village_boundaries, ODbL)
districts   : Karnataka district polygons with a "district" property

Each gauge is named after its gram panchayat / hobli, which is nearly always a village name. The gauge goes
to the centre of the village with that name in the same taluk, else the same district (exact name first,
then a close spelling). A match far from the other gauges of its hobli is dropped as a namesake. Gauges left
over go to the middle of the other gauges of their hobli (first 6 digits of the code) or taluk (first 4).
"""
import collections
import csv
import difflib
import json
import re
import statistics
import sys

from shapely.geometry import shape

ALIAS = {"BENGALURU SOUTH": "RAMANAGARA", "VIJAYANAGARA": "BALLARI", "KOPPALA": "KOPPAL"}


def norm(s):
    s = s.lower()
    s = re.sub(r'[_\-\s]*\d+$', '', s)
    s = re.sub(r'\(.*?\)', '', s)
    s = re.sub(r'[^a-z]', '', s)
    for a, b in [("hally", "halli"), ("aa", "a"), ("ee", "i"), ("oo", "u"), ("w", "v"), ("th", "t"), ("dh", "d"),
                 ("bh", "b"), ("kh", "k"), ("gh", "g"), ("ph", "p"), ("sh", "s"), ("z", "j"), ("y", "i"), ("ou", "u"),
                 ("ck", "k")]:
        s = s.replace(a, b)
    return re.sub(r'(.)\1+', r'\1', s)


def main(rows_path, vill_path, dist_path):
    rows = json.load(open(rows_path))
    dpoly = {f["properties"]["district"].upper(): shape(f["geometry"]).buffer(0.03)
             for f in json.load(open(dist_path))["features"]}
    vill = []
    for f in json.load(open(vill_path))["features"]:
        p = f["properties"]
        try:
            c = shape(f["geometry"]).representative_point()
        except Exception:
            continue
        vill.append((norm(p.get("VILL_NAME") or p.get("NAME") or ""), norm(p.get("TALUKA_NAM") or ""), c,
                     p.get("VILL_NAME") or ""))
    pool = {d: [v for v in vill if g.contains(v[2])] for d, g in dpoly.items()}

    def match(d, tal, hob):
        cands = pool.get(ALIAS.get(d, d), [])
        n, nt = norm(hob), norm(tal)
        in_t = [v for v in cands if v[1] == nt or (len(nt) > 4 and difflib.SequenceMatcher(None, v[1], nt).ratio() > 0.8)]
        for pl, lab in [(in_t, "taluk-exact"), (cands, "district-exact")]:
            ex = [v for v in pl if v[0] == n]
            if ex:
                return ex[0], lab
        for pl, lab, th in [(in_t, "taluk-fuzzy", 0.84), (cands, "district-fuzzy", 0.88)]:
            m = difflib.get_close_matches(n, list({v[0] for v in pl}), 1, th)
            if m:
                return [v for v in pl if v[0] == m[0]][0], lab
        return None, ""

    out = []
    for code, g, d, tal, hob, *_ in rows:
        v, lab = match(d, tal, hob)
        out.append(dict(code=code, gauge=g, district=d, taluk=tal, hobli=hob,
                        lat=round(v[2].y, 4) if v else None, lon=round(v[2].x, 4) if v else None, how=lab))

    def sib(o, n):
        return [x for x in out if x is not o and x["lat"] is not None and x["code"][:n] == o["code"][:n]
                and x["how"] not in ("hobli", "taluk")]
    for o in out:            # namesake far from its hobli: drop
        s = o["lat"] is not None and (sib(o, 6) or sib(o, 4))
        if s and len(s) >= 2:
            la, lo = statistics.median(x["lat"] for x in s), statistics.median(x["lon"] for x in s)
            if abs(la - o["lat"]) + abs(lo - o["lon"]) > 0.35:
                o["lat"] = o["lon"] = None
                o["how"] = ""
    for n, lab in [(6, "hobli"), (4, "taluk")]:
        for o in out:
            if o["lat"] is None and (s := sib(o, n)):
                o["lat"] = round(statistics.mean(x["lat"] for x in s), 4)
                o["lon"] = round(statistics.mean(x["lon"] for x in s), 4)
                o["how"] = lab
    print(collections.Counter(o["how"] for o in out), file=sys.stderr)
    w = csv.DictWriter(sys.stdout, fieldnames=list(out[0]))
    w.writeheader()
    w.writerows(out)


if __name__ == "__main__":
    main(*sys.argv[1:4])
