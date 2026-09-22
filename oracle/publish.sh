#!/usr/bin/env bash
set -euo pipefail

readonly STATE_DIR="/var/lib/laundry-tracker"
readonly HISTORY_DIR="${STATE_DIR}/history"
readonly LOCAL_DATA_DIR="${STATE_DIR}/data"

# Only one publisher can operate on the Git worktree at a time.
exec 8>"${STATE_DIR}/publisher.lock"
flock -w 50 8

# Wait for any active local write and copy a consistent snapshot. Release this
# lock before Git network operations, which must never block minute collection.
exec 9>"${STATE_DIR}/collector.lock"
flock -w 50 9
rsync -a "${LOCAL_DATA_DIR}/" "${HISTORY_DIR}/data/"
flock -u 9
exec 9>&-
cd "${HISTORY_DIR}"

# Commit local observations before using the network, so a temporary outage
# cannot discard or hide any of them.
git add -- data
if ! git diff --cached --quiet; then
  observed_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  git commit -m "Publish laundry observations through ${observed_at}"
fi

for attempt in 1 2 3 4 5; do
  if git fetch origin data; then
    if ! git rebase origin/data; then
      git rebase --abort || true
      echo "Could not reconcile local history with origin/data." >&2
      exit 1
    fi
  else
    echo "Could not fetch origin/data; trying the push anyway." >&2
  fi

  if [[ "$(git rev-list --count origin/data..HEAD)" == 0 ]]; then
    echo "No new observations to publish."
    exit 0
  fi
  if git push origin HEAD:data; then
    exit 0
  fi
  echo "Push attempt ${attempt} failed; retrying." >&2
  sleep $((attempt * 2))
done

echo "Could not publish observations after five attempts; local commits are retained." >&2
exit 1
