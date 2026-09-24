from __future__ import annotations

import csv
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from src import dashboard
from src import meti_manual_event as manual_event
from src import persistence
from src import state as state_store
from src.meti_manual_sla import SlaStateError, evaluate, main, run


DETECTED = datetime(2026, 9, 17, 6, 0, tzinfo=timezone.utc)
DUE = DETECTED + timedelta(minutes=60)
OPERATOR = "kenmizuno-cpu"
NOTICE_URL = "https://www.meti.go.jp/press/example.html"


def csv_rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def snapshot(paths: manual_event.LifecyclePaths) -> dict[Path, bytes | None]:
    return {
        path: path.read_bytes() if path.exists() else None
        for path in paths.transaction_targets()
    }


def meti_status(path: Path) -> dict:
    return next(
        row
        for row in csv_rows(path)
        if row["出所"] == "経済産業省"
    )


def seed_pending(paths: manual_event.LifecyclePaths) -> tuple[dict, list[dict]]:
    state, events, _ = manual_event.open_detection(
        {},
        [],
        operator=OPERATOR,
        notice_url=NOTICE_URL,
        title="外国ユーザーリストを改正しました",
        detected_at=DETECTED,
    )
    manual_event.persist_lifecycle(
        state=state,
        events=events,
        heartbeat_status="manual_pending",
        dashboard_event=[
            "経済産業省",
            "更新候補検知",
            "外国ユーザーリスト",
            "",
            "正本取得待ち",
        ],
        audit_row=None,
        now=DETECTED,
        paths=paths,
    )
    return state, events


