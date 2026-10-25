"""Temporary: which Andhra Pradesh rain hosts answer a GitHub runner."""
import re, socket, requests

H = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130 Safari/537.36"}
HOSTS = ["apsdps.ap.gov.in", "www.apsdps.ap.gov.in", "apwrims.ap.gov.in", "www.apwrims.ap.gov.in", "rtgs.ap.gov.in",
         "apsdma.ap.gov.in", "apsdmagis.ap.gov.in", "core.ap.gov.in", "aws.imd.gov.in", "city.imd.gov.in",
         "mausam.imd.gov.in", "indiawris.gov.in", "ksndmc.org", "beta-tnsmart.rimes.int", "apsac.ap.gov.in",
         "agrisnet.ap.gov.in", "www.ap.gov.in", "apagrisnet.gov.in", "apdrought.ap.gov.in"]
for h in HOSTS:
    try:
        addrs = sorted({a[4][0] for a in socket.getaddrinfo(h, 443)})
    except Exception as e:
        print(f"{h:28s} DNS fail {e}")
        continue
    res = []
    for scheme in ("https", "http"):
        try:
            r = requests.get(f"{scheme}://{h}/", headers=H, timeout=(12, 25))
            title = re.search(r"<title>(.*?)</title>", r.text, re.S | re.I)
            res.append(f"{scheme}:{r.status_code} {len(r.text)}B {r.url} [{(title.group(1).strip() if title else '')[:60]}]")
            break
        except Exception as e:
            res.append(f"{scheme}:ERR {type(e).__name__}: {str(e)[-90:]}")
    print(f"{h:28s} {addrs} :: {' | '.join(res)}")
