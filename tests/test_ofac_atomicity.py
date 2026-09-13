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

    def test_unchanged_primary_still_checks_alias_and_advanced(self):
        def make_state(key):
            return {
                "sha256": f"{key}-primary-sha",
                "etag": f'"{key}-primary-etag"',
                "last_modified": "Sat, 12 Sep 2026 00:00:00 GMT",
                "alt_sha256": f"{key}-alias-sha",
                "alt_etag": f'"{key}-alias-etag"',
                "alt_last_modified": "Sat, 12 Sep 2026 00:00:00 GMT",
                "advanced_sha256": f"{key}-advanced-sha",
                "advanced_etag": f'"{key}-advanced-etag"',
                "advanced_last_modified": "Sat, 12 Sep 2026 00:00:00 GMT",
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
        expected_sha = {}

        for key, cfg in ofac.LISTS.items():
            expected_urls.extend([
                cfg["prim"],
                cfg["alt"],
                cfg["advanced"],
            ])
            expected_sha[cfg["prim"]] = (
                f"{key}-primary-sha"
            )
            expected_sha[cfg["alt"]] = (
                f"{key}-alias-sha"
            )
            expected_sha[cfg["advanced"]] = (
                f"{key}-advanced-sha"
            )

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
                url: previous.get("sha256")
                for url, previous, _ in seen
            },
            expected_sha,
            "文書ごとに独立した前回SHAを使用する",
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
