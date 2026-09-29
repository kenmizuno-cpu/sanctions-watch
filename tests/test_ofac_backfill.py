"""OFAC初回master同期を公式更新と取り違えないための回帰テスト。"""

import unittest

from src import master as M
from src import notify
from src import watch


class OfacBackfillTest(unittest.TestCase):
    def test_rollout_labels_only_names_already_in_strong_source_index(self):
        diff = M.Diff(source="OFAC", added=[
            {"key": "old-strong", "name": "OLD STRONG", "remark": "SDN"},
            {"key": "old-weak", "name": "OLD WEAK", "remark": "SDN"},
            {"key": "new", "name": "NEW NAME", "remark": "SDN"},
        ])
        history = {
            ("SDN", "1", "OLD STRONG"): {
                "match_key": "old-strong", "party_current": "1",
                "alias_current": "1", "low_quality": "0",
            },
            ("SDN", "2", "OLD WEAK"): {
                "match_key": "old-weak", "party_current": "1",
                "alias_current": "1", "low_quality": "1",
            },
        }

        watch._classify_ofac_rollout_additions(
            diff,
            watch._existing_strong_names(history),
            rollout_pending=True,
            index_baseline=False,
        )

        self.assertEqual([r[1] for r in M.diff_rows([diff])], [
            "初回同期（Advanced XML）", "追加", "追加",
        ])
        self.assertEqual(diff.counts, {
            "追加": 2, "削除": 0, "変更": 0, "初回同期": 1,
        })
        self.assertIn("追加 2 / 初回同期 1", M.render_markdown([diff]))

    def test_first_source_baseline_is_backfill_and_later_run_is_not(self):
        initial = M.Diff(source="OFAC", added=[
            {"key": "a", "name": "ALPHA", "remark": "SDN"},
        ])
        watch._classify_ofac_rollout_additions(
            initial, set(), rollout_pending=True, index_baseline=True,
        )
        self.assertEqual(M.diff_rows([initial])[0][1], "初回同期（Advanced XML）")

        later = M.Diff(source="OFAC", added=[
            {"key": "a", "name": "ALPHA", "remark": "SDN"},
        ])
        watch._classify_ofac_rollout_additions(
            later, {"a"}, rollout_pending=False, index_baseline=False,
        )
        self.assertEqual(M.diff_rows([later])[0][1], "追加")

    def test_alert_names_backfill_without_claiming_official_addition(self):
        self.assertIn(
            "初回同期 4721",
            notify.diff_heading(added=0, removed=0, changed=0, backfilled=4721),
        )
        self.assertNotIn(
            "制裁リストに差分を検出",
            notify.diff_heading(added=0, removed=0, changed=0, backfilled=4721),
        )
        self.assertIn(
            "追加 2",
            notify.diff_heading(added=2, removed=0, changed=0, backfilled=1),
        )


if __name__ == "__main__":
    unittest.main()
