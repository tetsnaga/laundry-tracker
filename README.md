# Laundry monitoring

One WASH status snapshot every minute from an Oracle Cloud Always Free VM.
No browser or third-party Python packages are required. WASH credentials remain
in root-managed files on the VM; cookies exist only in memory during each run.

## Storage

The repository's `main` branch contains code. Its separate `data` branch
contains `data/observations/YYYY-MM-DD.csv`, `data/polls/YYYY-MM-DD.jsonl`, and
`data/inventory.json`. Observations are appended locally every minute and
published together every hour in one Git commit.
These are ordinary Git files, not temporary runner files, expiring artifacts, or
caches. Clone/download the `data` branch to analyze or back up the history.
Git history grows over time; this is a simple starting point, not an unlimited
database. Never force-push/reset the data branch while collecting.

Each attempted poll has a success/failure log. A skipped/dropped GitHub run has
no poll log and must be detected as a gap between timestamps. Failed fetches do
not create fake machine observations or carry forward previous availability.
The first successful poll pins the room and machine roster in `inventory.json`;
subsequent changes fail visibly for review rather than silently altering the cohort.

## Oracle Cloud deployment

The production collector and publisher are separate locked systemd one-shot
services. Collection runs every minute and records snapshots under
`/var/lib/laundry-tracker/data` without contacting GitHub. Publication runs every
hour, copies a consistent local snapshot into a separate Git worktree,
commits the pending observations together, reconciles with the `data` branch,
and retries the push. A slow or failed GitHub push cannot block collection. Local
files and Git commits retain observations during a temporary GitHub outage so a
later publication can catch up. The dashboard can lag the latest local
observation by roughly one publication interval.

Create an Always Free-eligible Ubuntu or Oracle Linux compute instance in the
tenancy's home region. A public IP is needed for initial SSH setup; the collector
itself only needs outbound HTTPS and SSH. Use an Always Free-labelled shape and
image, and confirm the estimated monthly cost is zero before creating it.

Create a repository deploy key on a trusted computer and add its public half to
`tetsnaga/laundry-tracker` with write access:

```bash
ssh-keygen -t ed25519 -f laundry-tracker-oracle -C oracle-laundry-collector -N ''
gh api repos/tetsnaga/laundry-tracker/keys \
  --method POST \
  -f title='Oracle laundry collector' \
  -f key="$(<laundry-tracker-oracle.pub)" \
  -F read_only=false
```

Copy the repository and private deploy key to the VM, then install. The installer
prompts for the WASH email and password without placing either in shell history:

```bash
scp -i OCI_LOGIN_KEY -r ./laundry-tracker ubuntu@PUBLIC_IP:/tmp/
scp -i OCI_LOGIN_KEY laundry-tracker-oracle ubuntu@PUBLIC_IP:/tmp/
ssh -i OCI_LOGIN_KEY ubuntu@PUBLIC_IP
cd /tmp/laundry-tracker
sudo ./oracle/install.sh /tmp/laundry-tracker-oracle
sudo systemctl start laundry-collector.service
sudo systemctl start laundry-publisher.service
sudo ./oracle/status.sh
```

Only after the proof run appears on the GitHub `data` branch, turn on the Oracle
timer. The repository's GitHub workflow is manual-only after the cutover:

```bash
sudo systemctl enable --now laundry-collector.timer
sudo systemctl enable --now laundry-publisher.timer
```

The timer survives reboots and catches up with one run after downtime. Inspect it
at any time with `sudo /opt/laundry-tracker/status.sh`, or with
`systemctl list-timers laundry-collector.timer laundry-publisher.timer` and
`journalctl -u laundry-collector.service -u laundry-publisher.service`.

Oracle documents that Always Free compute instances can be reclaimed when they
remain idle. This collector is intentionally light, so the dashboard's stale-data
indicator remains the practical health alert even after migration.

## Manual GitHub Actions fallback

The workflow remains available through `workflow_dispatch` for a manual fallback.
It has no scheduled trigger; Oracle is the only scheduled collector. An initialized
`data` branch must already exist in the repository.

Run these commands in your own terminal, replacing OWNER/REPO. Each secret command
prompts for its value; do not put a password directly in the command line.

```bash
gh secret set WASH_EMAIL --repo OWNER/REPO
gh secret set WASH_PASSWORD --repo OWNER/REPO
gh workflow run collect.yml --repo OWNER/REPO
gh run list --repo OWNER/REPO --workflow collect.yml --limit 3
```

Check that a manual run succeeds and that the data branch contains the new CSV
and poll log. Rotating the fallback password only requires re-running
`gh secret set WASH_PASSWORD`. Optionally set `WASH_ROOM_ID` as a repository
variable to select a specific room already available in the account.

## Recorded fields and countdown analysis

- `poll_id`: unique observation batch (GitHub run ID and attempt, or local UUID).
- `request_started_at_utc`, `observed_at_utc`: actual request boundaries in UTC.
- `room_id`, `machine_type`, `machine_id`: stable grouping keys.
- `raw_lmc_status`: full unmodified WASH status code.
- `server_timestamp_raw`: WASH response timestamp, preserved without timezone assumptions.
- `machine_report_time_raw`: the API's `SeverTime` field, preserving its spelling only at source.
- `remaining_min_raw`: WASH's `RemainingMin`, preserved before adjustment, including zero.
- `report_age_seconds`: server timestamp minus machine report timestamp, using WASH's numeric date components.
- `adjusted_remaining_seconds`: `RemainingMin * 60 - report_age_seconds`, including negatives; calculated wherever possible, but meaningful as a countdown principally for running codes.
- `display_minutes_left`: ceiling of adjusted seconds / 60 for a valid running countdown (0 represents the site's "less than 1 minute").
- `website_state`, `interpretation_note`: interpretation and WASH fallback behavior.

No account balances, email addresses, login HTML, passwords, or session cookies
are stored. Source JSON is allowlisted into these fields rather than saved wholesale.

For countdown accuracy, use raw running-to-finished transitions, not an automatic
"Available" fallback as proof of finishing. A transition is only bracketed between
the last running observation and the first finished observation. One-minute polls
give at least that sampling uncertainty, and delays/missing polls widen it. A direct
running-to-available transition may hide a missed finished state. Added dryer time,
pauses, and a new cycle must be treated separately. This dataset measures when WASH
reports completion; validating physical completion requires in-person observations.

The website's JavaScript maps codes beginning with 7 or 8 to Available, 4/1 to a
running countdown (1 includes door open), and 5 to Finished. It can map expired
countdowns and reports older than one hour in several states back to Available.
The raw codes and timestamps are retained so these fallbacks do not erase evidence.

## Local use and tests

```bash
python3 collect.py --prompt
python3 -m unittest -v
```

Local observations go into ignored `data/`. `wash_test.py` remains a display-only
login test. The collector uses only Python's standard library.

## References

- [WASH status renderer](https://www.getwashconnect.com/static/js/update_status.js?v=20190408)
- [GitHub scheduling](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)
- [Actions secrets](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets)
- [Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions)
