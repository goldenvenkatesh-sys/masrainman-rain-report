"""
Telangana: run this on the PC (in India) every hour. It reads TGDPS, saves data/tg and pushes it to GitHub,
which then makes the Telangana reports (tg-report.yml).

    python tg_pc.py            # one run: live "rain so far", plus the finished rain-day once it is out
    python tg_pc.py --setup    # Windows: add an hourly Task Scheduler job (at :20 past every hour)

Needs git with push access to this repo (the same login you use for git push) and
`pip install -r requirements.txt`.
"""
import os
import subprocess
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
sys.path.insert(0, HERE)


def git(*args, check=True):
    print("  git", " ".join(args))
    return subprocess.run(["git", *args], check=check, capture_output=True, text=True)


def setup():
    py = sys.executable.replace("python.exe", "pythonw.exe") if os.name == "nt" else sys.executable
    cmd = ["schtasks", "/Create", "/F", "/TN", "MasRainman Telangana rain", "/SC", "HOURLY", "/ST", "00:20",
           "/TR", f'"{py}" "{os.path.join(HERE, "tg_pc.py")}"']
    print(" ".join(cmd))
    subprocess.run(cmd, check=True)
    print("Done: runs every hour at :20. Check it in Task Scheduler → 'MasRainman Telangana rain'.")


def main():
    import tg_fetch
    import tnsmart_fetch as tf

    log = open(os.path.join(HERE, "tg_pc.log"), "a", encoding="utf-8")
    sys.stdout = sys.stderr = log
    print(f"\n=== {datetime.now(tf.IST):%Y-%m-%d %H:%M} IST")
    git("pull", "--rebase", "-q", check=False)

    try:
        tg_fetch.save_live()
    except Exception as e:
        print(f"  live failed: {e}")
    # the finished rain-day (08:30 to 08:30): try every run until it is saved
    today = tf.today_ist()
    if tf.rain_day_is_complete(today) and not os.path.exists(os.path.join(tg_fetch.DAILY, f"{today}.json")):
        try:
            tg_fetch.save_daily()
        except Exception as e:
            print(f"  daily failed: {e}")

    git("add", "data/tg")
    if git("diff", "--cached", "--quiet", check=False).returncode == 0:
        print("  nothing new")
        return
    git("commit", "-q", "-m", f"Telangana rainfall files {datetime.now(tf.IST):%Y-%m-%d %H:%M}")
    for _ in range(3):
        if git("push", "-q", check=False).returncode == 0:
            print("  pushed")
            return
        git("pull", "--rebase", "-q", check=False)
    print("  push failed; the next run will try again")


if __name__ == "__main__":
    setup() if "--setup" in sys.argv else main()
