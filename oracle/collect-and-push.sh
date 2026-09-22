#!/usr/bin/env bash
set -euo pipefail

readonly APP_DIR="/opt/laundry-tracker"
"${APP_DIR}/collect-local.sh"
"${APP_DIR}/publish.sh"
