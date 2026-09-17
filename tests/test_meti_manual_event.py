from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from src.meti_manual_event import (
    LifecycleError,
    advance,
    open_detection,
    record_no_change,
    validate_notice_url,
)


NOW = datetime(2026, 9, 17, 6, 0, tzinfo=timezone.utc)


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


if __name__ == "__main__":
    unittest.main()
