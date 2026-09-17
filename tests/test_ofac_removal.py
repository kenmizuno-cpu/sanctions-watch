from __future__ import annotations

import csv
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from src import ofac_removal as R
from src import master as M


SNAPSHOT_HASH = "a" * 64
OFFICIAL_URL = "https://ofac.treasury.gov/recent-actions/20260916"


def history_rows() -> dict[tuple[str, str, str], dict]:
    return {
        ("SDN", "100", "ALPHA"): {
            "list": "SDN",
            "party_id": "100",
            "name": "ALPHA",
            "match_key": "alpha",
            "alias_current": "0",
            "party_current": "0",
            "low_quality": "0",
        },
        ("SDN", "200", "BETA"): {
            "list": "SDN",
            "party_id": "200",
            "name": "BETA",
            "match_key": "beta",
            "alias_current": "0",
            "party_current": "0",
            "low_quality": "0",
        },
        ("SDN", "300", "GAMMA"): {
            "list": "SDN",
            "party_id": "300",
            "name": "GAMMA",
            "match_key": "gamma",
            "alias_current": "0",
            "party_current": "0",
            "low_quality": "0",
        },
    }


def approval_row(**overrides) -> dict:
    row = {
        "list": "SDN",
        "snapshot_sha256": SNAPSHOT_HASH,
        "party_id": "100",
        "party_name": "ALPHA",
        "decision": "APPROVED",
        "approved_by": "reviewer",
        "approved_at": "2026-09-17T00:00:00Z",
        "official_url": OFFICIAL_URL,
        "notes": "official deletion",
    }
    row.update(overrides)
    return row


def write_approvals(
    path: Path,
    rows: list[dict],
    fields: list[str] | None = None,
) -> None:
    fields = fields or R.APPROVAL_FIELDS
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


