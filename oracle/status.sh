#!/usr/bin/env bash
set -euo pipefail

echo "Timers"
systemctl list-timers laundry-collector.timer laundry-publisher.timer --no-pager
echo
echo "Latest collection result"
systemctl status laundry-collector.service --no-pager || true
echo
echo "Latest publication result"
systemctl status laundry-publisher.service --no-pager || true
echo
echo "Recent collector log"
journalctl -u laundry-collector.service -n 30 --no-pager
echo
echo "Latest local observation"
python3 -c 'import json; d=json.load(open("/var/lib/laundry-tracker/data/index.json")); print("Updated:", d["updated_at_utc"], "Status:", d["last_poll_status"])'
echo
echo "Latest published batch"
git -C /var/lib/laundry-tracker/history log -1 --format='%h %cI %s'
