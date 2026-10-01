"""OFAC取得実行の成否を業務データと別のファイルへ記録する。"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ATTEMPT_PATH = Path("data/monitoring/ofac_attempt.json")


def timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_attempt(root: Path, command: list[str], sources: list[str]) -> int:
    started_at = timestamp()
    try:
        code = subprocess.run(command, cwd=root, check=False).returncode
    except OSError as error:
        print(f"OFAC取得プロセスを開始できません: {error}", file=sys.stderr)
        code = 1
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    record = {
        "started_at": started_at,
        "finished_at": timestamp(),
        "outcome": "success" if code == 0 else "failure",
        "exit_code": code,
        "sources": sources,
        "run_id": run_id,
        "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", ""),
        "run_url": f"{server}/{repo}/actions/runs/{run_id}" if repo and run_id else "",
    }
    path = root / ATTEMPT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as file:
            staged = Path(file.name)
            file.write(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
        os.replace(staged, path)
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)
    return code


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sources", nargs="+", required=True, choices=["ofac", "mof", "all"])
    args = parser.parse_args()
    if "ofac" not in args.sources and "all" not in args.sources:
        parser.error("OFACを含む取得だけに使用してください")
    return run_attempt(ROOT, [sys.executable, "-m", "src.watch", "--sources", *args.sources], args.sources)


if __name__ == "__main__":
    sys.exit(main())
