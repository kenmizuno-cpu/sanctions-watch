"""財務省 原本レコード差分の回帰テスト。"""
from __future__ import annotations

import csv
import gzip
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import mof_record_diff as R  # noqa: E402


def make_row(**changes):
    row = {c: "" for c in R.EXPECTED_COLUMNS}
    row.update({
        "区分": "2",
        "番号": "002-000798",
        "告示日付": "2025.6.17",
        "告示番号": "160",
        "個人・団体": "個人",
        "氏名（日本語）": "アブバカル・スワレ",
        "氏名（英語）": "ABUBAKAR SWALLEH",
        "生年月日": "1992/1/13",
        "出生地（英語）": "Mengo, Uganda",
        "国籍（英語）": "Uganda",
        "旅券番号": "A00195974",
        "身分証番号": "CM920231090NZA",
        "国連参照番号": "QDi.436",
        "リスト掲載日": "2025/6/16",
    })
    row.update(changes)
    return row


def csv_bytes(rows, columns=None):
    cols = columns or R.EXPECTED_COLUMNS
    import io
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, lineterminator="\n")
    w.writeheader()
    for row in rows:
        w.writerow({c: row.get(c, "") for c in cols})
    return buf.getvalue().encode("utf-8-sig")


def write_raw(root: Path, name: str, rows):
    p = root / "data" / "raw" / "mof" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(gzip.compress(csv_bytes(rows), mtime=0))
    return p


