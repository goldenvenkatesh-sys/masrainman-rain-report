"""Make fake TN-SMART files for offline testing (same JSON shape as the real feed)."""
import json, os, sys
from datetime import date, timedelta
import numpy as np
from shapely.geometry import shape, Point
from tn_names import canon

COUNTS = {"Ariyalur": 23, "Chengalpattu": 33, "Chennai": 115, "Coimbatore": 59, "Cuddalore": 43, "Dharmapuri": 48,
          "Dindigul": 63, "Erode": 47, "Kallakurichi": 34, "Kancheepuram": 21, "Kanniyakumari": 29, "Karur": 33,
          "Krishnagiri": 56, "Madurai": 40, "Mayiladuthurai": 16, "Nagapattinam": 16, "Namakkal": 35, "Nilgiris": 35,
          "Perambalur": 21, "Pudukkottai": 47, "Ramanathapuram": 48, "Ranipet": 22, "Salem": 65, "Sivagangai": 45,
          "Tenkasi": 38, "Thanjavur": 33, "Theni": 31, "Thiruchirappalli": 49, "Thiruvarur": 31, "Thoothukudi": 50,
          "Tirunelveli": 47, "Tirupathur": 19, "Tiruppur": 50, "Tiruvallur": 39, "Tiruvannamalai": 62, "Vellore": 24,
          "Villupuram": 55, "Virudhunagar": 43}

out = sys.argv[1]
start, end = date.fromisoformat(sys.argv[2]), date.fromisoformat(sys.argv[3])
os.makedirs(out, exist_ok=True)
rng = np.random.default_rng(7)
here = os.path.dirname(os.path.abspath(__file__))
gj = json.load(open(os.path.join(here, "data", "tn_districts.geojson"), encoding="utf-8"))
geoms = {canon(f["properties"]["district"]): shape(f["geometry"]) for f in gj["features"]}
st = []
for dn, n in COUNTS.items():
    g = geoms[dn]; x0, y0, x1, y1 = g.bounds; k = 0
    while k < n:
        lon, lat = rng.uniform(x0, x1), rng.uniform(y0, y1)
        if g.contains(Point(lon, lat)):
            st.append((dn, f"{dn[:5]} Stn {k + 1:02d}", round(lat, 3), round(lon, 3))); k += 1
lat = np.array([s[2] for s in st]); lon = np.array([s[3] for s in st])
d = start
while d <= end:
    rain = np.zeros(len(st))
    for _ in range(rng.integers(3, 9)):            # random storm cells
        clat, clon = rng.uniform(8.1, 13.4), rng.uniform(76.6, 80.3)
        amp = rng.gamma(1.6, 18); rad = rng.uniform(0.15, 0.7)
        rain += amp * np.exp(-((lat - clat) ** 2 + (lon - clon) ** 2) / (2 * rad ** 2))
    rain *= rng.uniform(0.4, 1.6, len(st))
    rain[rain < 0.3] = 0
    recs = []
    for (dn, nm, la, lo), r in zip(st, rain):
        tot = None if rng.random() < 0.08 else round(float(r), 1)
        recs.append({"district_name": dn, "station_name": nm, "latitude": la, "longitude": lo, "total": tot})
    json.dump(recs, open(os.path.join(out, f"hourly_rainfall_{d.isoformat()}.json"), "w"))
    d += timedelta(days=1)
print(len(st), "stations")
