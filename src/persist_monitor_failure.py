"""失敗時にOFAC attemptファイルだけをクリーンなmainから保存する。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from .monitor_attempt import ATTEMPT_PATH, ROOT


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], check=check, capture_output=True, text=True)


def _finished(record: dict) -> datetime:
    return datetime.strptime(record["finished_at"], "%Y-%m-%dT%H:%M:%SZ")


def persist_failure(root: Path, *, run_id: str) -> bool:
    path = root / ATTEMPT_PATH
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    record = json.loads(text)
    if not run_id or record.get("run_id") != run_id or record.get("outcome") != "failure":
        return False
    if not ({"ofac", "all"} & set(record.get("sources", []))):
        return False
    finished = _finished(record)
    with tempfile.TemporaryDirectory(prefix="ofac-monitor-") as directory:
        clean = Path(directory) / "clean"
        created = False
        try:
            for _ in range(3):
                _git(root, "fetch", "origin", "+refs/heads/main:refs/remotes/origin/main")
                if not created:
                    _git(root, "worktree", "add", "--detach", str(clean), "origin/main")
                    created = True
                else:
                    # Only this disposable worktree is reset. Original staged data stays untouched.
                    _git(clean, "reset", "--hard", "origin/main")
                destination = clean / ATTEMPT_PATH
                if destination.exists():
                    previous = json.loads(destination.read_text(encoding="utf-8"))
                    if _finished(previous) >= finished:
                        return False
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(text, encoding="utf-8")
                _git(clean, "add", "--", ATTEMPT_PATH.as_posix())
                _git(clean, "-c", "user.name=sanctions-watch[bot]", "-c", "user.email=sanctions-watch@users.noreply.github.com", "commit", "-m", f"chore(monitor): OFAC取得失敗 run {run_id}")
                pushed = _git(clean, "push", "origin", "HEAD:refs/heads/main", check=False)
                if pushed.returncode == 0:
                    print("OFAC失敗状態のみをmainへ保存しました")
                    return True
            raise RuntimeError("OFAC失敗状態のpushに3回失敗しました")
        finally:
            if created:
                _git(root, "worktree", "remove", "--force", str(clean))
    return False


def main() -> int:
    if os.environ.get("GITHUB_REF") != "refs/heads/main":
        print("main以外では失敗状態の保存をスキップします")
        return 0
    persisted = persist_failure(ROOT, run_id=os.environ.get("GITHUB_RUN_ID", ""))
    if not persisted:
        print("今回のOFAC失敗状態は未作成、またはより新しい状態が保存済みです")
    return 0


if __name__ == "__main__":
    sys.exit(main())
