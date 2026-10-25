"""Temporary: save Andhra Pradesh DES weather pages from a GitHub runner (blocked elsewhere) into probe_out/."""
import os, re, sys, urllib.parse, requests

H = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130 Safari/537.36"}
OUT = "probe_out"
os.makedirs(OUT, exist_ok=True)
seen = set()


def save(u, depth):
    if u in seen or len(seen) > 80:
        return
    seen.add(u)
    try:
        r = requests.get(u, headers=H, timeout=(15, 90))
    except Exception as e:
        print(f"!! {u}: {e}")
        return
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", u.split("://", 1)[1])[:150]
    with open(os.path.join(OUT, name), "wb") as f:
        f.write(r.content)
    print(f"{r.status_code} {len(r.content):8d}B {r.headers.get('content-type')} {u}")
    if depth == 0 or "html" not in (r.headers.get("content-type") or "") and not u.endswith(".js"):
        return
    t = r.text
    for m in sorted(set(re.findall(r"""['"]([^'"#\s]+\.(?:js|jsp|json|do|html|php)(?:\?[^'"\s]*)?)['"]""", t))):
        full = urllib.parse.urljoin(r.url, m)
        if "desweather" in full or "webservice" in full and not re.search(r"jquery|bootstrap|leaflet|highcharts|datatables|plugins|vendor|Pdfs", full, re.I):
            save(full, depth - 1)


for s in sys.argv[1:] or ["http://desweather.ap.gov.in/Livemap/js/config.json", "http://desweather.ap.gov.in/webservice/rest/json1",
                          "http://desweather.ap.gov.in/weather/webservice/rest/json1", "http://desweather.ap.gov.in/webservice/rest/todo",
                          "http://desweather.ap.gov.in/weather/webservice/rest/todo", "http://desweather.ap.gov.in/Livemap/index.html",
                          "http://desweather.ap.gov.in/Livemap/", "http://desweather.ap.gov.in/AwsData/", "http://desweather.ap.gov.in/aws_report/",
                          "http://desweather.ap.gov.in/maps.jsp", "http://desweather.ap.gov.in/Fmaps.jsp"]:
    save(s, 1)
