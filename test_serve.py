import csv
from datetime import datetime, timedelta, timezone
from http.server import HTTPServer
from pathlib import Path
import tempfile
from threading import Thread
import unittest
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
            server = HTTPServer(("127.0.0.1", 0), Handler)
            worker = Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                with urllib.request.urlopen(f"{base}/observations?after=0") as response:
                    self.assertEqual(response.headers["Access-Control-Allow-Origin"], "*")
                    self.assertEqual(response.headers["Cache-Control"], "no-store")
                    self.assertEqual(response.status, 200)
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(f"{base}/inventory.json")
                self.assertEqual(error.exception.code, 404)
            finally:
                server.shutdown()
                worker.join()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
