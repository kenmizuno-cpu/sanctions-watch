from __future__ import annotations

import csv
import json
import tempfile
import unittest
from collections import Counter
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from src import dashboard as D
from src import meti_manual_event as manual_event
from src import state as state_store
from src import meti_apply as A
from src import meti_apply_plan as P


NOW = datetime(2026, 9, 17, 6, 0, tzinfo=timezone.utc)
DETECTION_ID = "d" * 64
SOURCE_HASH = "a" * 64


def write_csv(path: Path, fields: list[str], rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=fields,
            extrasaction="ignore",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def read_last_state(path):
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))[-1]["state"]


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
    def make_apply_fixture(self, td: str, *, with_hold: bool):
        root = Path(td)
        paths = manual_event.LifecyclePaths.for_root(root, NOW)
        raw = root / "data/raw/meti_manual/a.pdf"
        raw.parent.mkdir(parents=True, exist_ok=True)
        raw.write_bytes(b"%PDF-1.7\n" + b"x" * 20000)

        review_state = {
            "version": 1,
            "lifecycle_state": "REVIEW_REQUIRED",
            "detection_id": DETECTION_ID,
            "pending_detection": {
                "detection_id": DETECTION_ID,
                "attempt": 1,
                "notice_url": (
                    "https://www.meti.go.jp/press/2026/example.html"
                ),
                "publication_at": "2026-09-17T05:00:00Z",
            },
            "current_source_hash": SOURCE_HASH,
            "current_raw_path": "data/raw/meti_manual/a.pdf",
            "current_record_count": 2,
            "source_url": "https://www.meti.go.jp/policy/anpo/x.pdf",
            "effective_date": "2026-09-17",
            "review_status": "REVIEW_REQUIRED",
            "approved": False,
            "applied": False,
        }
        state, events, _ = manual_event.advance(
            review_state,
            [],
            new_state="APPROVED",
            event_at=NOW,
            operator="reviewer",
            source_url=review_state["source_url"],
            source_hash=SOURCE_HASH,
            detection_id=DETECTION_ID,
            detail="approved for apply",
        )
        state.update({
            "review_status": "APPROVED",
            "approved": True,
            "applied": False,
        })
        manual_event.persist_lifecycle(
            state=state,
            events=events,
            heartbeat_status="manual_approved",
            dashboard_event=None,
            audit_row=None,
            now=NOW,
            paths=paths,
        )

        master_path = root / "data/master/master.csv"
        master = {
            "beta": master_row("BETA", "OFAC"),
        }
        A.M.save(master, master_path)

        rows = [
            plan_row(
                P.ACTION_READY_TAG,
                "beta",
                "BETA",
            ),
        ]
        if with_hold:
            rows.append(
                plan_row(
                    P.ACTION_HOLD_WEAK,
                    "ec",
                    "EC",
                    kind="ALIAS:1",
                    review_reason="weak alias",
                )
            )
        legacy_rows = []
        merge_ts_ms = 1789615200000
        simulation = A._simulate(
            rows=rows,
            legacy_rows=legacy_rows,
            master=master,
            merge_ts_ms=merge_ts_ms,
        )
        candidate = root / "candidate-master.csv"
        A.M.save(simulation["after"], candidate)

        plan_path = root / "data/manual/meti/apply/plan.csv"
        legacy_path = root / "data/manual/meti/apply/legacy.csv"
        summary_path = root / "data/manual/meti/apply/summary.json"
        write_csv(plan_path, P.PLAN_COLS, rows)
        write_csv(legacy_path, P.LEGACY_COLS, legacy_rows)
        summary = {
            "status": "PREAPPLY_READY",
            "apply_executed": False,
            "approved": True,
            "applied": False,
            "source_hash": SOURCE_HASH,
            "source_url": state["source_url"],
            "plan_path": str(plan_path.relative_to(root)),
            "plan_sha256": A._sha256(plan_path),
            "legacy_hold_path": str(legacy_path.relative_to(root)),
            "legacy_hold_sha256": A._sha256(legacy_path),
        }
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        result = {
            "summary_path": summary_path,
            "summary": summary,
            "verified": {
                "state": state,
                "report": {"effective_date": "2026-09-17"},
                "raw_path": raw,
                "record_count": 2,
            },
            "plan_path": plan_path,
            "legacy_path": legacy_path,
            "rows": rows,
            "legacy_rows": legacy_rows,
            "master_before_sha256": A._sha256(master_path),
            "master_after_sha256": A._sha256(candidate),
            "master_merge_timestamp_ms": merge_ts_ms,
            "master_before_count": len(master),
            "master_after_count": len(simulation["after"]),
            "simulation": simulation,
            "counts": dict(Counter(row["action"] for row in rows)),
        }
        D.write_list(root, master)

        return {
            "root": root,
            "paths": paths,
            "raw": raw,
            "master": master_path,
            "dashboard_list": root / "data/dashboard/list.csv",
            "apply_ledger": root / "data/review/meti_apply_ledger.csv",
            "application_dir": root / "data/manual/meti/applications",
            "application": (
                root
                / "data/manual/meti/applications"
                / "20260917T060000Z__aaaaaaaaaaaa__apply.json"
            ),
            "runtime_dir": root / ".runtime",
            "journal": root / ".runtime/meti_apply_transaction.json",
            "result": result,
        }

    def apply_patches(self, fx):
        def save_manual_state(value):
            state_store.write_state(fx["paths"].state, value)

        def append_change(kind, subject, before, after):
            return D.prepend_change_rows(
                fx["paths"].changes,
                [["経済産業省", kind, subject, before, after]],
                when="2026-09-17 15:00:00",
            )

        return (
            patch.object(A, "ROOT", fx["root"]),
            patch.object(A, "MASTER_PATH", fx["master"]),
            patch.object(
                A,
                "DASHBOARD_LIST_PATH",
                fx["dashboard_list"],
            ),
            patch.object(A, "APPLICATION_DIR", fx["application_dir"]),
            patch.object(A, "APPLY_LEDGER", fx["apply_ledger"]),
            patch.object(A, "RUNTIME_DIR", fx["runtime_dir"]),
            patch.object(A, "JOURNAL_PATH", fx["journal"]),
            patch.object(A, "STATE_PATH", fx["paths"].state),
            patch.object(A, "CHANGES_PATH", fx["paths"].changes),
            patch.object(A, "_git_clean", return_value=None),
            patch.object(A, "_now", return_value=NOW),
            patch.object(
                A,
                "verify_executor",
                return_value=fx["result"],
            ),
            patch.object(A, "save_state", side_effect=save_manual_state),
            patch.object(
                A,
                "append_dashboard_row",
                side_effect=append_change,
            ),
            patch.object(
                A,
                "_write_and_verify_dashboard",
                side_effect=lambda master: D.write_list(
                    fx["root"],
                    master,
                ),
            ),
        )

    def run_apply(self, fx):
        result = fx["result"]
        summary = result["summary"]
        return A.apply_verified(
            result=result,
            operator="operator",
            confirm_plan_sha256=summary["plan_sha256"],
            confirm_master_before_sha256=result[
                "master_before_sha256"
            ],
            confirm_master_after_sha256=result[
                "master_after_sha256"
            ],
        )

    def assert_manual_ok_projection(self, fx):
        with fx["paths"].heartbeat.open(
            encoding="utf-8",
            newline="",
        ) as f:
            heartbeat_rows = list(csv.DictReader(f))
        self.assertEqual(heartbeat_rows[-1]["status"], "manual_ok")

        with fx["paths"].status.open(
            encoding="utf-8",
            newline="",
        ) as f:
            status_rows = list(csv.DictReader(f))
        meti = next(
            row for row in status_rows
            if row["出所"] == "経済産業省"
        )
        self.assertEqual(meti["状態"], "手動監視（正常）")

    def assert_audit_status(self, fx, expected):
        with fx["paths"].audit.open(
            encoding="utf-8",
            newline="",
        ) as f:
            audit_rows = list(csv.DictReader(f))
        self.assertEqual(audit_rows[-1]["status"], expected)

    @staticmethod
    def snapshot(paths):
        return {
            path: path.read_bytes() if path.exists() else None
            for path in paths
        }

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


    def test_dashboard_write_and_consistency_guard(self):
        master = {
            "alpha": master_row(
                "ALPHA",
                "経産省",
            ),
            "beta": master_row(
                "BETA",
                "OFAC;経産省",
            ),
        }

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            out = A._write_and_verify_dashboard(
                master,
                root=root,
            )

            self.assertTrue(out.exists())

            with out.open(
                encoding="utf-8-sig",
                newline="",
            ) as f:
                rows = list(csv.reader(f))

            self.assertEqual(
                rows[0],
                [
                    "受取人名",
                    "リスクタイプ",
                    "状態",
                    "リスク度",
                ],
            )
            self.assertEqual(
                len(rows) - 1,
                2,
            )

            # dashboardだけ古い/誤った状態になったケースを再現。
            rows[1][2] = "無効"

            with out.open(
                "w",
                encoding="utf-8",
                newline="",
            ) as f:
                w = csv.writer(
                    f,
                    lineterminator="\n",
                )
                w.writerows(rows)

            with self.assertRaises(
                A.ApplyError
            ):
                A._assert_dashboard_list_consistent(
                    master,
                    out,
                )

    def test_apply_with_weak_hold_records_applied_with_holds(self):
        with tempfile.TemporaryDirectory() as td, ExitStack() as stack:
            fx = self.make_apply_fixture(td, with_hold=True)
            for item in self.apply_patches(fx):
                stack.enter_context(item)

            result = self.run_apply(fx)

            self.assertEqual(
                read_last_state(fx["paths"].events),
                "APPLIED_WITH_HOLDS",
            )
            self.assertEqual(
                result["state"]["lifecycle_state"],
                "APPLIED_WITH_HOLDS",
            )
            self.assertEqual(
                result["application"]["status"],
                "APPLIED_WITH_HOLDS",
            )
            self.assert_manual_ok_projection(fx)
            self.assert_audit_status(
                fx,
                "APPLIED_WITH_HOLDS",
            )

    def test_apply_without_holds_records_applied(self):
        with tempfile.TemporaryDirectory() as td, ExitStack() as stack:
            fx = self.make_apply_fixture(td, with_hold=False)
            for item in self.apply_patches(fx):
                stack.enter_context(item)

            result = self.run_apply(fx)

            self.assertEqual(
                read_last_state(fx["paths"].events),
                "APPLIED",
            )
            self.assertEqual(
                result["state"]["lifecycle_state"],
                "APPLIED",
            )
            self.assertEqual(
                result["state"]["apply_status"],
                "APPLIED",
            )
            self.assertEqual(
                result["application"]["status"],
                "APPLIED",
            )
            self.assert_manual_ok_projection(fx)
            self.assert_audit_status(fx, "APPLIED")

    def test_apply_failure_after_master_replace_restores_lifecycle(self):
        with tempfile.TemporaryDirectory() as td, ExitStack() as stack:
            real_dashboard_writer = A._write_and_verify_dashboard
            real_json_writer = A._write_json_atomic
            fx = self.make_apply_fixture(td, with_hold=True)
            for item in self.apply_patches(fx):
                stack.enter_context(item)

            targets = [
                fx["master"],
                fx["dashboard_list"],
                fx["root"] / "data/dashboard/screening.csv",
                fx["root"] / "data/dashboard/screening.csv.gz",
                fx["paths"].state,
                fx["paths"].events,
                fx["paths"].heartbeat,
                fx["paths"].status,
                fx["paths"].changes,
                fx["paths"].audit,
                fx["apply_ledger"],
                fx["application"],
            ]
            before = self.snapshot(targets)

            def write_dashboard_then_verify(master):
                return real_dashboard_writer(
                    master,
                    root=fx["root"],
                )

            def fail_final_journal_commit(path, value):
                if (
                    path == fx["journal"]
                    and value.get("status") == "COMMITTED"
                ):
                    raise OSError("injected final journal failure")
                return real_json_writer(path, value)

            stack.enter_context(
                patch.object(
                    A,
                    "_write_and_verify_dashboard",
                    side_effect=write_dashboard_then_verify,
                )
            )
            stack.enter_context(
                patch.object(
                    A,
                    "_write_json_atomic",
                    side_effect=fail_final_journal_commit,
                )
            )

            with self.assertRaisesRegex(
                A.ApplyError,
                "injected final journal failure",
            ):
                self.run_apply(fx)

            self.assertEqual(self.snapshot(targets), before)
            self.assertFalse(fx["journal"].exists())


if __name__ == "__main__":
    unittest.main()
