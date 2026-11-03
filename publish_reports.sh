#!/usr/bin/env bash
# Save the new reports in out/ to the `reports` branch, then (optional) tell the
# WeatherNext 3 site to republish so the reports appear there within minutes.
#
# The branch is saved as a single fresh commit each time (no file history), so old
# videos don't pile up in the repo. Several workflows can finish at the same time,
# so a push that loses the race starts again from the newest branch (up to 5 tries).
#
# Optional secret WN3_DISPATCH_TOKEN: a fine-grained token with "Actions: read and write"
# on goldenvenkatesh-sys/masrainman-weathernext3. Without it the site still picks the
# reports up on its own every 2 hours.
set -uo pipefail

OUT=${1:-out}
BRANCH=reports
WORK="$RUNNER_TEMP/reports"
[ -n "$(ls "$OUT"/*.mp4 2>/dev/null)" ] || { echo "No new reports to save."; exit 0; }

ok=0
for try in 1 2 3 4 5; do
  rm -rf "$WORK" && mkdir -p "$WORK"
  OLD=""
  # time limits: a stuck fetch/push used to hang until the job timed out (28 Oct 22:45 TN run)
  if timeout 120 git fetch -q --depth 1 origin "$BRANCH" 2>/dev/null; then
    OLD=$(git rev-parse FETCH_HEAD)
    git --work-tree="$WORK" checkout -q FETCH_HEAD -- . && git reset -q
  fi
  python publish_reports.py "$OUT" "$WORK" || exit 1

  (
    cd "$WORK"
    rm -rf .git
    git init -q -b "$BRANCH"
    git config user.name "github-actions[bot]"
    git config user.email "github-actions[bot]@users.noreply.github.com"
    git add -A
    git commit -q -m "Rainfall reports $(TZ=Asia/Kolkata date '+%d %b %H:%M IST')"
  )
  URL=$(git remote get-url origin)
  AUTH=$(git config --get-all http.https://github.com/.extraheader || true)
  # replace the branch only if nobody changed it since we fetched it (empty OLD = it must not exist yet)
  if timeout 180 git -C "$WORK" ${AUTH:+-c http.https://github.com/.extraheader="$AUTH"} push -q \
       --force-with-lease="$BRANCH:$OLD" "$URL" "HEAD:$BRANCH"; then
    ok=1; echo "Saved to the '$BRANCH' branch."; break
  fi
  echo "Push was rejected or timed out (another run may have saved at the same time); trying again ($try/5)…"
  sleep $((try * 7))
done
[ "$ok" = 1 ] || { echo "Could not save the reports branch."; exit 1; }

if [ -n "${WN3_DISPATCH_TOKEN:-}" ]; then
  code=$(curl -s -o /dev/null -w '%{http_code}' -X POST \
    -H "Authorization: Bearer $WN3_DISPATCH_TOKEN" -H "Accept: application/vnd.github+json" \
    https://api.github.com/repos/goldenvenkatesh-sys/masrainman-weathernext3/dispatches \
    -d '{"event_type":"rain-reports"}')
  echo "Asked the WeatherNext 3 site to republish (HTTP $code)."
else
  echo "WN3_DISPATCH_TOKEN not set: the WeatherNext 3 site will pick these up on its 2-hourly check."
fi
