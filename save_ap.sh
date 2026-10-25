#!/usr/bin/env bash
# Save data/ap (Andhra Pradesh daily rain archive) to the repo without failing when another run saved first.
# If the push or rebase clashes, it drops its own commit, takes the latest repo, saves the day again on
# top of it, and tries again (up to 4 times).
set -u
git config user.name "github-actions[bot]"
git config user.email "41898699+github-actions[bot]@users.noreply.github.com"

for try in 1 2 3 4; do
  git add data/ap
  if git diff --cached --quiet; then
    echo "Andhra Pradesh files: nothing new to save."
    exit 0
  fi
  git commit -q -m "Andhra Pradesh rainfall files $(TZ=Asia/Kolkata date +'%Y-%m-%d %H:%M')"
  if git pull --rebase -q && git push -q; then
    echo "Andhra Pradesh files saved (try $try)."
    exit 0
  fi
  echo "Another run saved first (try $try); taking the latest repo and saving again."
  git rebase --abort 2>/dev/null || true
  git fetch -q origin "${GITHUB_REF_NAME:-main}"
  git reset -q --hard "origin/${GITHUB_REF_NAME:-main}"
  python ap_fetch.py daily
  sleep $((try * 10))
done
echo "Could not save Andhra Pradesh files after 4 tries; they will be saved by the next run."
exit 0
