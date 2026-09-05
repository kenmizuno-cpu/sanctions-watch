from __future__ import annotations

import unittest

from src import meti_apply as A
from src import meti_apply_plan as P


def master_row(
    name: str,
    sources: str,
):
    return {
        "match_key": name.lower(),
        "display_name": name,
        "status": "有効",
        "risk_type": "制裁リスト",
        "risk_level": "高",
        "first_seen_ms": "1",
        "last_updated_ms": "1",
        "sources": sources,
        "categories": "",
        "remark": "制裁リスト",
        "invalid_reason": "",
        "review_flag": "",
        "variants": f'["{name}"]',
    }


def plan_row(
    action: str,
    key: str,
    name: str,
    *,
    kind: str = "PRIMARY",
    party_nos: str = "1",
    review_reason: str = "",
    resolution: str = "",
):
    return {
        "action": action,
        "match_key": key,
        "name": name,
        "record_no": "1",
        "record_primary": name,
        "kind": kind,
        "party_nos": party_nos,
        "supporting_names": f'["{name}"]',
        "source_record_ids": "hash:1:PRIMARY",
        "review_reason": review_reason,
        "resolution": resolution,
        "existing_master_sources": "",
        "existing_master_display_name": "",
    }


def legacy_row(
    key: str,
    name: str,
):
    return {
        "action": "HOLD_LEGACY_NOT_IN_CURRENT",
        "match_key": key,
        "display_name": name,
        "sources": "経産省",
        "status": "有効",
        "remark": "制裁リスト（経産省）",
    }