class TestMetiManualSlaEvaluation(unittest.TestCase):
    def state(self) -> dict:
        return {
            "lifecycle_state": "MANUAL_FETCH_REQUIRED",
            "detected_at": "2026-09-17T06:00:00Z",
            "sla_due_at": "2026-09-17T07:00:00Z",
        }

    def test_one_second_before_due_is_pending(self):
        self.assertEqual(
            evaluate(
                self.state(),
                DETECTED + timedelta(minutes=60, seconds=-1),
            ),
            "pending",
        )

    def test_exact_due_time_is_breached(self):
        self.assertEqual(evaluate(self.state(), DUE), "breached")

    def test_diffed_before_due_is_healthy(self):
        state = self.state()
        state.update(
            lifecycle_state="REVIEW_REQUIRED",
            diffed_at="2026-09-17T06:40:00Z",
        )
        self.assertEqual(evaluate(state, DUE), "healthy")

    def test_diffed_at_or_after_due_without_marker_is_recovered(self):
        for diffed_at in (
            "2026-09-17T07:00:00Z",
            "2026-09-17T07:05:00Z",
        ):
            with self.subTest(diffed_at=diffed_at):
                state = self.state()
                state.update(
                    lifecycle_state="REVIEW_REQUIRED",
                    diffed_at=diffed_at,
                )
                self.assertEqual(
                    evaluate(state, DUE + timedelta(minutes=20)),
                    "recovered",
                )

    def test_blocked_is_blocked_without_sla_timestamps(self):
        self.assertEqual(
            evaluate({"lifecycle_state": "BLOCKED"}, DETECTED),
            "blocked",
        )

    def test_closed_check_without_sla_timestamps_is_healthy(self):
        self.assertEqual(
            evaluate(
                {"lifecycle_state": "CHECKED_NO_CHANGE"},
                DETECTED,
            ),
            "healthy",
        )

    def test_first_post_breach_diff_is_recovered(self):
        state = self.state()
        state.update(
            lifecycle_state="REVIEW_REQUIRED",
            diffed_at="2026-09-17T07:05:00Z",
            sla_breached_at="2026-09-17T07:00:00Z",
            sla_recovered_at="",
        )
        self.assertEqual(
            evaluate(state, DETECTED + timedelta(minutes=66)),
            "recovered",
        )

    def test_recorded_recovery_is_healthy(self):
        state = self.state()
        state.update(
            lifecycle_state="REVIEW_REQUIRED",
            diffed_at="2026-09-17T07:05:00Z",
            sla_breached_at="2026-09-17T07:00:00Z",
            sla_recovered_at="2026-09-17T07:06:00Z",
        )
        self.assertEqual(
            evaluate(state, DETECTED + timedelta(minutes=67)),
            "healthy",
        )

    def test_missing_or_unknown_lifecycle_state_is_invalid(self):
        for state in ({}, {"lifecycle_state": "UNKNOWN"}):
            with self.subTest(state=state):
                with self.assertRaises(SlaStateError):
                    evaluate(state, DETECTED)

    def test_open_state_requires_valid_sla_timestamps(self):
        state = self.state()
        state["sla_due_at"] = "not-a-timestamp"
        with self.assertRaises(SlaStateError):
            evaluate(state, DETECTED)

    def test_due_time_must_be_exactly_60_minutes_after_detection(self):
        for due_at in (
            "2026-09-17T06:59:59Z",
            "2026-09-17T08:00:00Z",
        ):
            with self.subTest(due_at=due_at):
                state = self.state()
                state["sla_due_at"] = due_at
                with self.assertRaises(SlaStateError):
                    evaluate(state, DETECTED)

    def test_recovery_requires_breach_and_diff(self):
        cases = (
            {
                "lifecycle_state": "REVIEW_REQUIRED",
                "detected_at": "2026-09-17T06:00:00Z",
                "sla_due_at": "2026-09-17T07:00:00Z",
                "diffed_at": "2026-09-17T07:05:00Z",
                "sla_recovered_at": "2026-09-17T07:06:00Z",
            },
            {
                "lifecycle_state": "MANUAL_FETCH_REQUIRED",
                "detected_at": "2026-09-17T06:00:00Z",
                "sla_due_at": "2026-09-17T07:00:00Z",
                "sla_breached_at": "2026-09-17T07:00:00Z",
                "sla_recovered_at": "2026-09-17T07:06:00Z",
            },
        )
        for state in cases:
            with self.subTest(state=state):
                with self.assertRaises(SlaStateError):
                    evaluate(state, DUE + timedelta(minutes=10))

    def test_sla_history_must_be_chronological(self):
        cases = (
            {
                "sla_breached_at": "2026-09-17T06:59:59Z",
            },
            {
                "lifecycle_state": "REVIEW_REQUIRED",
                "diffed_at": "2026-09-17T07:05:00Z",
                "sla_breached_at": "2026-09-17T07:06:00Z",
            },
            {
                "lifecycle_state": "REVIEW_REQUIRED",
                "diffed_at": "2026-09-17T07:05:00Z",
                "sla_breached_at": "2026-09-17T07:00:00Z",
                "sla_recovered_at": "2026-09-17T07:04:59Z",
            },
        )
        for changes in cases:
            with self.subTest(changes=changes):
                state = self.state()
                state.update(changes)
                with self.assertRaises(SlaStateError):
                    evaluate(state, DUE + timedelta(minutes=10))

    def test_open_state_rejects_diff_timestamp(self):
        state = self.state()
        state["diffed_at"] = "2026-09-17T07:05:00Z"
        with self.assertRaises(SlaStateError):
            evaluate(state, DUE + timedelta(minutes=20))

    def test_recorded_transition_timestamps_cannot_be_in_the_future(self):
        cases = (
            {
                "lifecycle_state": "REVIEW_REQUIRED",
                "diffed_at": "2026-09-17T07:30:00Z",
            },
            {
                "sla_breached_at": "2026-09-17T07:30:00Z",
            },
            {
                "lifecycle_state": "REVIEW_REQUIRED",
                "diffed_at": "2026-09-17T07:05:00Z",
                "sla_breached_at": "2026-09-17T07:00:00Z",
                "sla_recovered_at": "2026-09-17T07:30:00Z",
            },
        )
        for changes in cases:
            with self.subTest(changes=changes):
                state = self.state()
                state.update(changes)
                with self.assertRaises(SlaStateError):
                    evaluate(state, DUE + timedelta(minutes=20))

    def test_diffed_state_requires_diffed_at(self):
        with self.assertRaises(SlaStateError):
            evaluate({"lifecycle_state": "DIFFED"}, DETECTED)


