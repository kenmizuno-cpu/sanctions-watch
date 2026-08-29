"""Slack 通知。SLACK_WEBHOOK_URL 未設定なら黙ってスキップする。"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def post(text: str, blocks=None) -> bool:
    url = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
    if not url:
        print("SLACK_WEBHOOK_URL 未設定のため通知をスキップ")
        return False
    payload = {"text": text}
    if blocks:
        payload["blocks"] = blocks
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status == 200


def _run_url() -> str:
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    rid = os.environ.get("GITHUB_RUN_ID", "")
    return f"{server}/{repo}/actions/runs/{rid}" if repo and rid else ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", choices=["diff", "meti", "failure"], required=True)
    ap.add_argument("--added", default="0")
    ap.add_argument("--removed", default="0")
    ap.add_argument("--changed", default="0")
    args = ap.parse_args()

    link = _run_url()
    if args.kind == "diff":
        head = (f":rotating_light: 制裁リストに差分を検出 "
                f"（追加 {args.added} / 掲載終了 {args.removed} / 変更 {args.changed}）")
        body = ""
        p = ROOT / "data" / "diff" / "latest.md"
        if p.exists():
            body = p.read_text(encoding="utf-8")[:2500]
        text = head + ("\n\n```\n" + body + "\n```" if body else "")
    elif args.kind == "meti":
        text = (":memo: 経産省 外国ユーザーリストの告知ページが更新された。"
                "PDFのため自動取込できない。手作業での取り込みが必要")
    else:
        text = ":x: 制裁リスト監視が失敗した。取得元の書式変更かネットワーク障害の可能性"

    if link:
        text += f"\n{link}"
    post(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
