"""
Add finished reports (out/*.mp4, .gif, .png, .txt) to the `reports` branch folder
and rebuild its index, so the WeatherNext 3 site and the GitHub app can show them.

Usage: python publish_reports.py <out dir> <reports folder>

The reports folder holds:
  files/<name>.mp4 / .gif / .png / .txt   the report files
  index.json                              list of reports, newest first (read by the website)
  README.md                               latest reports with previews (shows in the GitHub app)

What is kept (older ones are removed so the branch stays small):
  live (rain so far)      the newest 24 per region (one day of hourly pictures)
  daily / live24 / accum  the last 10 days (also the satellite sat24 / gpm3d reports)
  month                   the last 13 months
"""
import json
import os
import re
import shutil
import sys
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
REGIONS = {"": ("tn", "Tamil Nadu"), "kl_": ("kl", "Kerala"), "ka_": ("ka", "Karnataka"),
           "ap_": ("ap", "Andhra Pradesh")}
KINDS = {"live": "Rain so far (live)", "daily": "24-hour rain", "live24": "Past 24 hours, hour by hour", "accum": "Total rain (multi-day)",
         "month": "Monthly total", "sat24": "Satellite rain, past 24 hours", "gpm3d": "Satellite radar 3D pass"}
LANGS = {"ta": "Tamil", "en": "English", "ml": "Malayalam"}
EXTS = ("mp4", "gif", "png", "txt")
KEEP_LIVE = 24
KEEP_DAYS = 10
KEEP_MONTHS = 13

NAME = re.compile(
    r"^rain_(?P<tag>kl_|ka_|ap_)?(?P<kind>live24|live|daily|accum|month|sat24|gpm3d)_(?P<rest>.+?)(?:_(?P<lang>ta|en|ml))?$")


def parse(base):
    """rain_kl_daily_2026-10-24_ml → dict with region, kind, dates, language; None if unknown."""
    m = NAME.match(base)
    if not m:
        return None
    region, region_name = REGIONS[m["tag"] or ""]
    kind, rest = m["kind"], m["rest"]
    try:
        if kind == "live":
            t = datetime.strptime(rest, "%Y-%m-%d_%H%M")
            day, label = t.date().isoformat(), "up to " + t.strftime("%d %b, %I:%M %p").replace(" 0", " ")
            sort = t.strftime("%Y-%m-%dT%H:%M")
        elif kind in ("sat24", "gpm3d"):          # <time, IST>: end of the 24 h / time of the pass
            t = datetime.strptime(rest, "%Y-%m-%d_%H%M")
            when = t.strftime("%d %b, %I:%M %p").replace(" 0", " ")
            day, sort = t.date().isoformat(), t.strftime("%Y-%m-%dT%H:%M")
            label = ("24 h up to " if kind == "sat24" else "pass at ") + when
        elif kind in ("daily", "live24"):
            d = datetime.strptime(rest, "%Y-%m-%d")
            day, label, sort = rest, "rain-day " + d.strftime("%d %b %Y"), rest + "T08:30"
        elif kind == "month":
            d = datetime.strptime(rest, "%Y-%m")
            day, label, sort = rest + "-01", d.strftime("%B %Y"), rest + "-01T00:00"
        else:  # accum: <start>_to_<end>
            a, b = rest.split("_to_")
            da, db = datetime.strptime(a, "%Y-%m-%d"), datetime.strptime(b, "%Y-%m-%d")
            day, sort = b, b + "T08:30"
            label = da.strftime("%d %b") + " → " + db.strftime("%d %b %Y")
    except ValueError:
        return None
    return {"id": base, "region": region, "region_name": region_name, "kind": kind,
            "kind_name": KINDS[kind], "lang": m["lang"] or ("ta" if region == "tn" else "en"),
            "day": day, "label": label, "sort": sort}


