#!/usr/bin/env bash
# Save the new reports in out/ to the `reports` branch, then (optional) tell the
# WeatherNext 3 site to republish so the reports appear there within minutes.
#
# Each save is a commit on top of the branch, so only the new files are uploaded (a full re-upload
# of the ~600 MB branch kept timing out with HTTP 408). Once a day (the first save of the IST day)
# the branch is instead replaced by a single fresh commit, so deleted old videos don't pile up in
# its history; if that big upload fails, the save falls back to a normal small one.
# Several workflows can finish at the same time, so a push that loses the race starts again from
# the newest branch (up to 5 tries).
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
squash_failed=0
TODAY=$(TZ=Asia/Kolkata date +%Y-%m-%d)
export GIT_AUTHOR_NAME="github-actions[bot]" GIT_COMMITTER_NAME="github-actions[bot]"
export GIT_AUTHOR_EMAIL="github-actions[bot]@users.noreply.github.com"
export GIT_COMMITTER_EMAIL="github-actions[bot]@users.noreply.github.com"
for try in 1 2 3 4 5; do
  rm -rf "$WORK" && mkdir -p "$WORK"
  OLD=""
  if git fetch -q --depth 1 origin "$BRANCH" 2>/dev/null; then
    OLD=$(git rev-parse FETCH_HEAD)
    git --work-tree="$WORK" checkout -q FETCH_HEAD -- . && git reset -q
  fi
  python publish_reports.py "$OUT" "$WORK" || exit 1

  # tree of the new folder, built in this repo (which already has the old branch's files)
  export GIT_INDEX_FILE="$RUNNER_TEMP/reports.index"
  rm -f "$GIT_INDEX_FILE"
  GD=$(git rev-parse --absolute-git-dir)
  git --git-dir="$GD" --work-tree="$WORK" -C "$WORK" add -A . && TREE=$(git --git-dir="$GD" write-tree)
  unset GIT_INDEX_FILE
  MSG="Rainfall reports $(TZ=Asia/Kolkata date '+%d %b %H:%M IST')"
  OLD_DAY=""
  [ -n "$OLD" ] && OLD_DAY=$(TZ=Asia/Kolkata git log -1 --format=%cd --date=format-local:%Y-%m-%d "$OLD")
  if [ -n "$OLD" ] && { [ "$OLD_DAY" = "$TODAY" ] || [ "$squash_failed" = 1 ]; }; then
    NEW=$(git commit-tree "$TREE" -p "$OLD" -m "$MSG"); how="new files only"
  else
    NEW=$(git commit-tree "$TREE" -m "$MSG (fresh daily copy)"); how="fresh daily copy"
  fi
  # replace the branch only if nobody changed it since we fetched it (empty OLD = it must not exist yet)
  if git push -q --force-with-lease="refs/heads/$BRANCH:$OLD" origin "$NEW:refs/heads/$BRANCH"; then
    ok=1; echo "Saved to the '$BRANCH' branch ($how)."; break
  fi
  [ "$how" = "fresh daily copy" ] && [ -n "$OLD" ] && squash_failed=1
  echo "Could not save ($how): another run saved at the same time or the upload failed; trying again ($try/5)…"
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
