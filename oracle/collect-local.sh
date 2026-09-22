#!/usr/bin/env bash
set -euo pipefail

readonly APP_DIR="/opt/laundry-tracker"
readonly STATE_DIR="/var/lib/laundry-tracker"
readonly LOCAL_DATA_DIR="${STATE_DIR}/data"
readonly SECRETS_DIR="/etc/laundry-tracker"

# The publisher uses this lock only while copying local data, so it cannot
# export a half-written poll.
exec 9>"${STATE_DIR}/collector.lock"
flock -w 50 9

if [[ ! -s "${SECRETS_DIR}/wash-email" || ! -s "${SECRETS_DIR}/wash-password" ]]; then
  echo "WASH credential files are missing or empty." >&2
  exit 1
fi

export WASH_EMAIL WASH_PASSWORD
WASH_EMAIL="$(<"${SECRETS_DIR}/wash-email")"
WASH_PASSWORD="$(<"${SECRETS_DIR}/wash-password")"

python3 "${APP_DIR}/collect.py" --data-dir "${LOCAL_DATA_DIR}"
