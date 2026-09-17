from __future__ import annotations

import copy
import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import ofac_removal as OR
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

    def test_unchanged_primary_still_checks_alias_and_advanced(self):
        def make_state(key):
            return {
                "sha256": f"{key}-primary-sha",
                "etag": f'"{key}-primary-etag"',
                "last_modified": "Sat, 12 Sep 2026 00:00:00 GMT",
                "filename": f"{key}-primary.csv",
                "alt_sha256": f"{key}-alias-sha",
                "alt_etag": f'"{key}-alias-etag"',
                "alt_last_modified": "Sat, 12 Sep 2026 00:00:00 GMT",
                "alt_filename": f"{key}-alias.csv",
                "advanced_sha256": f"{key}-advanced-sha",
                "advanced_etag": f'"{key}-advanced-etag"',
                "advanced_last_modified": "Sat, 12 Sep 2026 00:00:00 GMT",
                "advanced_filename": f"{key}-advanced.xml",
                "raw_advanced": f"data/raw/old/{key}.xml.gz",
                "advanced_baseline_synced": True,
                "advanced_master_synced": True,
                "record_count": 1,
            }

        st = {
            key: make_state(key)
            for key in ofac.LISTS
        }
        rows = {}
        hb = []
        opts = {"audit": []}
        seen = []

        def fake_fetch(
            url,
            prev=None,
            session=None,
            allow_conditional=True,
        ):
            previous = copy.deepcopy(prev or {})
            seen.append(
                (url, previous, allow_conditional)
            )

            fetched = FakeFetched(
                url,
                sha256=previous.get("sha256", ""),
            )
            fetched.etag = previous.get("etag", "")
            fetched.last_modified = previous.get(
                "last_modified",
                "",
            )
            fetched.filename = previous.get(
                "filename",
                "",
            )
            fetched.not_modified = True
            fetched.body = None
            fetched.http_status = 304
            return fetched

        with (
            patch.object(
                watch,
                "fetch",
                side_effect=fake_fetch,
            ),
            patch.object(
                watch.OI,
                "load",
                return_value={},
            ),
            patch.object(
                watch,
                "_sync_ofac_weak_alias_audit",
                return_value=0,
            ),
        ):
            result = watch.run_ofac(
                session=object(),
                st=st,
                rows=rows,
                hb=hb,
                opts=opts,
            )

        expected_urls = []
        expected_previous = {}
        expected_filenames = []
        last_modified = (
            "Sat, 12 Sep 2026 00:00:00 GMT"
        )

        for key, cfg in ofac.LISTS.items():
            expected_urls.extend([
                cfg["prim"],
                cfg["alt"],
                cfg["advanced"],
            ])

            expected_previous[cfg["prim"]] = {
                "sha256": f"{key}-primary-sha",
                "etag": f'"{key}-primary-etag"',
                "last_modified": last_modified,
                "filename": f"{key}-primary.csv",
            }
            expected_previous[cfg["alt"]] = {
                "sha256": f"{key}-alias-sha",
                "etag": f'"{key}-alias-etag"',
                "last_modified": last_modified,
                "filename": f"{key}-alias.csv",
            }
            expected_previous[cfg["advanced"]] = {
                "sha256": f"{key}-advanced-sha",
                "etag": f'"{key}-advanced-etag"',
                "last_modified": last_modified,
                "filename": f"{key}-advanced.xml",
            }

            expected_filenames.extend([
                f"{key}-primary.csv",
                f"{key}-alias.csv",
                f"{key}-advanced.xml",
            ])

        self.assertCountEqual(
            [url for url, _, _ in seen],
            expected_urls,
            "Primaryが304でもAliasとAdvancedを確認する",
        )
        self.assertTrue(
            all(
                allow_conditional
                for _, _, allow_conditional in seen
            ),
            "3文書すべて条件付きGETで確認する",
        )
        self.assertEqual(
            {
                url: previous
                for url, previous, _ in seen
            },
            expected_previous,
            "文書ごとに独立した前回メタデータを使用する",
        )
        self.assertCountEqual(
            [
                row["fetched_file"]
                for row in opts["audit"]
                if row["status"] == "unchanged"
            ],
            expected_filenames,
            "304監査行にも各文書名を残す",
        )
        self.assertEqual(result, [])

    def test_same_sha_http_200_refreshes_document_metadata(self):
        st = {}
        role_by_url = {}

        for key, cfg in ofac.LISTS.items():
            st[key] = {
                "sha256": f"{key}-primary-sha",
                "etag": f'"{key}-old-primary-etag"',
                "last_modified": "Sat, 12 Sep 2026 00:00:00 GMT",
                "filename": f"{key}-old-primary.csv",
                "alt_sha256": f"{key}-alt-sha",
                "alt_etag": f'"{key}-old-alt-etag"',
                "alt_last_modified": "Sat, 12 Sep 2026 00:00:00 GMT",
                "alt_filename": f"{key}-old-alt.csv",
                "advanced_sha256": f"{key}-advanced-sha",
                "advanced_etag": f'"{key}-old-advanced-etag"',
                "advanced_last_modified": "Sat, 12 Sep 2026 00:00:00 GMT",
                "advanced_filename": f"{key}-old-advanced.xml",
                "raw_advanced": f"data/raw/old/{key}.xml.gz",
                "advanced_baseline_synced": True,
                "advanced_master_synced": True,
                "record_count": 1,
            }

            role_by_url[cfg["prim"]] = (key, "primary")
            role_by_url[cfg["alt"]] = (key, "alt")
            role_by_url[cfg["advanced"]] = (key, "advanced")

        rows = {}
        hb = []
        opts = {"audit": []}

        def fake_fetch(
            url,
            prev=None,
            session=None,
            allow_conditional=True,
        ):
            key, role = role_by_url[url]
            fetched = FakeFetched(
                url,
                sha256=(prev or {}).get("sha256", ""),
            )
            fetched.etag = f'"{key}-new-{role}-etag"'
            fetched.last_modified = (
                "Sun, 13 Sep 2026 00:00:00 GMT"
            )
            fetched.filename = f"{key}-new-{role}.dat"
            fetched.not_modified = False
            fetched.http_status = 200
            return fetched

        with (
            patch.object(
                watch,
                "fetch",
                side_effect=fake_fetch,
            ),
            patch.object(
                watch.OI,
                "load",
                return_value={},
            ),
            patch.object(
                watch,
                "_sync_ofac_weak_alias_audit",
                return_value=0,
            ),
        ):
            result = watch.run_ofac(
                session=object(),
                st=st,
                rows=rows,
                hb=hb,
                opts=opts,
            )

        for key in ofac.LISTS:
            self.assertEqual(
                st[key]["etag"],
                f'"{key}-new-primary-etag"',
            )
            self.assertEqual(
                st[key]["last_modified"],
                "Sun, 13 Sep 2026 00:00:00 GMT",
            )
            self.assertEqual(
                st[key]["filename"],
                f"{key}-new-primary.dat",
            )
            self.assertEqual(
                st[key]["alt_etag"],
                f'"{key}-new-alt-etag"',
            )
            self.assertEqual(
                st[key]["alt_last_modified"],
                "Sun, 13 Sep 2026 00:00:00 GMT",
            )
            self.assertEqual(
                st[key]["alt_filename"],
                f"{key}-new-alt.dat",
            )
            self.assertEqual(
                st[key]["advanced_etag"],
                f'"{key}-new-advanced-etag"',
            )
            self.assertEqual(
                st[key]["advanced_last_modified"],
                "Sun, 13 Sep 2026 00:00:00 GMT",
            )
            self.assertEqual(
                st[key]["advanced_filename"],
                f"{key}-new-advanced.dat",
            )

        self.assertEqual(result, [])

    def test_alias_or_advanced_only_change_triggers_validation(self):
        def old_sha(key, role):
            if role == "prim":
                return f"{key}-primary-sha"
            return f"{key}-{role}-sha"

        for changed_role in ("alt", "advanced"):
            with self.subTest(changed_role=changed_role):
                st = {}
                role_by_url = {}

                for key, cfg in ofac.LISTS.items():
                    st[key] = {
                        "sha256": old_sha(key, "prim"),
                        "etag": f'"{key}-primary-etag"',
                        "last_modified": (
                            "Sat, 12 Sep 2026 00:00:00 GMT"
                        ),
                        "alt_sha256": old_sha(key, "alt"),
                        "alt_etag": f'"{key}-alt-etag"',
                        "alt_last_modified": (
                            "Sat, 12 Sep 2026 00:00:00 GMT"
                        ),
                        "advanced_sha256": old_sha(
                            key,
                            "advanced",
                        ),
                        "advanced_etag": (
                            f'"{key}-advanced-etag"'
                        ),
                        "advanced_last_modified": (
                            "Sat, 12 Sep 2026 00:00:00 GMT"
                        ),
                        "raw_advanced": (
                            f"data/raw/old/{key}.xml.gz"
                        ),
                        "advanced_baseline_synced": True,
                        "advanced_master_synced": True,
                        "record_count": 1,
                    }

                    role_by_url[cfg["prim"]] = (
                        key,
                        "prim",
                    )
                    role_by_url[cfg["alt"]] = (
                        key,
                        "alt",
                    )
                    role_by_url[cfg["advanced"]] = (
                        key,
                        "advanced",
                    )

                rows = {}
                hb = []
                opts = {"audit": []}

                def fake_fetch(
                    url,
                    prev=None,
                    session=None,
                    allow_conditional=True,
                ):
                    key, role = role_by_url[url]
                    previous_sha = (prev or {}).get(
                        "sha256",
                        "",
                    )

                    sha256 = (
                        f"{key}-{role}-changed-sha"
                        if role == changed_role
                        else previous_sha
                    )

                    fetched = FakeFetched(
                        url,
                        sha256=sha256,
                    )

                    if (
                        allow_conditional
                        and role != changed_role
                    ):
                        fetched.not_modified = True
                        fetched.body = None
                        fetched.http_status = 304

                    return fetched

                def fake_advanced(fetched, label):
                    return (
                        [
                            record(
                                label,
                                "advanced_xml_v3",
                            )
                        ],
                        {"1"},
                    )

                def fake_classic(prim, alt, label):
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
                    ) as parse_advanced_mock,
                    patch.object(
                        watch.ofac,
                        "parse",
                        side_effect=fake_classic,
                    ) as parse_classic_mock,
                    patch.object(
                        watch.ofac,
                        "validate_party_coverage",
                    ) as coverage_mock,
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
                        return_value=watch.M.Diff(
                            source=ofac.SOURCE,
                        ),
                    ),
                    patch.object(
                        watch,
                        "_sync_ofac_weak_alias_audit",
                        return_value=0,
                    ),
                ):
                    watch.run_ofac(
                        session=object(),
                        st=st,
                        rows=rows,
                        hb=hb,
                        opts=opts,
                    )

                expected_calls = len(ofac.LISTS)

                self.assertEqual(
                    parse_advanced_mock.call_count,
                    expected_calls,
                    f"{changed_role}単独変更でもAdvancedを検証する",
                )
                self.assertEqual(
                    parse_classic_mock.call_count,
                    expected_calls,
                    f"{changed_role}単独変更でもClassicを検証する",
                )
                self.assertEqual(
                    coverage_mock.call_count,
                    expected_calls,
                    f"{changed_role}単独変更でもcoverageを検証する",
                )

                state_field = (
                    "alt_sha256"
                    if changed_role == "alt"
                    else "advanced_sha256"
                )

                for key in ofac.LISTS:
                    self.assertEqual(
                        st[key][state_field],
                        f"{key}-{changed_role}-changed-sha",
                    )

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

        self.assertEqual(
            hb,
            [],
            "OFAC全体失敗時に正常heartbeatを残してはいけない",
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


class OfacRemovalTransactionTest(unittest.TestCase):
    snapshot_hash = "a" * 64
    official_url = (
        "https://ofac.treasury.gov/recent-actions/20260916"
    )

    @staticmethod
    def _state() -> dict:
        def item(key: str) -> dict:
            return {
                "sha256": f"old-{key}-primary",
                "alt_sha256": f"old-{key}-alias",
                "advanced_sha256": f"old-{key}-advanced",
                "raw_advanced": f"data/raw/old/{key}.xml.gz",
                "advanced_baseline_synced": True,
                "advanced_master_synced": True,
            }

        return {key: item(key) for key in ofac.LISTS}

    @staticmethod
    def _master_rows() -> dict:
        return {
            "alpha": {
                "match_key": "alpha",
                "display_name": "ALPHA",
                "status": watch.M.STATUS_ACTIVE,
                "risk_type": watch.M.RISK_TYPE,
                "risk_level": watch.M.RISK_LEVEL,
                "first_seen_ms": "1000",
                "last_updated_ms": "1000",
                "sources": "OFAC",
                "categories": "OFAC:SDN",
                "remark": "OFAC SDN",
                "invalid_reason": "",
                "review_flag": "",
                "variants": '["ALPHA"]',
            },
        }

    @staticmethod
    def _history() -> dict:
        def item(
            list_name: str,
            party_id: str,
            name: str,
            key: str,
        ) -> dict:
            return {
                "list": list_name,
                "party_id": party_id,
                "name": name,
                "match_key": key,
                "first_seen_ms": "1000",
                "last_changed_ms": "1000",
                "alias_current": "1",
                "party_current": "1",
                "formats": "advanced_xml_v3;classic_csv",
                "primary": "1",
                "low_quality": "0",
            }

        return {
            ("SDN", "100", "ALPHA"): item(
                "SDN", "100", "ALPHA", "alpha"
            ),
            ("SDN", "1", "SDN TEST NAME"): item(
                "SDN", "1", "SDN TEST NAME", "sdntestname"
            ),
            (
                "Consolidated",
                "1",
                "Consolidated TEST NAME",
            ): item(
                "Consolidated",
                "1",
                "Consolidated TEST NAME",
                "consolidatedtestname",
            ),
        }

    def _write_approval(
        self,
        path: Path,
        *,
        snapshot_hash: str | None = None,
        extra_party: bool = False,
    ) -> None:
        rows = [{
            "list": "SDN",
            "snapshot_sha256": (
                snapshot_hash or self.snapshot_hash
            ),
            "party_id": "100",
            "party_name": "ALPHA",
            "decision": "APPROVED",
            "approved_by": "reviewer",
            "approved_at": "2026-09-17T00:00:00Z",
            "official_url": self.official_url,
            "notes": "official deletion",
        }]
        if extra_party:
            rows.append({
                **rows[0],
                "party_id": "999",
                "party_name": "EXTRA",
            })

        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=OR.APPROVAL_FIELDS,
            )
            writer.writeheader()
            writer.writerows(rows)

    def _run(
        self,
        approval_path: Path,
        *,
        audit_failure: bool = False,
        st: dict | None = None,
        rows: dict | None = None,
        hb: list[dict] | None = None,
        opts: dict | None = None,
    ) -> tuple[dict, dict, list, dict]:
        st = self._state() if st is None else st
        rows = self._master_rows() if rows is None else rows
        hb = [] if hb is None else hb
        opts = {"audit": []} if opts is None else opts
        history = self._history()

        def fake_fetch(
            url,
            prev=None,
            session=None,
            allow_conditional=True,
        ):
            if url == ofac.LISTS["ofac_sdn"]["advanced"]:
                sha256 = self.snapshot_hash
            elif url == ofac.LISTS["ofac_cons"]["advanced"]:
                sha256 = "b" * 64
            else:
                sha256 = "c" * 64
            return FakeFetched(url, sha256=sha256)

        def fake_advanced(fetched, label):
            return [record(label, "advanced_xml_v3")], {"1"}

        def fake_classic(prim, alt, label):
            return [record(label, "classic_csv")]

        audit_side_effect = (
            OR.AuditError("forced audit failure")
            if audit_failure
            else None
        )

        with (
            patch.object(watch, "OFAC_REMOVAL_APPROVALS", approval_path),
            patch.object(watch, "fetch", side_effect=fake_fetch),
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
            patch.object(watch.ofac, "validate_party_coverage"),
            patch.object(watch.OI, "load", return_value=history),
            patch.object(
                watch.M,
                "merge",
                return_value=watch.M.Diff(source=ofac.SOURCE),
            ),
            patch.object(
                watch,
                "_sync_ofac_weak_alias_audit",
                return_value=0,
            ),
            patch.object(
                watch.OR,
                "build_audit_rows",
                side_effect=audit_side_effect,
                wraps=(
                    None
                    if audit_failure
                    else OR.build_audit_rows
                ),
            ),
        ):
            watch.run_ofac(
                session=object(),
                st=st,
                rows=rows,
                hb=hb,
                opts=opts,
            )

        return st, rows, hb, opts

    def test_exact_approval_commits_removal_index_and_audit_together(self):
        with tempfile.TemporaryDirectory() as td:
            approval_path = Path(td) / "approvals.csv"
            self._write_approval(approval_path)

            st, rows, hb, opts = self._run(approval_path)

        self.assertEqual(rows["alpha"]["status"], watch.M.STATUS_INACTIVE)
        self.assertEqual(rows["alpha"]["sources"], "")
        self.assertEqual(rows["alpha"]["invalid_reason"], watch.M.DELISTED)
        self.assertEqual(len(hb), 2)

        index_rows = opts["ofac_index_rows"]
        alpha_index = index_rows[("SDN", "100", "ALPHA")]
        self.assertEqual(alpha_index["party_current"], "0")
        self.assertEqual(alpha_index["alias_current"], "0")
        self.assertEqual(
            opts["ofac_index_diff"].removed_parties,
            {("SDN", "100")},
        )

        audit_rows = opts["ofac_removal_audit_rows"]
        self.assertEqual(len(audit_rows), 1)
        self.assertEqual(audit_rows[0]["party_id"], "100")
        self.assertEqual(audit_rows[0]["snapshot_sha256"], self.snapshot_hash)
        for field in (
            "master_before_sha256",
            "master_after_sha256",
            "index_before_sha256",
            "index_after_sha256",
        ):
            self.assertEqual(len(audit_rows[0][field]), 64)
        self.assertNotEqual(
            audit_rows[0]["master_before_sha256"],
            audit_rows[0]["master_after_sha256"],
        )
        self.assertNotEqual(
            audit_rows[0]["index_before_sha256"],
            audit_rows[0]["index_after_sha256"],
        )

    def test_non_exact_approval_keeps_transaction_unpublished(self):
        cases = (
            ("missing", None),
            ("wrong hash", {"snapshot_hash": "d" * 64}),
            ("extra party", {"extra_party": True}),
        )

        for label, approval_options in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as td:
                approval_path = Path(td) / "approvals.csv"
                if approval_options is not None:
                    self._write_approval(
                        approval_path,
                        **approval_options,
                    )

                st = self._state()
                rows = self._master_rows()
                before_st = copy.deepcopy(st)
                before_rows = copy.deepcopy(rows)
                hb: list[dict] = []
                opts = {"audit": []}

                with self.assertRaises(OR.ApprovalError):
                    self._run(
                        approval_path,
                        st=st,
                        rows=rows,
                        hb=hb,
                        opts=opts,
                    )

                self.assertEqual(st, before_st)
                self.assertEqual(rows, before_rows)
                self.assertEqual(hb, [])
                self.assertNotIn("ofac_index_rows", opts)
                self.assertNotIn("ofac_removal_audit_rows", opts)

    def test_audit_build_failure_rolls_back_removal_and_index(self):
        with tempfile.TemporaryDirectory() as td:
            approval_path = Path(td) / "approvals.csv"
            self._write_approval(approval_path)
            st = self._state()
            rows = self._master_rows()
            before_st = copy.deepcopy(st)
            before_rows = copy.deepcopy(rows)
            hb: list[dict] = []
            opts = {"audit": []}

            with self.assertRaisesRegex(
                OR.AuditError,
                "forced audit failure",
            ):
                self._run(
                    approval_path,
                    audit_failure=True,
                    st=st,
                    rows=rows,
                    hb=hb,
                    opts=opts,
                )

            self.assertEqual(st, before_st)
            self.assertEqual(rows, before_rows)
            self.assertEqual(hb, [])
            self.assertNotIn("ofac_index_rows", opts)
            self.assertNotIn("ofac_removal_audit_rows", opts)


if __name__ == "__main__":
    unittest.main()
