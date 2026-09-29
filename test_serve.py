import csv
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import tempfile
from threading import Thread
import time
import unittest
from unittest import mock
import urllib.error
import urllib.request

from serve import Handler, recent_rows


class RecentRowsTests(unittest.TestCase):
    def test_only_newer_rows_across_utc_midnight(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "observations"
            root.mkdir()
            times = ["2026-09-21T23:59:00+00:00", "2026-09-22T00:00:00+00:00",
                     "2026-09-22T00:01:00+00:00"]
            for day, entries in (("2026-09-21", times[:1]), ("2026-09-22", times[1:])):
                with (root / f"{day}.csv").open("w", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=["observed_at_utc", "machine_id"])
                    writer.writeheader()
                    writer.writerows({"observed_at_utc": stamp, "machine_id": "123"} for stamp in entries)
            now = datetime(2026, 9, 22, 0, 2, tzinfo=timezone.utc)
            after = int(datetime.fromisoformat(times[0]).timestamp() * 1000)
            result = recent_rows(directory, after, now)
            self.assertEqual([row["observed_at_utc"] for row in result["rows"]], times[1:])
            self.assertFalse(result["truncated"])
            self.assertEqual(result["observed_through_utc"], times[-1])

    def test_old_request_is_bounded_and_future_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            now = datetime(2026, 9, 22, tzinfo=timezone.utc)
            old = int((now - timedelta(days=2)).timestamp() * 1000)
            self.assertTrue(recent_rows(directory, old, now)["truncated"])
            future = int((now + timedelta(minutes=6)).timestamp() * 1000)
            with self.assertRaises(ValueError):
                recent_rows(directory, future, now)

    def test_http_endpoint_exposes_only_observations_with_cors(self):
        with tempfile.TemporaryDirectory() as directory:
            Handler.data_dir = Path(directory)
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            worker = Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                with urllib.request.urlopen(f"{base}/observations?after=0") as response:
                    self.assertEqual(response.headers["Access-Control-Allow-Origin"], "https://tets.ai")
                    self.assertEqual(response.headers["Cache-Control"], "no-store")
                    self.assertEqual(response.status, 200)
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(f"{base}/inventory.json")
                self.assertEqual(error.exception.code, 404)
            finally:
                server.shutdown()
                worker.join()
                server.server_close()

    def test_refresh_polls_once_then_reuses_recent_check(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "observations"
            root.mkdir()
            Handler.data_dir = Path(directory)
            Handler.last_refresh_started = time.monotonic() - 60
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            worker = Thread(target=server.serve_forever, daemon=True)
            worker.start()
            calls = []

            def collect(*args, **kwargs):
                calls.append(1)
                with (root / f"{datetime.now(timezone.utc).date()}.csv").open("w", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=["observed_at_utc", "machine_id"])
                    writer.writeheader()
                    writer.writerow({"observed_at_utc": datetime.now(timezone.utc).isoformat(),
                                     "machine_id": "123"})
                return subprocess.CompletedProcess(args[0], 0)

            try:
                url = f"http://127.0.0.1:{server.server_port}/refresh?after=0"
                with mock.patch("serve.subprocess.run", side_effect=collect):
                    for expected in (True, False):
                        request = urllib.request.Request(url, data=b"", method="POST",
                                                         headers={"Origin": "https://tets.ai"})
                        with urllib.request.urlopen(request) as response:
                            body = json.load(response)
                            self.assertEqual(body["fresh_poll"], expected)
                            self.assertEqual(body["rows"][0]["machine_id"], "123")
                    self.assertEqual(len(calls), 1)
                    bad = urllib.request.Request(url, data=b"", method="POST",
                                                 headers={"Origin": "https://other.example"})
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        urllib.request.urlopen(bad)
                    self.assertEqual(error.exception.code, 403)
                    self.assertEqual(len(calls), 1)
                    preflight = urllib.request.Request(url, method="OPTIONS",
                                                       headers={"Origin": "https://tets.ai"})
                    with urllib.request.urlopen(preflight) as response:
                        self.assertEqual(response.status, 204)
                        self.assertIn("POST", response.headers["Access-Control-Allow-Methods"])
            finally:
                server.shutdown()
                worker.join()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
