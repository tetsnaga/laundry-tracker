import contextlib
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest

from collect import CollectionError, collect, normalize

STAMP = "2026-09-21 21:49:47"
OBSERVED = "2026-09-21T21:49:48.000+00:00"


def payload(code="42000000", minutes="10", report="2026-09-21 21:44:47"):
    return {"timestamp": STAMP, "washers": [
        {"SerialNumber": "123", "LMCStatus": code, "RemainingMin": minutes,
         "SeverTime": report, "private_extra": "do not persist"}], "dryers": []}


class FakeClient:
    def __init__(self, result=None, failure=None):
        self.result = result if result is not None else payload()
        self.failure = failure

    def login(self, email, password, room_override):
        if self.failure:
            raise self.failure
        return "test-room"

    def snapshot(self, room):
        return self.result, OBSERVED, OBSERVED


class CollectorTests(unittest.TestCase):
    def row(self, **kwargs):
        return normalize(payload(**kwargs), "room", "poll", OBSERVED, OBSERVED)[0]

    def test_preserves_raw_and_adjusted_countdowns(self):
        row = self.row()
        self.assertEqual(row["remaining_min_raw"], "10")
        self.assertEqual(row["report_age_seconds"], 300)
        self.assertEqual(row["adjusted_remaining_seconds"], 300)
        self.assertEqual(row["display_minutes_left"], "5")
        self.assertEqual(row["website_state"], "Running")

    def test_expired_timer_is_not_mislabeled_as_observed_finish(self):
        row = self.row(minutes="1")
        self.assertEqual(row["adjusted_remaining_seconds"], -240)
        self.assertEqual(row["raw_lmc_status"], "42000000")
        self.assertEqual(row["website_state"], "Available")
        self.assertIn("countdown expired", row["interpretation_note"])

    def test_stale_finished_keeps_raw_evidence(self):
        row = self.row(code="52000000", report="2026-09-21 20:00:00")
        self.assertEqual(row["raw_lmc_status"], "52000000")
        self.assertEqual(row["website_state"], "Available")
        self.assertIn("1 hour", row["interpretation_note"])

    def test_invalid_report_time_is_unknown_not_available(self):
        row = self.row(report="bad date")
        self.assertEqual(row["website_state"], "Unknown")
        self.assertEqual(row["machine_report_time_raw"], "bad date")

    def test_object_groups_and_unknown_codes(self):
        source = payload(code="Z0000000")
        source["washers"] = {"123": source["washers"][0]}
        row = normalize(source, "room", "poll", OBSERVED, OBSERVED)[0]
        self.assertEqual(row["raw_lmc_status"], "Z0000000")

    def test_malformed_snapshot_is_rejected(self):
        for source in [{}, {"washers": [], "dryers": []},
                       {"washers": [{"SerialNumber": "123"}], "dryers": []}]:
            with self.assertRaises(CollectionError):
                normalize(source, "room", "poll", OBSERVED, OBSERVED)

    def test_persistence_idempotence_and_allowlist(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            for _ in range(2):
                self.assertEqual(collect(tmp, FakeClient(), "secret-email", "secret-password", poll_id="same"), 0)
            files = list(Path(tmp).rglob("*.csv"))
            with files[0].open() as handle:
                self.assertEqual(len(list(csv.DictReader(handle))), 1)
            saved = "".join(p.read_text() for p in Path(tmp).rglob("*") if p.is_file())
            for excluded in ["secret-email", "secret-password", "private_extra", "do not persist"]:
                self.assertNotIn(excluded, saved)

    def test_failure_logs_gap_without_observations_or_exception_details(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stderr(io.StringIO()) as logs:
            code = collect(tmp, FakeClient(failure=RuntimeError("secret-password")), "email", "pw")
            self.assertEqual(code, 1)
            self.assertEqual(list(Path(tmp).rglob("*.csv")), [])
            poll = json.loads(next(Path(tmp).rglob("*.jsonl")).read_text())
            self.assertEqual(poll["error_code"], "unexpected_error")
            self.assertNotIn("secret-password", logs.getvalue())

    def test_inventory_change_fails_without_appending_partial_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(collect(tmp, FakeClient(), "e", "p"), 0)
            changed = payload()
            changed["washers"][0]["SerialNumber"] = "456"
            self.assertEqual(collect(tmp, FakeClient(changed), "e", "p"), 1)
            with next(Path(tmp).rglob("*.csv")).open() as handle:
                self.assertEqual(len(list(csv.DictReader(handle))), 1)


if __name__ == "__main__":
    unittest.main()
