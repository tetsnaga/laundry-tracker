#!/usr/bin/env bash
set -uo pipefail

readonly APP_DIR="/opt/laundry-tracker"
readonly STATE_DIR="/var/lib/laundry-tracker"
readonly HISTORY_DIR="${STATE_DIR}/history"
readonly SECRETS_DIR="/etc/laundry-tracker"

exec 9>"${STATE_DIR}/collector.lock"
if ! flock -n 9; then
  echo "A laundry collection is already running; skipping this timer tick."
  exit 0
fi

if [[ ! -s "${SECRETS_DIR}/wash-email" || ! -s "${SECRETS_DIR}/wash-password" ]]; then
  echo "WASH credential files are missing or empty." >&2
  exit 1
fi

export WASH_EMAIL
export WASH_PASSWORD
WASH_EMAIL="$(<"${SECRETS_DIR}/wash-email")"
WASH_PASSWORD="$(<"${SECRETS_DIR}/wash-password")"

cd "${HISTORY_DIR}"

# Incorporate any data that was published elsewhere. A temporary network failure
# must not prevent the local observation from being recorded.
if git fetch origin data; then
  if ! git rebase origin/data; then
    git rebase --abort || true
    echo "Could not reconcile the local history with origin/data." >&2
    exit 1
  fi
else
  echo "Could not fetch origin/data; collecting locally and retrying the push." >&2
fi

python3 "${APP_DIR}/collect.py" --data-dir "${HISTORY_DIR}/data"
collector_status=$?

git add -- data
if ! git diff --cached --quiet; then
  observed_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  git commit -m "Record Oracle laundry poll ${observed_at}"
fi

for attempt in 1 2 3 4 5; do
  if git push origin HEAD:data; then
    exit "${collector_status}"
  fi
  echo "Push attempt ${attempt} failed; reconciling with origin/data." >&2
  if git fetch origin data && git rebase origin/data; then
    sleep $((attempt * 2))
    continue
  fi
  git rebase --abort || true
  echo "Could not reconcile the data branch after a rejected push." >&2
  exit 1
done

echo "Could not publish the observation after five attempts." >&2
exit 1
