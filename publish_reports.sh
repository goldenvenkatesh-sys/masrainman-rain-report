#!/usr/bin/env bash
# Save the new reports in out/ to the `reports` branch, then (optional) tell the
# WeatherNext 3 site to republish so the reports appear there within minutes.
#
# The branch is saved as a single fresh commit each time (no file history), so old
# videos don't pile up in the repo. Several workflows can finish at the same time,
# so a push that loses the race starts again from the newest branch (up to 5 tries).
#
# Only the new files travel: the branch holds ~1 GB of videos, and downloading all of
# it and uploading all of it again on every save took 7+ minutes each way and made the
# live runs hit their time limit (3 Nov 2026). So the branch is fetched without file
# contents (--filter=blob:none), the old files are kept by reference, and the push
# sends only the new pictures, index.json and README.md.
#
# Optional secret WN3_DISPATCH_TOKEN: a fine-grained token with "Actions: read and write"
# on goldenvenkatesh-sys/masrainman-weathernext3. Without it the site still picks the
# reports up on its own every 2 hours.
set -uo pipefail

OUT=${1:-out}
BRANCH=reports
WORK="$RUNNER_TEMP/reports"
IDX="$RUNNER_TEMP/reports.index"      # a separate git index, so the main checkout is untouched
[ -n "$(ls "$OUT"/*.mp4 2>/dev/null)" ] || { echo "No new reports to save."; exit 0; }

ok=0
for try in 1 2 3 4 5; do
  rm -rf "$WORK" "$IDX" && mkdir -p "$WORK/files"
  OLD=""
  # time limits: a stuck fetch/push used to hang until the job timed out
  if timeout 300 git fetch -q --depth 1 --filter=blob:none origin "$BRANCH" 2>/dev/null; then
    OLD=$(git rev-parse FETCH_HEAD)
    # index.json is the only old content the update needs (one small file)
    # (if it can't be read, try again: going on without it would drop every old report)
    if [ -n "$(git ls-tree --name-only "$OLD" -- index.json)" ] &&
       ! timeout 120 git cat-file blob "$OLD:index.json" > "$WORK/index.json"; then
      echo "Could not read index.json; trying again ($try/5)…"; sleep $((try * 7)); continue
    fi
    # empty stand-ins for the old report files, so publish_reports.py can see and prune them
    git ls-tree -r --name-only "$OLD" -- files/ | while IFS= read -r p; do : > "$WORK/$p"; done
    GIT_INDEX_FILE="$IDX" git read-tree "$OLD"
  fi
  python publish_reports.py "$OUT" "$WORK" || exit 1

  # new tree = old tree, minus the files pruned, plus the new/changed (non-empty) files
  GD=$(git rev-parse --absolute-git-dir)
  g() { GIT_INDEX_FILE="$IDX" git --git-dir="$GD" --work-tree="$WORK" -C "$WORK" "$@"; }
  [ -z "$OLD" ] || g ls-files -z --deleted | g rm -q --cached --pathspec-from-file=- --pathspec-file-nul 2>/dev/null
  ( cd "$WORK" && find . -type f ! -empty -printf '%P\0' ) | g add -f --pathspec-from-file=- --pathspec-file-nul
  TREE=$(g write-tree --missing-ok)
  COMMIT=$(GIT_AUTHOR_NAME="github-actions[bot]" GIT_AUTHOR_EMAIL="github-actions[bot]@users.noreply.github.com" \
           GIT_COMMITTER_NAME="github-actions[bot]" GIT_COMMITTER_EMAIL="github-actions[bot]@users.noreply.github.com" \
           git commit-tree "$TREE" -m "Rainfall reports $(TZ=Asia/Kolkata date '+%d %b %H:%M IST')")

  # replace the branch only if nobody changed it since we fetched it (empty OLD = it must not exist yet)
  if timeout 300 git push -q --force-with-lease="$BRANCH:$OLD" origin "$COMMIT:refs/heads/$BRANCH"; then
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
