from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from src import state


class StateLineEndingsTest(unittest.TestCase):

    def test_append_heartbeat_uses_lf_line_endings(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "heartbeat.csv"

            state.append_heartbeat(
                target,
                [{
                    "source": "meti",
                    "status": "manual_ok",
                    "content_hash": "abc123",
                    "source_updated": "2026-06-16",
                    "record_count": 835,
                    "raw_path": "data/raw/meti/manual.pdf",
                }],
                now=datetime(
                    2026,
                    9,
                    25,
                    2,
                    3,
                    25,
                    tzinfo=timezone.utc,
                ),
            )

            payload = target.read_bytes()
            self.assertNotIn(b"\r", payload)
            self.assertEqual(payload.count(b"\n"), 2)


if __name__ == "__main__":
    unittest.main()
