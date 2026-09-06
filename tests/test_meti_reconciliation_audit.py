from __future__ import annotations

import unittest

from src import meti_apply_plan as P
from src.meti_reconciliation_audit import (
    ROOT,
    audit,
)


BASELINE_SOURCE_HASH = (
    "c632d657f9c13b1250d688394eb7c999"
    "56a994527cb56f4a77c86ed61ff1dbda"
)


class TestMetiReconciliationAudit(
    unittest.TestCase
):
    @classmethod
    def setUpClass(cls):
        cls.result = audit(
            root=ROOT
        )

    def test_approved_baseline_complete(self):
        r = self.result

        self.assertEqual(
            r["status"],
            "PASS",
        )
        self.assertEqual(
            r["source_hash"],
            BASELINE_SOURCE_HASH,
        )
        self.assertEqual(
            r["record_count"],
            835,
        )
        self.assertEqual(
            r["parsed_record_count"],
            835,
        )
        self.assertEqual(
            r["evidence_record_count"],
            835,
        )
        self.assertEqual(
            r["source_token_count"],
            2250,
        )
        self.assertEqual(
            r["primary_outcome_count"],
            835,
        )
        self.assertEqual(
            r["primary_hold_count"],
            0,
        )
        self.assertEqual(
            r["actionable_master_count"],
            2208,
        )

    def test_baseline_action_counts(self):
        counts = self.result[
            "action_counts"
        ]

        expected = {
            P.ACTION_READY_ADD: 314,
            P.ACTION_READY_TAG: 432,
            P.ACTION_NOOP: 1462,
            P.ACTION_HOLD_WEAK: 22,
            P.ACTION_HOLD_COLLISION: 2,
            P.ACTION_HOLD_REVIEW: 0,
            P.ACTION_HOLD_INVALID: 0,
        }

        for action, value in (
            expected.items()
        ):
            self.assertEqual(
                counts.get(
                    action,
                    0,
                ),
                value,
            )

    def test_primary_outcomes(self):
        counts = self.result[
            "primary_action_counts"
        ]

        self.assertEqual(
            counts.get(
                P.ACTION_READY_ADD,
                0,
            ),
            234,
        )
        self.assertEqual(
            counts.get(
                P.ACTION_READY_TAG,
                0,
            ),
            165,
        )
        self.assertEqual(
            counts.get(
                P.ACTION_NOOP,
                0,
            ),
            436,
        )

        self.assertEqual(
            sum(
                counts.values()
            ),
            835,
        )


if __name__ == "__main__":
    unittest.main()
