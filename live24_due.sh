#!/usr/bin/env bash
# Past-24-hours video (live24) for Karnataka, Andhra Pradesh or Telangana, once a day.
#   bash live24_due.sh <ka|ap|tg> [YYYY-MM-DD]
# Without a date: makes the rain-day that ended at 08:30 today, if it is between 08:35 and 14:00 IST and the
# video is not on the reports branch yet. The hours come from the hourly "rain so far" checks, so this runs in
# the same workflow (and with the same saved snapshots in data/raw) as that state's live picture.
set -uo pipefail
R=$1; D=${2:-}
if [ -z "$D" ]; then
  hm=$((10#$(TZ=Asia/Kolkata date +%H%M)))
  if [ "$hm" -lt 835 ] || [ "$hm" -gt 1400 ]; then echo "Past-24-hours video: not due (IST $hm)"; exit 0; fi
  D=$(TZ=Asia/Kolkata date +%F)
  have=$(curl -s "https://raw.githubusercontent.com/$GITHUB_REPOSITORY/reports/index.json?t=$(date +%s)" | python3 -c "
import json,sys
try: d=json.load(sys.stdin)
except Exception: d={'reports':[]}
print('yes' if any(r['region']=='$R' and r['kind']=='live24' and r['day']=='$D' for r in d['reports']) else 'no')")
  if [ "$have" = yes ]; then echo "Past-24-hours video for $D already made"; exit 0; fi
fi
python rain_report.py live24 --region "$R" --date "$D" || echo "No past-24-hours video for $D"
