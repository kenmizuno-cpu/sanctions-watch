import csv
import tempfile
import unittest
from pathlib import Path

from src import dashboard as D
from src.normalize import match_key
from src.screening import secondary_screening_key


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


if __name__ == "__main__":
    unittest.main()
