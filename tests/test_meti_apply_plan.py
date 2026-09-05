from __future__ import annotations

import unittest

from src.meti_apply_plan import (
    ACTION_HOLD_COLLISION,
    ACTION_HOLD_WEAK,
    ACTION_READY_ADD,
    ACTION_READY_TAG,
    ACTION_NOOP,
    classify_key,
)

HASH = (
    "c632d657f9c13b1250d688394eb7c999"
    "56a994527cb56f4a77c86ed61ff1dbda"
)


def c(
    no,
    kind,
    name,
    *,
    invalid="",
    review="",
):
    return {
        "record_no": no,
        "record_primary": "PRIMARY",
        "kind": kind,
        "name": name,
        "source_record_id": (
            f"{HASH}:{no}:{kind}"
        ),
        "invalid_reason": invalid,
        "review_reason": review,
    }


class TestMetiApplyPlan(unittest.TestCase):
    def test_new_safe_name_ready_add(self):
        result = classify_key(
            source_hash=HASH,
            key="alpha",
            candidates=[
                c(1, "PRIMARY", "ALPHA"),
            ],
            master_row=None,
        )
        self.assertEqual(
            result["action"],
            ACTION_READY_ADD,
        )

    def test_existing_other_source_ready_tag(self):
        result = classify_key(
            source_hash=HASH,
            key="alpha",
            candidates=[
                c(1, "PRIMARY", "ALPHA"),
            ],
            master_row={
                "sources": "OFAC;財務省",
            },
        )
        self.assertEqual(
            result["action"],
            ACTION_READY_TAG,
        )

    def test_existing_meti_noop(self):
        result = classify_key(
            source_hash=HASH,
            key="alpha",
            candidates=[
                c(1, "PRIMARY", "ALPHA"),
            ],
            master_row={
                "sources": "経産省",
            },
        )
        self.assertEqual(
            result["action"],
            ACTION_NOOP,
        )

    def test_short_alias_is_held(self):
        result = classify_key(
            source_hash=HASH,
            key="htc",
            candidates=[
                c(
                    120,
                    "ALIAS:7",
                    "HTC",
                    review=(
                        "3文字と短く、照合時に"
                        "誤検知が多発する見込み（HTC）"
                    ),
                ),
            ],
            master_row=None,
        )
        self.assertEqual(
            result["action"],
            ACTION_HOLD_WEAK,
        )

    def test_cross_party_collision_is_held(self):
        result = classify_key(
            source_hash=HASH,
            key="advancedtechnologies",
            candidates=[
                c(
                    39,
                    "ALIAS:1",
                    "ADVANCED TECHNOLOGIES",
                ),
                c(
                    666,
                    "ALIAS:2",
                    "Advanced Technologies",
                ),
            ],
            master_row=None,
        )
        self.assertEqual(
            result["action"],
            ACTION_HOLD_COLLISION,
        )

    def test_same_party_duplicate_not_collision(self):
        result = classify_key(
            source_hash=HASH,
            key="ektelectronics",
            candidates=[
                c(
                    730,
                    "PRIMARY",
                    "EKT Electronics",
                ),
                c(
                    730,
                    "ALIAS:8",
                    "EKT ELECTRONICS",
                ),
            ],
            master_row=None,
        )
        self.assertEqual(
            result["action"],
            ACTION_READY_ADD,
        )

    def test_verified_long_alias_is_released(self):
        name = (
            "811th Research Institute, 8th Academy, "
            "China Aerospace Science and Technology "
            "Corporation (CASC) "
            "(中国航天科技集団公司第八研究院第八一一研究所)"
        )
        result = classify_key(
            source_hash=HASH,
            key="x",
            candidates=[
                c(
                    572,
                    "ALIAS:1",
                    name,
                    review=(
                        "121文字と異常に長い。"
                        "複数名が1セルに連結された疑い"
                    ),
                ),
            ],
            master_row=None,
        )
        self.assertEqual(
            result["action"],
            ACTION_READY_ADD,
        )
        self.assertEqual(
            result["resolution"],
            "SOURCE_VERIFIED_LONG_ALIAS",
        )


if __name__ == "__main__":
    unittest.main()

