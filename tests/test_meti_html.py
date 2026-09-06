from __future__ import annotations

import unittest

from src.fetch import Fetched
from src.meti_html import (
    SensorFetchError,
    SensorSchemaError,
    _validate_fetched,
    law_semantic_text,
    parse_press_entries,
)
from src.meti_rss import classify


PRESS_HTML = """
<html>
<head>
<title>ニュースリリース 対外経済カテゴリー一覧</title>
</head>
<body>
<h1>ニュースリリース</h1>

<a href="/press/2025/09/20250929006/20250929006.html">
大量破壊兵器等の懸念又は通常兵器の開発等、
取引状況等の確認を要する外国・地域所在団体の
情報を提供する「外国ユーザーリスト」を改正しました
</a>

<a href="/press/2026/06/20260619003/20260619003.html">
通常のニュースリリースです
</a>

<a href="/press/archive.html">
アーカイブ
</a>
</body>
</html>
"""


LAW_HTML = """
<html>
<head>
<title>改正情報</title>
</head>
<body>
<h1>改正情報</h1>
<h2>外国ユーザーリストの改正について</h2>
<p>令和7年9月29日 改正</p>
</body>
</html>
"""


class TestMetiHtml(unittest.TestCase):
    def test_press_release_extraction(self):
        entries = parse_press_entries(
            PRESS_HTML
        )

        self.assertEqual(
            len(entries),
            2,
        )

        self.assertEqual(
            entries[0].published,
            "2025-09-29",
        )

    def test_press_direct_candidate(self):
        entries = parse_press_entries(
            PRESS_HTML
        )

        candidate = classify(
            entries[0]
        )

        self.assertIsNotNone(
            candidate
        )
        self.assertEqual(
            candidate.confidence,
            "HIGH",
        )

    def test_irrelevant_press_not_candidate(self):
        entries = parse_press_entries(
            PRESS_HTML
        )

        self.assertIsNone(
            classify(entries[1])
        )

    def test_law_page_schema(self):
        text = law_semantic_text(
            LAW_HTML
        )

        self.assertIn(
            "外国ユーザーリスト",
            text,
        )
        self.assertIn(
            "改正",
            text,
        )

    def test_http_202_empty_is_fetch_error(self):
        fetched = Fetched(
            url="https://www.meti.go.jp/example.html",
            body=b"",
            http_status=202,
            headers={
                "Content-Type": "text/html",
            },
        )

        with self.assertRaises(
            SensorFetchError
        ):
            _validate_fetched(
                fetched
            )

    def test_http_200_non_html_is_schema_error(self):
        fetched = Fetched(
            url="https://www.meti.go.jp/example.html",
            body=b"not html",
            http_status=200,
            headers={
                "Content-Type": "application/json",
            },
        )

        with self.assertRaises(
            SensorSchemaError
        ):
            _validate_fetched(
                fetched
            )


    def test_law_page_missing_marker_blocks(self):
        with self.assertRaises(
            SensorSchemaError
        ):
            law_semantic_text(
                "<html><body>"
                "通常ページ"
                "</body></html>"
            )


if __name__ == "__main__":
    unittest.main()
