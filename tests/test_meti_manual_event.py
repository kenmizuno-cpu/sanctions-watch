from __future__ import annotations

import csv
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from src import dashboard, persistence, source_audit
from src import meti_manual_event as manual_event
from src import state as state_store
from src.meti_manual_event import (
    LifecycleError,
    advance,
    open_detection,
    record_no_change,
    validate_notice_url,
)


NOW = datetime(2026, 9, 17, 6, 0, tzinfo=timezone.utc)


def write_csv_header(path, columns):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        csv.writer(f, lineterminator="\n").writerow(columns)


def seed_valid_generation(paths):
    paths.state.parent.mkdir(parents=True, exist_ok=True)
    paths.state.write_text("{}\n", encoding="utf-8")
    for path, columns in (
        (paths.events, manual_event.EVENT_COLS),
        (paths.heartbeat, state_store.HB_COLS),
        (paths.status, dashboard.STATUS_COLS),
        (paths.changes, dashboard.CHANGE_COLS),
        (paths.audit, source_audit.AUDIT_COLS),
    ):
        write_csv_header(path, columns)


def record_detection_fixture(paths, *, now):
    return manual_event.main(
        [
            "detect",
            "--operator",
            "kenmizuno-cpu",
            "--notice-url",
            "https://www.meti.go.jp/press/example.html",
            "--title",
            "外国ユーザーリストを改正しました",
            "--detected-at",
            "2026-09-17T06:00:00Z",
        ],
        paths=paths,
        now=now,
    )


