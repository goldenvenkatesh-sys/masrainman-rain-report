"""
Backup copy of every report on GitHub, so it can be downloaded even when
Telegram is down or the bot is blocked.

  Latest of each kind : https://github.com/<repo>/releases/tag/latest
                        (stable names, e.g. rain_tn_live.mp4, rain_daily_ta.png)
  Daily / multi-day / monthly reports are also kept by date in a monthly
  release, e.g. tag reports-2026-10.

Needs GH_TOKEN (the workflow's github.token) and runs the gh CLI that GitHub's
runners come with. Never fails the job: a problem here only prints a warning.

Usage: python publish_github.py out/*.mp4      (send_telegram.py calls it too)
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))


def _gh(*args):
    return subprocess.run(["gh", *args], capture_output=True, text=True, timeout=300)


def _ensure_release(tag, title, notes):
    if _gh("release", "view", tag).returncode != 0:
        _gh("release", "create", tag, "--title", title, "--notes", notes, "--latest=false")


def stable_name(path):
    """rain_tn_live_2026-10-25_1030.mp4 -> rain_tn_live.mp4"""
    name = os.path.basename(path)
    name = re.sub(r"_\d{4}-\d{2}(-\d{2})?(_\d{4})?", "", name)
    return name.replace("_to.", ".").replace("_to_", "_")


def publish(videos):
    if not (os.environ.get("GH_TOKEN") and os.environ.get("GITHUB_REPOSITORY") and shutil.which("gh")):
        print("GitHub backup skipped (needs GH_TOKEN in the workflow).")
        return
    files = []
    for v in videos:
        if os.path.exists(v):
            files.append(v)
            png = v[:-4] + ".png"
            if os.path.exists(png):
                files.append(png)
    if not files:
        return
    repo = os.environ["GITHUB_REPOSITORY"]
    try:
        _ensure_release("latest", "Latest rainfall reports",
                        "The newest report of each kind. Files are replaced every time a report is made.")
        with tempfile.TemporaryDirectory() as tmp:
            aliases = []
            for f in files:
                a = os.path.join(tmp, stable_name(f))
                shutil.copyfile(f, a)
                aliases.append(a)
            r = _gh("release", "upload", "latest", *aliases, "--clobber")
        if r.returncode:
            print("GitHub backup (latest) failed:", r.stderr.strip()[:300])
        else:
            print(f"GitHub backup: {len(files)} file(s) -> https://github.com/{repo}/releases/tag/latest")
        dated = [f for f in files if "_live_" not in os.path.basename(f)]
        if dated:
            tag = "reports-" + datetime.now(IST).strftime("%Y-%m")
            _ensure_release(tag, f"Rainfall reports {tag[8:]}", "Daily, multi-day and monthly reports of this month, by date.")
            r = _gh("release", "upload", tag, *dated, "--clobber")
            if r.returncode:
                print(f"GitHub backup ({tag}) failed:", r.stderr.strip()[:300])
    except Exception as e:                      # backup must never stop the Telegram send
        print("GitHub backup failed:", e)


if __name__ == "__main__":
    publish(sys.argv[1:])
