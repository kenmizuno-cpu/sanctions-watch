from __future__ import annotations

import csv
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from src import dashboard, persistence
from src import meti_manual_event as manual_event
import src.meti_review as mr


NOW = datetime(2026, 9, 17, 6, 0, tzinfo=timezone.utc)
DETECTION_ID = "d" * 64


def write_csv(path: Path, fields: list[str], rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


class TestMetiReview(unittest.TestCase):
    def make_fixture(self, td: str):
        root = Path(td)
        manual = root / "data/manual/meti"
        raw = root / "data/raw/meti_manual/a.pdf"
        records = manual / "records/a.csv"
        diff = manual / "diffs/a.csv"
        report = manual / "reports/a.json"
        state = manual / "state.json"
        evidence = root / "data/evidence/meti_foreign_user_list.csv"

        raw.parent.mkdir(parents=True, exist_ok=True)
        raw.write_bytes(b"%PDF-1.7\n" + b"x" * 20000)
        digest = mr.sha256_file(raw)

        record_rows = [
            {
                "no": "1",
                "country": "A",
                "company": "ALPHA",
                "aliases": "",
                "wmd": "N",
                "conventional_weapons": "",
                "match_key": "alpha",
            },
            {
                "no": "2",
                "country": "B",
                "company": "BETA",
                "aliases": "",
                "wmd": "M",
                "conventional_weapons": "CW",
                "match_key": "beta",
            },
        ]
        write_csv(
            records,
            [
                "no",
                "country",
                "company",
                "aliases",
                "wmd",
                "conventional_weapons",
                "match_key",
            ],
            record_rows,
        )
        write_csv(
            diff,
            [
                "action",
                "match_key",
                "old_no",
                "new_no",
                "old_company",
                "new_company",
                "old_country",
                "new_country",
                "old_aliases",
                "new_aliases",
                "old_wmd",
                "new_wmd",
                "old_conventional_weapons",
                "new_conventional_weapons",
            ],
            [],
        )
        write_csv(
            evidence,
            [
                "canonical_record",
                "match_key",
                "source",
                "source_record_id",
                "source_url",
                "source_document",
                "publication_date",
                "effective_date",
                "first_seen",
                "last_seen",
                "current",
                "source_hash",
                "evidence",
            ],
            [
                {
                    "canonical_record": "ALPHA",
                    "match_key": "alpha",
                    "source": "経済産業省 外国ユーザーリスト",
                    "source_record_id": f"{digest}:1",
                    "source_url": "https://www.meti.go.jp/policy/anpo/x.pdf",
                    "source_document": "a.pdf",
                    "publication_date": "2025-01-01",
                    "effective_date": "2025-01-02",
                    "first_seen": "x",
                    "last_seen": "x",
                    "current": "1",
                    "source_hash": digest,
                    "evidence": "{}",
                },
                {
                    "canonical_record": "BETA",
                    "match_key": "beta",
                    "source": "経済産業省 外国ユーザーリスト",
                    "source_record_id": f"{digest}:2",
                    "source_url": "https://www.meti.go.jp/policy/anpo/x.pdf",
                    "source_document": "a.pdf",
                    "publication_date": "2025-01-01",
                    "effective_date": "2025-01-02",
                    "first_seen": "x",
                    "last_seen": "x",
                    "current": "1",
                    "source_hash": digest,
                    "evidence": "{}",
                },
            ],
        )

        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(
            json.dumps(
                {
                    "status": "REVIEW_REQUIRED",
                    "review_required": True,
                    "auto_import": "BLOCKED",
                    "baseline": True,
                    "source_hash": digest,
                    "source_url": "https://www.meti.go.jp/policy/anpo/x.pdf",
                    "raw_path": "data/raw/meti_manual/a.pdf",
                    "records_path": "data/manual/meti/records/a.csv",
                    "diff_path": "data/manual/meti/diffs/a.csv",
                    "record_count": 2,
                    "expected_count": 2,
                    "effective_date": "2025-01-02",
                    "diff": {"追加": 0, "変更": 0, "削除": 0},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        state.parent.mkdir(parents=True, exist_ok=True)
        manual_state = {
            "version": 1,
            "lifecycle_state": "REVIEW_REQUIRED",
            "detection_id": DETECTION_ID,
            "pending_detection": {
                "detection_id": DETECTION_ID,
                "attempt": 1,
                "notice_url": (
                    "https://www.meti.go.jp/press/2026/example.html"
                ),
                "publication_at": "2026-09-17T05:00:00Z",
            },
            "current_source_hash": digest,
            "current_raw_path": "data/raw/meti_manual/a.pdf",
            "current_records_path": "data/manual/meti/records/a.csv",
            "current_diff_path": "data/manual/meti/diffs/a.csv",
            "current_report_path": "data/manual/meti/reports/a.json",
            "current_record_count": 2,
            "source_url": "https://www.meti.go.jp/policy/anpo/x.pdf",
            "effective_date": "2025-01-02",
            "review_status": "REVIEW_REQUIRED",
            "approved": False,
            "applied": False,
        }
        paths = manual_event.LifecyclePaths.for_root(root, NOW)
        review_required_event = {
            "event_id": "e" * 64,
            "event_at": "2026-09-17T05:30:00Z",
            "state": "REVIEW_REQUIRED",
            "detection_id": DETECTION_ID,
            "source_url": manual_state["source_url"],
            "source_hash": digest,
            "operator": "importer",
            "detail": "{}",
        }
        manual_event.persist_lifecycle(
            state=manual_state,
            events=[review_required_event],
            heartbeat_status="manual_review",
            dashboard_event=None,
            audit_row=None,
            now=NOW,
            paths=paths,
        )

        review_ledger = root / "data/review/meti_foreign_user_list.csv"
        write_csv(review_ledger, mr.REVIEW_COLS, [])

        return {
            "root": root,
            "raw": raw,
            "records": records,
            "diff": diff,
            "report": report,
            "state": state,
            "evidence": evidence,
            "digest": digest,
            "paths": paths,
            "review_ledger": review_ledger,
        }

    def patches(self, fx):
        return (
            patch.object(mr, "ROOT", fx["root"]),
            patch.object(mr, "STATE_PATH", fx["state"]),
            patch.object(mr, "EVIDENCE_PATH", fx["evidence"]),
            patch.object(
                mr,
                "REVIEW_DIR",
                fx["root"] / "data/review",
            ),
            patch.object(
                mr,
                "REVIEW_LEDGER",
                fx["review_ledger"],
            ),
            patch.object(
                mr,
                "REVIEW_ARTIFACT_DIR",
                fx["root"] / "data/manual/meti/reviews",
            ),
            patch.object(mr, "_now", return_value=NOW),
            patch.object(
                mr,
                "append_dashboard_row",
                side_effect=lambda kind, subject, before, after: (
                    dashboard.prepend_change_rows(
                        fx["paths"].changes,
                        [[
                            "経済産業省",
                            kind,
                            subject,
                            before,
                            after,
                        ]],
                        when="2026-09-17 15:00:00",
                    )
                ),
            ),
        )

    def assert_last_lifecycle_event(
        self,
        path,
        expected_state,
        expected_hash,
    ):
        with path.open(encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(rows[-1]["state"], expected_state)
        self.assertEqual(rows[-1]["source_hash"], expected_hash)
        self.assertEqual(rows[-1]["operator"], "reviewer")

    def assert_manual_projection(
        self,
        fx,
        *,
        heartbeat_status,
        dashboard_status,
    ):
        with fx["paths"].heartbeat.open(
            encoding="utf-8",
            newline="",
        ) as f:
            heartbeat_rows = list(csv.DictReader(f))
        self.assertEqual(
            heartbeat_rows[-1]["status"],
            heartbeat_status,
        )

        with fx["paths"].status.open(
            encoding="utf-8",
            newline="",
        ) as f:
            status_rows = list(csv.DictReader(f))
        meti = next(
            row for row in status_rows
            if row["出所"] == "経済産業省"
        )
        self.assertEqual(meti["状態"], dashboard_status)

    @staticmethod
    def snapshot(paths):
        return {
            path: path.read_bytes() if path.exists() else None
            for path in paths
        }

    def test_verify_valid_snapshot(self):
        with tempfile.TemporaryDirectory() as td:
            fx = self.make_fixture(td)
            ps = self.patches(fx)
            for p in ps:
                p.start()
            try:
                v = mr.verify_snapshot(
                    expected_hash=fx["digest"],
                )
                self.assertEqual(v["record_count"], 2)
                self.assertEqual(
                    v["diff_counts"],
                    {"追加": 0, "変更": 0, "削除": 0},
                )
            finally:
                for p in reversed(ps):
                    p.stop()

    def test_hash_mismatch_blocks(self):
        with tempfile.TemporaryDirectory() as td:
            fx = self.make_fixture(td)
            ps = self.patches(fx)
            for p in ps:
                p.start()
            try:
                with self.assertRaises(mr.ReviewError):
                    mr.verify_snapshot(
                        expected_hash="0" * 64,
                    )
            finally:
                for p in reversed(ps):
                    p.stop()

    def test_raw_tamper_blocks(self):
        with tempfile.TemporaryDirectory() as td:
            fx = self.make_fixture(td)
            fx["raw"].write_bytes(b"%PDF-1.7\ntampered")
            ps = self.patches(fx)
            for p in ps:
                p.start()
            try:
                with self.assertRaises(mr.ReviewError):
                    mr.verify_snapshot(
                        expected_hash=fx["digest"],
                    )
            finally:
                for p in reversed(ps):
                    p.stop()

    def test_evidence_missing_blocks(self):
        with tempfile.TemporaryDirectory() as td:
            fx = self.make_fixture(td)

            rows = []
            with fx["evidence"].open(
                encoding="utf-8",
                newline="",
            ) as f:
                rows = list(csv.DictReader(f))

            fields = list(rows[0].keys())
            write_csv(
                fx["evidence"],
                fields,
                rows[:1],
            )

            ps = self.patches(fx)
            for p in ps:
                p.start()
            try:
                with self.assertRaises(mr.ReviewError):
                    mr.verify_snapshot(
                        expected_hash=fx["digest"],
                    )
            finally:
                for p in reversed(ps):
                    p.stop()

    def test_baseline_diff_nonzero_blocks(self):
        with tempfile.TemporaryDirectory() as td:
            fx = self.make_fixture(td)

            with fx["report"].open(encoding="utf-8") as f:
                report = json.load(f)
            report["diff"] = {
                "追加": 1,
                "変更": 0,
                "削除": 0,
            }
            fx["report"].write_text(
                json.dumps(
                    report,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            fields = [
                "action",
                "match_key",
                "old_no",
                "new_no",
                "old_company",
                "new_company",
                "old_country",
                "new_country",
                "old_aliases",
                "new_aliases",
                "old_wmd",
                "new_wmd",
                "old_conventional_weapons",
                "new_conventional_weapons",
            ]
            write_csv(
                fx["diff"],
                fields,
                [{
                    "action": "追加",
                    "match_key": "x",
                }],
            )

            ps = self.patches(fx)
            for p in ps:
                p.start()
            try:
                with self.assertRaises(mr.ReviewError):
                    mr.verify_snapshot(
                        expected_hash=fx["digest"],
                    )
            finally:
                for p in reversed(ps):
                    p.stop()

    def test_approve_requires_reviewer(self):
        with self.assertRaises(mr.ReviewError):
            mr.decide(
                expected_hash="0" * 64,
                decision=mr.DECISION_APPROVED,
                reviewer="",
                note="",
            )

    def test_reject_requires_note(self):
        with self.assertRaises(mr.ReviewError):
            mr.decide(
                expected_hash="0" * 64,
                decision=mr.DECISION_REJECTED,
                reviewer="reviewer",
                note="",
            )

    def test_approve_records_lifecycle_and_projection(self):
        with tempfile.TemporaryDirectory() as td:
            fx = self.make_fixture(td)
            ps = self.patches(fx)
            for p in ps:
                p.start()
            try:
                result = mr.decide(
                    expected_hash=fx["digest"],
                    decision=mr.DECISION_APPROVED,
                    reviewer="reviewer",
                    note="verified",
                )
                self.assertFalse(result["idempotent"])
                self.assert_last_lifecycle_event(
                    fx["paths"].events,
                    "APPROVED",
                    fx["digest"],
                )
                self.assert_manual_projection(
                    fx,
                    heartbeat_status="manual_approved",
                    dashboard_status="要確認：反映待ち",
                )
            finally:
                for p in reversed(ps):
                    p.stop()

    def test_reject_records_lifecycle_and_projection(self):
        with tempfile.TemporaryDirectory() as td:
            fx = self.make_fixture(td)
            ps = self.patches(fx)
            for p in ps:
                p.start()
            try:
                result = mr.decide(
                    expected_hash=fx["digest"],
                    decision=mr.DECISION_REJECTED,
                    reviewer="reviewer",
                    note="source mismatch",
                )
                self.assertFalse(result["idempotent"])
                self.assert_last_lifecycle_event(
                    fx["paths"].events,
                    "REJECTED",
                    fx["digest"],
                )
                self.assert_manual_projection(
                    fx,
                    heartbeat_status="manual_rejected",
                    dashboard_status="要確認：取込却下",
                )
            finally:
                for p in reversed(ps):
                    p.stop()

    def test_review_transaction_failure_restores_every_file(self):
        with tempfile.TemporaryDirectory() as td:
            fx = self.make_fixture(td)
            artifact = (
                fx["root"]
                / "data/manual/meti/reviews"
                / (
                    "20260917T060000Z__"
                    f"{fx['digest'][:12]}__approved__review.json"
                )
            )
            targets = [
                fx["state"],
                fx["review_ledger"],
                artifact,
                fx["paths"].events,
                fx["paths"].heartbeat,
                fx["paths"].status,
                fx["paths"].audit,
                fx["paths"].changes,
            ]
            before = self.snapshot(targets)
            real_replace = persistence.os.replace
            calls = 0

            def fail_second_replace(src, dst):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("injected review commit failure")
                return real_replace(src, dst)

            ps = self.patches(fx)
            for p in ps:
                p.start()
            try:
                with patch.object(
                    persistence.os,
                    "replace",
                    side_effect=fail_second_replace,
                ):
                    with self.assertRaisesRegex(
                        OSError,
                        "injected review commit failure",
                    ):
                        mr.decide(
                            expected_hash=fx["digest"],
                            decision=mr.DECISION_APPROVED,
                            reviewer="reviewer",
                            note="verified",
                        )
                self.assertEqual(self.snapshot(targets), before)
                self.assertFalse(list(fx["root"].rglob("*.tmp")))
                self.assertFalse(list(fx["root"].rglob("*.bak")))
            finally:
                for p in reversed(ps):
                    p.stop()


if __name__ == "__main__":
    unittest.main()