class OfacRemovalApprovalTest(unittest.TestCase):

    def test_exact_hash_pinned_party_set_is_authorized(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "approvals.csv"
            write_approvals(
                path,
                [
                    approval_row(),
                    approval_row(
                        party_id="200",
                        party_name="BETA",
                    ),
                ],
            )

            approved = R.load_and_authorize(
                path,
                {("SDN", "100"), ("SDN", "200")},
                {"SDN": SNAPSHOT_HASH},
                history_rows(),
            )

            self.assertEqual(
                [(item.list_name, item.party_id) for item in approved],
                [("SDN", "100"), ("SDN", "200")],
            )
            self.assertEqual(approved[0].approved_by, "reviewer")
            self.assertEqual(approved[0].official_url, OFFICIAL_URL)

    def test_missing_approval_file_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "missing.csv"

            with self.assertRaisesRegex(
                R.ApprovalError,
                "承認台帳が存在しない",
            ):
                R.load_and_authorize(
                    path,
                    {("SDN", "100")},
                    {"SDN": SNAPSHOT_HASH},
                    history_rows(),
                )


def master_row(
    name: str,
    key: str,
    *,
    sources: str = "OFAC",
    categories: str = "OFAC:SDN",
) -> dict:
    return {
        "match_key": key,
        "display_name": name,
        "status": M.STATUS_ACTIVE,
        "risk_type": M.RISK_TYPE,
        "risk_level": M.RISK_LEVEL,
        "first_seen_ms": "1000",
        "last_updated_ms": "1000",
        "sources": sources,
        "categories": categories,
        "remark": "before",
        "invalid_reason": "",
        "review_flag": "",
        "variants": f'["{name}"]',
    }


def index_row(
    list_name: str,
    party_id: str,
    name: str,
    key: str,
    *,
    current: bool,
    low_quality: bool = False,
) -> dict:
    return {
        "list": list_name,
        "party_id": party_id,
        "name": name,
        "match_key": key,
        "first_seen_ms": "1000",
        "last_changed_ms": "2000",
        "alias_current": "1" if current else "0",
        "party_current": "1" if current else "0",
        "formats": "advanced_xml_v3",
        "primary": "0",
        "low_quality": "1" if low_quality else "0",
    }


class OfacRemovalApplicationTest(unittest.TestCase):

    def test_removes_only_ofac_and_preserves_other_sources(self):
        rows = {
            "alpha": master_row("ALPHA", "alpha"),
            "beta": master_row(
                "BETA",
                "beta",
                sources="OFAC;財務省",
                categories="OFAC:SDN;財務省:資産凍結",
            ),
        }
        history = {
            ("SDN", "100", "ALPHA"): index_row(
                "SDN", "100", "ALPHA", "alpha", current=False
            ),
            ("SDN", "200", "BETA"): index_row(
                "SDN", "200", "BETA", "beta", current=False
            ),
        }

        diff, effects = R.apply_to_master(
            rows,
            history,
            {("SDN", "100"), ("SDN", "200")},
            ts=3000,
        )

        self.assertEqual(rows["alpha"]["status"], M.STATUS_INACTIVE)
        self.assertEqual(rows["alpha"]["sources"], "")
        self.assertEqual(rows["alpha"]["categories"], "")
        self.assertEqual(rows["alpha"]["invalid_reason"], M.DELISTED)
        self.assertEqual(rows["alpha"]["last_updated_ms"], "3000")

        self.assertEqual(rows["beta"]["status"], M.STATUS_ACTIVE)
        self.assertEqual(rows["beta"]["sources"], "財務省")
        self.assertEqual(rows["beta"]["categories"], "財務省:資産凍結")
        self.assertEqual(rows["beta"]["invalid_reason"], "")
        self.assertEqual(len(diff.removed), 2)
        self.assertEqual(
            {item["match_key"]: item["action"] for item in effects},
            {
                "alpha": "deactivated",
                "beta": "removed_ofac_source",
            },
        )

    def test_current_strong_party_keeps_name_but_weak_party_does_not(self):
        rows = {
            "shared": master_row("SHARED", "shared"),
            "weak-shared": master_row("WEAK SHARED", "weak-shared"),
        }
        history = {
            ("SDN", "100", "SHARED"): index_row(
                "SDN", "100", "SHARED", "shared", current=False
            ),
            ("SDN", "900", "SHARED"): index_row(
                "SDN", "900", "SHARED", "shared", current=True
            ),
            ("SDN", "200", "WEAK SHARED"): index_row(
                "SDN", "200", "WEAK SHARED", "weak-shared", current=False
            ),
            ("SDN", "901", "WEAK SHARED"): index_row(
                "SDN",
                "901",
                "WEAK SHARED",
                "weak-shared",
                current=True,
                low_quality=True,
            ),
        }

        diff, effects = R.apply_to_master(
            rows,
            history,
            {("SDN", "100"), ("SDN", "200")},
            ts=3000,
        )

        self.assertEqual(rows["shared"]["status"], M.STATUS_ACTIVE)
        self.assertEqual(rows["shared"]["sources"], "OFAC")
        self.assertEqual(rows["shared"]["last_updated_ms"], "1000")
        self.assertEqual(rows["weak-shared"]["status"], M.STATUS_INACTIVE)
        self.assertEqual(rows["weak-shared"]["sources"], "")
        self.assertEqual(len(diff.removed), 1)
        self.assertEqual(
            {item["match_key"]: item["action"] for item in effects},
            {
                "shared": "kept_current_ofac_party",
                "weak-shared": "deactivated",
            },
        )

    def test_all_removed_party_aliases_are_processed_and_absence_is_audited(self):
        rows = {
            "alpha": master_row("ALPHA", "alpha"),
            "alpha-alt": master_row("ALPHA ALT", "alpha-alt"),
            "source-absent": master_row(
                "SOURCE ABSENT",
                "source-absent",
                sources="財務省",
                categories="財務省:資産凍結",
            ),
        }
        history = {
            ("SDN", "100", "ALPHA"): index_row(
                "SDN", "100", "ALPHA", "alpha", current=False
            ),
            ("SDN", "100", "ALPHA ALT"): index_row(
                "SDN", "100", "ALPHA ALT", "alpha-alt", current=False
            ),
            ("SDN", "100", "MISSING"): index_row(
                "SDN", "100", "MISSING", "missing", current=False
            ),
            ("SDN", "100", "SOURCE ABSENT"): index_row(
                "SDN",
                "100",
                "SOURCE ABSENT",
                "source-absent",
                current=False,
            ),
        }

        diff, effects = R.apply_to_master(
            rows,
            history,
            {("SDN", "100")},
            ts=3000,
        )

        self.assertEqual(len(diff.removed), 2)
        self.assertEqual(rows["alpha"]["status"], M.STATUS_INACTIVE)
        self.assertEqual(rows["alpha-alt"]["status"], M.STATUS_INACTIVE)
        self.assertNotIn("missing", rows)
        self.assertEqual(rows["source-absent"]["sources"], "財務省")
        self.assertEqual(
            {item["match_key"]: item["action"] for item in effects},
            {
                "alpha": "deactivated",
                "alpha-alt": "deactivated",
                "missing": "master_row_absent",
                "source-absent": "ofac_source_absent",
            },
        )

    def test_effects_can_be_refreshed_after_weak_alias_reconciliation(self):
        rows = {
            "alpha": master_row("ALPHA", "alpha"),
        }
        history = {
            ("SDN", "100", "ALPHA"): index_row(
                "SDN", "100", "ALPHA", "alpha", current=False
            ),
        }

        _, effects = R.apply_to_master(
            rows,
            history,
            {("SDN", "100")},
            ts=3000,
        )
        rows["alpha"]["invalid_reason"] = M.OFAC_WEAK_ALIAS_REASON
        rows["alpha"]["review_flag"] = M.OFAC_WEAK_ALIAS_FLAG

        refreshed = R.refresh_effects(rows, effects)

        self.assertIs(refreshed, effects)
        self.assertEqual(
            effects[0]["after"]["invalid_reason"],
            M.OFAC_WEAK_ALIAS_REASON,
        )
        self.assertEqual(
            effects[0]["after"]["review_flag"],
            M.OFAC_WEAK_ALIAS_FLAG,
        )

    def test_logical_state_hashes_are_order_independent_and_content_sensitive(self):
        first_master = {
            "alpha": master_row("ALPHA", "alpha"),
            "beta": master_row("BETA", "beta"),
        }
        second_master = {
            "beta": dict(first_master["beta"]),
            "alpha": dict(first_master["alpha"]),
        }
        first_index = {
            ("SDN", "100", "ALPHA"): index_row(
                "SDN", "100", "ALPHA", "alpha", current=False
            ),
            ("SDN", "200", "BETA"): index_row(
                "SDN", "200", "BETA", "beta", current=True
            ),
        }
        second_index = dict(reversed(list(first_index.items())))

        master_hash = R.master_state_sha256(first_master)
        index_hash = R.index_state_sha256(first_index)

        self.assertEqual(master_hash, R.master_state_sha256(second_master))
        self.assertEqual(index_hash, R.index_state_sha256(second_index))
        self.assertEqual(len(master_hash), 64)
        self.assertEqual(len(index_hash), 64)

        second_master["alpha"]["status"] = M.STATUS_INACTIVE
        second_index[("SDN", "200", "BETA")]["party_current"] = "0"

        self.assertNotEqual(master_hash, R.master_state_sha256(second_master))
        self.assertNotEqual(index_hash, R.index_state_sha256(second_index))


class OfacRemovalApprovalValidationTest(unittest.TestCase):

    def test_non_exact_or_invalid_approvals_fail_closed(self):
        cases = [
            (
                "partial",
                [approval_row()],
                {("SDN", "100"), ("SDN", "200")},
                {"SDN": SNAPSHOT_HASH},
                "承認FixedRef集合が不一致",
            ),
            (
                "extra",
                [
                    approval_row(),
                    approval_row(party_id="200", party_name="BETA"),
                    approval_row(party_id="300", party_name="GAMMA"),
                ],
                {("SDN", "100"), ("SDN", "200")},
                {"SDN": SNAPSHOT_HASH},
                "承認FixedRef集合が不一致",
            ),
            (
                "wrong_hash",
                [approval_row(snapshot_sha256="b" * 64)],
                {("SDN", "100")},
                {"SDN": SNAPSHOT_HASH},
                "承認FixedRef集合が不一致",
            ),
            (
                "duplicate",
                [approval_row(), approval_row()],
                {("SDN", "100")},
                {"SDN": SNAPSHOT_HASH},
                "重複",
            ),
            (
                "rejected",
                [approval_row(decision="REJECTED")],
                {("SDN", "100")},
                {"SDN": SNAPSHOT_HASH},
                "decision",
            ),
            (
                "non_official_url",
                [approval_row(official_url="https://example.com/deletion")],
                {("SDN", "100")},
                {"SDN": SNAPSHOT_HASH},
                "Treasury公式URL",
            ),
            (
                "name_mismatch",
                [approval_row(party_name="WRONG NAME")],
                {("SDN", "100")},
                {"SDN": SNAPSHOT_HASH},
                "party_name",
            ),
        ]

        for (
            label,
            rows,
            removed,
            hashes,
            message,
        ) in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as td:
                path = Path(td) / "approvals.csv"
                write_approvals(path, rows)

                with self.assertRaisesRegex(R.ApprovalError, message):
                    R.load_and_authorize(
                        path,
                        removed,
                        hashes,
                        history_rows(),
                    )

    def test_wrong_header_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "approvals.csv"
            wrong_fields = R.APPROVAL_FIELDS[:-1]
            write_approvals(path, [approval_row()], wrong_fields)

            with self.assertRaisesRegex(
                R.ApprovalError,
                "列構造",
            ):
                R.load_and_authorize(
                    path,
                    {("SDN", "100")},
                    {"SDN": SNAPSHOT_HASH},
                    history_rows(),
                )


class OfacRemovalAuditTest(unittest.TestCase):

    def test_builds_and_appends_inspectable_application_audit(self):
        approval = R.Approval(
            list_name="SDN",
            snapshot_sha256=SNAPSHOT_HASH,
            party_id="100",
            party_name="ALPHA",
            approved_by="reviewer",
            approved_at="2026-09-17T00:00:00Z",
            official_url=OFFICIAL_URL,
            notes="official deletion",
        )
        effects = [{
            "match_key": "alpha",
            "display_name": "ALPHA",
            "removed_parties": [{"list": "SDN", "party_id": "100"}],
            "action": "deactivated",
            "before": {"status": M.STATUS_ACTIVE, "sources": "OFAC"},
            "after": {"status": M.STATUS_INACTIVE, "sources": ""},
        }]

        audit_rows = R.build_audit_rows(
            [approval],
            effects,
            master_before_sha256="1" * 64,
            master_after_sha256="2" * 64,
            index_before_sha256="3" * 64,
            index_after_sha256="4" * 64,
        )

        self.assertEqual(len(audit_rows), 1)
        row = audit_rows[0]
        self.assertEqual(row["applied_at"], "")
        self.assertEqual(row["approved_by"], "reviewer")
        self.assertEqual(row["official_url"], OFFICIAL_URL)
        self.assertEqual(row["effect_count"], "1")
        self.assertEqual(json.loads(row["effects_json"]), effects)
        self.assertEqual(row["master_before_sha256"], "1" * 64)
        self.assertEqual(row["master_after_sha256"], "2" * 64)
        self.assertEqual(row["index_before_sha256"], "3" * 64)
        self.assertEqual(row["index_after_sha256"], "4" * 64)

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "audit.csv"
            applied_at = datetime(
                2026,
                9,
                17,
                1,
                2,
                3,
                tzinfo=timezone.utc,
            )

            R.append_audit(path, audit_rows, applied_at=applied_at)

            with path.open(encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle)
                saved = list(reader)

            self.assertEqual(reader.fieldnames, R.AUDIT_FIELDS)
            self.assertEqual(len(saved), 1)
            self.assertEqual(saved[0]["applied_at"], "2026-09-17T01:02:03Z")
            self.assertEqual(json.loads(saved[0]["effects_json"]), effects)

    def test_existing_audit_with_wrong_header_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "audit.csv"
            path.write_text("wrong,header\n", encoding="utf-8")

            with self.assertRaisesRegex(R.AuditError, "列構造"):
                R.append_audit(
                    path,
                    [{field: "" for field in R.AUDIT_FIELDS}],
                    applied_at=datetime.now(timezone.utc),
                )


if __name__ == "__main__":
    unittest.main()