class TestMetiManualEvent(unittest.TestCase):
    def test_validate_official_notice_urls(self):
        cases = [
            "https://meti.go.jp/press/example.html",
            "https://www.meti.go.jp/press/example.html",
        ]
        for value in cases:
            with self.subTest(value=value):
                self.assertEqual(validate_notice_url(value), value)

    def test_reject_unsafe_notice_urls(self):
        cases = [
            "http://www.meti.go.jp/press/example.html",
            "https://www.meti.go.jp.example.com/notice",
            "https://operator@www.meti.go.jp/notice",
            "https://www.meti.go.jp:443/notice",
            "https://www.meti.go.jp/notice#fragment",
        ]
        for value in cases:
            with self.subTest(value=value):
                with self.assertRaises(LifecycleError):
                    validate_notice_url(value)

    def test_detection_opens_manual_fetch_required(self):
        state, events, detection_id = open_detection(
            {},
            [],
            operator="kenmizuno-cpu",
            notice_url="https://www.meti.go.jp/press/example.html",
            title="外国ユーザーリストを改正しました",
            detected_at=NOW,
        )

        self.assertEqual(
            state["lifecycle_state"],
            "MANUAL_FETCH_REQUIRED",
        )
        self.assertEqual(state["detection_id"], detection_id)
        self.assertEqual(
            [event["state"] for event in events],
            ["DETECTED", "MANUAL_FETCH_REQUIRED"],
        )

    def test_second_open_detection_is_blocked(self):
        state, events, _ = open_detection(
            {},
            [],
            operator="kenmizuno-cpu",
            notice_url="https://www.meti.go.jp/press/one.html",
            title="更新1",
            detected_at=NOW,
        )

        with self.assertRaises(LifecycleError):
            open_detection(
                state,
                events,
                operator="kenmizuno-cpu",
                notice_url="https://www.meti.go.jp/press/two.html",
                title="更新2",
                detected_at=NOW,
            )

    def test_same_pending_notice_replay_is_idempotent(self):
        state, events, detection_id = open_detection(
            {},
            [],
            operator="kenmizuno-cpu",
            notice_url="https://www.meti.go.jp/press/one.html",
            title="更新1",
            detected_at=NOW,
        )

        replay_state, replay_events, replay_id = open_detection(
            state,
            events,
            operator="kenmizuno-cpu",
            notice_url="https://www.meti.go.jp/press/one.html",
            title="更新1",
            detected_at=NOW + timedelta(minutes=1),
        )

        self.assertEqual(replay_id, detection_id)
        self.assertEqual(replay_state, state)
        self.assertEqual(replay_events, events)

    def test_duplicate_transition_is_idempotent(self):
        state, events, detection_id = open_detection(
            {},
            [],
            operator="kenmizuno-cpu",
            notice_url="https://www.meti.go.jp/press/example.html",
            title="更新",
            detected_at=NOW,
        )

        first_state, first_events, first = advance(
            state,
            events,
            new_state="FILE_RECEIVED",
            event_at=NOW,
            operator="kenmizuno-cpu",
            detection_id=detection_id,
            source_hash="a" * 64,
        )
        second_state, second_events, second = advance(
            first_state,
            first_events,
            new_state="FILE_RECEIVED",
            event_at=NOW + timedelta(minutes=1),
            operator="kenmizuno-cpu",
            detection_id=detection_id,
            source_hash="a" * 64,
        )

        self.assertEqual(first, second)
        self.assertEqual(first_state, second_state)
        self.assertEqual(first_events, second_events)

    def test_no_change_cannot_close_pending_detection(self):
        state, events, _ = open_detection(
            {},
            [],
            operator="kenmizuno-cpu",
            notice_url="https://www.meti.go.jp/press/example.html",
            title="更新",
            detected_at=NOW,
        )

        with self.assertRaises(LifecycleError):
            record_no_change(
                state,
                events,
                operator="kenmizuno-cpu",
                source_url=(
                    "https://www.meti.go.jp/"
                    "policy/anpo/law09-2.html"
                ),
                checked_at=NOW,
            )

    def test_invalid_transition_is_blocked(self):
        state, events, detection_id = open_detection(
            {},
            [],
            operator="kenmizuno-cpu",
            notice_url="https://www.meti.go.jp/press/example.html",
            title="更新",
            detected_at=NOW,
        )

        with self.assertRaises(LifecycleError):
            advance(
                state,
                events,
                new_state="APPROVED",
                event_at=NOW,
                operator="kenmizuno-cpu",
                detection_id=detection_id,
            )

    def test_retry_after_blocked_starts_a_new_attempt(self):
        state, events, detection_id = open_detection(
            {},
            [],
            operator="kenmizuno-cpu",
            notice_url="https://www.meti.go.jp/press/example.html",
            title="更新",
            detected_at=NOW,
        )

        state, events, first = advance(
            state,
            events,
            new_state="FILE_RECEIVED",
            event_at=NOW,
            operator="kenmizuno-cpu",
            detection_id=detection_id,
            source_hash="a" * 64,
        )
        state, events, _ = advance(
            state,
            events,
            new_state="BLOCKED",
            event_at=NOW,
            operator="kenmizuno-cpu",
            detection_id=detection_id,
            source_hash="a" * 64,
        )
        state, events, retry = advance(
            state,
            events,
            new_state="FILE_RECEIVED",
            event_at=NOW + timedelta(minutes=1),
            operator="kenmizuno-cpu",
            detection_id=detection_id,
            source_hash="a" * 64,
        )

        self.assertNotEqual(first["event_id"], retry["event_id"])
        self.assertEqual(state["pending_detection"]["attempt"], 2)

    def test_detect_persists_complete_generation_and_prints_detection_id(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            paths = manual_event.LifecyclePaths.for_root(root, NOW)
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                rc = record_detection_fixture(paths, now=NOW)

            self.assertEqual(rc, 0)
            self.assertRegex(
                stdout.getvalue(),
                r"^DETECTION_ID=[0-9a-f]{64}\n$",
            )

            state = json.loads(paths.state.read_text(encoding="utf-8"))
            events = manual_event.load_events(paths.events)
            self.assertEqual(
                state["lifecycle_state"],
                "MANUAL_FETCH_REQUIRED",
            )
            self.assertEqual(
                events[-1]["detection_id"],
                state["detection_id"],
            )

            with paths.heartbeat.open(encoding="utf-8", newline="") as f:
                heartbeat_rows = list(csv.DictReader(f))
            self.assertEqual(
                heartbeat_rows[-1]["status"],
                "manual_pending",
            )

            with paths.status.open(encoding="utf-8", newline="") as f:
                status_rows = list(csv.DictReader(f))
            meti = next(
                row for row in status_rows
                if row["出所"] == "経済産業省"
            )
            self.assertEqual(meti["状態"], "要確認：正本取得待ち")

            for path in paths.transaction_targets():
                self.assertTrue(path.is_file(), str(path))

    def test_check_uses_injected_time_and_persists_manual_ok(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            paths = manual_event.LifecyclePaths.for_root(root, NOW)

            rc = manual_event.main(
                [
                    "check",
                    "--operator",
                    "kenmizuno-cpu",
                    "--source-url",
                    (
                        "https://www.meti.go.jp/"
                        "policy/anpo/law09-2.html"
                    ),
                    "--note",
                    "公式ページをブラウザ確認、更新なし",
                ],
                paths=paths,
                now=NOW,
            )

            self.assertEqual(rc, 0)
            state = json.loads(paths.state.read_text(encoding="utf-8"))
            self.assertEqual(state["lifecycle_state"], "CHECKED_NO_CHANGE")
            self.assertEqual(
                state["last_manual_check_at"],
                "2026-09-17T06:00:00Z",
            )

            with paths.heartbeat.open(encoding="utf-8", newline="") as f:
                heartbeat_rows = list(csv.DictReader(f))
            self.assertEqual(heartbeat_rows[-1]["status"], "manual_ok")

    def test_atomic_failure_preserves_every_existing_file(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            paths = manual_event.LifecyclePaths.for_root(root, NOW)
            seed_valid_generation(paths)
            before = {
                path: path.read_bytes()
                for path in paths.transaction_targets()
            }
            real_replace = persistence.os.replace
            calls = 0

            def fail_second_replace(src, dst):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("injected commit failure")
                return real_replace(src, dst)

            with patch.object(
                persistence.os,
                "replace",
                side_effect=fail_second_replace,
            ):
                with self.assertRaisesRegex(
                    OSError,
                    "injected commit failure",
                ):
                    record_detection_fixture(paths, now=NOW)

            self.assertEqual(
                before,
                {
                    path: path.read_bytes()
                    for path in paths.transaction_targets()
                },
            )
            self.assertFalse(list(root.rglob("*.tmp")))
            self.assertFalse(list(root.rglob("*.bak")))


if __name__ == "__main__":
    unittest.main()
