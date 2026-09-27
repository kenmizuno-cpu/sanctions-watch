from __future__ import annotations

import csv
import hashlib
import json
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from src import dashboard
from src import meti_manual_event as manual_event
from src import meti_manual_import as manual_import
from src import meti_review
from src import persistence, source_audit


NOW = datetime(2026, 9, 17, 6, 0, tzinfo=timezone.utc)
PDF_URL = "https://www.meti.go.jp/policy/anpo/20260917_1.pdf"
NOTICE_URL = "https://www.meti.go.jp/press/2026/example.html"
PDF_BYTES = b"%PDF-1.7\n" + b"x" * 20000


class TestMetiManualImportLifecycle(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.pdf = self.root / "downloaded.pdf"
        self.pdf.write_bytes(PDF_BYTES)
        self.digest = hashlib.sha256(PDF_BYTES).hexdigest()
        self.paths = manual_event.LifecyclePaths.for_root(self.root, NOW)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)

        replacements = {
            "ROOT": self.root,
            "RAW_DIR": self.root / "data/raw/meti_manual",
            "QUARANTINE_DIR": self.root / "data/quarantine/meti_manual",
            "REPORT_DIR": self.root / "data/manual/meti/reports",
            "TEXT_DIR": self.root / "data/manual/meti/text",
            "RECORD_DIR": self.root / "data/manual/meti/records",
            "DIFF_DIR": self.root / "data/manual/meti/diffs",
            "STATE_PATH": self.paths.state,
            "EVIDENCE_PATH": (
                self.root / "data/evidence/meti_foreign_user_list.csv"
            ),
            "CHANGES_PATH": self.paths.changes,
        }
        for name, value in replacements.items():
            self.stack.enter_context(
                patch.object(manual_import, name, value)
            )
        self.stack.enter_context(
            patch.object(manual_import, "_now", return_value=NOW)
        )

    def records(self):
        return [
            manual_import.Record(
                number,
                "Country",
                "ORG %03d" % number,
                (),
                "N",
                "",
            )
            for number in range(1, 101)
        ]

    def seed_open_detection(self, *, applied_hash=None):
        applied_hash = applied_hash or ("a" * 64)
        previous_records = (
            self.root / "data/manual/meti/records/applied.csv"
        )
        previous_raw = self.root / "data/raw/meti_manual/applied.pdf"
        previous_report = (
            self.root / "data/manual/meti/reports/applied.json"
        )
        previous_diff = self.root / "data/manual/meti/diffs/applied.csv"
        master_path = self.root / "data/master/master.csv"

        manual_import.save_records(self.records(), previous_records)
        previous_raw.parent.mkdir(parents=True, exist_ok=True)
        previous_raw.write_bytes(b"applied raw evidence\n")
        previous_report.parent.mkdir(parents=True, exist_ok=True)
        previous_report.write_text('{"status":"APPLIED"}\n', encoding="utf-8")
        previous_diff.parent.mkdir(parents=True, exist_ok=True)
        previous_diff.write_text("action,match_key\n", encoding="utf-8")
        master_path.parent.mkdir(parents=True, exist_ok=True)
        master_path.write_bytes(b"master-before-import\n")

        manual_import.update_evidence(
            self.records(),
            source_hash=applied_hash,
            source_url=PDF_URL,
            source_document=self.relative(previous_raw),
            publication_date="2025-09-29",
            effective_date="2025-10-09",
            seen_at="2025-09-29T00:00:00Z",
        )

        state = {
            "version": 1,
            "lifecycle_state": "APPLIED",
            "current_source_hash": applied_hash,
            "current_raw_path": self.relative(previous_raw),
            "current_records_path": self.relative(previous_records),
            "current_report_path": self.relative(previous_report),
            "current_diff_path": self.relative(previous_diff),
            "current_record_count": 100,
            "source_url": PDF_URL,
            "publication_date": "2025-09-29",
            "effective_date": "2025-10-09",
            "review_status": "APPROVED",
            "approved": True,
            "applied": True,
            "applied_source_hash": applied_hash,
            "apply_plan_path": "data/manual/meti/apply/applied.json",
            "master_before_sha256": "b" * 64,
            "master_after_sha256": "c" * 64,
        }
        state, events, detection_id = manual_event.open_detection(
            state,
            [],
            operator="detector",
            notice_url=NOTICE_URL,
            title="外国ユーザーリストを改正しました",
            detected_at=NOW,
        )
        audit_row = source_audit.entry(
            "meti_manual",
            "foreign_user_list_notice",
            "manual_detection",
            url=NOTICE_URL,
        )
        manual_event.persist_lifecycle(
            state=state,
            events=events,
            heartbeat_status="manual_pending",
            dashboard_event=None,
            audit_row=audit_row,
            now=NOW,
            paths=self.paths,
        )
        return {
            "detection_id": detection_id,
            "state": state,
            "events": events,
            "previous_records": previous_records,
            "previous_raw": previous_raw,
            "previous_report": previous_report,
            "previous_diff": previous_diff,
            "master": master_path,
        }

    def relative(self, path):
        return str(path.relative_to(self.root))

    def run_import(self, *, detection_id=None, expected_count=100):
        argv = [
            str(self.pdf),
            "--source-url",
            PDF_URL,
            "--publication-date",
            "2026-09-17",
            "--effective-date",
            "2026-09-24",
            "--expected-count",
            str(expected_count),
        ]
        if detection_id is not None:
            argv.extend(["--detection-id", detection_id])
        try:
            return manual_import.main(argv)
        except SystemExit as exc:
            self.fail(
                "meti_manual_import rejected the lifecycle CLI contract: "
                "SystemExit(%s)" % exc.code
            )

    def read_state(self):
        return json.loads(self.paths.state.read_text(encoding="utf-8"))

    def read_report(self, state):
        return json.loads(
            (self.root / state["current_report_path"]).read_text(
                encoding="utf-8"
            )
        )

    def generation_targets(self):
        stamp = manual_import._stamp(NOW)
        stem = "%s__%s" % (stamp, self.digest[:12])
        return [
            self.paths.state,
            self.paths.events,
            manual_import.TEXT_DIR / (stem + ".txt"),
            manual_import.RECORD_DIR / (stem + ".csv"),
            manual_import.DIFF_DIR / (stem + ".csv"),
            manual_import.REPORT_DIR / (stamp + "__manual_import.json"),
            manual_import.EVIDENCE_PATH,
            self.paths.heartbeat,
            self.paths.status,
            self.paths.changes,
            self.paths.audit,
        ]

    def snapshot(self, paths):
        return {
            self.relative(path): path.read_bytes() if path.exists() else None
            for path in paths
        }

    def applied_fields(self, state):
        names = [
            "current_source_hash",
            "current_raw_path",
            "current_records_path",
            "current_report_path",
            "current_diff_path",
            "current_record_count",
            "review_status",
            "approved",
            "applied",
            "applied_source_hash",
            "apply_plan_path",
            "master_before_sha256",
            "master_after_sha256",
        ]
        return {name: state.get(name) for name in names}

    def preserved_files(self, fixture):
        return [
            fixture["previous_records"],
            fixture["previous_raw"],
            fixture["previous_report"],
            fixture["previous_diff"],
            manual_import.EVIDENCE_PATH,
            fixture["master"],
        ]

    def review_patches(self):
        review_dir = self.root / "data/review"
        return (
            patch.object(meti_review, "ROOT", self.root),
            patch.object(meti_review, "STATE_PATH", self.paths.state),
            patch.object(
                meti_review,
                "EVIDENCE_PATH",
                manual_import.EVIDENCE_PATH,
            ),
            patch.object(meti_review, "REVIEW_DIR", review_dir),
            patch.object(
                meti_review,
                "REVIEW_LEDGER",
                review_dir / "meti_foreign_user_list.csv",
            ),
            patch.object(
                meti_review,
                "REVIEW_ARTIFACT_DIR",
                self.root / "data/manual/meti/reviews",
            ),
            patch.object(meti_review, "_now", return_value=NOW),
        )

    def assert_blocked_failure(
        self,
        *,
        extractor_side_effect=None,
        expected_count=100,
    ):
        fixture = self.seed_open_detection()
        state_before = self.read_state()
        events_before = manual_event.load_events(self.paths.events)
        files_before = self.snapshot(self.preserved_files(fixture))

        patch_args = {}
        if extractor_side_effect is None:
            patch_args["return_value"] = (self.records(), "text", 10)
        else:
            patch_args["side_effect"] = extractor_side_effect
        with patch.object(manual_import, "extract_pdf", **patch_args):
            rc = self.run_import(
                detection_id=fixture["detection_id"],
                expected_count=expected_count,
            )

        state_after = self.read_state()
        events_after = manual_event.load_events(self.paths.events)
        appended = events_after[len(events_before):]
        self.assertEqual(rc, 2)
        self.assertEqual(state_after["lifecycle_state"], "BLOCKED")
        self.assertEqual(
            [event["state"] for event in appended].count("BLOCKED"),
            1,
        )
        self.assertEqual(
            self.applied_fields(state_after),
            self.applied_fields(state_before),
        )
        self.assertEqual(
            self.snapshot(self.preserved_files(fixture)),
            files_before,
        )

    def test_routine_import_requires_matching_open_detection(self):
        fixture = self.seed_open_detection()
        before = self.snapshot([self.paths.state, self.paths.events])

        with patch.object(
            manual_import,
            "extract_pdf",
            return_value=(self.records(), "text", 10),
        ):
            rc = self.run_import(detection_id="f" * 64)

        self.assertEqual(rc, 2)
        self.assertEqual(
            self.snapshot([self.paths.state, self.paths.events]),
            before,
        )
        self.assertEqual(
            self.read_state()["detection_id"],
            fixture["detection_id"],
        )

    def test_matching_detection_reaches_review_required(self):
        fixture = self.seed_open_detection()
        before_event_count = len(fixture["events"])

        with patch.object(
            manual_import,
            "extract_pdf",
            return_value=(self.records(), "text", 10),
        ):
            rc = self.run_import(detection_id=fixture["detection_id"])

        state = self.read_state()
        events = manual_event.load_events(self.paths.events)
        self.assertEqual(rc, 0)
        self.assertEqual(state.get("lifecycle_state"), "REVIEW_REQUIRED")
        self.assertEqual(
            [event["state"] for event in events[before_event_count:]],
            ["FILE_RECEIVED", "VALIDATED", "DIFFED", "REVIEW_REQUIRED"],
        )
        self.assertTrue(state["diffed_at"])
        self.assertIs(state["approved"], False)
        self.assertIs(state["applied"], False)
        self.assertEqual(state["applied_source_hash"], "a" * 64)

    def test_validation_failure_records_blocked_and_preserves_applied_fields(self):
        self.assert_blocked_failure(
            extractor_side_effect=manual_import.PdfStructureError(
                "table header changed"
            )
        )

    def test_blocked_routine_import_retries_same_detection(self):
        fixture = self.seed_open_detection()
        with patch.object(
            manual_import,
            "extract_pdf",
            side_effect=manual_import.PdfStructureError(
                "temporary table parse failure"
            ),
        ):
            self.assertEqual(
                self.run_import(detection_id=fixture["detection_id"]),
                2,
            )
        blocked = self.read_state()
        self.assertEqual(blocked["lifecycle_state"], "BLOCKED")
        self.assertEqual(blocked["pending_detection"]["attempt"], 1)

        with patch.object(
            manual_import,
            "extract_pdf",
            return_value=(self.records(), "text", 10),
        ):
            retry_rc = self.run_import(
                detection_id=fixture["detection_id"]
            )

        retried = self.read_state()
        self.assertEqual(retry_rc, 0)
        self.assertEqual(retried["lifecycle_state"], "REVIEW_REQUIRED")
        self.assertEqual(retried["detection_id"], fixture["detection_id"])
        self.assertEqual(retried["pending_detection"]["attempt"], 2)

    def test_diffed_timestamp_uses_stage_completion_time(self):
        fixture = self.seed_open_detection()
        import_started = NOW + timedelta(minutes=59)
        diff_completed = NOW + timedelta(minutes=61)
        first = True

        def clock():
            nonlocal first
            if first:
                first = False
                return import_started
            return diff_completed

        with (
            patch.object(manual_import, "_now", side_effect=clock),
            patch.object(
                manual_import,
                "extract_pdf",
                return_value=(self.records(), "text", 10),
            ),
        ):
            rc = self.run_import(detection_id=fixture["detection_id"])

        state = self.read_state()
        events = manual_event.load_events(self.paths.events)
        diffed = next(
            event
            for event in reversed(events)
            if event["state"] == "DIFFED"
        )
        self.assertEqual(rc, 0)
        self.assertEqual(diffed["event_at"], "2026-09-17T07:01:00Z")
        self.assertEqual(state["diffed_at"], "2026-09-17T07:01:00Z")
        self.assertGreater(state["diffed_at"], state["sla_due_at"])

    def test_persistence_failure_rolls_back_current_state_and_ledgers(self):
        fixture = self.seed_open_detection()
        targets = self.generation_targets()
        before = self.snapshot(targets)
        real_replace = persistence.os.replace
        calls = 0

        def fail_second_replace(src, dst):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected import commit failure")
            return real_replace(src, dst)

        with (
            patch.object(
                manual_import,
                "extract_pdf",
                return_value=(self.records(), "text", 10),
            ),
            patch.object(
                persistence.os,
                "replace",
                side_effect=fail_second_replace,
            ),
        ):
            with self.assertRaisesRegex(
                OSError,
                "injected import commit failure",
            ):
                self.run_import(detection_id=fixture["detection_id"])

        self.assertEqual(self.snapshot(targets), before)
        self.assertFalse(list(self.root.rglob("*.tmp")))
        self.assertFalse(list(self.root.rglob("*.bak")))

    def test_historical_baseline_without_detection_is_reviewable(self):
        with patch.object(
            manual_import,
            "extract_pdf",
            return_value=(self.records(), "text", 10),
        ):
            rc = self.run_import(detection_id=None)

        reports = list(manual_import.REPORT_DIR.glob("*__manual_import.json"))
        self.assertEqual(rc, 0)
        self.assertEqual(len(reports), 1)
        report = json.loads(reports[0].read_text(encoding="utf-8"))
        self.assertIs(report["baseline"], True)
        self.assertEqual(report["status"], "REVIEW_REQUIRED")
        self.assertFalse((self.root / "data/master/master.csv").exists())
        state = self.read_state()
        events = manual_event.load_events(self.paths.events)
        self.assertEqual(state.get("lifecycle_state"), "REVIEW_REQUIRED")
        self.assertRegex(state["detection_id"], r"^[0-9a-f]{64}$")
        self.assertEqual(
            [event["state"] for event in events],
            [
                "DETECTED",
                "MANUAL_FETCH_REQUIRED",
                "FILE_RECEIVED",
                "VALIDATED",
                "DIFFED",
                "REVIEW_REQUIRED",
            ],
        )

        with ExitStack() as stack:
            for review_patch in self.review_patches():
                stack.enter_context(review_patch)
            decision = meti_review.decide(
                expected_hash=self.digest,
                decision=meti_review.DECISION_APPROVED,
                reviewer="reviewer",
                note="baseline verified",
            )
        self.assertFalse(decision["idempotent"])
        self.assertEqual(decision["state"]["lifecycle_state"], "APPROVED")

    def test_detection_bound_first_baseline_uses_open_detection(self):
        state, events, detection_id = manual_event.open_detection(
            {},
            [],
            operator="detector",
            notice_url=NOTICE_URL,
            title="foreign user list updated",
            detected_at=NOW,
        )
        manual_event.persist_lifecycle(
            state=state,
            events=events,
            heartbeat_status="manual_pending",
            dashboard_event=None,
            audit_row=None,
            now=NOW,
            paths=self.paths,
        )

        with patch.object(
            manual_import,
            "extract_pdf",
            return_value=(self.records(), "text", 10),
        ):
            rc = self.run_import(detection_id=detection_id)

        imported = self.read_state()
        self.assertEqual(rc, 0)
        self.assertEqual(imported["detection_id"], detection_id)
        self.assertEqual(imported["lifecycle_state"], "REVIEW_REQUIRED")
        self.assertEqual(imported["pending_detection"]["attempt"], 1)

    def test_blocked_historical_baseline_retries_internal_detection(self):
        with patch.object(
            manual_import,
            "extract_pdf",
            side_effect=manual_import.PdfStructureError(
                "temporary baseline parse failure"
            ),
        ):
            self.assertEqual(self.run_import(), 2)

        self.assertTrue(
            self.paths.state.exists(),
            "failed baseline must persist its internal detection",
        )
        blocked = self.read_state()
        detection_id = blocked["detection_id"]
        self.assertEqual(blocked["lifecycle_state"], "BLOCKED")
        self.assertEqual(blocked["pending_detection"]["attempt"], 1)

        with patch.object(
            manual_import,
            "extract_pdf",
            return_value=(self.records(), "text", 10),
        ):
            retry_rc = self.run_import()

        retried = self.read_state()
        self.assertEqual(retry_rc, 0)
        self.assertEqual(retried["detection_id"], detection_id)
        self.assertEqual(retried["lifecycle_state"], "REVIEW_REQUIRED")
        self.assertEqual(retried["pending_detection"]["attempt"], 2)

    def test_same_hash_for_new_detection_is_still_diffed(self):
        fixture = self.seed_open_detection(applied_hash=self.digest)
        before_event_count = len(fixture["events"])

        with patch.object(
            manual_import,
            "extract_pdf",
            return_value=(self.records(), "text", 10),
        ):
            rc = self.run_import(detection_id=fixture["detection_id"])

        state = self.read_state()
        events = manual_event.load_events(self.paths.events)
        report = self.read_report(state)
        self.assertEqual(rc, 0)
        self.assertEqual(state["lifecycle_state"], "REVIEW_REQUIRED")
        self.assertEqual(
            [event["state"] for event in events[before_event_count:]],
            ["FILE_RECEIVED", "VALIDATED", "DIFFED", "REVIEW_REQUIRED"],
        )
        self.assertEqual(
            report["diff"],
            {"追加": 0, "削除": 0, "変更": 0},
        )

    def test_same_hash_replay_for_same_detection_is_byte_idempotent(self):
        fixture = self.seed_open_detection()
        with patch.object(
            manual_import,
            "extract_pdf",
            return_value=(self.records(), "text", 10),
        ):
            first_rc = self.run_import(
                detection_id=fixture["detection_id"]
            )
        self.assertEqual(first_rc, 0)
        targets = self.generation_targets()
        before = self.snapshot(targets)

        with patch.object(
            manual_import,
            "extract_pdf",
            return_value=(self.records(), "text", 10),
        ):
            second_rc = self.run_import(
                detection_id=fixture["detection_id"]
            )

        self.assertEqual(second_rc, 0)
        self.assertEqual(self.snapshot(targets), before)

    def test_malformed_header_records_one_blocked_event(self):
        self.assert_blocked_failure(
            extractor_side_effect=manual_import.PdfStructureError(
                "foreign user list header not found"
            )
        )

    def test_image_only_pdf_records_one_blocked_event(self):
        self.assert_blocked_failure(
            extractor_side_effect=manual_import.PdfStructureError(
                "PDF text is too short; image-only PDF suspected"
            )
        )

    def test_wrong_count_records_one_blocked_event(self):
        self.assert_blocked_failure(expected_count=101)

    def test_changed_table_schema_records_one_blocked_event(self):
        self.assert_blocked_failure(
            extractor_side_effect=manual_import.PdfStructureError(
                "required table columns changed"
            )
        )


if __name__ == "__main__":
    unittest.main()