class TestMetiManualSlaPersistence(unittest.TestCase):
    def test_pending_check_does_not_write(self):
        with tempfile.TemporaryDirectory() as td:
            paths = manual_event.LifecyclePaths.for_root(Path(td), DUE)
            seed_pending(paths)
            before = snapshot(paths)

            self.assertEqual(
                run(paths=paths, now=DUE - timedelta(seconds=1)),
                0,
            )
            self.assertEqual(snapshot(paths), before)

    def test_first_breach_is_persisted_once(self):
        with tempfile.TemporaryDirectory() as td:
            paths = manual_event.LifecyclePaths.for_root(Path(td), DUE)
            seed_pending(paths)
            events_before = paths.events.read_bytes()
            changes_before = csv_rows(paths.changes)

            self.assertEqual(run(paths=paths, now=DUE), 2)

            state = json.loads(paths.state.read_text(encoding="utf-8"))
            self.assertEqual(
                state["sla_breached_at"],
                "2026-09-17T07:00:00Z",
            )
            self.assertEqual(paths.events.read_bytes(), events_before)

            heartbeat = csv_rows(paths.heartbeat)
            self.assertEqual(heartbeat[-1]["status"], "manual_critical")
            self.assertEqual(
                meti_status(paths.status)["状態"],
                "重大：更新候補未検証",
            )

            changes = csv_rows(paths.changes)
            self.assertEqual(len(changes), len(changes_before) + 1)
            critical = [
                row for row in changes
                if row["種別"] == "SLA超過"
            ]
            self.assertEqual(len(critical), 1)
            self.assertEqual(critical[0]["出所"], "経済産業省")
            self.assertEqual(
                critical[0]["変更後"],
                "重大：更新候補未検証",
            )

            after_first = snapshot(paths)
            self.assertEqual(
                run(paths=paths, now=DUE + timedelta(minutes=1)),
                2,
            )
            self.assertEqual(snapshot(paths), after_first)

    def test_first_recovery_is_persisted_once(self):
        with tempfile.TemporaryDirectory() as td:
            paths = manual_event.LifecyclePaths.for_root(Path(td), DUE)
            seed_pending(paths)
            self.assertEqual(run(paths=paths, now=DUE), 2)

            state = json.loads(paths.state.read_text(encoding="utf-8"))
            state.update(
                lifecycle_state="REVIEW_REQUIRED",
                diffed_at="2026-09-17T07:05:00Z",
            )
            state_store.write_state(paths.state, state)
            events_before = paths.events.read_bytes()
            changes_before = csv_rows(paths.changes)
            recovered_at = DUE + timedelta(minutes=6)

            self.assertEqual(run(paths=paths, now=recovered_at), 0)

            recovered = json.loads(
                paths.state.read_text(encoding="utf-8")
            )
            self.assertEqual(
                recovered["sla_recovered_at"],
                "2026-09-17T07:06:00Z",
            )
            self.assertEqual(paths.events.read_bytes(), events_before)

            heartbeat = csv_rows(paths.heartbeat)
            self.assertEqual(heartbeat[-1]["status"], "manual_review")
            self.assertEqual(meti_status(paths.status)["状態"], "要レビュー")

            changes = csv_rows(paths.changes)
            self.assertEqual(len(changes), len(changes_before) + 1)
            recovery = [
                row for row in changes
                if row["種別"] == "SLA復旧"
            ]
            self.assertEqual(len(recovery), 1)
            self.assertEqual(recovery[0]["出所"], "経済産業省")
            self.assertEqual(recovery[0]["変更後"], "要レビュー")

            after_first = snapshot(paths)
            self.assertEqual(
                run(
                    paths=paths,
                    now=recovered_at + timedelta(minutes=1),
                ),
                0,
            )
            self.assertEqual(snapshot(paths), after_first)

    def test_late_diff_records_missed_breach_and_recovery_once(self):
        with tempfile.TemporaryDirectory() as td:
            paths = manual_event.LifecyclePaths.for_root(Path(td), DUE)
            seed_pending(paths)
            state = json.loads(paths.state.read_text(encoding="utf-8"))
            state.update(
                lifecycle_state="REVIEW_REQUIRED",
                diffed_at="2026-09-17T07:05:00Z",
            )
            state_store.write_state(paths.state, state)
            events_before = paths.events.read_bytes()
            changes_before = csv_rows(paths.changes)
            checked_at = DUE + timedelta(minutes=20)

            self.assertEqual(run(paths=paths, now=checked_at), 0)

            recovered = json.loads(
                paths.state.read_text(encoding="utf-8")
            )
            self.assertEqual(
                recovered["sla_breached_at"],
                "2026-09-17T07:00:00Z",
            )
            self.assertEqual(
                recovered["sla_recovered_at"],
                "2026-09-17T07:20:00Z",
            )
            self.assertEqual(paths.events.read_bytes(), events_before)
            self.assertEqual(csv_rows(paths.heartbeat)[-1]["status"], "manual_review")
            self.assertEqual(meti_status(paths.status)["状態"], "要レビュー")

            changes = csv_rows(paths.changes)
            self.assertEqual(len(changes), len(changes_before) + 2)
            self.assertEqual(changes[0]["種別"], "SLA復旧")
            self.assertEqual(changes[1]["種別"], "SLA超過")

            after_first = snapshot(paths)
            self.assertEqual(
                run(paths=paths, now=checked_at + timedelta(minutes=1)),
                0,
            )
            self.assertEqual(snapshot(paths), after_first)

    def test_breach_transaction_failure_restores_every_output(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            paths = manual_event.LifecyclePaths.for_root(root, DUE)
            seed_pending(paths)
            before = snapshot(paths)
            real_replace = persistence.os.replace
            calls = 0

            def fail_second_replace(src, dst):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("injected SLA commit failure")
                return real_replace(src, dst)

            with patch.object(
                persistence.os,
                "replace",
                side_effect=fail_second_replace,
            ):
                with self.assertRaisesRegex(
                    OSError,
                    "injected SLA commit failure",
                ):
                    run(paths=paths, now=DUE)

            self.assertEqual(snapshot(paths), before)
            leftovers = [
                path
                for path in root.rglob("*")
                if path.name.endswith((".tmp", ".bak"))
            ]
            self.assertEqual(leftovers, [])

    def test_blocked_state_returns_two_without_duplicate_writes(self):
        with tempfile.TemporaryDirectory() as td:
            paths = manual_event.LifecyclePaths.for_root(Path(td), DUE)
            state, events = seed_pending(paths)
            detection_id = state["detection_id"]
            blocked_at = DETECTED + timedelta(minutes=10)
            state, events, _ = manual_event.advance(
                state,
                events,
                new_state="BLOCKED",
                event_at=blocked_at,
                operator=OPERATOR,
                source_url=NOTICE_URL,
                detection_id=detection_id,
                detail="PDF structure changed",
            )
            manual_event.persist_lifecycle(
                state=state,
                events=events,
                heartbeat_status="manual_blocked",
                dashboard_event=[
                    "経済産業省",
                    "手動取込BLOCKED",
                    "外国ユーザーリスト",
                    "正本取得待ち",
                    "重大：正本解析BLOCKED",
                ],
                audit_row=None,
                now=blocked_at,
                paths=paths,
            )
            before = snapshot(paths)

            self.assertEqual(run(paths=paths, now=DUE), 2)
            self.assertEqual(snapshot(paths), before)

    def test_invalid_state_returns_one_without_writes(self):
        with tempfile.TemporaryDirectory() as td:
            paths = manual_event.LifecyclePaths.for_root(Path(td), DUE)
            state_store.write_state(
                paths.state,
                {"lifecycle_state": "DIFFED"},
            )
            before = snapshot(paths)

            stderr = io.StringIO()
            with redirect_stderr(stderr):
                rc = run(paths=paths, now=DUE)

            self.assertEqual(rc, 1)
            self.assertIn("diffed_at is required", stderr.getvalue())
            self.assertEqual(snapshot(paths), before)

    def test_open_state_with_diff_returns_one_without_writes(self):
        with tempfile.TemporaryDirectory() as td:
            paths = manual_event.LifecyclePaths.for_root(Path(td), DUE)
            seed_pending(paths)
            state = json.loads(paths.state.read_text(encoding="utf-8"))
            state["diffed_at"] = "2026-09-17T07:05:00Z"
            state_store.write_state(paths.state, state)
            before = snapshot(paths)

            stderr = io.StringIO()
            with redirect_stderr(stderr):
                rc = run(paths=paths, now=DUE + timedelta(minutes=20))

            self.assertEqual(rc, 1)
            self.assertIn("open lifecycle", stderr.getvalue())
            self.assertEqual(snapshot(paths), before)

    def test_future_diff_returns_one_without_writes(self):
        with tempfile.TemporaryDirectory() as td:
            paths = manual_event.LifecyclePaths.for_root(Path(td), DUE)
            seed_pending(paths)
            state = json.loads(paths.state.read_text(encoding="utf-8"))
            state.update(
                lifecycle_state="REVIEW_REQUIRED",
                diffed_at="2026-09-17T07:30:00Z",
            )
            state_store.write_state(paths.state, state)
            before = snapshot(paths)

            stderr = io.StringIO()
            with redirect_stderr(stderr):
                rc = run(paths=paths, now=DUE + timedelta(minutes=20))

            self.assertEqual(rc, 1)
            self.assertIn("future", stderr.getvalue())
            self.assertEqual(snapshot(paths), before)

    def test_main_uses_injected_paths_and_returns_invalid_exit_code(self):
        with tempfile.TemporaryDirectory() as td:
            paths = manual_event.LifecyclePaths.for_root(Path(td), DUE)
            state_store.write_state(
                paths.state,
                {"lifecycle_state": "UNKNOWN"},
            )

            stderr = io.StringIO()
            with redirect_stderr(stderr):
                rc = main([], paths=paths, now=DUE)

            self.assertEqual(rc, 1)
            self.assertIn("unknown or missing", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
