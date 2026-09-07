import unittest

from src.normalize import match_key
from src.screening import (
    DECISION_HIT,
    DECISION_NO_HIT,
    DECISION_REVIEW,
    MATCH_EXACT,
    MATCH_NONE,
    MATCH_PARENTHESIS_RELAXED,
    build_screening_index,
    screen_name,
    secondary_collision_groups,
    secondary_screening_key,
)


def row(
    name,
    status="有効",
    sources="財務省",
):
    return {
        "match_key": match_key(name),
        "display_name": name,
        "status": status,
        "sources": sources,
        "categories": "",
    }


class ScreeningTest(unittest.TestCase):

    def test_secondary_does_not_change_canonical(self):
        a = "LEADER (HONG KONG) INTERNATIONAL"
        b = "LEADER HONG KONG INTERNATIONAL"

        self.assertNotEqual(
            match_key(a),
            match_key(b),
        )

        self.assertEqual(
            secondary_screening_key(a),
            secondary_screening_key(b),
        )

    def test_fullwidth_parentheses_are_absorbed(self):
        self.assertEqual(
            secondary_screening_key(
                "スーパーチップ（HK）有限会社"
            ),
            secondary_screening_key(
                "スーパーチップ HK 有限会社"
            ),
        )

    def test_exact_has_priority_over_relaxed_collision(self):
        rows = [
            row("PCC (UK)", sources="OFAC"),
            row("PCC UK", sources="OFAC"),
        ]

        idx = build_screening_index(
            rows
        )

        result = screen_name(
            "PCC (UK)",
            idx,
        )

        self.assertEqual(
            result["decision"],
            DECISION_HIT,
        )
        self.assertEqual(
            result["match_type"],
            MATCH_EXACT,
        )
        self.assertEqual(
            len(result["candidate_keys"]),
            1,
        )

    def test_unique_relaxed_match_is_hit(self):
        rows = [
            row(
                "LEADER HONG KONG INTERNATIONAL"
            )
        ]

        idx = build_screening_index(
            rows
        )

        result = screen_name(
            "LEADER (HONG KONG) INTERNATIONAL",
            idx,
        )

        self.assertEqual(
            result["decision"],
            DECISION_HIT,
        )
        self.assertEqual(
            result["match_type"],
            MATCH_PARENTHESIS_RELAXED,
        )
        self.assertEqual(
            result["candidate_keys"],
            [
                match_key(
                    "LEADER HONG KONG INTERNATIONAL"
                )
            ],
        )

    def test_relaxed_multiple_canonical_records_is_review(self):
        rows = [
            row(
                "Riyadus-Salikhin Reconnaissance "
                "and Sabotage battalion of Shahids "
                "(martyrs)",
                sources="OFAC",
            ),
            row(
                "Riyadus-Salikhin Reconnaissance "
                "and Sabotage battalion of Shahids "
                "martyrs",
                sources="財務省",
            ),
        ]

        idx = build_screening_index(
            rows
        )

        result = screen_name(
            "Riyadus-Salikhin Reconnaissance "
            "and Sabotage battalion of Shahids "
            "(martyrs))",
            idx,
        )

        self.assertEqual(
            result["decision"],
            DECISION_REVIEW,
        )
        self.assertEqual(
            result["match_type"],
            MATCH_PARENTHESIS_RELAXED,
        )
        self.assertEqual(
            len(result["candidate_keys"]),
            2,
        )

    def test_inactive_record_not_screened(self):
        rows = [
            row(
                "LEGACY BROKEN NAME)",
                status="無効",
                sources="",
            )
        ]

        idx = build_screening_index(
            rows
        )

        result = screen_name(
            "LEGACY BROKEN NAME",
            idx,
        )

        self.assertEqual(
            result["decision"],
            DECISION_NO_HIT,
        )
        self.assertEqual(
            result["match_type"],
            MATCH_NONE,
        )

    def test_no_hit(self):
        idx = build_screening_index(
            [
                row("ALPHA CORP")
            ]
        )

        result = screen_name(
            "COMPLETELY DIFFERENT NAME",
            idx,
        )

        self.assertEqual(
            result["decision"],
            DECISION_NO_HIT,
        )

    def test_collision_inventory(self):
        rows = [
            row("PCC (UK)"),
            row("PCC UK"),
            row("ALPHA LTD"),
        ]

        idx = build_screening_index(
            rows
        )

        collisions = (
            secondary_collision_groups(
                idx
            )
        )

        self.assertEqual(
            len(collisions),
            1,
        )


if __name__ == "__main__":
    unittest.main()
