"""
TN-SMART daily rain-gauge report (TNSDMA "Rainfall Statistics Abstract").

TN-SMART's hourly JSON (tnsmart_fetch.py) comes from the automatic gauges. The Revenue department's
~570 daily rain gauges (read once, 8:30 am to 8:30 am) are a separate network: the same town can have
both, with different totals (9 Nov 2026: Kayalpattinam hourly AWS 42.8 mm, daily gauge 107.4 mm).

The daily gauges are public only as a PDF, out at about 10:00-11:45 IST:
  https://beta-tnsmart.rimes.int/DATA/Rainfall/daily_reports/rainfall_analysis_YYYY-MM-DD.pdf
It lists every station with heavy rain or more (>= 64.5 mm) and each district's average; stations
below heavy rain are not listed. daily_gauges(d) returns
  {"heavy": [{district, taluk, station, rain, category}], "districts": {name: (stations, average)},
   "state_avg": float}
or None when the PDF is not out yet.
"""
import os
import re
import time
from datetime import date

import requests

import tnsmart_fetch as tf

URL = "https://beta-tnsmart.rimes.int/DATA/Rainfall/daily_reports/rainfall_analysis_{d}.pdf"
CATEGORIES = [("extreme", "Extremely heavy"), ("very heavy", "Very heavy"), ("heavy", "Heavy")]
_NUM = re.compile(r"^\d+(\.\d+)?$")


def _pdf_path(d: date):
    return os.path.join(tf.RAW_DIR, f"rainfall_analysis_{d.isoformat()}.pdf")


def download_pdf(d: date, retries=3):
    """Path of the day's PDF (cached), or None if TN-SMART has not put it up yet."""
    os.makedirs(tf.RAW_DIR, exist_ok=True)
    path = _pdf_path(d)
    if os.path.exists(path) and os.path.getsize(path) > 1000:
        return path
    if tf.OFFLINE:
        return None
    for attempt in range(retries):
        try:
            r = requests.get(URL.format(d=d.isoformat()), headers=tf.HEADERS, timeout=60)
            if r.status_code == 404 or not r.content.startswith(b"%PDF"):
                return None
            r.raise_for_status()
            with open(path, "wb") as f:
                f.write(r.content)
            return path
        except Exception as e:  # network hiccup: retry
            print(f"  daily gauge PDF: {e}")
            time.sleep(3 * (attempt + 1))
    return None


def _lines(words):
    """words: (page, x0, top, text) -> lines [(page, top, [(x0, text), ...])] in reading order."""
    out = []
    for p, x, y, t in sorted(words, key=lambda w: (w[0], w[2], w[1])):
        if out and out[-1][0] == p and abs(out[-1][1] - y) <= 3:
            out[-1][2].append((x, t))
        else:
            out.append((p, y, [(x, t)]))
    for ln in out:
        ln[2].sort()
    return out


def parse_words(words):
    """Parse the report from positioned words (page, x0, top, text); text may hold several words."""
    heavy, districts, state_avg = [], {}, None
    cat, cols, cur = None, None, None
    for page, top, cells in _lines(words):
        line = " ".join(t for _, t in cells).strip()
        low = line.lower()
        # category headings, e.g. "Very Heavy Rainfall (115.6 to 204.4 mm)"
        if "rainfall (" in low and " mm)" in low and not _NUM.match(cells[0][1]):
            cat = next((name for key, name in CATEGORIES if low.startswith(key)), None)
            cols, cur = None, None
            continue
        # table header: remember where the columns are (a cell starts nearest its header's left side)
        if cat and "district" in low and "station" in low:
            pos = {}
            for x, t in cells:
                for key in ("district", "taluk", "station", "rainfall"):
                    if t.lower().startswith(key):
                        pos[key] = x
            if len(pos) == 4:
                xs = [cells[0][0], pos["district"], pos["taluk"], pos["station"], pos["rainfall"]]
                cols = [(a + b) / 2 for a, b in zip(xs, xs[1:])]   # boundaries between the 5 columns
            continue
        if cat and cols:
            parts = [[] for _ in range(5)]
            for x, t in cells:
                parts[sum(x >= c for c in cols)].append(t)
            sno, dist, taluk, stn, val = (" ".join(p).strip() for p in parts)
            if sno.isdigit() and _NUM.match(val):
                cur = {"district": dist, "taluk": taluk, "station": stn, "rain": float(val), "category": cat}
                heavy.append(cur)
            elif cur and not sno and not val and (stn or taluk):
                # a long name wrapped onto the next line (or the next page)
                if stn:
                    cur["station"] = (cur["station"] + " " + stn).strip()
                if taluk:
                    cur["taluk"] = (cur["taluk"] + " " + taluk).strip()
            elif "powered by" not in low:
                cur = None
            continue
        # district status table: "1 Mayiladuthurai 6 6 166.8 27.8" and the "Total ..." row
        toks = line.split()
        if len(toks) >= 5 and all(_NUM.match(t) for t in toks[-4:]):
            name = " ".join(toks[1:-4]) if toks[0].isdigit() else " ".join(toks[:-4])
            if name.lower() == "total":
                state_avg = float(toks[-1])
            elif toks[0].isdigit() and name:
                districts[name] = (int(float(toks[-3])), float(toks[-1]))
    for s in heavy:
        s["station"] = re.sub(r"\(\s+", "(", re.sub(r"\s+", " ", s["station"]))
    return {"heavy": heavy, "districts": districts, "state_avg": state_avg}


def _pdf_words(path):
    import pdfplumber
    words = []
    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages, 1):
            for w in page.extract_words(keep_blank_chars=False, use_text_flow=False):
                words.append((i, float(w["x0"]), float(w["top"]), w["text"]))
    return words


def daily_gauges(d: date):
    """The day's daily-gauge report (see module doc), or None if not out yet / unreadable."""
    path = download_pdf(d)
    if not path:
        return None
    try:
        rep = parse_words(_pdf_words(path))
    except Exception as e:
        print(f"  daily gauge PDF for {d} could not be read: {e}")
        return None
    if not rep["districts"]:
        print(f"  daily gauge PDF for {d}: no district table found")
        return None
    return rep


if __name__ == "__main__":
    import sys
    d = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else tf.today_ist()
    rep = daily_gauges(d)
    if not rep:
        raise SystemExit(f"No daily gauge report for {d} yet")
    print(f"{len(rep['districts'])} districts, state average {rep['state_avg']} mm")
    for s in rep["heavy"]:
        print(f"  {s['rain']:6.1f}  {s['category']:16} {s['station']} ({s['taluk']}, {s['district']})")