class TestMetiApplyExecutor(unittest.TestCase):
    def test_simulate_add_tag_noop_hold_and_legacy(self):
        master = {
            "beta": master_row(
                "BETA",
                "OFAC",
            ),
            "gamma": master_row(
                "GAMMA",
                "経産省",
            ),
            "legacy": master_row(
                "LEGACY",
                "経産省",
            ),
        }

        rows = [
            plan_row(
                P.ACTION_READY_ADD,
                "alpha",
                "ALPHA",
            ),
            plan_row(
                P.ACTION_READY_TAG,
                "beta",
                "BETA",
            ),
            plan_row(
                P.ACTION_NOOP,
                "gamma",
                "GAMMA",
            ),
            plan_row(
                P.ACTION_HOLD_WEAK,
                "ec",
                "EC",
                kind="ALIAS:1",
                review_reason="2文字と短く、照合時に誤検知が多発する見込み（EC）",
            ),
            plan_row(
                P.ACTION_HOLD_COLLISION,
                "shared",
                "SHARED",
                kind="ALIAS:1",
                party_nos="10;20",
                review_reason="multi party",
            ),
        ]

        sim = A._simulate(
            rows=rows,
            legacy_rows=[
                legacy_row(
                    "legacy",
                    "LEGACY",
                )
            ],
            master=master,
            merge_ts_ms=1234567890000,
        )

        self.assertEqual(
            len(sim["diff"].added),
            1,
        )
        self.assertEqual(
            len(sim["diff"].changed),
            1,
        )
        self.assertEqual(
            len(sim["diff"].removed),
            0,
        )
        self.assertIn(
            "alpha",
            sim["after"],
        )
        self.assertIn(
            "経産省",
            sim["after"]["beta"][
                "sources"
            ],
        )
        self.assertEqual(
            A._string_row(
                sim["before"]["legacy"]
            ),
            A._string_row(
                sim["after"]["legacy"]
            ),
        )

    def test_simulate_is_deterministic_with_fixed_timestamp(self):
        master = {
            "beta": master_row(
                "BETA",
                "OFAC",
            ),
        }
        rows = [
            plan_row(
                P.ACTION_READY_ADD,
                "alpha",
                "ALPHA",
            ),
            plan_row(
                P.ACTION_READY_TAG,
                "beta",
                "BETA",
            ),
        ]

        a = A._simulate(
            rows=rows,
            legacy_rows=[],
            master=master,
            merge_ts_ms=1234567890000,
        )
        b = A._simulate(
            rows=rows,
            legacy_rows=[],
            master=master,
            merge_ts_ms=1234567890000,
        )

        self.assertEqual(a["after"], b["after"])
        self.assertEqual(
            a["after"]["alpha"]["first_seen_ms"],
            1234567890000,
        )
        self.assertEqual(
            a["after"]["alpha"]["last_updated_ms"],
            1234567890000,
        )
        self.assertEqual(
            a["after"]["beta"]["last_updated_ms"],
            1234567890000,
        )

    def test_summary_merge_timestamp_is_stable(self):
        summary = {
            "generated_at": "2026-09-05T20:38:46Z",
        }
        a = A._summary_merge_ts_ms(summary)
        b = A._summary_merge_ts_ms(summary)
        self.assertEqual(a, b)
        self.assertEqual(a, 1788640726000)

    def test_summary_merge_timestamp_requires_timezone(self):
        with self.assertRaises(A.ApplyError):
            A._summary_merge_ts_ms({
                "generated_at": "2026-09-05T20:38:46",
            })

    def test_unknown_action_blocks(self):
        master = {}
        rows = [
            plan_row(
                "UNKNOWN",
                "alpha",
                "ALPHA",
            )
        ]
        with self.assertRaises(
            A.ApplyError
        ):
            A._assert_plan_semantics(
                rows=rows,
                legacy_rows=[],
                master=master,
            )

    def test_ready_add_existing_blocks(self):
        master = {
            "alpha": master_row(
                "ALPHA",
                "OFAC",
            )
        }
        with self.assertRaises(
            A.ApplyError
        ):
            A._assert_plan_semantics(
                rows=[
                    plan_row(
                        P.ACTION_READY_ADD,
                        "alpha",
                        "ALPHA",
                    )
                ],
                legacy_rows=[],
                master=master,
            )

    def test_ready_tag_missing_blocks(self):
        with self.assertRaises(
            A.ApplyError
        ):
            A._assert_plan_semantics(
                rows=[
                    plan_row(
                        P.ACTION_READY_TAG,
                        "alpha",
                        "ALPHA",
                    )
                ],
                legacy_rows=[],
                master={},
            )

    def test_noop_without_meti_blocks(self):
        master = {
            "alpha": master_row(
                "ALPHA",
                "OFAC",
            )
        }
        with self.assertRaises(
            A.ApplyError
        ):
            A._assert_plan_semantics(
                rows=[
                    plan_row(
                        P.ACTION_NOOP,
                        "alpha",
                        "ALPHA",
                    )
                ],
                legacy_rows=[],
                master=master,
            )

    def test_weak_primary_blocks(self):
        with self.assertRaises(
            A.ApplyError
        ):
            A._assert_plan_semantics(
                rows=[
                    plan_row(
                        P.ACTION_HOLD_WEAK,
                        "ec",
                        "EC",
                        kind="PRIMARY",
                    )
                ],
                legacy_rows=[],
                master={},
            )

    def test_collision_single_party_blocks(self):
        with self.assertRaises(
            A.ApplyError
        ):
            A._assert_plan_semantics(
                rows=[
                    plan_row(
                        P.ACTION_HOLD_COLLISION,
                        "shared",
                        "SHARED",
                        kind="ALIAS:1",
                        party_nos="10",
                    )
                ],
                legacy_rows=[],
                master={},
            )

    def test_legacy_requires_existing_meti(self):
        master = {
            "legacy": master_row(
                "LEGACY",
                "OFAC",
            )
        }
        with self.assertRaises(
            A.ApplyError
        ):
            A._assert_plan_semantics(
                rows=[
                    plan_row(
                        P.ACTION_READY_ADD,
                        "alpha",
                        "ALPHA",
                    )
                ],
                legacy_rows=[
                    legacy_row(
                        "legacy",
                        "LEGACY",
                    )
                ],
                master=master,
            )

    def test_unresolved_review_on_ready_blocks(self):
        with self.assertRaises(
            A.ApplyError
        ):
            A._assert_plan_semantics(
                rows=[
                    plan_row(
                        P.ACTION_READY_ADD,
                        "longname",
                        "LONGNAME",
                        review_reason="要レビュー",
                    )
                ],
                legacy_rows=[],
                master={},
            )

    def test_source_verified_review_on_ready_allowed(self):
        result = A._assert_plan_semantics(
            rows=[
                plan_row(
                    P.ACTION_READY_ADD,
                    "longname",
                    "LONGNAME",
                    review_reason="要レビュー",
                    resolution="SOURCE_VERIFIED_LONG_ALIAS",
                )
            ],
            legacy_rows=[],
            master={},
        )
        self.assertEqual(
            result["counts"][
                P.ACTION_READY_ADD
            ],
            1,
        )


if __name__ == "__main__":
    unittest.main()

