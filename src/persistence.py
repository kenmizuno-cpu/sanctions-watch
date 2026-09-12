"""複数ファイルを1世代として保存するための永続化補助。

各ファイルを正式パスと同じfilesystem上のtempへ先に書き、
全tempの生成成功後だけ正式パスへ置換する。
置換途中のI/O例外では、確定済みファイルを旧内容へ戻す。

保証範囲は、プロセスが捕捉できるI/O例外に対するfailure atomicity。
複数のos.replaceは単一OS操作ではないため、同時読取時の瞬間的な可視性や
SIGKILL・電源断を跨ぐatomicityまでは保証しない。
"""
from __future__ import annotations

import logging
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


LOGGER = logging.getLogger(__name__)


class AtomicRollbackError(RuntimeError):
    """commit失敗後に旧世代へ戻せなかったtargetがある。"""

    def __init__(
        self,
        commit_error: Exception,
        rollback_errors: dict[Path, Exception],
        recovery_backups: dict[Path, Path],
    ) -> None:
        self.commit_error = commit_error
        self.rollback_errors = rollback_errors
        self.recovery_backups = recovery_backups

        detail = "; ".join(
            f"{target}: {error}"
            for target, error in rollback_errors.items()
        )
        recovery = "; ".join(
            f"{target} -> {backup}"
            for target, backup in recovery_backups.items()
        ) or "none"
        super().__init__(
            "正式ファイル確定失敗後のrollbackにも失敗した。"
            f" commit={commit_error}; rollback={detail}; "
            f"recovery_backups={recovery}"
        )


@dataclass(frozen=True)
class FileWrite:
    """1つの正式ファイルと、そのtemp内容を生成するwriter。"""

    target: Path
    writer: Callable[[Path], object]
    seed_existing: bool = False


def _temp_path(target: Path, suffix: str) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_path = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=suffix,
        dir=target.parent,
    )
    path = Path(raw_path)

    try:
        os.close(fd)
    except Exception as error:
        try:
            path.unlink(missing_ok=True)
        except Exception as cleanup_error:
            error.add_note(
                f"temp cleanup also failed: {path}: {cleanup_error}"
            )
        raise

    return path


def _fsync_file(path: Path) -> None:
    with path.open("rb") as f:
        os.fsync(f.fileno())


def _cleanup_paths(paths: list[Path]) -> None:
    errors = []

    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except Exception as error:
            errors.append((path, error))

    if errors:
        detail = "; ".join(
            f"{path}: {error}"
            for path, error in errors
        )
        LOGGER.error(
            "persistence temp cleanup failed: %s",
            detail,
        )


def atomic_replace_many(writes: list[FileWrite]) -> None:
    """全writer成功後に正式ファイルを置換し、途中失敗時は復元する。"""
    seen: set[Path] = set()

    for item in writes:
        normalized = item.target.resolve(strict=False)

        if normalized in seen:
            raise ValueError(
                f"duplicate target: {item.target}"
            )

        seen.add(normalized)

    staged: list[tuple[FileWrite, Path]] = []
    backups: dict[Path, Path | None] = {}
    committed: list[Path] = []
    preserved_backups: set[Path] = set()

    try:
        for item in writes:
            temp_path = _temp_path(item.target, ".tmp")
            staged.append((item, temp_path))

            if item.seed_existing and item.target.exists():
                shutil.copy2(item.target, temp_path)

            item.writer(temp_path)

            if item.target.exists():
                shutil.copymode(item.target, temp_path)
            else:
                temp_path.chmod(0o644)

            _fsync_file(temp_path)

        for item, _ in staged:
            if item.target.exists():
                backup_path = _temp_path(item.target, ".bak")
                backups[item.target] = backup_path
                shutil.copy2(item.target, backup_path)
                _fsync_file(backup_path)
            else:
                backups[item.target] = None

        try:
            for item, temp_path in staged:
                os.replace(temp_path, item.target)
                committed.append(item.target)
        except Exception as commit_error:
            rollback_errors: dict[Path, Exception] = {}
            recovery_backups: dict[Path, Path] = {}

            for target in reversed(committed):
                backup_path = backups[target]

                if backup_path is not None:
                    preserved_backups.add(backup_path)

                try:
                    if backup_path is None:
                        target.unlink(missing_ok=True)
                    else:
                        os.replace(backup_path, target)
                except Exception as rollback_error:
                    rollback_errors[target] = rollback_error

                    if backup_path is not None:
                        recovery_backups[target] = backup_path
                else:
                    if backup_path is not None:
                        preserved_backups.discard(backup_path)

            if rollback_errors:
                raise AtomicRollbackError(
                    commit_error,
                    rollback_errors,
                    recovery_backups,
                ) from commit_error

            raise
    finally:
        cleanup_paths = [
            temp_path
            for _, temp_path in staged
        ]
        cleanup_paths.extend(
            backup_path
            for backup_path in backups.values()
            if (
                backup_path is not None
                and backup_path not in preserved_backups
            )
        )
        _cleanup_paths(cleanup_paths)
