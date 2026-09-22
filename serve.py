"""Read-only HTTP access to recent observations saved on the Oracle VM."""

import argparse
import csv
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


MAX_LOOKBACK = timedelta(days=1)


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

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path != "/observations":
            self.send_json(404, {"error": "not_found"})
            return
        try:
            values = parse_qs(url.query, strict_parsing=True)
            if set(values) != {"after"} or len(values["after"]) != 1:
                raise ValueError("invalid query")
            after_ms = int(values["after"][0])
            if after_ms < 0:
                raise ValueError("invalid timestamp")
            result = recent_rows(self.data_dir, after_ms)
        except (OverflowError, ValueError):
            self.send_json(400, {"error": "invalid_after"})
            return
        self.send_json(200, result)

    def send_json(self, code, value):
        body = json.dumps(value, separators=(",", ":")).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="/var/lib/laundry-tracker/data")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    Handler.data_dir = Path(args.data_dir)
    HTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
