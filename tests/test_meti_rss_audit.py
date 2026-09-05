from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from src import meti_rss as mr
from src.meti_rss_audit import (
    RUNTIME_HB_COLS,
    entry_age_hours,
    latest_entry_updated,
    stale_threshold_hours,
    write_runtime_heartbeat,
)


class TestMetiRssAudit(unittest.TestCase):
    def test_actual_meti_style_atom_without_id_uses_url(self):
        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
          <title>経済産業省 | 新着情報</title>
          <entry>
            <title>通常のニュース</title>
            <link rel="alternate" href="https://www.meti.go.jp/press/x.html"/>
            <updated>2026-06-19T05:00:00Z</updated>
            <summary>本文</summary>
          </entry>
        </feed>
        """
        entries = mr.parse_entries(xml)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].entry_id, "https://www.meti.go.jp/press/x.html")

    def test_latest_entry_updated(self):
        entries = [
            mr.Entry("1", "a", "u1", "2026-06-18T01:00:00Z", "a"),
            mr.Entry("2", "b", "u2", "2026-06-19T05:00:00Z", "b"),
        ]
        self.assertEqual(latest_entry_updated(entries), "2026-06-19T05:00:00Z")

    def test_entry_age_hours(self):
        now = datetime(2026, 6, 20, 5, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(entry_age_hours("2026-06-19T05:00:00Z", now), 24.0)

    def test_stale_threshold_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("METI_RSS_STALE_HOURS", None)
            self.assertEqual(stale_threshold_hours(), 168)

    def test_stale_threshold_rejects_too_small(self):
        with patch.dict(os.environ, {"METI_RSS_STALE_HOURS": "12"}):
            with self.assertRaises(mr.FeedSchemaError):
                stale_threshold_hours()

    def test_runtime_heartbeat_and_status(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            row = {c: "" for c in RUNTIME_HB_COLS}
            row.update(
                checked_at="2026-09-06T00:00:00Z",
                source="meti_rss",
                document_role="METI_NEWS_RELEASE_RSS_SENSOR",
                status="ok",
                http_status=200,
                content_hash="abc",
                record_count=25,
                candidate_count=0,
                latest_entry_updated="2026-09-05T00:00:00Z",
                entry_age_hours="24.0",
                fetch_failed="0",
                schema_changed="0",
                url=mr.FEED_URL,
                final_url=mr.FEED_URL,
            )
            write_runtime_heartbeat(
                row,
                healthy=True,
                fetch_success=True,
                base_dir=base,
            )

            hb = base / "heartbeat" / "2026-09.csv"
            self.assertTrue(hb.exists())

            status = json.loads((base / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(status["last_fetch_success_at"], "2026-09-06T00:00:00Z")
            self.assertEqual(status["last_healthy_at"], "2026-09-06T00:00:00Z")
            self.assertEqual(status["content_hash"], "abc")


if __name__ == "__main__":
    unittest.main()
