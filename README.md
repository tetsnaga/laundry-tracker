# Laundry monitoring

One WASH status snapshot every five minutes using GitHub Actions. No browser or
third-party Python packages are required. Credentials are repository Actions
secrets; cookies exist only in memory during each run.

## Storage

The repository's `main` branch contains code. Its separate `data` branch
contains `data/observations/YYYY-MM-DD.csv`, `data/polls/YYYY-MM-DD.jsonl`, and
`data/inventory.json`. Observations are appended and committed after every poll.
These are ordinary Git files, not temporary runner files, expiring artifacts, or
caches. Clone/download the `data` branch to analyze or back up the history.
Git history grows over time; this is a simple starting point, not an unlimited
database. Never force-push/reset the data branch while collecting.

Each attempted poll has a success/failure log. A skipped/dropped GitHub run has
no poll log and must be detected as a gap between timestamps. Failed fetches do
not create fake machine observations or carry forward previous availability.
The first successful poll pins the room and machine roster in `inventory.json`;
subsequent changes fail visibly for review rather than silently altering the cohort.

## Start on GitHub

The workflow and an initialized `data` branch must be pushed to the repo.
Scheduling is disabled until repository variable `COLLECTION_ENABLED` is `true`.
Manual runs work while scheduling is disabled.

Run these commands in your own terminal, replacing OWNER/REPO. Each secret command
prompts for its value; do not put a password directly in the command line.

```bash
gh secret set WASH_EMAIL --repo OWNER/REPO
gh secret set WASH_PASSWORD --repo OWNER/REPO
gh workflow run collect.yml --repo OWNER/REPO
gh run list --repo OWNER/REPO --workflow collect.yml --limit 3
```

Check that the manual run succeeds AND that the data branch contains the new CSV
and poll log. Then enable the schedule:

```bash
gh variable set COLLECTION_ENABLED --repo OWNER/REPO --body true
```

Pause with the same command and `--body false`. Rotating a password only requires
re-running `gh secret set WASH_PASSWORD`. Optionally set `WASH_ROOM_ID` as a repo
variable to select a specific room already available in the account.

GitHub schedules can be late or dropped. This schedule uses minutes 2, 7, 12, ...,
57 to avoid the top of the hour. Five minutes is the minimum scheduled interval.
At that interval there are 8,640 scheduled runs per 30 days, before missed runs.
Standard GitHub-hosted runners are free in public repositories. Public repositories
also make the collected machine IDs and usage history public; credentials remain
separate Actions secrets. Private-repository runner use draws on the account's
included minutes; check your Actions budget before enabling. This setup does not
change billing or spending limits. If a quota/budget stops execution, no observations
can be collected. Public-repository schedules can be disabled after 60 days without
repository activity. Watch for missing observations even when there is no failed run.

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
the last running observation and the first finished observation. Five-minute polls
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
