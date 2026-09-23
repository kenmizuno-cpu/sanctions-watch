from __future__ import annotations

import copy
import csv
import tempfile
import unittest
from pathlib import Path

from src import ofac_removal_queue as ORQ


class OfacRemovalQueueTest(unittest.TestCase):
    hash_a = "a" * 64
    hash_b = "b" * 64

    @staticmethod
    def _history() -> dict:
        def row(
            party_id: str,
            name: str,
            *,
            primary: str,
            low_quality: str = "0",
        ) -> dict:
            return {
                "list": "SDN",
                "party_id": party_id,
                "name": name,
                "match_key": name.lower().replace(" ", ""),
                "first_seen_ms": "1",
                "last_changed_ms": "1",
                "alias_current": "0",
                "party_current": "0",
                "formats": "advanced_xml_v3",
                "primary": primary,
                "low_quality": low_quality,
            }

        return {
            ("SDN", "100", "ALPHA ALT"): row(
                "100",
                "ALPHA ALT",
                primary="0",
            ),
            ("SDN", "100", "Alpha Primary"): row(
                "100",
                "Alpha Primary",
                primary="1",
            ),
            ("SDN", "200", "Beta Weak"): row(
                "200",
                "Beta Weak",
                primary="0",
                low_quality="1",
            ),
            ("SDN", "200", "Beta Strong"): row(
                "200",
                "Beta Strong",
                primary="0",
            ),
            ("SDN", "300", "Gamma"): row(
                "300",
                "Gamma",
                primary="1",
            ),
        }

    def _create_event(self) -> list[dict]:
        rows: list[dict] = []
        diff = ORQ.reconcile(
            rows,
            removed_parties={("SDN", "100"), ("SDN", "200")},
            current_parties=set(),
            snapshot_hashes={"SDN": self.hash_a},
            history=self._history(),
            ts=1000,
        )
        self.assertEqual(len(diff.created_events), 1)
        return rows

    def test_missing_queue_is_empty_and_round_trip_is_stable(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "queue.csv"
            self.assertEqual(ORQ.load(path), [])

            rows = self._create_event()
            ORQ.save(rows, path)
            loaded = ORQ.load(path)

        self.assertEqual(loaded, rows)
        self.assertEqual(
            list(loaded[0]),
            ORQ.FIELDS,
        )

    def test_reconcile_creates_deterministic_multi_party_event(self):
        first = self._create_event()
        second = self._create_event()

        self.assertEqual(first, second)
        self.assertEqual(len(first), 2)
        self.assertEqual(
            {row["event_id"] for row in first},
            {first[0]["event_id"]},
        )
        self.assertEqual(len(first[0]["event_id"]), 64)
        self.assertEqual(
            {row["party_name"] for row in first},
            {"Alpha Primary", "Beta Strong"},
        )
        self.assertTrue(
            all(row["status"] == ORQ.PENDING_REVIEW for row in first)
        )
        self.assertTrue(
            all(row["detected_at_ms"] == "1000" for row in first)
        )
        self.assertEqual(
            ORQ.pending_groups(first),
            {
                (
                    first[0]["event_id"],
                    "SDN",
                    self.hash_a,
                ): {"100", "200"},
            },
        )

    def test_reconcile_deduplicates_and_tracks_later_event(self):
        rows = self._create_event()

        same = ORQ.reconcile(
            rows,
            removed_parties=set(),
            current_parties=set(),
            snapshot_hashes={"SDN": self.hash_a},
            history=self._history(),
            ts=2000,
        )
        self.assertEqual(same.created_events, set())
        self.assertEqual(len(rows), 2)
        self.assertTrue(
            all(row["last_seen_at_ms"] == "2000" for row in rows)
        )

        later = ORQ.reconcile(
            rows,
            removed_parties={("SDN", "300")},
            current_parties=set(),
            snapshot_hashes={"SDN": self.hash_b},
            history=self._history(),
            ts=3000,
        )

        self.assertEqual(len(later.created_events), 1)
        self.assertEqual(len(rows), 3)
        self.assertEqual(len(ORQ.pending_groups(rows)), 2)

    def test_reappearance_cancels_before_authorization(self):
        rows = self._create_event()
        original_event = rows[0]["event_id"]

        diff = ORQ.reconcile(
            rows,
            removed_parties=set(),
            current_parties={("SDN", "100")},
            snapshot_hashes={"SDN": self.hash_b},
            history=self._history(),
            ts=2000,
        )

        old_rows = [
            row for row in rows if row["event_id"] == original_event
        ]
        self.assertEqual(len(old_rows), 2)
        self.assertTrue(
            all(
                row["status"] == ORQ.CANCELLED_REAPPEARED
                for row in old_rows
            )
        )
        self.assertTrue(
            all(row["resolved_at_ms"] == "2000" for row in old_rows)
        )
        self.assertTrue(
            all(row["resolution"] == "REAPPEARED" for row in old_rows)
        )

        replacement = [
            row
            for row in rows
            if row["status"] == ORQ.PENDING_REVIEW
        ]
        self.assertEqual(len(replacement), 1)
        self.assertEqual(replacement[0]["party_id"], "200")
        self.assertEqual(replacement[0]["snapshot_sha256"], self.hash_b)
        self.assertEqual(diff.cancelled_parties, {("SDN", "100")})
        self.assertEqual(len(diff.created_events), 1)
        self.assertEqual(
            list(ORQ.pending_groups(rows).values()),
            [{"200"}],
        )

    def test_mark_applied_requires_exact_pending_keys(self):
        rows = self._create_event()
        event_id = rows[0]["event_id"]
        approved = {
            ("SDN", self.hash_a, "100"),
            ("SDN", self.hash_a, "200"),
        }

        ORQ.mark_applied(rows, approved, ts=2000)

        self.assertEqual(ORQ.pending_groups(rows), {})
        self.assertTrue(
            all(row["status"] == ORQ.APPLIED for row in rows)
        )
        self.assertTrue(
            all(row["resolved_at_ms"] == "2000" for row in rows)
        )
        self.assertTrue(
            all(row["resolution"] == "HUMAN_APPROVED" for row in rows)
        )
        self.assertEqual({row["event_id"] for row in rows}, {event_id})

        before = copy.deepcopy(rows)
        with self.assertRaisesRegex(ORQ.QueueError, "pending queue row"):
            ORQ.mark_applied(
                rows,
                {("SDN", self.hash_a, "999")},
                ts=3000,
            )
        self.assertEqual(rows, before)

    def test_reconcile_failure_does_not_partially_mutate_rows(self):
        rows = self._create_event()
        before = copy.deepcopy(rows)

        with self.assertRaisesRegex(ORQ.QueueError, "snapshot SHA256"):
            ORQ.reconcile(
                rows,
                removed_parties={("SDN", "300")},
                current_parties=set(),
                snapshot_hashes={"SDN": "wrong"},
                history=self._history(),
                ts=3000,
            )

        self.assertEqual(rows, before)

    def test_noop_reconcile_ignores_unused_snapshot_hash(self):
        rows: list[dict] = []

        diff = ORQ.reconcile(
            rows,
            removed_parties=set(),
            current_parties={("SDN", "1")},
            snapshot_hashes={"SDN": "test-fixture-sha"},
            history={},
            ts=1000,
        )

        self.assertEqual(rows, [])
        self.assertEqual(diff.created_events, set())
        self.assertEqual(diff.cancelled_parties, set())

    def test_load_rejects_invalid_schema_duplicate_and_open_party(self):
        valid = self._create_event()
        cases = []

        wrong_header = [field for field in ORQ.FIELDS if field != "resolution"]
        cases.append(("schema", wrong_header, valid))

        duplicate = valid + [dict(valid[0])]
        cases.append(("duplicate", ORQ.FIELDS, duplicate))

        second_open = dict(valid[0])
        second_open["event_id"] = "f" * 64
        second_open["snapshot_sha256"] = self.hash_b
        cases.append(("multiple open", ORQ.FIELDS, valid + [second_open]))

        bad_status = [dict(valid[0])]
        bad_status[0]["status"] = "UNKNOWN"
        cases.append(("status", ORQ.FIELDS, bad_status))

        mixed_event = copy.deepcopy(valid)
        mixed_event[0]["status"] = ORQ.APPLIED
        mixed_event[0]["resolved_at_ms"] = "2000"
        mixed_event[0]["resolution"] = "HUMAN_APPROVED"
        cases.append(("mixed event state", ORQ.FIELDS, mixed_event))

        for label, fields, rows in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as td:
                path = Path(td) / "queue.csv"
                with path.open("w", encoding="utf-8", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=fields)
                    writer.writeheader()
                    writer.writerows(
                        {
                            field: row.get(field, "")
                            for field in fields
                        }
                        for row in rows
                    )

                with self.assertRaises(ORQ.QueueError):
                    ORQ.load(path)


if __name__ == "__main__":
    unittest.main()