def main(out_dir, rep):
    files = os.path.join(rep, "files")
    os.makedirs(files, exist_ok=True)
    idx_path = os.path.join(rep, "index.json")
    try:
        items = {i["id"]: i for i in json.load(open(idx_path, encoding="utf-8"))["reports"]}
    except (OSError, ValueError, KeyError):
        items = {}

    now = datetime.now(IST)
    added = 0
    for fn in sorted(os.listdir(out_dir)) if os.path.isdir(out_dir) else []:
        base, ext = os.path.splitext(fn)
        if ext != ".mp4":
            continue
        info = parse(base)
        if not info:
            print("  skip (unknown name):", fn)
            continue
        info["files"] = {}
        for e in EXTS:
            src = os.path.join(out_dir, base + "." + e)
            if os.path.exists(src):
                shutil.copy(src, os.path.join(files, base + "." + e))
                info["files"][e] = "files/" + base + "." + e
                if e != "txt":
                    info["files"][e + "_mb"] = round(os.path.getsize(src) / 1e6, 1)
        lst = os.path.join(out_dir, base + "_list.png")     # station-wise readings (daily / accum)
        if os.path.exists(lst):
            shutil.copy(lst, os.path.join(files, base + "_list.png"))
            info["files"]["list"] = "files/" + base + "_list.png"
            info["files"]["list_mb"] = round(os.path.getsize(lst) / 1e6, 1)
        if info["files"].get("txt"):
            info["caption"] = open(os.path.join(files, base + ".txt"), encoding="utf-8").read().strip()
        info["made"] = now.isoformat(timespec="minutes")
        items[base] = info
        added += 1
        print("  added:", base)

    # keep the newest live pictures per region, and recent daily/accum/month reports
    keep, live_count = {}, {}
    day_cut = (now - timedelta(days=KEEP_DAYS)).date().isoformat()
    month_cut = (now.replace(day=1) - timedelta(days=31 * KEEP_MONTHS)).date().isoformat()
    for it in sorted(items.values(), key=lambda i: i["sort"], reverse=True):
        k = it["kind"]
        if k == "live":
            live_count[it["region"]] = live_count.get(it["region"], 0) + 1
            if live_count[it["region"]] > KEEP_LIVE:
                continue
        elif k == "month":
            if it["day"] < month_cut:
                continue
        elif it["day"] < day_cut:
            continue
        keep[it["id"]] = it

    # remove files no longer listed
    wanted = {os.path.basename(p) for it in keep.values() for e, p in it["files"].items() if not e.endswith("_mb")}
    for fn in os.listdir(files):
        if fn not in wanted:
            os.remove(os.path.join(files, fn))

    reports = sorted(keep.values(), key=lambda i: (i["sort"], i["id"]), reverse=True)
    with open(idx_path, "w", encoding="utf-8") as f:
        json.dump({"updated": now.isoformat(timespec="minutes"), "reports": reports}, f,
                  ensure_ascii=False, indent=1)
    write_readme(rep, reports, now)
    print(f"Reports: {added} added, {len(reports)} kept.")


def write_readme(rep, reports, now):
    """Latest report of each kind with a picture: easy to open from the GitHub phone app."""
    lines = ["# MasRainman rainfall reports", "",
             f"Updated {now.strftime('%d %b %Y, %I:%M %p')} IST. Tap a file to open it; "
             "use ⋯ → *Download* (or *Open in browser*) to save it.", ""]
    seen = set()
    for it in reports:
        key = (it["region"], it["kind"], it["lang"])
        if key in seen:
            continue
        seen.add(key)
        f = it["files"]
        title = f"{it['region_name']} · {it['kind_name']}"
        if it["region"] == "kl":
            title += f" ({LANGS.get(it['lang'], it['lang'])})"
        links = " · ".join(f"[{e.upper()}]({f[e]})" for e in ("mp4", "gif", "png") if f.get(e))
        if f.get("list"):
            links += f" · [Station list]({f['list']})"
        lines += [f"## {title}", f"{it['label']} — {links}", ""]
        if f.get("png"):
            lines += [f"![{title}]({f['png']})", ""]
    lines += ["## All reports", "", "| Report | Period | Files |", "|---|---|---|"]
    for it in reports:
        f = it["files"]
        links = " ".join(f"[{e.upper()}]({f[e]})" for e in ("mp4", "gif", "png") if f.get(e))
        if f.get("list"):
            links += f" [LIST]({f['list']})"
        lines.append(f"| {it['region_name']} · {it['kind_name']} | {it['label']} | {links} |")
    with open(os.path.join(rep, "README.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    main(sys.argv[1], sys.argv[2])
