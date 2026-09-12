from __future__ import annotations

import csv
import os
import stat
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from src import persistence as P
from src import dashboard as D
from src import master as M
from src import ofac_index as OI
from src import state as S
from src import watch


class PersistenceAtomicityTest(unittest.TestCase):

    def test_temp_close_failure_removes_orphan(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "state.json"
            target.write_text("old\n", encoding="utf-8")
            real_close = os.close

            def close_then_fail(fd):
                real_close(fd)
                raise OSError("injected temp close failure")

            with patch.object(
                P.os,
                "close",
                side_effect=close_then_fail,
            ):
                with self.assertRaisesRegex(
                    OSError,
                    "injected temp close failure",
                ):
                    P.atomic_replace_many([
                        P.FileWrite(
                            target=target,
                            writer=lambda path: path.write_text(
                                "new\n",
                                encoding="utf-8",
                            ),
                        ),
                    ])

            self.assertEqual(
                sorted(path.name for path in root.iterdir()),
                ["state.json"],
            )

    def test_duplicate_targets_are_rejected_before_staging(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "state.json"
            target.write_text("old\n", encoding="utf-8")

            writes = [
                P.FileWrite(
                    target=target,
                    writer=lambda path: path.write_text(
                        "first\n",
                        encoding="utf-8",
                    ),
                ),
                P.FileWrite(
                    target=target,
                    writer=lambda path: path.write_text(
                        "second\n",
                        encoding="utf-8",
                    ),
                ),
            ]

            with self.assertRaisesRegex(
                ValueError,
                "duplicate target",
            ):
                P.atomic_replace_many(writes)

            self.assertEqual(
                target.read_text(encoding="utf-8"),
                "old\n",
            )
            self.assertEqual(
                sorted(path.name for path in root.iterdir()),
                ["state.json"],
            )

    def test_success_uses_normal_mode_for_new_formal_file(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "new-heartbeat.csv"

            P.atomic_replace_many([
                P.FileWrite(
                    target=target,
                    writer=lambda path: path.write_text(
                        "new\n",
                        encoding="utf-8",
                    ),
                ),
            ])

            self.assertEqual(
                stat.S_IMODE(target.stat().st_mode),
                0o644,
            )

    def test_temp_fsync_failure_does_not_touch_formal_file(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "state.json"
            target.write_text("old-state\n", encoding="utf-8")

            with patch.object(
                P.os,
                "fsync",
                side_effect=OSError("injected temp fsync failure"),
            ):
                with self.assertRaisesRegex(
                    OSError,
                    "injected temp fsync failure",
                ):
                    P.atomic_replace_many([
                        P.FileWrite(
                            target=target,
                            writer=lambda path: path.write_text(
                                "new-state\n",
                                encoding="utf-8",
                            ),
                        ),
                    ])

            self.assertEqual(
                target.read_text(encoding="utf-8"),
                "old-state\n",
            )
            self.assertEqual(
                sorted(path.name for path in root.iterdir()),
                ["state.json"],
            )

    def test_backup_fsync_failure_removes_partial_backup(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "state.json"
            target.write_text("old-state\n", encoding="utf-8")
            fsync_calls = 0

            def fail_backup_fsync(_fd):
                nonlocal fsync_calls
                fsync_calls += 1

                if fsync_calls == 2:
                    raise OSError("injected backup fsync failure")

            with patch.object(
                P.os,
                "fsync",
                side_effect=fail_backup_fsync,
            ):
                with self.assertRaisesRegex(
                    OSError,
                    "injected backup fsync failure",
                ):
                    P.atomic_replace_many([
                        P.FileWrite(
                            target=target,
                            writer=lambda path: path.write_text(
                                "new-state\n",
                                encoding="utf-8",
                            ),
                        ),
                    ])

            self.assertEqual(
                target.read_text(encoding="utf-8"),
                "old-state\n",
            )
            self.assertEqual(
                sorted(path.name for path in root.iterdir()),
                ["state.json"],
            )

    def test_cleanup_failure_does_not_mask_success_or_stop_cleanup(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "state.json"
            target.write_text("old-state\n", encoding="utf-8")
            real_unlink = Path.unlink
            failed_once = False

            def fail_first_temp_cleanup(path, *args, **kwargs):
                nonlocal failed_once

                if path.suffix == ".tmp" and not failed_once:
                    failed_once = True
                    raise OSError("injected cleanup failure")

                return real_unlink(path, *args, **kwargs)

            with (
                patch.object(
                    Path,
                    "unlink",
                    side_effect=fail_first_temp_cleanup,
                    autospec=True,
                ),
                self.assertLogs(
                    "src.persistence",
                    level="ERROR",
                ) as caught,
            ):
                P.atomic_replace_many([
                    P.FileWrite(
                        target=target,
                        writer=lambda path: path.write_text(
                            "new-state\n",
                            encoding="utf-8",
                        ),
                    ),
                ])

            self.assertEqual(
                target.read_text(encoding="utf-8"),
                "new-state\n",
            )
            self.assertFalse(
                list(root.glob("*.bak")),
                "temp cleanup失敗後もbackup cleanupを継続する",
            )
            self.assertTrue(
                any(
                    "injected cleanup failure" in item
                    for item in caught.output
                )
            )

    def test_success_preserves_existing_file_mode(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "master.csv"
            target.write_text("old\n", encoding="utf-8")
            target.chmod(0o640)

            P.atomic_replace_many([
                P.FileWrite(
                    target=target,
                    writer=lambda path: path.write_text(
                        "new\n",
                        encoding="utf-8",
                    ),
                ),
            ])

            self.assertEqual(
                target.read_text(encoding="utf-8"),
                "new\n",
            )
            self.assertEqual(
                stat.S_IMODE(target.stat().st_mode),
                0o640,
            )

    def test_staging_failure_keeps_formal_files_and_removes_temps(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            first = root / "first.txt"
            second = root / "second.txt"

            first.write_text("old-first\n", encoding="utf-8")
            second.write_text("old-second\n", encoding="utf-8")

            def fail_while_writing(path: Path):
                path.write_text("partial\n", encoding="utf-8")
                raise OSError("injected staging failure")

            writes = [
                P.FileWrite(
                    target=first,
                    writer=lambda path: path.write_text(
                        "new-first\n",
                        encoding="utf-8",
                    ),
                ),
                P.FileWrite(
                    target=second,
                    writer=fail_while_writing,
                ),
            ]

            with self.assertRaisesRegex(
                OSError,
                "injected staging failure",
            ):
                P.atomic_replace_many(writes)

            self.assertEqual(
                first.read_text(encoding="utf-8"),
                "old-first\n",
            )
            self.assertEqual(
                second.read_text(encoding="utf-8"),
                "old-second\n",
            )
            self.assertEqual(
                sorted(path.name for path in root.iterdir()),
                ["first.txt", "second.txt"],
            )


class WatchPersistenceAtomicityTest(unittest.TestCase):

    def test_success_commits_core_and_dashboard_generation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fixed_now = datetime(
                2026,
                9,
                12,
                1,
                2,
                3,
                tzinfo=timezone.utc,
            )
            rows = {
                "alpha": {
                    "match_key": "alpha",
                    "display_name": "ALPHA",
                    "status": "有効",
                    "risk_type": "制裁リスト",
                    "risk_level": "高",
                    "sources": "OFAC",
                    "categories": "OFAC:SDN",
                    "remark": "OFAC:SDN",
                },
            }
            index_rows = {
                ("SDN", "1", "ALPHA"): {
                    "list": "SDN",
                    "party_id": "1",
                    "name": "ALPHA",
                    "match_key": "alpha",
                    "alias_current": "1",
                    "party_current": "1",
                },
            }
            state = {
                "ofac_sdn": {
                    "sha256": "new-sha-1234567890",
                    "record_count": 1,
                },
            }
            heartbeat = [
                {
                    "source": "ofac_sdn",
                    "status": "changed",
                    "content_hash": "new-sha-1234567890",
                    "record_count": 1,
                },
            ]
            diffs = [
                M.Diff(
                    source="OFAC",
                    added=[
                        {
                            "name": "ALPHA",
                            "remark": "OFAC:SDN",
                        },
                    ],
                ),
            ]

            watch._persist_outputs_atomically(
                root=root,
                rows=rows,
                state=state,
                heartbeat=heartbeat,
                diffs=diffs,
                ofac_index_rows=index_rows,
                now=fixed_now,
            )

            self.assertIn(
                ("SDN", "1", "ALPHA"),
                OI.load(root / watch.OFAC_INDEX_REL),
            )
            self.assertIn(
                "alpha",
                M.load(root / watch.MASTER_REL),
            )
            self.assertEqual(
                S.load_state(root),
                state,
            )

            with (
                root / "data/heartbeat/2026-09.csv"
            ).open(encoding="utf-8", newline="") as f:
                saved_heartbeat = list(csv.DictReader(f))

            self.assertEqual(
                saved_heartbeat[-1]["checked_at"],
                "2026-09-12T01:02:03Z",
            )
            self.assertIn(
                "ALPHA",
                (root / watch.DIFF_MD_REL).read_text(
                    encoding="utf-8"
                ),
            )
            self.assertIn(
                "ALPHA",
                (root / watch.DIFF_CSV_REL).read_text(
                    encoding="utf-8"
                ),
            )

            dashboard_root = root / D.DASH
            for name in (
                "status.csv",
                "changes.csv",
                "list.csv",
                "screening.csv",
                "screening.csv.gz",
            ):
                self.assertTrue(
                    (dashboard_root / name).exists(),
                    name,
                )

            self.assertIn(
                "ALPHA",
                (dashboard_root / "changes.csv").read_text(
                    encoding="utf-8"
                ),
            )
            D.assert_screening_gzip_consistent(
                rows,
                dashboard_root / "screening.csv.gz",
            )
            self.assertFalse(list(root.rglob("*.tmp")))
            self.assertFalse(list(root.rglob("*.bak")))

    def test_projection_cleanup_failure_does_not_mask_committed_generation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fixed_now = datetime(
                2026,
                9,
                12,
                1,
                2,
                3,
                tzinfo=timezone.utc,
            )
            rows = {
                "alpha": {
                    "match_key": "alpha",
                    "display_name": "ALPHA",
                    "status": "有効",
                    "sources": "OFAC",
                },
            }

            with (
                patch.object(
                    watch.shutil,
                    "rmtree",
                    side_effect=OSError(
                        "injected projection cleanup failure"
                    ),
                ),
                self.assertLogs(
                    "src.watch",
                    level="ERROR",
                ) as caught,
            ):
                watch._persist_outputs_atomically(
                    root=root,
                    rows=rows,
                    state={"ofac_sdn": {"sha256": "new-sha"}},
                    heartbeat=[
                        {
                            "source": "ofac_sdn",
                            "status": "changed",
                        },
                    ],
                    diffs=[],
                    now=fixed_now,
                )

            self.assertIn(
                '"sha256": "new-sha"',
                (root / "data/state.json").read_text(
                    encoding="utf-8"
                ),
            )
            self.assertTrue(
                any(
                    "injected projection cleanup failure" in item
                    for item in caught.output
                )
            )

    def test_projection_cleanup_failure_does_not_mask_primary_failure(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            invalid_rows = {
                "broken": {
                    "match_key": "",
                    "display_name": "BROKEN ACTIVE NAME",
                    "status": "有効",
                },
            }

            with (
                patch.object(
                    watch.shutil,
                    "rmtree",
                    side_effect=OSError(
                        "injected projection cleanup failure"
                    ),
                ),
                self.assertLogs(
                    "src.watch",
                    level="ERROR",
                ),
            ):
                with self.assertRaisesRegex(
                    ValueError,
                    "空match_key",
                ):
                    watch._persist_outputs_atomically(
                        root=root,
                        rows=invalid_rows,
                        state={},
                        heartbeat=[],
                        diffs=[],
                        now=datetime(
                            2026,
                            9,
                            12,
                            tzinfo=timezone.utc,
                        ),
                    )

    def test_dashboard_validation_failure_keeps_old_generation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fixed_now = datetime(
                2026,
                9,
                12,
                1,
                2,
                3,
                tzinfo=timezone.utc,
            )
            relative_targets = [
                "data/master/ofac_alias_history.csv",
                "data/heartbeat/2026-09.csv",
                "data/master/master.csv",
                "data/diff/latest.md",
                "data/diff/latest.csv",
                "data/state.json",
                "data/dashboard/status.csv",
                "data/dashboard/changes.csv",
                "data/dashboard/list.csv",
                "data/dashboard/screening.csv",
                "data/dashboard/screening.csv.gz",
            ]
            targets = [
                root / relative
                for relative in relative_targets
            ]
            before = {}

            for number, target in enumerate(targets, start=1):
                target.parent.mkdir(parents=True, exist_ok=True)
                payload = f"old-{number}\n".encode()
                target.write_bytes(payload)
                before[target] = payload

            invalid_rows = {
                "broken": {
                    "match_key": "",
                    "display_name": "BROKEN ACTIVE NAME",
                    "status": "有効",
                },
            }
            diffs = [
                M.Diff(
                    source="OFAC",
                    added=[
                        {
                            "name": "BROKEN ACTIVE NAME",
                            "remark": "OFAC:SDN",
                        },
                    ],
                ),
            ]

            with self.assertRaisesRegex(
                ValueError,
                "空match_key",
            ):
                watch._persist_outputs_atomically(
                    root=root,
                    rows=invalid_rows,
                    state={"ofac_sdn": {"sha256": "new-sha"}},
                    heartbeat=[
                        {
                            "source": "ofac_sdn",
                            "status": "changed",
                        },
                    ],
                    diffs=diffs,
                    ofac_index_rows={},
                    now=fixed_now,
                )

            self.assertEqual(
                {target: target.read_bytes() for target in targets},
                before,
            )

    def test_state_replace_failure_restores_whole_ofac_generation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fixed_now = datetime(
                2026,
                9,
                12,
                1,
                2,
                3,
                tzinfo=timezone.utc,
            )

            targets = [
                root / "data/master/ofac_alias_history.csv",
                root / "data/heartbeat/2026-09.csv",
                root / "data/master/master.csv",
                root / "data/diff/latest.md",
                root / "data/diff/latest.csv",
                root / "data/state.json",
            ]

            before = {}

            for number, target in enumerate(targets, start=1):
                target.parent.mkdir(parents=True, exist_ok=True)
                payload = f"old-{number}\n".encode()
                target.write_bytes(payload)
                before[target] = payload

            rows = {
                "new-name": {
                    "match_key": "new-name",
                    "display_name": "NEW NAME",
                    "status": "有効",
                },
            }
            index_rows = {
                ("SDN", "1", "NEW NAME"): {
                    "list": "SDN",
                    "party_id": "1",
                    "name": "NEW NAME",
                    "match_key": "new-name",
                },
            }
            state = {
                "ofac_sdn": {
                    "sha256": "new-sha",
                },
            }
            heartbeat = [
                {
                    "source": "ofac_sdn",
                    "status": "changed",
                    "content_hash": "new-sha",
                },
            ]
            diffs = [
                M.Diff(
                    source="OFAC",
                    added=[
                        {
                            "name": "NEW NAME",
                            "remark": "OFAC:SDN",
                        },
                    ],
                ),
            ]

            state_path = root / "data/state.json"
            real_replace = os.replace
            failed_once = False

            def fail_state_once(src, dst):
                nonlocal failed_once

                if Path(dst) == state_path and not failed_once:
                    failed_once = True
                    raise OSError("injected state replace failure")

                return real_replace(src, dst)

            with patch.object(
                P.os,
                "replace",
                side_effect=fail_state_once,
            ):
                with self.assertRaisesRegex(
                    OSError,
                    "injected state replace failure",
                ):
                    watch._persist_outputs_atomically(
                        root=root,
                        rows=rows,
                        state=state,
                        heartbeat=heartbeat,
                        diffs=diffs,
                        ofac_index_rows=index_rows,
                        now=fixed_now,
                    )

            self.assertEqual(
                {target: target.read_bytes() for target in targets},
                before,
            )
            self.assertFalse(
                list((root / "data/dashboard").glob("*")),
                "state確定失敗時は新規dashboardファイルも除去する",
            )


class PersistenceReplaceFailureTest(unittest.TestCase):

    def test_recovery_backup_stat_failure_cannot_abort_other_rollbacks(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            first = root / "first.txt"
            second = root / "second.txt"
            third = root / "third.txt"

            for target in (first, second, third):
                target.write_text(
                    f"old-{target.stem}\n",
                    encoding="utf-8",
                )

            real_replace = os.replace
            real_exists = Path.exists

            def fail_commit_and_one_rollback(src, dst):
                src = Path(src)
                dst = Path(dst)

                if dst == third and src.suffix == ".tmp":
                    raise OSError("injected commit failure")

                if dst == second and src.suffix == ".bak":
                    raise OSError("injected rollback failure")

                return real_replace(src, dst)

            def fail_backup_stat(path):
                if path.suffix == ".bak":
                    raise OSError("injected backup stat failure")

                return real_exists(path)

            writes = [
                P.FileWrite(
                    target=target,
                    writer=lambda path, name=target.stem: path.write_text(
                        f"new-{name}\n",
                        encoding="utf-8",
                    ),
                )
                for target in (first, second, third)
            ]

            with (
                patch.object(
                    P.os,
                    "replace",
                    side_effect=fail_commit_and_one_rollback,
                ),
                patch.object(
                    Path,
                    "exists",
                    side_effect=fail_backup_stat,
                    autospec=True,
                ),
            ):
                with self.assertRaises(P.AtomicRollbackError) as raised:
                    P.atomic_replace_many(writes)

            self.assertEqual(
                first.read_text(encoding="utf-8"),
                "old-first\n",
                "backup stat失敗でも残りのrollbackを継続する",
            )
            self.assertEqual(
                second.read_text(encoding="utf-8"),
                "new-second\n",
            )
            self.assertEqual(
                third.read_text(encoding="utf-8"),
                "old-third\n",
            )
            self.assertIn(
                second,
                raised.exception.recovery_backups,
            )

    def test_rollback_failure_continues_and_keeps_recovery_backup(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            first = root / "first.txt"
            second = root / "second.txt"
            third = root / "third.txt"

            for target in (first, second, third):
                target.write_text(
                    f"old-{target.stem}\n",
                    encoding="utf-8",
                )

            real_replace = os.replace

            def fail_commit_and_one_rollback(src, dst):
                src = Path(src)
                dst = Path(dst)

                if dst == third and src.suffix == ".tmp":
                    raise OSError("injected commit failure")

                if dst == second and src.suffix == ".bak":
                    raise OSError("injected rollback failure")

                return real_replace(src, dst)

            writes = [
                P.FileWrite(
                    target=target,
                    writer=lambda path, name=target.stem: path.write_text(
                        f"new-{name}\n",
                        encoding="utf-8",
                    ),
                )
                for target in (first, second, third)
            ]

            with patch.object(
                P.os,
                "replace",
                side_effect=fail_commit_and_one_rollback,
            ):
                with self.assertRaises(P.AtomicRollbackError) as raised:
                    P.atomic_replace_many(writes)

            self.assertEqual(
                first.read_text(encoding="utf-8"),
                "old-first\n",
                "別targetの復元失敗後も残りのrollbackを継続する",
            )
            self.assertEqual(
                second.read_text(encoding="utf-8"),
                "new-second\n",
            )
            self.assertEqual(
                third.read_text(encoding="utf-8"),
                "old-third\n",
            )

            recovery = raised.exception.recovery_backups
            self.assertEqual(set(recovery), {second})
            self.assertEqual(
                recovery[second].read_text(encoding="utf-8"),
                "old-second\n",
            )
            self.assertIn(
                f"{second} -> {recovery[second]}",
                str(raised.exception),
            )

    def test_replace_failure_restores_every_formal_file(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            first = root / "first.txt"
            second = root / "second.txt"

            first.write_text("old-first\n", encoding="utf-8")
            second.write_text("old-second\n", encoding="utf-8")

            real_replace = os.replace
            failed_once = False

            def fail_second_once(src, dst):
                nonlocal failed_once

                if Path(dst) == second and not failed_once:
                    failed_once = True
                    raise OSError("injected replace failure")

                return real_replace(src, dst)

            writes = [
                P.FileWrite(
                    target=first,
                    writer=lambda path: path.write_text(
                        "new-first\n",
                        encoding="utf-8",
                    ),
                ),
                P.FileWrite(
                    target=second,
                    writer=lambda path: path.write_text(
                        "new-second\n",
                        encoding="utf-8",
                    ),
                ),
            ]

            with patch.object(
                P.os,
                "replace",
                side_effect=fail_second_once,
            ):
                with self.assertRaisesRegex(
                    OSError,
                    "injected replace failure",
                ):
                    P.atomic_replace_many(writes)

            self.assertEqual(
                first.read_text(encoding="utf-8"),
                "old-first\n",
            )
            self.assertEqual(
                second.read_text(encoding="utf-8"),
                "old-second\n",
            )


if __name__ == "__main__":
    unittest.main()
