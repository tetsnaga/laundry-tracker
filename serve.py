"""Serve recent observations and allow an on-demand WASH status check."""

import argparse
import csv
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import threading
import time
from urllib.parse import parse_qs, urlsplit


MAX_LOOKBACK = timedelta(days=1)
REFRESH_COOLDOWN_SECONDS = 30
ALLOWED_ORIGIN = "https://tets.ai"


def recent_rows(data_dir, after_ms, now=None):
    """Return local rows newer than a published observation timestamp."""
    now = now or datetime.now(timezone.utc)
    requested = datetime.fromtimestamp(after_ms / 1000, timezone.utc)
    if requested > now + timedelta(minutes=5):
        raise ValueError("after timestamp is in the future")
    effective = max(requested, now - MAX_LOOKBACK)
    rows = []
    latest = None
    for path in sorted((Path(data_dir) / "observations").glob("????-??-??.csv")):
        if path.stem < effective.date().isoformat():
            continue
        with path.open(newline="") as handle:
            for row in csv.DictReader(handle):
                try:
                    observed = datetime.fromisoformat(row["observed_at_utc"])
                except (KeyError, ValueError):
                    continue
                if observed.tzinfo is None:
                    continue
                if observed > effective:
                    rows.append(row)
                    if latest is None or observed > latest:
                        latest = observed
    return {"schema_version": 1, "rows": rows,
            "truncated": requested < effective,
            "observed_through_utc": latest.isoformat() if latest else None}


class Handler(BaseHTTPRequestHandler):
    data_dir = Path("/var/lib/laundry-tracker/data")
    collector_command = ("/opt/laundry-tracker/collect-local.sh",)
    refresh_lock = threading.Lock()
    last_refresh_started = 0.0

    def parse_after(self):
        values = parse_qs(urlsplit(self.path).query, strict_parsing=True)
        if set(values) != {"after"} or len(values["after"]) != 1:
            raise ValueError("invalid query")
        after_ms = int(values["after"][0])
        if after_ms < 0:
            raise ValueError("invalid timestamp")
        return after_ms

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path != "/observations":
            self.send_json(404, {"error": "not_found"})
            return
        try:
            result = recent_rows(self.data_dir, self.parse_after())
        except (OverflowError, ValueError):
            self.send_json(400, {"error": "invalid_after"})
            return
        self.send_json(200, result)

    def do_POST(self):
        if urlsplit(self.path).path != "/refresh":
            self.send_json(404, {"error": "not_found"})
            return
        origin = self.headers.get("Origin")
        if origin and origin != ALLOWED_ORIGIN:
            self.send_json(403, {"error": "origin_not_allowed"})
            return
        try:
            after_ms = self.parse_after()
            recent_rows(self.data_dir, after_ms)  # Reject invalid timestamps before polling.
        except (OverflowError, ValueError):
            self.send_json(400, {"error": "invalid_after"})
            return
        if not self.refresh_lock.acquire(blocking=False):
            self.send_json(409, {"error": "refresh_in_progress"})
            return
        try:
            now = time.monotonic()
            fresh_poll = now - type(self).last_refresh_started >= REFRESH_COOLDOWN_SECONDS
            if fresh_poll:
                type(self).last_refresh_started = now
                try:
                    completed = subprocess.run(self.collector_command, capture_output=True,
                                               timeout=58, check=False)
                except (OSError, subprocess.TimeoutExpired):
                    self.send_json(503, {"error": "collection_unavailable"})
                    return
                if completed.returncode:
                    self.send_json(503, {"error": "collection_failed"})
                    return
            result = recent_rows(self.data_dir, after_ms)
            result["fresh_poll"] = fresh_poll
            self.send_json(200, result)
        finally:
            self.refresh_lock.release()

    def do_OPTIONS(self):
        if urlsplit(self.path).path not in ("/observations", "/refresh"):
            self.send_json(404, {"error": "not_found"})
            return
        if self.headers.get("Origin") != ALLOWED_ORIGIN:
            self.send_json(403, {"error": "origin_not_allowed"})
            return
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", ALLOWED_ORIGIN)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "3600")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def send_json(self, code, value):
        body = json.dumps(value, separators=(",", ":")).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", ALLOWED_ORIGIN)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="/var/lib/laundry-tracker/data")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    Handler.data_dir = Path(args.data_dir)
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
