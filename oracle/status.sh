#!/usr/bin/env bash
set -euo pipefail

echo "Timer"
systemctl list-timers laundry-collector.timer --no-pager
echo
echo "Latest service result"
systemctl status laundry-collector.service --no-pager || true
echo
echo "Recent collector log"
journalctl -u laundry-collector.service -n 30 --no-pager
echo
echo "Latest locally committed observation"
git -C /var/lib/laundry-tracker/history log -1 --format='%h %cI %s'
