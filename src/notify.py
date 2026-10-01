"""Slack 通知。未設定・送信成功・送信失敗を明示する。"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def post(text: str, blocks=None) -> bool:
    url = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
    if not url:
        prefix = "::warning::" if os.environ.get("GITHUB_ACTIONS") == "true" else ""
        print(prefix + "SLACK_WEBHOOK_URL 未設定のため通知をスキップ（配信されていません）")
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


def diff_heading(*, added: int, removed: int, changed: int, backfilled: int) -> str:
    if not (added or removed or changed) and backfilled:
        return f":information_source: OFAC Advanced XML 初回同期 {backfilled} 件（公式の新規追加ではありません）"
    head = (f":rotating_light: 制裁リストに差分を検出 "
            f"（追加 {added} / 掲載終了 {removed} / 変更 {changed}")
    if backfilled:
        head += f" / 初回同期 {backfilled}"
    return head + "）"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", choices=["diff", "failure"], required=True)
    ap.add_argument("--added", default="0")
    ap.add_argument("--removed", default="0")
    ap.add_argument("--changed", default="0")
    ap.add_argument("--backfilled", default="0")
    args = ap.parse_args()

    link = _run_url()
    if args.kind == "diff":
        head = diff_heading(
            added=int(args.added),
            removed=int(args.removed),
            changed=int(args.changed),
            backfilled=int(args.backfilled),
        )
        body = ""
        p = ROOT / "data" / "diff" / "latest.md"
        if p.exists():
            body = p.read_text(encoding="utf-8")[:2500]
        text = head + ("\n\n```\n" + body + "\n```" if body else "")
    else:
        text = ":x: 制裁リスト監視が失敗した。取得元の書式変更かネットワーク障害の可能性"

    if link:
        text += f"\n{link}"
    configured = bool(os.environ.get("SLACK_WEBHOOK_URL", "").strip())
    try:
        delivered = post(text)
    except (urllib.error.URLError, OSError) as error:
        # Exception messages can contain the webhook secret; record only the type.
        print(f"Slack通知の送信に失敗: {type(error).__name__}", file=sys.stderr)
        delivered = False
    status = "sent" if delivered else ("failed" if configured else "skipped")
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as file:
            file.write(f"notification_status={status}\nnotification_sent={str(delivered).lower()}\n")
    print(f"Slack notification_status={status}")
    return 1 if status == "failed" else 0


if __name__ == "__main__":
    sys.exit(main())