class TestMofRecordDiff(unittest.TestCase):
    def test_identification_amendment_is_detected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            b = write_raw(root, "20260901T000000Z__shisantouketsu20260902.csv.gz", [make_row()])
            a = write_raw(root, "20260909T000000Z__shisantouketsu20260910.csv.gz", [
                make_row(**{
                    "生年月日": "1992/1/13；1993/5/9",
                    "身分証番号": "CM920231090NZA；Z15105123",
                })
            ])
            diff = R.compare_snapshots(R._parse_csv(b), R._parse_csv(a))
            self.assertEqual(len(diff.added), 0)
            self.assertEqual(len(diff.removed), 0)
            self.assertEqual(diff.amended_record_ids, {"002-000798"})
            fields = {c.field_name for c in diff.amended}
            self.assertEqual(fields, {"生年月日", "身分証番号"})
            self.assertEqual({c.un_reference for c in diff.amended}, {"QDi.436"})
            self.assertTrue(all(c.kind.startswith("情報改訂") for c in diff.amended))

    def test_format_only_changes_are_audit_only(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            b = write_raw(root, "20260901T000000Z__a.csv.gz", [make_row(**{
                "別名・別称（日本語）": "アリ; ハサン",
                "別名・別称（英語）": "Abdul Salam Hanafi",
                "称号（日本語）": "ムラー; マウラヴィ",
                "生年月日": "1969/1/1",
            })])
            a = write_raw(root, "20260909T000000Z__b.csv.gz", [make_row(**{
                "別名・別称（日本語）": "アリ； ハサン",
                "別名・別称（英語）": "ABDUL SALAM HANAFI",
                "称号（日本語）": "ムラー； マウラヴィ",
                "生年月日": "1969/１/１",
            })])
            diff = R.compare_snapshots(R._parse_csv(b), R._parse_csv(a))
            self.assertEqual(len(diff.amended), 4)
            self.assertEqual(len(diff.material_amended), 0)
            self.assertEqual(len(diff.cosmetic_amended), 4)
            self.assertEqual(diff.amended_record_ids, set())

    def test_aggregate_notice_field_is_not_double_counted(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            b = write_raw(root, "20260901T000000Z__a.csv.gz", [make_row(**{
                "外務省告示情報": "旧本文",
            })])
            a = write_raw(root, "20260909T000000Z__b.csv.gz", [make_row(**{
                "外務省告示情報": "新本文",
            })])
            diff = R.compare_snapshots(R._parse_csv(b), R._parse_csv(a))
            self.assertEqual(len(diff.amended), 1)
            self.assertEqual(diff.amended[0].impact, "集約原文（重複）")
            self.assertFalse(diff.amended[0].material)
            self.assertEqual(diff.amended_record_ids, set())


    def test_date_zero_padding_and_location_comma_are_cosmetic(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            b = write_raw(root, "20260901T000000Z__a.csv.gz", [make_row(**{
                "生年月日": "1994/10/28; 1990/05/24",
                "出生地（英語）": "Mir Bacha Kot District, Kabul Province, Afghanistan",
                "住所・所在地（都市その他の情報）（英語）": "Kunduz",
            })])
            a = write_raw(root, "20260909T000000Z__b.csv.gz", [make_row(**{
                "生年月日": "1994/10/28; 1990/５/24",
                "出生地（英語）": "Mir Bacha, Kot District, Kabul Province, Afghanistan",
                "住所・所在地（都市その他の情報）（英語）": "Kunduz,",
            })])
            diff = R.compare_snapshots(R._parse_csv(b), R._parse_csv(a))
            self.assertEqual(len(diff.amended), 3)
            self.assertEqual(len(diff.material_amended), 0)
            self.assertEqual(len(diff.cosmetic_amended), 3)

    def test_existing_information_moved_to_dedicated_column_is_audit_only(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            b = write_raw(root, "20260901T000000Z__a.csv.gz", [make_row(**{
                "称号（英語）": "",
                "国連参照番号": "",
                "外務省告示情報": "Title: Dr. Permanent ref QDi.431",
            })])
            a = write_raw(root, "20260909T000000Z__b.csv.gz", [make_row(**{
                "称号（英語）": "Dr.",
                "国連参照番号": "QDi.431",
                "外務省告示情報": "Title: Dr. Permanent ref QDi.431",
            })])
            diff = R.compare_snapshots(R._parse_csv(b), R._parse_csv(a))
            self.assertEqual(len(diff.amended), 2)
            self.assertEqual(len(diff.material_amended), 0)
            self.assertEqual(len(diff.structure_amended), 2)

    def test_real_style_un_reference_echo_is_audit_only(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            b = write_raw(root, "20260901T000000Z__a.csv.gz", [make_row(**{
                "国連参照番号": "",
                "外務省告示情報": "国連参照番号：QDi．431 その他の情報",
            })])
            a = write_raw(root, "20260909T000000Z__b.csv.gz", [make_row(**{
                "国連参照番号": "QDi.431",
                "外務省告示情報": "国連参照番号：QDi.431 その他の情報",
            })])
            diff = R.compare_snapshots(R._parse_csv(b), R._parse_csv(a))
            self.assertEqual(len(diff.amended), 2)
            self.assertEqual(len(diff.material_amended), 0)
            self.assertEqual(len(diff.structure_amended), 1)
            self.assertEqual(len(diff.aggregate_amended), 1)

    def test_passport_detail_contraction_is_audit_only_when_detail_survives_elsewhere(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            b = write_raw(root, "20260901T000000Z__a.csv.gz", [make_row(**{
                "旅券番号": "ウガンダ旅券A00195974（2029年12月16日に失効）",
                "外務省告示情報": "旅券番号：ウガンダ旅券A00195974（2029年12月16日に失効）",
            })])
            a = write_raw(root, "20260909T000000Z__b.csv.gz", [make_row(**{
                "旅券番号": "ウガンダ旅券A00195974",
                "外務省告示情報": "旅券番号：ウガンダ旅券A00195974（2029年12月16日に失効）",
            })])
            diff = R.compare_snapshots(R._parse_csv(b), R._parse_csv(a))
            self.assertEqual(len(diff.amended), 1)
            self.assertEqual(len(diff.material_amended), 0)
            self.assertEqual(len(diff.structure_amended), 1)

    def test_passport_detail_loss_remains_material_if_not_preserved_elsewhere(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            b = write_raw(root, "20260901T000000Z__a.csv.gz", [make_row(**{
                "旅券番号": "ウガンダ旅券A00195974（2029年12月16日に失効）",
                "外務省告示情報": "旧本文",
            })])
            a = write_raw(root, "20260909T000000Z__b.csv.gz", [make_row(**{
                "旅券番号": "ウガンダ旅券A00195974",
                "外務省告示情報": "新本文",
            })])
            diff = R.compare_snapshots(R._parse_csv(b), R._parse_csv(a))
            material = [c for c in diff.material_amended if c.field_name == "旅券番号"]
            self.assertEqual(len(material), 1)

    def test_passport_identifier_change_remains_material(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            b = write_raw(root, "20260901T000000Z__a.csv.gz", [make_row(**{
                "旅券番号": "ウガンダ旅券A00195974（2029年12月16日に失効）",
                "外務省告示情報": "旅券番号 A00195974",
            })])
            a = write_raw(root, "20260909T000000Z__b.csv.gz", [make_row(**{
                "旅券番号": "ウガンダ旅券B00999999",
                "外務省告示情報": "旅券番号 B00999999",
            })])
            diff = R.compare_snapshots(R._parse_csv(b), R._parse_csv(a))
            material = [c for c in diff.material_amended if c.field_name == "旅券番号"]
            self.assertEqual(len(material), 1)

    def test_weak_alias_promoted_to_strong_remains_material(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            b = write_raw(root, "20260901T000000Z__a.csv.gz", [make_row(**{
                "別名・別称（英語）": "TOM KIYURIGE",
                "確定に十分でない別名（英語）": "ISAAC MUPETA",
            })])
            a = write_raw(root, "20260909T000000Z__b.csv.gz", [make_row(**{
                "別名・別称（英語）": "TOM KIYURIGE; ISAAC MUPETA",
                "確定に十分でない別名（英語）": "",
            })])
            diff = R.compare_snapshots(R._parse_csv(b), R._parse_csv(a))
            self.assertEqual(len(diff.amended), 2)
            self.assertEqual(len(diff.material_amended), 2)
            self.assertEqual(len(diff.structure_amended), 0)

    def test_supplemental_whitespace_only_change_is_cosmetic(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            b = write_raw(root, "20260901T000000Z__a.csv.gz", [make_row(**{
                "その他の情報": "国連安全保障理事会 特別手配書",
            })])
            a = write_raw(root, "20260909T000000Z__b.csv.gz", [make_row(**{
                "その他の情報": "国連安全 保障理事会特別手配書",
            })])
            diff = R.compare_snapshots(R._parse_csv(b), R._parse_csv(a))
            self.assertEqual(len(diff.amended), 1)
            self.assertEqual(len(diff.material_amended), 0)
            self.assertEqual(len(diff.cosmetic_amended), 1)

    def test_administrative_amendment_is_not_silenced(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            b = write_raw(root, "20260901T000000Z__a.csv.gz", [make_row()])
            a = write_raw(root, "20260909T000000Z__b.csv.gz", [make_row(**{"告示日付": "2026.9.10"})])
            diff = R.compare_snapshots(R._parse_csv(b), R._parse_csv(a))
            self.assertEqual(len(diff.amended), 1)
            self.assertEqual(diff.amended[0].kind, "管理情報改訂（告示日付）")

    def test_source_id_change_can_match_by_un_reference(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            b = write_raw(root, "20260901T000000Z__a.csv.gz", [make_row()])
            a = write_raw(root, "20260909T000000Z__b.csv.gz", [make_row(**{"番号": "002-000999"})])
            diff = R.compare_snapshots(R._parse_csv(b), R._parse_csv(a))
            self.assertEqual(len(diff.added), 0)
            self.assertEqual(len(diff.removed), 0)
            self.assertEqual(len(diff.amended), 1)
            self.assertEqual(diff.amended[0].field_name, "番号")

    def test_added_removed_records_are_separate_from_amendment(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            b = write_raw(root, "20260901T000000Z__a.csv.gz", [make_row()])
            a = write_raw(root, "20260909T000000Z__b.csv.gz", [
                make_row(**{"番号": "777", "国連参照番号": "", "氏名（日本語）": "別人", "氏名（英語）": "OTHER PERSON"})
            ])
            diff = R.compare_snapshots(R._parse_csv(b), R._parse_csv(a))
            self.assertEqual(len(diff.added), 1)
            self.assertEqual(len(diff.removed), 1)
            self.assertEqual(len(diff.amended), 0)

    def test_schema_change_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p = root / "bad.csv.gz"
            bad_cols = R.EXPECTED_COLUMNS[:-1]
            p.write_bytes(gzip.compress(csv_bytes([make_row()], bad_cols), mtime=0))
            with self.assertRaises(R.RecordDiffError):
                R._parse_csv(p)

    def test_duplicate_source_id_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p = root / "dup.csv.gz"
            p.write_bytes(gzip.compress(csv_bytes([make_row(), make_row()]), mtime=0))
            with self.assertRaises(R.RecordDiffError):
                R._parse_csv(p)

    def test_end_to_end_backfill_and_no_duplicate_second_run(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_raw(root, "20260901T000000Z__shisantouketsu20260902.csv.gz", [make_row()])
            write_raw(root, "20260909T000000Z__shisantouketsu20260910.csv.gz", [
                make_row(**{"身分証番号": "CM920231090NZA；Z15105123"})
            ])

            first = R.run(root)
            self.assertTrue(first["source_changed"])
            self.assertEqual(first["amended"], 1)
            self.assertEqual(first["field_changes"], 1)
            self.assertEqual(first["cosmetic_changes"], 0)
            self.assertEqual(first["aggregate_changes"], 0)
            self.assertEqual(first["dashboard_rows"], 1)

            changes = root / "data" / "dashboard" / "changes.csv"
            with changes.open(encoding="utf-8", newline="") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["出所"], "財務省")
            self.assertEqual(rows[0]["種別"], "情報改訂（身分証番号）")
            self.assertIn("Z15105123", rows[0]["変更後"])

            state = root / "data" / "source_record_state" / "mof.json"
            self.assertTrue(state.exists())
            source_diff = root / "data" / "source_diff" / "mof_latest.csv"
            self.assertTrue(source_diff.exists())

            second = R.run(root)
            self.assertFalse(second["source_changed"])
            with changes.open(encoding="utf-8", newline="") as f:
                rows2 = list(csv.DictReader(f))
            self.assertEqual(len(rows2), 1)

    def test_history_gap_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_raw(root, "20260909T000000Z__b.csv.gz", [make_row()])
            state_path = root / "data" / "source_record_state" / "mof.json"
            state_path.parent.mkdir(parents=True)
            state_path.write_text('{"processed_raw":"data/raw/mof/missing.csv.gz"}', encoding="utf-8")
            with self.assertRaises(R.RecordDiffError):
                R.plan_transitions(root, R._load_state(root))


if __name__ == "__main__":
    unittest.main()
