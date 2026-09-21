"""Collect one WASH snapshot into daily CSV files; Python standard library only."""
import argparse
import csv
import http.cookiejar
import json
import math
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone

from wash_test import BASE, RoomParser, display_status, parse_time

FIELDS = [
    "schema_version", "poll_id", "request_started_at_utc", "observed_at_utc",
    "room_id", "machine_type", "machine_id", "raw_lmc_status",
    "server_timestamp_raw", "machine_report_time_raw", "remaining_min_raw",
    "report_age_seconds", "adjusted_remaining_seconds", "display_minutes_left",
    "website_state", "interpretation_note",
]


class CollectionError(Exception):
    """Safe error code, never a response body or credentials."""


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class SameOriginRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl)[:2] != urllib.parse.urlsplit(BASE)[:2]:
            raise CollectionError("unexpected_redirect_origin")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class WashClient:
    def __init__(self):
        self.opener = urllib.request.build_opener(
            SameOriginRedirect(),
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
        )

    def request(self, path, fields=None, ajax=False):
        headers = {"User-Agent": "WASH-personal-collector/1.0"}
        if ajax:
            headers.update({"X-Requested-With": "XMLHttpRequest",
                            "Referer": BASE + "/tenant/laundry"})
        body = None if fields is None else urllib.parse.urlencode(fields).encode()
        req = urllib.request.Request(BASE + path, data=body, headers=headers)
        try:
            with self.opener.open(req, timeout=30) as response:
                return response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as error:
            raise CollectionError(f"http_{error.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise CollectionError("network_error") from None

    def login(self, email, password, room_override=""):
        if not email or not password:
            raise CollectionError("missing_credentials")
        self.request("/")
        self.request("/", {"login": email, "password": password, "next": ""})
        parser = RoomParser()
        parser.feed(self.request("/tenant/laundry"))
        if room_override:
            if room_override not in {value for value, _ in parser.options}:
                raise CollectionError("configured_room_not_in_account")
            return room_override
        try:
            return parser.selected_room()
        except ValueError:
            raise CollectionError("login_or_room_selection_failed") from None

    def snapshot(self, room):
        # This POST is WASH's own read-only status refresh request.
        started = utcnow()
        raw = self.request("/tenant/update_machine_status", {"room": room}, ajax=True)
        observed = utcnow()
        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            raise CollectionError("status_not_json") from None
        return result, started, observed


def scalar(value):
    if value is None:
        return ""
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        return str(value)
    raise CollectionError("unexpected_field_type")


def normalize(result, room, poll_id, started, observed):
    if not isinstance(result, dict):
        raise CollectionError("invalid_status_schema")
    timestamp = scalar(result.get("timestamp"))
    rows, seen = [], set()
    for kind in ("washers", "dryers"):
        group = result.get(kind)
        if isinstance(group, dict):
            group = list(group.values())
        if not isinstance(group, list):
            raise CollectionError("missing_machine_group")
        for machine in group:
            if not isinstance(machine, dict):
                raise CollectionError("invalid_machine_record")
            serial = scalar(machine.get("SerialNumber"))
            code = machine.get("LMCStatus")
            if not serial or not isinstance(code, str) or not code:
                raise CollectionError("missing_machine_id_or_status")
            key = (kind, serial)
            if key in seen:
                raise CollectionError("duplicate_machine")
            seen.add(key)
            remaining = scalar(machine.get("RemainingMin"))
            reported = scalar(machine.get("SeverTime"))  # WASH's spelling
            age, adjusted = "", ""
            try:
                age = (parse_time(timestamp) - parse_time(reported)).total_seconds()
                number = float(remaining)
                if math.isfinite(number):
                    adjusted = number * 60 - age
            except (ValueError, OverflowError):
                pass
            state, minutes, note = display_status(machine, timestamp)
            rows.append(dict(zip(FIELDS, [
                1, poll_id, started, observed, room, kind, serial, code,
                timestamp, reported, remaining, age, adjusted, minutes, state, note,
            ])))
    if not rows:
        raise CollectionError("empty_snapshot")
    return rows


def validate_inventory(rows, root, room):
    """Pin the room and inventory; fail visibly instead of silently losing machines."""
    inventory = {"room_id": room, "machines": sorted(
        [[r["machine_type"], r["machine_id"]] for r in rows])}
    path = root / "inventory.json"
    if path.exists() and json.loads(path.read_text()) != inventory:
        raise CollectionError("room_or_machine_inventory_changed")
    return path, inventory


def append_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        with path.open(newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != FIELDS:
                raise CollectionError("csv_schema_mismatch")
            if any(row["poll_id"] == rows[0]["poll_id"] for row in reader):
                return  # Idempotent when the same poll is persisted twice.
    with path.open("a", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        if handle.tell() == 0:
            writer.writeheader()
        writer.writerows(rows)


def collect(root, client, email, password, room_override="", poll_id=None):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    poll_id = poll_id or str(uuid.uuid4())
    record = {"schema_version": 1, "poll_id": poll_id, "started_at_utc": utcnow()}
    start = time.monotonic()
    exit_code = 1
    try:
        room = client.login(email, password, room_override)
        result, requested, observed = client.snapshot(room)
        rows = normalize(result, room, poll_id, requested, observed)
        inventory_path, inventory = validate_inventory(rows, root, room)
        append_csv(root / "observations" / f"{observed[:10]}.csv", rows)
        if not inventory_path.exists():
            inventory_path.write_text(json.dumps(inventory, indent=2) + "\n")
        record.update(status="ok", machine_count=len(rows), observed_at_utc=observed)
        exit_code = 0
        print(f"Saved {len(rows)} machine observations at {observed}.")
    except CollectionError as error:
        record.update(status="error", error_code=str(error))
        print(f"Collection failed: {error}", file=sys.stderr)
    except Exception:
        # Never dump HTML, HTTP bodies, account information, or credentials to logs.
        record.update(status="error", error_code="unexpected_error")
        print("Collection failed: unexpected_error", file=sys.stderr)
    record.update(finished_at_utc=utcnow(), duration_seconds=round(time.monotonic() - start, 3))
    poll_path = root / "polls" / f"{record['started_at_utc'][:10]}.jsonl"
    poll_path.parent.mkdir(parents=True, exist_ok=True)
    with poll_path.open("a") as handle:
        handle.write(json.dumps(record, separators=(",", ":")) + "\n")
    return exit_code


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--prompt", action="store_true", help="Prompt locally instead of using environment secrets")
    args = parser.parse_args()
    email, password = os.environ.get("WASH_EMAIL", ""), os.environ.get("WASH_PASSWORD", "")
    if args.prompt:
        import getpass
        email = input("WASH email: ").strip()
        password = getpass.getpass("WASH password (hidden): ")
    run = os.environ.get("GITHUB_RUN_ID")
    poll_id = f"github-{run}-{os.environ.get('GITHUB_RUN_ATTEMPT', '1')}" if run else None
    sys.exit(collect(args.data_dir, WashClient(), email, password,
                     os.environ.get("WASH_ROOM_ID", ""), poll_id))
