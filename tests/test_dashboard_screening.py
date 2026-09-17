import csv
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from src import dashboard as D
from src.normalize import match_key
from src.screening import secondary_screening_key


NOW = datetime(2026, 9, 17, 6, 0, tzinfo=timezone.utc)


def entry(
    name,
    *,
    status="有効",
    sources="財務省",
    categories="財務省:TEST",
    risk_type="制裁リスト",
    risk_level="高",
    review_flag="",
    key=None,
):
    return {
        "match_key": (
            match_key(name)
            if key is None
            else key
        ),
        "display_name": name,
        "status": status,
        "sources": sources,
        "categories": categories,
        "risk_type": risk_type,
        "risk_level": risk_level,
        "review_flag": review_flag,
    }


class DashboardScreeningTest(
    unittest.TestCase
):

    def test_review_required_has_operator_facing_label(self):
        self.assertEqual(
            D.STATUS_LABEL["review_required"],
            "掲載終了候補・要レビュー",
        )

    def read_csv(self, path):
        with Path(path).open(
            encoding="utf-8",
            newline="",
        ) as f:
            return list(
                csv.reader(f)
            )

    def test_header_and_active_only(self):
        with tempfile.TemporaryDirectory() as td:

            root = Path(td)

            active = entry(
                "ALPHA CORP"
            )

            inactive = entry(
                "OLD CORP",
                status="無効",
                sources="",
            )

            path = D.write_screening_list(
                root,
                [
                    active,
                    inactive,
                ],
            )

            rows = self.read_csv(
                path
            )

            self.assertEqual(
                rows[0],
                D.SCREENING_COLS,
            )

            self.assertEqual(
                len(rows),
                2,
            )

            self.assertEqual(
                rows[1][2],
                "ALPHA CORP",
            )

    def test_leader_secondary_key(self):
        with tempfile.TemporaryDirectory() as td:

            root = Path(td)

            row = entry(
                "LEADER HONG KONG INTERNATIONAL"
            )

            path = D.write_screening_list(
                root,
                [row],
            )

            rows = self.read_csv(
                path
            )

            self.assertEqual(
                rows[1][0],
                match_key(
                    "LEADER HONG KONG INTERNATIONAL"
                ),
            )

            self.assertEqual(
                rows[1][1],
                secondary_screening_key(
                    "LEADER (HONG KONG) INTERNATIONAL"
                ),
            )

    def test_dict_input(self):
        with tempfile.TemporaryDirectory() as td:

            root = Path(td)

            row = entry(
                "BETA LTD"
            )

            path = D.write_screening_list(
                root,
                {
                    row["match_key"]:
                        row
                },
            )

            rows = self.read_csv(
                path
            )

            self.assertEqual(
                len(rows),
                2,
            )

            self.assertEqual(
                rows[1][2],
                "BETA LTD",
            )

    def test_review_flag_is_preserved(self):
        with tempfile.TemporaryDirectory() as td:

            root = Path(td)

            row = entry(
                "REVIEW CORP",
                review_flag=(
                    "MANUAL_REVIEW"
                ),
            )

            path = D.write_screening_list(
                root,
                [row],
            )

            rows = self.read_csv(
                path
            )

            self.assertEqual(
                rows[1][8],
                "MANUAL_REVIEW",
            )

    def test_trailing_artifact_not_exported(self):
        with tempfile.TemporaryDirectory() as td:

            root = Path(td)

            row = entry(
                "ALPHA CORP不明"
            )

            path = D.write_screening_list(
                root,
                [row],
            )

            rows = self.read_csv(
                path
            )

            self.assertEqual(
                rows,
                [
                    D.SCREENING_COLS
                ],
            )

    def test_active_empty_match_key_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:

            root = Path(td)

            row = entry(
                "BROKEN CORP",
                key="",
            )

            with self.assertRaises(
                ValueError
            ):
                D.write_screening_list(
                    root,
                    [row],
                )


    def test_gzip_roundtrip(self):
        import gzip

        with tempfile.TemporaryDirectory() as td:

            root = Path(td)

            rows = [
                entry(
                    "LEADER HONG KONG INTERNATIONAL"
                ),
                entry(
                    "ALPHA CORP"
                ),
            ]

            gz_path = D.write_screening_gzip(
                root,
                rows,
            )

            csv_path = (
                root
                / D.DASH
                / "screening.csv"
            )

            self.assertTrue(
                gz_path.exists()
            )

            raw = csv_path.read_bytes()

            with gzip.open(
                gz_path,
                "rb",
            ) as f:
                restored = f.read()

            self.assertEqual(
                restored,
                raw,
            )

    def test_gzip_cleanup_failure_does_not_mask_write_failure(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            real_unlink = Path.unlink

            def fail_tmp_cleanup(path, *args, **kwargs):
                if path.name.endswith(".tmp"):
                    raise OSError("injected gzip cleanup failure")

                return real_unlink(path, *args, **kwargs)

            with (
                patch.object(
                    D.gzip.GzipFile,
                    "write",
                    side_effect=OSError(
                        "injected gzip write failure"
                    ),
                ),
                patch.object(
                    Path,
                    "unlink",
                    side_effect=fail_tmp_cleanup,
                    autospec=True,
                ),
            ):
                with self.assertRaisesRegex(
                    OSError,
                    "injected gzip write failure",
                ):
                    D.write_screening_gzip(
                        root,
                        [entry("ALPHA CORP")],
                    )

    def test_gzip_is_deterministic(self):
        import hashlib

        with tempfile.TemporaryDirectory() as td:

            root = Path(td)

            rows = [
                entry(
                    "LEADER HONG KONG INTERNATIONAL"
                ),
                entry(
                    "ALPHA CORP"
                ),
            ]

            first = D.write_screening_gzip(
                root,
                rows,
            ).read_bytes()

            second = D.write_screening_gzip(
                root,
                rows,
            ).read_bytes()

            self.assertEqual(
                first,
                second,
            )

            self.assertEqual(
                hashlib.sha256(
                    first
                ).hexdigest(),
                hashlib.sha256(
                    second
                ).hexdigest(),
            )


    def test_gzip_consistency_rejects_tampering(self):
        import gzip

        with tempfile.TemporaryDirectory() as td:

            root = Path(td)

            rows = [
                entry(
                    "ALPHA CORP"
                )
            ]

            gz_path = D.write_screening_gzip(
                root,
                rows,
            )

            with gzip.open(
                gz_path,
                "wt",
                encoding="utf-8",
                newline="",
            ) as f:
                w = csv.writer(f)
                w.writerow(
                    D.SCREENING_COLS
                )
                w.writerow([
                    "wrong",
                    "wrong",
                    "WRONG NAME",
                    "財務省",
                    "",
                    "制裁リスト",
                    "高",
                    "有効",
                    "",
                ])

            with self.assertRaises(
                ValueError
            ):
                D.assert_screening_gzip_consistent(
                    rows,
                    gz_path,
                )


class DashboardManualLifecycleTest(unittest.TestCase):
    def test_manual_status_labels_are_operator_readable(self):
        expected = {
            "manual_ok": "手動監視（正常）",
            "manual_pending": "要確認：正本取得待ち",
            "manual_review": "要レビュー",
            "manual_approved": "要確認：反映待ち",
            "manual_critical": "重大：更新候補未検証",
            "manual_blocked": "重大：正本解析BLOCKED",
            "manual_rejected": "要確認：取込却下",
        }
        for raw, label in expected.items():
            with self.subTest(raw=raw):
                self.assertEqual(D.STATUS_LABEL.get(raw), label)

    def test_manual_lifecycle_projects_status_without_reordering_sources(self):
        cases = [
            (
                {
                    "lifecycle_state": "CHECKED_NO_CHANGE",
                    "last_manual_check_at": "2026-09-17T06:00:00Z",
                },
                "手動監視（正常）",
            ),
            (
                {
                    "lifecycle_state": "MANUAL_FETCH_REQUIRED",
                    "sla_due_at": "2026-09-17T07:00:00Z",
                },
                "要確認：正本取得待ち",
            ),
            (
                {
                    "lifecycle_state": "REVIEW_REQUIRED",
                    "diffed_at": "2026-09-17T06:30:00Z",
                },
                "要レビュー",
            ),
            (
                {
                    "lifecycle_state": "APPROVED",
                    "diffed_at": "2026-09-17T06:30:00Z",
                },
                "要確認：反映待ち",
            ),
            (
                {
                    "lifecycle_state": "MANUAL_FETCH_REQUIRED",
                    "sla_due_at": "2026-09-17T07:00:00Z",
                    "sla_breached_at": "2026-09-17T07:01:00Z",
                },
                "重大：更新候補未検証",
            ),
            ({"lifecycle_state": "BLOCKED"}, "重大：正本解析BLOCKED"),
            ({"lifecycle_state": "REJECTED"}, "要確認：取込却下"),
        ]

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for meti_state, expected_status in cases:
                with self.subTest(state=meti_state["lifecycle_state"]):
                    rows = D.build_status_rows(
                        root,
                        [],
                        {},
                        now=NOW,
                        meti_state=meti_state,
                    )
                    self.assertEqual(
                        [row[0] for row in rows],
                        [
                            "財務省",
                            "経済産業省",
                            "OFAC SDN",
                            "OFAC Consolidated",
                        ],
                    )
                    meti = next(row for row in rows if row[0] == "経済産業省")
                    self.assertEqual(meti[1], expected_status)

    def test_manual_dates_populate_check_and_update_columns(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            rows = D.build_status_rows(
                root,
                [],
                {},
                now=NOW,
                meti_state={
                    "lifecycle_state": "CHECKED_NO_CHANGE",
                    "last_manual_check_at": "2026-09-17T06:00:00Z",
                    "publication_date": "2026-09-16T00:00:00Z",
                    "effective_date": "2026-09-17T00:00:00Z",
                },
            )
            meti = next(row for row in rows if row[0] == "経済産業省")
            self.assertEqual(meti[2], "2026-09-17 15:00:00")
            self.assertEqual(meti[3], "2026-09-17 09:00:00")

    def test_omitted_manual_state_loads_committed_snapshot(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / "data/manual/meti/state.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps({"lifecycle_state": "BLOCKED"}),
                encoding="utf-8",
            )

            rows = D.build_status_rows(root, [], {}, now=NOW)
            meti = next(row for row in rows if row[0] == "経済産業省")
            self.assertEqual(meti[1], "重大：正本解析BLOCKED")

    def test_supplied_heartbeat_newer_than_history_wins(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / "data/heartbeat/2026-09.csv"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=[
                        "checked_at",
                        "source",
                        "status",
                        "content_hash",
                        "source_updated",
                        "record_count",
                        "raw_path",
                    ],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "checked_at": "2026-09-17T05:00:00Z",
                        "source": "ofac_sdn",
                        "status": "unchanged",
                    }
                )

            rows = D.build_status_rows(
                root,
                [{"source": "ofac_sdn", "status": "changed"}],
                {},
                now=NOW,
                meti_state={},
            )
            ofac = next(row for row in rows if row[0] == "OFAC SDN")
            self.assertEqual(ofac[1], "更新あり")
            self.assertEqual(ofac[2], "2026-09-17 15:00:00")


if __name__ == "__main__":
    unittest.main()
