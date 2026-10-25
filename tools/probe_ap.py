"""Temporary: look around the Andhra Pradesh rain sites from a GitHub runner (they are blocked elsewhere)."""
import re, sys, urllib.parse, requests

H = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130 Safari/537.36"}
START = sys.argv[1:] or [
    "https://apsdps.ap.gov.in/", "http://apsdps.ap.gov.in/",
    "https://apsdmagis.ap.gov.in/weather-watch/index.html",
    "https://apsdmagis.ap.gov.in/openweather/hub.html", "https://apsdmagis.ap.gov.in/",
]
KEY = re.compile(r"""["'`]([^"'`\s]*(?:api|json|rain|aws|ashx|asmx|svc|\.php|MapServer|FeatureServer|geojson|csv|report|weather|data)[^"'`\s]*)["'`]""", re.I)
seen = set()


def visit(u, depth):
    if u in seen or len(seen) > 60:
        return
    seen.add(u)
    try:
        r = requests.get(u, headers=H, timeout=40)
    except Exception as e:
        print(f"!! {u}: {e}")
        return
    t = r.text
    print(f"\n===== {r.status_code} {len(t)}B {r.headers.get('content-type')} {u} -> {r.url}")
    if "html" in (r.headers.get("content-type") or ""):
        print(re.sub(r"\s+", " ", t)[:2500])
    hits = sorted(set(KEY.findall(t)))
    for h in hits[:120]:
        print("  >", h[:200])
    if depth == 0:
        return
    links = set(re.findall(r"""(?:src|href)=["']([^"'#]+)["']""", t)) | {h for h in hits if h.endswith((".js", ".json", ".html", ".geojson"))}
    for l in sorted(links):
        full = urllib.parse.urljoin(r.url, l)
        host = urllib.parse.urlparse(full).netloc
        if "ap.gov.in" in host and full.split("?")[0].endswith((".js", ".json", ".html", ".geojson", "/", ".aspx", ".php")) \
                and not re.search(r"jquery|bootstrap|leaflet|font", full, re.I):
            visit(full, depth - 1)


for s in START:
    visit(s, 2)
