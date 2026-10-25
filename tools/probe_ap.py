"""Temporary: which Andhra Pradesh rain hosts answer a GitHub runner."""
import re, socket, requests

H = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130 Safari/537.36"}
HOSTS = ["desweather.ap.gov.in", "www.apsdps.ap.gov.in", "apwrims.ap.gov.in", "www.apwrims.ap.gov.in", "rtgs.ap.gov.in",
         "apsdma.ap.gov.in", "apsdmagis.ap.gov.in", "core.ap.gov.in", "aws.imd.gov.in", "city.imd.gov.in",
         "mausam.imd.gov.in", "indiawris.gov.in", "ksndmc.org", "beta-tnsmart.rimes.int", "apsac.ap.gov.in",
         "agrisnet.ap.gov.in", "www.ap.gov.in", "apagrisnet.gov.in", "apdrought.ap.gov.in"]
print("\n===== desweather pages")
import urllib.parse
seen = set()
def show(u, depth=1):
    if u in seen or len(seen) > 40:
        return
    seen.add(u)
    try:
        r = requests.get(u, headers=H, timeout=(15, 60))
    except Exception as e:
        print(f"!! {u}: {e}"); return
    t = r.text
    print(f"\n----- {r.status_code} {len(t)}B {r.headers.get('content-type')} {u} -> {r.url}")
    print(t[:12000])
    for m in sorted(set(re.findall(r"""(?:url|href|src|action|open\(\s*['"](?:GET|POST)['"]\s*,)\s*[:=]?\s*['"]([^'"#]+)['"]""", t, re.I))):
        print("  >", m)
    if depth:
        for m in sorted(set(re.findall(r"""(?:src|url|action)\s*[:=]\s*['"]([^'"#]+\.(?:js|jsp|json|do|php)[^'"]*)['"]""", t, re.I))):
            full = urllib.parse.urljoin(r.url, m)
            if "desweather" in full and not re.search(r"jquery|bootstrap|leaflet|highcharts|datatables", full, re.I):
                show(full, depth - 1)
show("http://desweather.ap.gov.in/Realtime/Rainfall.jsp")
show("http://desweather.ap.gov.in/", 0)
