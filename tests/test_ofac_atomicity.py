from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from src import watch
from src.sources import ofac


class FakeFetched:
    def __init__(
        self,
        url: str,
        *,
        sha256: str = "new-sha",
        body: bytes = b"dummy",
    ):
        self.url = url
        self.final_url = url
        self.body = body
        self.sha256 = sha256
        self.not_modified = False
        self.etag = '"new-etag"'
        self.last_modified = "Sun, 13 Sep 2026 00:00:00 GMT"
        self.filename = url.rsplit("/", 1)[-1].lower()
        self.http_status = 200
        self.raw_path = (
            "data/raw/test/"
            + self.filename
            + ".gz"
        )
        self.headers = {
            "Content-Type": "text/csv",
        }

    @property
    def text(self) -> str:
        return self.body.decode(
            "utf-8",
            errors="replace",
        )


def record(label: str, fmt: str) -> dict:
    return {
        "source": ofac.SOURCE,
        "category": label,
        "name": f"{label} TEST NAME",
        "source_id": "1",
        "format": fmt,
        "alias_primary": True,
    }


class OfacAtomicityTest(unittest.TestCase):

    def test_second_list_failure_does_not_mutate_state_or_rows(self):
        st = {
            "ofac_sdn": {
                "sha256": "old-sdn-sha",
                "advanced_sha256": "old-sdn-advanced-sha",
                "raw_advanced": "data/raw/old/sdn.xml.gz",
                "advanced_baseline_synced": True,
                "record_count": 100,
            },
            "ofac_cons": {
                "sha256": "old-cons-sha",
                "advanced_sha256": "old-cons-advanced-sha",
                "raw_advanced": "data/raw/old/cons.xml.gz",
                "advanced_baseline_synced": True,
                "record_count": 50,
            },
        }

        rows = {
            "sentinel": {
                "display_name": "KEEP ME",
                "status": "有効",
            },
        }

        before_st = copy.deepcopy(st)
        before_rows = copy.deepcopy(rows)

        hb = []
        opts = {
            "audit": [],
        }

        sdn = ofac.LISTS["ofac_sdn"]
        cons = ofac.LISTS["ofac_cons"]

        def fake_fetch(
            url,
            prev=None,
            session=None,
            allow_conditional=True,
        ):
            if url == cons["prim"]:
                raise RuntimeError(
                    "injected CONS fetch failure"
                )

            return FakeFetched(
                url,
                sha256="new-" + url.rsplit("/", 1)[-1],
            )

        def fake_advanced(
            fetched,
            label,
        ):
            return (
                [
                    record(
                        label,
                        "advanced_xml_v3",
                    )
                ],
                {"1"},
            )

        def fake_classic(
            prim,
            alt,
            label,
        ):
            return [
                record(
                    label,
                    "classic_csv",
                )
            ]

        with (
            patch.object(
                watch,
                "fetch",
                side_effect=fake_fetch,
            ),
            patch.object(
                watch,
                "archive",
            ),
            patch.object(
                watch,
                "prune_raw",
            ),
            patch.object(
                watch.ofac,
                "classic_party_ids",
                return_value={"1"},
            ),
            patch.object(
                watch.ofac,
                "parse_advanced",
                side_effect=fake_advanced,
            ),
            patch.object(
                watch.ofac,
                "parse",
                side_effect=fake_classic,
            ),
            patch.object(
                watch.ofac,
                "validate_party_coverage",
            ),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "injected CONS fetch failure",
            ):
                watch.run_ofac(
                    session=object(),
                    st=st,
                    rows=rows,
                    hb=hb,
                    opts=opts,
                )

        self.assertEqual(
            st,
            before_st,
            "OFAC片側失敗時にstateを部分更新してはいけない",
        )

        self.assertEqual(
            rows,
            before_rows,
            "OFAC失敗時にmaster rowsを部分更新してはいけない",
        )



    def test_merge_failure_rolls_back_state_rows_and_index_staging(self):
        st = {
            "ofac_sdn": {
                "sha256": "old-sdn-sha",
                "advanced_sha256": "old-sdn-advanced-sha",
                "raw_advanced": "data/raw/old/sdn.xml.gz",
                "advanced_baseline_synced": True,
                "advanced_master_synced": True,
                "record_count": 100,
            },
            "ofac_cons": {
                "sha256": "old-cons-sha",
                "advanced_sha256": "old-cons-advanced-sha",
                "raw_advanced": "data/raw/old/cons.xml.gz",
                "advanced_baseline_synced": True,
                "advanced_master_synced": True,
                "record_count": 50,
            },
        }

        rows = {
            "sentinel": {
                "display_name": "KEEP ME",
                "status": "有効",
            },
        }

        before_st = copy.deepcopy(st)
        before_rows = copy.deepcopy(rows)

        hb = []
        opts = {
            "audit": [],
        }

        def fake_fetch(
            url,
            prev=None,
            session=None,
            allow_conditional=True,
        ):
            return FakeFetched(
                url,
                sha256="new-" + url.rsplit("/", 1)[-1],
            )

        def fake_advanced(
            fetched,
            label,
        ):
            return (
                [
                    record(
                        label,
                        "advanced_xml_v3",
                    )
                ],
                {"1"},
            )

        def fake_classic(
            prim,
            alt,
            label,
        ):
            return [
                record(
                    label,
                    "classic_csv",
                )
            ]

        def exploding_merge(
            target_rows,
            *args,
            **kwargs,
        ):
            target_rows["PARTIAL"] = {
                "display_name": "MUST ROLLBACK",
                "status": "有効",
            }

            raise RuntimeError(
                "injected merge failure"
            )

        with (
            patch.object(
                watch,
                "fetch",
                side_effect=fake_fetch,
            ),
            patch.object(
                watch,
                "archive",
            ),
            patch.object(
                watch,
                "prune_raw",
            ),
            patch.object(
                watch.ofac,
                "classic_party_ids",
                return_value={"1"},
            ),
            patch.object(
                watch.ofac,
                "parse_advanced",
                side_effect=fake_advanced,
            ),
            patch.object(
                watch.ofac,
                "parse",
                side_effect=fake_classic,
            ),
            patch.object(
                watch.ofac,
                "validate_party_coverage",
            ),
            patch.object(
                watch.OI,
                "load",
                return_value={},
            ),
            patch.object(
                watch.OI,
                "update",
                return_value=watch.OI.IndexDiff(),
            ),
            patch.object(
                watch.M,
                "merge",
                side_effect=exploding_merge,
            ),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "injected merge failure",
            ):
                watch.run_ofac(
                    session=object(),
                    st=st,
                    rows=rows,
                    hb=hb,
                    opts=opts,
                )

        self.assertEqual(
            st,
            before_st,
            "後段失敗時もOFAC state全体をrollbackする",
        )

        self.assertEqual(
            rows,
            before_rows,
            "後段失敗時もmaster rowsをrollbackする",
        )

        self.assertNotIn(
            "ofac_index_rows",
            opts,
            "失敗snapshotのParty indexを保存対象にしてはいけない",
        )

        self.assertNotIn(
            "ofac_index_diff",
            opts,
            "失敗snapshotのParty index diffを残してはいけない",
        )



    def test_success_commits_state_rows_and_index_together(self):
        st = {
            "ofac_sdn": {
                "sha256": "old-sdn",
                "advanced_sha256": "old-sdn-advanced",
                "raw_advanced": "data/raw/old/sdn.xml.gz",
                "advanced_baseline_synced": True,
                "advanced_master_synced": True,
            },
            "ofac_cons": {
                "sha256": "old-cons",
                "advanced_sha256": "old-cons-advanced",
                "raw_advanced": "data/raw/old/cons.xml.gz",
                "advanced_baseline_synced": True,
                "advanced_master_synced": True,
            },
        }

        rows = {
            "sentinel": {
                "display_name": "KEEP ME",
                "status": "有効",
            },
        }

        hb = []
        opts = {"audit": []}

        def fake_fetch(
            url,
            prev=None,
            session=None,
            allow_conditional=True,
        ):
            return FakeFetched(
                url,
                sha256="new-" + url.rsplit("/", 1)[-1],
            )

        def fake_advanced(fetched, label):
            return (
                [record(label, "advanced_xml_v3")],
                {"1"},
            )

        def fake_classic(prim, alt, label):
            return [
                record(label, "classic_csv"),
            ]

        def successful_merge(
            target_rows,
            *args,
            **kwargs,
        ):
            target_rows["committed"] = {
                "display_name": "COMMITTED",
                "status": "有効",
            }

            return watch.M.Diff(
                source=ofac.SOURCE,
            )

        history = {}

        with (
            patch.object(
                watch,
                "fetch",
                side_effect=fake_fetch,
            ),
            patch.object(watch, "archive"),
            patch.object(watch, "prune_raw"),
            patch.object(
                watch.ofac,
                "classic_party_ids",
                return_value={"1"},
            ),
            patch.object(
                watch.ofac,
                "parse_advanced",
                side_effect=fake_advanced,
            ),
            patch.object(
                watch.ofac,
                "parse",
                side_effect=fake_classic,
            ),
            patch.object(
                watch.ofac,
                "validate_party_coverage",
            ),
            patch.object(
                watch.OI,
                "load",
                return_value=history,
            ),
            patch.object(
                watch.OI,
                "update",
                return_value=watch.OI.IndexDiff(),
            ),
            patch.object(
                watch.M,
                "merge",
                side_effect=successful_merge,
            ),
        ):
            watch.run_ofac(
                session=object(),
                st=st,
                rows=rows,
                hb=hb,
                opts=opts,
            )

        self.assertEqual(
            st["ofac_sdn"]["sha256"],
            "new-SDN.CSV",
        )

        self.assertEqual(
            st["ofac_cons"]["sha256"],
            "new-CONS_PRIM.CSV",
        )

        self.assertIn(
            "committed",
            rows,
        )

        self.assertIs(
            opts["ofac_index_rows"],
            history,
        )

        self.assertIn(
            "ofac_index_diff",
            opts,
        )


if __name__ == "__main__":
    unittest.main()
