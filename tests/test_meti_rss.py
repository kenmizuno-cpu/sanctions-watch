from __future__ import annotations

import csv
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from src.dashboard import CHANGE_COLS
from src.meti_rss import (
    FeedSchemaError,
    append_dashboard_rows,
    candidate_dashboard_row,
    classify,
    parse_entries,
)


ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>METI News</title>
  <entry>
    <id>tag:meti.go.jp,2026:1</id>
    <title>通常のニュースリリースです</title>
    <updated>2026-09-05T00:00:00+09:00</updated>
    <link rel="alternate" href="https://www.meti.go.jp/press/example1.html"/>
    <summary>一般的な政策発表です。</summary>
  </entry>
  <entry>
    <id>tag:meti.go.jp,2026:2</id>
    <title>外国ユーザーリストを改正しました</title>
    <updated>2026-09-05T01:00:00+09:00</updated>
    <link rel="alternate" href="https://www.meti.go.jp/press/example2.html"/>
    <summary>安全保障貿易管理に関する発表です。</summary>
  </entry>
</feed>
"""

RSS = """<?xml version="1.0"?>
<rss version="2.0">
  <channel>
    <title>METI</title>
    <item>
      <guid>g-1</guid>
      <title>安全保障貿易管理制度の一部を改正します</title>
      <link>https://www.meti.go.jp/press/example3.html</link>
      <pubDate>Sat, 05 Sep 2026 01:00:00 +0900</pubDate>
      <description>補完的輸出規制の見直しについて</description>
    </item>
  </channel>
</rss>
"""


class TestMetiRss(unittest.TestCase):
    def test_parse_atom(self):
        entries = parse_entries(ATOM)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[1].entry_id, "tag:meti.go.jp,2026:2")
        self.assertIn("example2.html", entries[1].url)

    def test_direct_candidate(self):
        c = classify(parse_entries(ATOM)[1])
        self.assertIsNotNone(c)
        self.assertEqual(c.confidence, "HIGH")
        self.assertIn("外国ユーザーリスト", c.reason)

    def test_irrelevant_is_not_candidate(self):
        self.assertIsNone(classify(parse_entries(ATOM)[0]))

    def test_contextual_candidate(self):
        c = classify(parse_entries(RSS)[0])
        self.assertIsNotNone(c)
        self.assertEqual(c.confidence, "REVIEW")

    def test_unknown_root_is_schema_error(self):
        with self.assertRaises(FeedSchemaError):
            parse_entries("<html><body>blocked</body></html>")

    def test_dashboard_row_contains_url(self):
        c = classify(parse_entries(ATOM)[1])
        row = candidate_dashboard_row(
            c,
            datetime(2026, 9, 5, 0, 0, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(len(row), 6)
        self.assertEqual(row[1], "経済産業省")
        self.assertEqual(row[2], "RSS更新候補（未確定）")
        self.assertTrue(row[5].startswith("https://"))

    def test_append_dashboard_schema_and_dedup(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "changes.csv"
            row = [
                "2026-09-05 09:00:00",
                "経済産業省",
                "RSS更新候補（未確定）",
                "外国ユーザーリストを改正しました",
                "HIGH: 直接一致",
                "https://www.meti.go.jp/press/example2.html",
            ]
            append_dashboard_rows([row], path=p)
            append_dashboard_rows([row], path=p)

            with p.open(encoding="utf-8", newline="") as f:
                rows = list(csv.reader(f))

            self.assertEqual(rows[0], CHANGE_COLS)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[1][5], row[5])


if __name__ == "__main__":
    unittest.main()

