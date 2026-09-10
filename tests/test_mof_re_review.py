"""財務省 再審査キュー生成の回帰テスト。"""
from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import mof_re_review as R  # noqa: E402


def row(**changes):
    base = {
        "検知日時": "2026-09-10 10:54:53",
        "前回原本": "20260901T124902Z__shisantouketsu20260902.csv.gz",
        "今回原本": "20260909T125053Z__shisantouketsu20260910.csv.gz",
        "種別": "情報改訂",
        "番号": "002-000798",
        "国連参照番号": "QDi.436",
        "受取人名": "アブバカ・スワレ",
        "項目": "",
        "変更前": "",
        "変更後": "",
        "影響区分": "識別・スクリーニング",
    }
    base.update(changes)
    return base


def write_source(root: Path, rows, headers=None):
    p = root / "data" / "source_diff" / "mof_latest.csv"
    p.parent.mkdir(parents=True, exist_ok=True)
    cols = headers or R.SOURCE_DIFF_COLS
    with p.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, lineterminator="\n")
        w.writeheader()
        for x in rows:
            w.writerow({c: x.get(c, "") for c in cols})
    return p


class TestMofReReview(unittest.TestCase):
    def test_weak_to_strong_and_id_change_make_one_high_case(self):
        rows = [
            row(**{"項目": "別名・別称（英語）", "変更前": "TOM KIYURIGE", "変更後": "TOM KIYURIGE; ISAAC MUPETA"}),
            row(**{"項目": "確定に十分でない別名（英語）", "変更前": "ISAAC MUPETA", "変更後": ""}),
            row(**{"項目": "身分証番号", "変更前": "CM920231090NZA", "変更後": "CM920231090NZA; Z15105123"}),
        ]
        candidates = R.build_candidates(rows)
        self.assertEqual(len(candidates), 1)
        c = candidates[0]
        self.assertEqual(c.priority, "高")
        self.assertIn("Weak Alias→Strong Alias昇格", c.reasons)
        self.assertIn("身分証番号変更", c.reasons)
        self.assertIn("確定に十分でない別名（英語）", c.fields)

    def test_un_reference_only_does_not_trigger(self):
        candidates = R.build_candidates([
            row(**{"番号": "002-000790", "国連参照番号": "QDi.431", "受取人名": "サナウッラー・ガファーリー", "項目": "国連参照番号", "変更前": "", "変更後": "QDi.431"})
        ])
        self.assertEqual(candidates, [])

    def test_weak_alias_only_does_not_trigger(self):
        candidates = R.build_candidates([
            row(**{"項目": "確定に十分でない別名（英語）", "変更前": "OLD WEAK", "変更後": "NEW WEAK"})
        ])
        self.assertEqual(candidates, [])

    def test_dob_change_is_medium_case(self):
        candidates = R.build_candidates([
            row(**{"項目": "生年月日", "変更前": "1992/1/13", "変更後": "1992/1/13; 1993/3/16"})
        ])
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].priority, "中")
        self.assertIn("生年月日変更", candidates[0].reasons)

    def test_non_screening_impacts_do_not_trigger(self):
        candidates = R.build_candidates([
            row(**{"項目": "旅券番号", "変更前": "A001", "変更後": "A001", "影響区分": "列再構成（既知情報）"}),
            row(**{"項目": "告示日付", "変更前": "old", "変更後": "new", "影響区分": "管理"}),
        ])
        self.assertEqual(candidates, [])

    def test_write_queue_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            rows = [row(**{"項目": "身分証番号", "変更前": "A", "変更後": "A; B"})]
            write_source(root, rows)
            first = R.run(root)
            second = R.run(root)
            self.assertEqual(first["new_cases"], 1)
            self.assertEqual(second["new_cases"], 0)
            with (root / "data" / "dashboard" / "re_review.csv").open(encoding="utf-8", newline="") as f:
                saved = list(csv.reader(f))
            self.assertEqual(len(saved), 2)

    def test_schema_change_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bad_headers = R.SOURCE_DIFF_COLS[:-1]
            write_source(root, [row()], headers=bad_headers)
            with self.assertRaises(R.ReReviewError):
                R.run(root)


if __name__ == "__main__":
    unittest.main()
