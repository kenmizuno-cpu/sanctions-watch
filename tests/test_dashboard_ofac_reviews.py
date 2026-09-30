from __future__ import annotations

import copy
import csv
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from src import dashboard as D
from src import master as M
from src import mof_record_diff as R
from src import ofac_removal_queue as ORQ
from src import persistence as P
from src import watch


DETECTED_MS = 1790173941528
NOW = datetime(2026, 9, 30, 0, 32, 34, tzinfo=timezone.utc)


def removal_queue(*, list_name="SDN", names=None):
    names = names or {"21263": "Olenga Francois", "21264": "Safari Club"}
    history = {
        (list_name, party_id, name): {
            "list": list_name,
            "party_id": party_id,
            "name": name,
            "primary": "1",
            "low_quality": "0",
        }
        for party_id, name in names.items()
    }
    queue = []
    ORQ.reconcile(
        queue,
        removed_parties={(list_name, party_id) for party_id in names},
        current_parties=set(),
        snapshot_hashes={list_name: "a" * 64},
        history=history,
        ts=DETECTED_MS,
    )
    return queue


class DashboardOfacRemovalReviewTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.master = {
            "safari club": {
                "match_key": "safari club",
                "display_name": "Safari Club",
                "status": "有効",
                "risk_type": "制裁リスト",
                "risk_level": "高",
                "sources": "OFAC",
                "categories": "OFAC:SDN",
            }
        }
        M.save(self.master, self.root / watch.MASTER_REL)

    def persist(self, *, queue=None, now=NOW, diffs=None, source="ofac_sdn"):
        watch._persist_outputs_atomically(
            root=self.root,
            rows=self.master,
            state={},
            heartbeat=[{"source": source, "status": "review_required"}],
            diffs=diffs or [],
            ofac_removal_queue_rows=queue,
            now=now,
        )

    def read_changes(self):
        with (self.root / D.DASH / "changes.csv").open(
            encoding="utf-8", newline=""
        ) as handle:
            reader = csv.DictReader(handle)
            self.assertEqual(reader.fieldnames, D.CHANGE_COLS)
            return list(reader)

    def test_all_304_exposes_pending_parties_without_deactivating_master(self):
        # A missing queue-to-dashboard projection must fail this test.
        before = (self.root / watch.MASTER_REL).read_bytes()
        before_rows = copy.deepcopy(self.master)
        queue = removal_queue()

        self.persist(queue=queue)
        changes = self.read_changes()

        self.assertEqual(len(changes), 2)
        self.assertEqual(
            {row["受取人名"] for row in changes},
            {"Olenga Francois", "Safari Club"},
        )
        for row in changes:
            self.assertEqual(row["検知日時"], "2026-09-23 23:32:21")
            self.assertEqual(row["出所"], "OFAC")
            self.assertEqual(row["種別"], "掲載終了候補（要確認）")
            self.assertIn("SDN", row["変更前"])
            self.assertIn("a" * 64, row["変更前"])
            self.assertIn(queue[0]["event_id"], row["変更前"])
            self.assertIn("削除承認待ち", row["変更後"])
        self.assertEqual((self.root / watch.MASTER_REL).read_bytes(), before)
        self.assertEqual(self.master, before_rows)
        self.assertFalse((self.root / watch.DIFF_CSV_REL).exists())
        self.assertEqual(ORQ.load(self.root / watch.OFAC_REMOVAL_QUEUE_REL), queue)

    def test_repeat_checks_preserve_sheets_event_keys_and_do_not_duplicate(self):
        queue = removal_queue()
        self.persist(queue=queue)
        first = self.read_changes()
        self.assertEqual(len(first), 2)
        for row in queue:
            row["last_seen_at_ms"] = str(DETECTED_MS + 86400000)

        self.persist(queue=queue, now=NOW + timedelta(hours=1))

        # Sheets keys use all six values; a changed check time reopens a review.
        self.assertEqual(self.read_changes(), first)

    def test_jp_run_projects_saved_backlog_without_a_new_ofac_snapshot(self):
        ORQ.save(removal_queue(), self.root / watch.OFAC_REMOVAL_QUEUE_REL)

        self.persist(source="mof")

        self.assertEqual(len(self.read_changes()), 2)

    def test_applied_and_reappeared_events_are_not_new_review_candidates(self):
        queue = removal_queue()
        ORQ.mark_applied(
            queue,
            {("SDN", "a" * 64, "21263"), ("SDN", "a" * 64, "21264")},
            ts=DETECTED_MS + 1000,
        )
        cancelled = removal_queue(list_name="Consolidated")
        ORQ.reconcile(
            cancelled,
            removed_parties=set(),
            current_parties={
                ("Consolidated", "21263"), ("Consolidated", "21264")
            },
            snapshot_hashes={"Consolidated": "b" * 64},
            history={},
            ts=DETECTED_MS + 1000,
        )

        self.persist(queue=queue + cancelled)

        self.assertEqual(self.read_changes(), [])

    def test_same_name_parties_have_distinct_review_keys(self):
        self.persist(queue=removal_queue(
            names={"21263": "SAME", "21264": "SAME"}
        ))

        changes = self.read_changes()

        self.assertEqual(len(changes), 2)
        self.assertEqual({row["受取人名"] for row in changes}, {"SAME"})
        self.assertEqual(len({tuple(row.values()) for row in changes}), 2)
        self.assertTrue(any("FixedRef=21263" in row["変更前"] for row in changes))
        self.assertTrue(any("FixedRef=21264" in row["変更前"] for row in changes))

    def test_candidates_survive_history_limit_and_keep_regular_changes(self):
        queue = removal_queue()
        self.persist(queue=queue)
        additions = [M.Diff(
            source="OFAC", added=[{"name": "NEW", "remark": "OFAC:SDN"}]
        )]

        with patch.object(D, "MAX_CHANGES", 3):
            self.persist(queue=queue, now=NOW + timedelta(hours=1), diffs=additions)

        changes = self.read_changes()
        self.assertEqual(len(changes), 3)
        self.assertEqual(
            {row["受取人名"] for row in changes},
            {"Olenga Francois", "Safari Club", "NEW"},
        )
        self.assertEqual(sum(row["種別"] == "追加" for row in changes), 1)

    def test_mof_post_processing_preserves_pending_reviews_at_history_limit(self):
        queue = removal_queue()
        self.persist(queue=queue)
        first = self.read_changes()
        diff = R.SourceDiff(
            before_path=Path("before.csv.gz"),
            after_path=Path("after.csv.gz"),
            before_count=1,
            after_count=1,
            amended=[R.FieldChange(
                before_id="1", after_id="1", un_reference="QDi.1",
                name="MOF PARTY", field_name=field, before="OLD", after="NEW",
            ) for field in ["国籍（英語）", "旅券番号", "身分証番号"]],
        )
        with patch.object(D, "MAX_CHANGES", 3):
            self.assertEqual(
                R._append_dashboard(self.root, [diff], "2026-09-30 12:00:00"), 3
            )

        changes = self.read_changes()
        self.assertEqual(len(changes), 3)
        self.assertEqual(
            [row for row in changes if row["出所"] == "OFAC"], first
        )
        self.assertEqual(sum(row["出所"] == "財務省" for row in changes), 1)
        self.assertEqual(ORQ.load(self.root / watch.OFAC_REMOVAL_QUEUE_REL), queue)

    def test_meti_staged_writer_keeps_all_pending_even_above_history_limit(self):
        queue = removal_queue(names={str(n): f"PARTY {n}" for n in range(4)})
        with patch.object(D, "MAX_CHANGES", 3):
            self.persist(queue=queue)
            first = self.read_changes()
            P.atomic_replace_many([P.FileWrite(
                target=self.root / D.DASH / "changes.csv",
                writer=lambda path: D.prepend_change_rows(
                    path,
                    [["経済産業省", "要対応", "METI", "", "SLA超過"]],
                    when="2026-09-30 12:00:00",
                ),
                seed_existing=True,
            )])

        self.assertEqual(len(first), 4)
        self.assertEqual(self.read_changes(), first)
        self.assertEqual(ORQ.load(self.root / watch.OFAC_REMOVAL_QUEUE_REL), queue)

    def test_resolved_reviews_are_subject_to_the_normal_history_limit(self):
        queue = removal_queue()
        self.persist(queue=queue)
        ORQ.mark_applied(
            queue,
            {("SDN", "a" * 64, "21263"), ("SDN", "a" * 64, "21264")},
            ts=DETECTED_MS + 1000,
        )
        ORQ.save(queue, self.root / watch.OFAC_REMOVAL_QUEUE_REL)

        with patch.object(D, "MAX_CHANGES", 3):
            D.prepend_change_rows(
                self.root / D.DASH / "changes.csv",
                [["経済産業省", "追加", f"NEW {n}", "", ""] for n in range(3)],
                when="2026-09-30 12:00:00",
            )

        changes = self.read_changes()
        self.assertEqual(len(changes), 3)
        self.assertEqual({row["出所"] for row in changes}, {"経済産業省"})


if __name__ == "__main__":
    unittest.main()
