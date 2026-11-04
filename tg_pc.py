"""
NOTE (Nov 2026): GitHub now reads TGDPS itself through the Vercel relay (tg-fetch.yml), so this PC job is
no longer needed. Turn off the Task Scheduler job "MasRainman Telangana rain" so the two do not both push.

Telangana: run this on the PC (in India) every hour. It reads TGDPS, saves data/tg and pushes it to GitHub,
which then makes the Telangana reports (tg-report.yml).

    python tg_pc.py            # one run: rain so far (the 08:25 run also saves the finished rain-day)
    python tg_pc.py --setup    # Windows: silent job every hour at :25 and when you sign in

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
    """Windows: one Task Scheduler job that runs silently (pythonw, no window)
       - every hour at :25,
       - 1 minute after you sign in to Windows (so turning the PC on updates at once),
       - as soon as possible after a run that was missed while the PC was off or asleep,
       - and wakes the PC from sleep for the hourly run."""
    py = sys.executable.replace("python.exe", "pythonw.exe") if os.name == "nt" else sys.executable
    script = os.path.join(HERE, "tg_pc.py")
    ps = f"""
$a = New-ScheduledTaskAction -Execute '{py}' -Argument '"{script}"' -WorkingDirectory '{HERE}'
$t1 = New-ScheduledTaskTrigger -Once -At '00:25' -RepetitionInterval (New-TimeSpan -Hours 1)
$t2 = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$t2.Delay = 'PT1M'
$s = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
     -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
Register-ScheduledTask -TaskName 'MasRainman Telangana rain' -Action $a -Trigger $t1,$t2 -Settings $s -Force | Out-Null
"""
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps], check=True)
    print("Done: runs silently every hour at :25 and 1 minute after you sign in to Windows,\n"
          "catches up on missed runs and wakes the PC from sleep. "
          "See Task Scheduler → 'MasRainman Telangana rain'.")


def main():
    import tg_fetch
    import tnsmart_fetch as tf

    log = open(os.path.join(HERE, "tg_pc.log"), "a", encoding="utf-8")
    sys.stdout = sys.stderr = log
    print(f"\n=== {datetime.now(tf.IST):%Y-%m-%d %H:%M} IST")
    git("pull", "--rebase", "-q", check=False)

    try:
        tg_fetch.save_live()          # after 07:30 this also saves the finished rain-day
    except Exception as e:
        print(f"  TGDPS live failed: {e}")
    try:
        tg_fetch.save_mandal_day()    # finished rain-day from the mandal pages, if 08:25 was missed
    except Exception as e:
        print(f"  TGDPS mandal day failed: {e}")

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
