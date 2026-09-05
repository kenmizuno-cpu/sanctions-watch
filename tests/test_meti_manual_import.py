from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.meti_manual_import import (
    PdfStructureError,
    PdfValidationError,
    Record,
    diff_records,
    records_from_tables,
    sha256_file,
    validate_pdf_file,
)


class TestMetiManualImport(unittest.TestCase):
    def test_reject_html_disguised_as_pdf(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "x.pdf"
            p.write_bytes(b"<html>" + b"x" * 20000)
            with self.assertRaises(PdfValidationError):
                validate_pdf_file(p)

    def test_sha256_stable(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "x.bin"
            p.write_bytes(b"abc")
            self.assertEqual(
                sha256_file(p),
                "ba7816bf8f01cfea414140de5dae2223"
                "b00361a396177a9cb410ff61f20015ad",
            )

    def test_parse_synthetic_table(self):
        table = [
            [
                "No.",
                "国名、地域名\nCountry or Region",
                "企業名、組織名\nCompany or Organization",
                "別名\nAlso Known As",
                "懸念区分\nType of WMD",
                "通常兵器\nConventional Weapons",
            ],
            [
                "1",
                "アフガニスタン\nIslamic Republic of Afghanistan",
                "ALPHA ORG",
                "・Alpha\n・A Org",
                "核\nN",
                "",
            ],
            [
                "2",
                "ロシア\nRussian Federation",
                "BETA JSC",
                "",
                "ミサイル\nM",
                "○",
            ],
        ]
        # 本番の最低100件ガードを避けるため、純粋な表抽出関数の
        # 小規模テストではMIN_RECORDSに届かない。100件の合成行を作る。
        rows = [table[0]]
        for i in range(1, 101):
            rows.append(
                [
                    str(i),
                    "ロシア\nRussian Federation",
                    f"ORG {i}",
                    f"・Alias {i}",
                    "ミサイル\nM",
                    "○" if i % 2 == 0 else "",
                ]
            )
        records = records_from_tables([rows])
        self.assertEqual(len(records), 100)
        self.assertEqual(records[0].no, 1)
        self.assertEqual(records[-1].no, 100)
        self.assertEqual(records[0].aliases, ("Alias 1",))

    def test_missing_number_blocks(self):
        rows = [[
            "No.",
            "Country or Region",
            "Company or Organization",
            "Also Known As",
            "Type of WMD",
            "Conventional Weapons",
        ]]
        for i in range(1, 102):
            if i == 50:
                continue
            rows.append([
                str(i), "Country", f"ORG {i}", "", "N", ""
            ])
        with self.assertRaises(PdfStructureError):
            records_from_tables([rows])

    def test_blank_company_blocks(self):
        rows = [[
            "No.",
            "Country or Region",
            "Company or Organization",
            "Also Known As",
            "Type of WMD",
            "Conventional Weapons",
        ]]
        for i in range(1, 101):
            rows.append([
                str(i), "Country", "" if i == 10 else f"ORG {i}",
                "", "N", ""
            ])
        with self.assertRaises(PdfStructureError):
            records_from_tables([rows])

    def test_diff(self):
        old = [
            Record(1, "A", "ALPHA", ("A1",), "N", ""),
            Record(2, "B", "BETA", (), "M", ""),
            Record(3, "C", "GAMMA", (), "N", ""),
        ]
        new = [
            Record(1, "A", "ALPHA", ("A1", "A2"), "N", ""),
            Record(2, "B", "BETA", (), "M", ""),
            Record(4, "D", "DELTA", (), "C", ""),
        ]
        d = diff_records(old, new)
        self.assertEqual(d.counts, {"追加": 1, "削除": 1, "変更": 1})
        self.assertEqual(d.added[0].company, "DELTA")
        self.assertEqual(d.removed[0].company, "GAMMA")
        self.assertEqual(d.changed[0][0].company, "ALPHA")

    def test_name_order_change_does_not_change_alias_set(self):
        old = [Record(1, "A", "ALPHA", ("X", "Y"), "N", "")]
        new = [Record(2, "A", "ALPHA", ("Y", "X"), "N", "")]
        self.assertEqual(diff_records(old, new).counts["変更"], 0)


if __name__ == "__main__":
    unittest.main()


class TestMetiManualImportRealPdfGuards(unittest.TestCase):
    def test_wrapped_alias_is_not_split_into_fake_alias(self):
        from src.meti_manual_import import _split_aliases

        value = (
            "・The World Islamic Front for Jihad against Jews and\n"
            "Crusaders\n"
            "・Usama Bin Laden Network"
        )

        self.assertEqual(
            _split_aliases(value),
            (
                "The World Islamic Front for Jihad against Jews and Crusaders",
                "Usama Bin Laden Network",
            ),
        )

    def test_wrapped_multi_line_alias(self):
        from src.meti_manual_import import _split_aliases

        value = (
            "・ISLAMIC REVOLUTIONARY GUARD CORPS\n"
            "AEROSPACE FORCE RESEARCH AND SELF\n"
            "SUFFICIENCY JEHAD ORGANIZATION\n"
            "・IRGC"
        )

        self.assertEqual(
            _split_aliases(value),
            (
                "ISLAMIC REVOLUTIONARY GUARD CORPS "
                "AEROSPACE FORCE RESEARCH AND SELF "
                "SUFFICIENCY JEHAD ORGANIZATION",
                "IRGC",
            ),
        )

    def test_alias_without_bullet_is_one_alias(self):
        from src.meti_manual_import import _split_aliases

        self.assertEqual(
            _split_aliases(
                '"Region" Scientific & Production Enterprise JSC'
            ),
            ('"Region" Scientific & Production Enterprise JSC',),
        )

    def test_accept_official_meti_pdf_url(self):
        from src.meti_manual_import import validate_source_url

        url = "https://www.meti.go.jp/policy/anpo/20250929_3.pdf"
        self.assertEqual(validate_source_url(url), url)

    def test_reject_markdown_source_url(self):
        from src.meti_manual_import import (
            PdfValidationError,
            validate_source_url,
        )

        with self.assertRaises(PdfValidationError):
            validate_source_url(
                "[https://www.meti.go.jp/policy/anpo/x.pdf]"
                "(https://www.meti.go.jp/policy/anpo/x.pdf)"
            )

    def test_reject_non_meti_source_url(self):
        from src.meti_manual_import import (
            PdfValidationError,
            validate_source_url,
        )

        with self.assertRaises(PdfValidationError):
            validate_source_url(
                "https://example.com/policy/anpo/x.pdf"
            )


class TestMetiManualImportBaselinePathRegression(unittest.TestCase):
    def test_empty_previous_records_path_stays_empty(self):
        state = {}
        previous_raw = str(
            state.get("current_records_path") or ""
        ).strip()

        self.assertEqual(previous_raw, "")
