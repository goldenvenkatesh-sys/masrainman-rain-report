"""
Rebuild data/ap_districts.geojson: Andhra Pradesh's 28 districts, named as the DES/APSDPS feed names them.

    python tools/build_ap_districts.py ap26.geojson json1.json > data/ap_districts.geojson

ap26.geojson : the 26 districts of 2022 (github.com/udit-001/india-maps-data, geojson/states/andhra-pradesh.geojson)
json1.json   : one reply of the DES live feed (http://desweather.ap.gov.in/webservice/rest/json1), which gives
               every station's district under the present 28-district map

Markapuram and Polavaram were cut out of older districts after 2022. A district polygon that holds stations
the feed puts in a district the 26-district file does not have is split between those districts by the
stations' Voronoi cells (each part = the cells of its stations), then smoothed a little. Other districts keep
their outline and only take the feed's name.
"""
import collections
import json
import sys

import numpy as np
from scipy.spatial import Voronoi
from shapely.geometry import Point, Polygon, mapping, shape
from shapely.ops import unary_union

sys.path.insert(0, __file__.rsplit("/tools/", 1)[0])
from ap_fetch import canon  # noqa: E402


def cells(points, clip):
    """Voronoi cell of each point, clipped to `clip`."""
    pts = np.asarray(points)
    x0, y0, x1, y1 = clip.bounds
    far = [[x0 - 5, y0 - 5], [x0 - 5, y1 + 5], [x1 + 5, y0 - 5], [x1 + 5, y1 + 5]]
    vor = Voronoi(np.vstack([pts, far]))
    out = []
    for i in range(len(pts)):
        reg = vor.regions[vor.point_region[i]]
        out.append(Polygon([vor.vertices[j] for j in reg]).intersection(clip) if reg and -1 not in reg else Polygon())
    return out


def main(geo_path, feed_path):
    old = {f["properties"]["district"]: shape(f["geometry"]).buffer(0) for f in json.load(open(geo_path))["features"]}
    st = [(canon(r["district"]), Point(r["longitude"], r["latitude"])) for r in json.load(open(feed_path))]
    st = list({(d, p.x, p.y): (d, p) for d, p in st}.values())        # drop exact duplicate positions
    out = {}
    for name, g in old.items():
        inside = [(d, p) for d, p in st if g.contains(p)]
        votes = collections.Counter(d for d, _ in inside)
        main_name = canon(name)
        new = [d for d, n in votes.items() if d != main_name and n >= 5 and d not in {canon(k) for k in old}]
        if not new:
            out[main_name] = out[main_name].union(g) if main_name in out else g
            print(f"{name:30s} -> {main_name}", file=sys.stderr)
            continue
        keep = [(d if d in new else main_name, p) for d, p in inside]
        parts = collections.defaultdict(list)
        for (d, _), c in zip(keep, cells([(p.x, p.y) for _, p in keep], g)):
            parts[d].append(c)
        for d, cs in parts.items():
            u = unary_union(cs).buffer(0.02).buffer(-0.02).intersection(g)
            if u.geom_type == "MultiPolygon":                 # drop small islands of a stray station
                u = unary_union([p for p in u.geoms if p.area > 0.05 * u.area])
            out[d] = out[d].union(u) if d in out else u
            print(f"{name:30s} -> {d} ({len(cs)} stations)", file=sys.stderr)
    # parts overlap a little after smoothing: give each overlap to the older district
    names = sorted(out, key=lambda d: d in {canon(k) for k in old})
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            out[a] = out[a].difference(out[b])
    feats = [{"type": "Feature", "properties": {"district": d},
              "geometry": mapping(g.simplify(0.003, preserve_topology=True))} for d, g in sorted(out.items())]
    json.dump({"type": "FeatureCollection", "features": feats}, sys.stdout, separators=(",", ":"))
    print(f"{len(feats)} districts", file=sys.stderr)


if __name__ == "__main__":
    main(*sys.argv[1:3])
