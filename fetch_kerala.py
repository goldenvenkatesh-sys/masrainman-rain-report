"""
Save today's Kerala rainfall reports (IMD Thiruvananthapuram + KSDMA) into data/kerala/raw/YYYY-MM-DD/.
Both sites overwrite their files every day, so this builds our own history.
Run by .github/workflows/kerala-archive.yml a few times a day; files already saved are skipped.
"""
import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, unquote

import requests

IST = timezone(timedelta(hours=5, minutes=30))
HERE = os.path.dirname(os.path.abspath(__file__))
UA = {"User-Agent": "Mozilla/5.0 (MasRainman rainfall archive)"}

IMD_FILES = {
    "imd_station_daily.pdf": "https://mausam.imd.gov.in/thiruvananthapuram/mcdata/iadp%20rainfall.pdf",
    "imd_rainfall_map.jpg": "https://mausam.imd.gov.in/thiruvananthapuram/mcdata/realized%2024%20hour%20rainfall%20over%20kerala.jpg",
    "imd_season.pdf": "https://mausam.imd.gov.in/thiruvananthapuram/mcdata/SEASONSRAINFALL.pdf",
}
KSDMA_PAGE = "https://sdma.kerala.gov.in/rainfall-2/"
AWS_TEST = "http://aws.imd.gov.in:8091/state/KERALA"


def get(url, timeout=60):
    return requests.get(url, headers=UA, timeout=timeout)


def sha(b):
    return hashlib.sha1(b).hexdigest()


def already_have(digest, root):
    """True if a file with this content was saved on any earlier day."""
    for day in os.listdir(root):
        mf = os.path.join(root, day, "manifest.json")
        if os.path.exists(mf):
            with open(mf, encoding="utf-8") as f:
                if any(e.get("sha1") == digest for e in json.load(f).get("files", [])):
                    return True
    return False


def main():
    now = datetime.now(IST)
    root = os.path.join(HERE, "data", "kerala", "raw")
    day_dir = os.path.join(root, now.date().isoformat())
    os.makedirs(day_dir, exist_ok=True)
    mf_path = os.path.join(day_dir, "manifest.json")
    manifest = {"files": [], "checks": {}}
    if os.path.exists(mf_path):
        with open(mf_path, encoding="utf-8") as f:
            manifest = json.load(f)

    def save(name, url):
        try:
            r = get(url)
            manifest["checks"][name] = r.status_code
            if r.status_code != 200 or len(r.content) < 500:
                print(f"  {name}: HTTP {r.status_code}")
                return
            dg = sha(r.content)
            if already_have(dg, root):
                print(f"  {name}: unchanged since an earlier save, skipped")
                return
            path = os.path.join(day_dir, name)
            if os.path.exists(path):  # same name today but new content: keep both
                stem, ext = os.path.splitext(name)
                path = os.path.join(day_dir, f"{stem}_{now:%H%M}{ext}")
            with open(path, "wb") as f:
                f.write(r.content)
            manifest["files"].append({"name": os.path.basename(path), "url": url, "sha1": dg,
                                      "saved_at": now.isoformat(timespec="minutes"),
                                      "last_modified": r.headers.get("Last-Modified")})
            print(f"  {name}: saved ({len(r.content) // 1024} KB)")
        except Exception as e:
            manifest["checks"][name] = f"error: {e.__class__.__name__}"
            print(f"  {name}: {e}")

    print("IMD Thiruvananthapuram")
    for name, url in IMD_FILES.items():
        save(name, url)

    print("KSDMA")
    try:
        page = get(KSDMA_PAGE).text
        links = sorted(set(re.findall(r'https?://sdma\.kerala\.gov\.in/wp-content/uploads/[^"\'\s>]+\.(?:pdf|jpe?g|png)',
                                      page, flags=re.I)))
        links = [l for l in links if "logo" not in l.lower() and not re.search(r"-\d+x\d+\.", l)]
        for l in links:
            save("ksdma_" + os.path.basename(unquote(l)), l)
        manifest["checks"]["ksdma_page"] = f"{len(links)} files linked"
    except Exception as e:
        manifest["checks"]["ksdma_page"] = f"error: {e}"
        print("  page:", e)

    print("IMD AWS portal test")
    try:
        r = get(AWS_TEST, timeout=30)
        manifest["checks"]["imd_aws_portal"] = r.status_code
        if r.ok:
            with open(os.path.join(day_dir, "imd_aws_kerala.html"), "w", encoding="utf-8") as f:
                f.write(r.text)
        print("  HTTP", r.status_code)
    except Exception as e:
        manifest["checks"]["imd_aws_portal"] = f"error: {e.__class__.__name__}"
        print("  ", e)

    with open(mf_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
