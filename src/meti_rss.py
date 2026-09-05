"""METIニュースリリースRSSを使った外国ユーザーリスト更新候補センサー。

RSSは正本ではなく「検知センサー」。
候補を見つけたら data/dashboard/changes.csv に
「RSS更新候補（未確定）」として記録し、既存のGoogle Sheets同期へ流す。

Slack等の外部通知は行わない。
既存の制裁リスト監視ダッシュボードを運用・レビュー画面とする。
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable
import xml.etree.ElementTree as ET

import requests

from .dashboard import CHANGE_COLS, MAX_CHANGES

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "data" / "meti_rss" / "state.json"
DASH_CHANGES = ROOT / "data" / "dashboard" / "changes.csv"

# 経産省公式RSSページの「ニュースリリース」Atom。
FEED_URL = "https://www.meti.go.jp/ml_index_release_atom.xml"
UA = "sanctions-watch/1.0 (METI RSS compliance monitor; contact via repository issues)"
JST = timezone(timedelta(hours=9))

DIRECT_TERMS = (
    "外国ユーザーリスト",
    "外国ユーザー・リスト",
)

SUBJECT_TERMS = (
    "安全保障貿易管理",
    "キャッチオール規制",
    "補完的輸出規制",
    "大量破壊兵器",
    "通常兵器",
    "輸出管理",
)

ACTION_TERMS = (
    "改正",
    "更新",
    "見直し",
    "追加",
    "変更",
)

STRIP_TAGS_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")


class FeedSchemaError(RuntimeError):
    """RSS/Atomの構造が想定外。変更なし扱いにしない。"""


@dataclass(frozen=True)
class Entry:
    entry_id: str
    title: str
    url: str
    published: str
    text: str


@dataclass(frozen=True)
class Candidate:
    entry_id: str
    title: str
    url: str
    published: str
    reason: str
    confidence: str


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _clean_text(value: str | None) -> str:
    s = html.unescape(value or "")
    s = STRIP_TAGS_RE.sub(" ", s)
    return SPACE_RE.sub(" ", s).strip()


def _child_text(node: ET.Element, names: Iterable[str]) -> str:
    wanted = {n.lower() for n in names}
    for child in list(node):
        if _local(child.tag) in wanted:
            return _clean_text("".join(child.itertext()))
    return ""


def _entry_url(node: ET.Element) -> str:
    # Atom: <link href="..."/>
    for child in list(node):
        if _local(child.tag) == "link":
            href = (child.attrib.get("href") or "").strip()
            rel = (child.attrib.get("rel") or "alternate").strip().lower()
            if href and rel in {"alternate", ""}:
                return href

    # RSS: <link>https://...</link>
    return _child_text(node, ("link",))


def parse_entries(xml_body: bytes | str) -> list[Entry]:
    try:
        root = ET.fromstring(xml_body)
    except ET.ParseError as exc:
        raise FeedSchemaError(f"METI RSS/Atom XMLを解析できない: {exc}") from exc

    root_name = _local(root.tag)
    if root_name not in {"feed", "rss", "rdf"}:
        raise FeedSchemaError(f"未知のRSSルート要素: {root.tag}")

    if root_name == "feed":
        nodes = [n for n in list(root) if _local(n.tag) == "entry"]
    else:
        nodes = [n for n in root.iter() if _local(n.tag) == "item"]

    if not nodes:
        raise FeedSchemaError("RSS/Atomにentry/itemが1件も見つからない")

    entries: list[Entry] = []
    for node in nodes:
        title = _child_text(node, ("title",))
        url = _entry_url(node)
        entry_id = _child_text(node, ("id", "guid")) or url
        published = _child_text(node, ("published", "updated", "pubdate", "date"))
        summary = _child_text(node, ("summary", "content", "description"))
        combined = _clean_text(" ".join([title, summary, url]))

        if not title:
            raise FeedSchemaError("RSS項目にtitleがない")
        if not entry_id:
            raise FeedSchemaError(f"RSS項目にid/guid/linkがない: title={title!r}")

        entries.append(
            Entry(
                entry_id=entry_id,
                title=title,
                url=url,
                published=published,
                text=combined,
            )
        )

    return entries


def classify(entry: Entry) -> Candidate | None:
    text = entry.text

    direct_hits = [t for t in DIRECT_TERMS if t in text]
    if direct_hits:
        return Candidate(
            entry_id=entry.entry_id,
            title=entry.title,
            url=entry.url,
            published=entry.published,
            reason="直接一致: " + ", ".join(direct_hits),
            confidence="HIGH",
        )

    subject_hits = [t for t in SUBJECT_TERMS if t in text]
    action_hits = [t for t in ACTION_TERMS if t in text]

    if subject_hits and action_hits:
        return Candidate(
            entry_id=entry.entry_id,
            title=entry.title,
            url=entry.url,
            published=entry.published,
            reason=(
                "文脈一致: "
                + ", ".join(subject_hits[:3])
                + " + "
                + ", ".join(action_hits[:3])
            ),
            confidence="REVIEW",
        )

    return None


def load_state(path: Path = STATE_PATH) -> dict:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise FeedSchemaError(f"METI RSS stateを読めない: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise FeedSchemaError("METI RSS stateのルートがobjectではない")
    return value


def save_state(state: dict, path: Path = STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def _read_dashboard_changes(path: Path) -> list[list[str]]:
    if not path.exists():
        return []

    with path.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))

    if not rows:
        return []

    if rows[0] != CHANGE_COLS:
        raise FeedSchemaError(
            "dashboard/changes.csv のスキーマ不一致: "
            f"expected={CHANGE_COLS} actual={rows[0]}"
        )

    return rows[1:]


def append_dashboard_rows(rows: list[list[str]], path: Path = DASH_CHANGES) -> None:
    """既存ダッシュボードの変更履歴CSVへ新しいイベントを先頭追加する。"""
    if not rows:
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    old = _read_dashboard_changes(path)

    # 同一行の重複を避ける。Apps Script側でもイベントキーで二重取込を防ぐ。
    old_keys = {tuple(r[:6]) for r in old if len(r) >= 6}
    new = [r for r in rows if tuple(r[:6]) not in old_keys]

    keep = (new + old)[:MAX_CHANGES]

    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(CHANGE_COLS)
        w.writerows(keep)


def candidate_dashboard_row(c: Candidate, detected_at: datetime) -> list[str]:
    stamp = detected_at.astimezone(JST).strftime("%Y-%m-%d %H:%M:%S")
    reason = f"{c.confidence}: {c.reason}"
    if c.published:
        reason += f" / 公開日時: {c.published}"

    return [
        stamp,
        "経済産業省",
        "RSS更新候補（未確定）",
        c.title,
        reason,
        c.url or FEED_URL,
    ]


def system_dashboard_row(
    event_type: str,
    detail: str,
    detected_at: datetime,
) -> list[str]:
    stamp = detected_at.astimezone(JST).strftime("%Y-%m-%d %H:%M:%S")
    return [
        stamp,
        "経済産業省",
        event_type,
        "METIニュースリリースRSS",
        detail[:1000],
        FEED_URL,
    ]


def _write_output(key: str, value: str | int | bool) -> None:
    target = os.environ.get("GITHUB_OUTPUT", "").strip()
    if not target:
        return

    if isinstance(value, bool):
        text = "true" if value else "false"
    else:
        text = str(value)

    text = text.replace("\r", " ").replace("\n", " ")
    with open(target, "a", encoding="utf-8") as f:
        f.write(f"{key}={text}\n")


def fetch_feed(session: requests.Session) -> bytes:
    r = session.get(
        FEED_URL,
        headers={
            "User-Agent": UA,
            "Accept": (
                "application/atom+xml, application/rss+xml, "
                "application/xml, text/xml;q=0.9"
            ),
            "Cache-Control": "no-cache",
        },
        timeout=45,
    )
    r.raise_for_status()

    if not r.content.strip():
        raise FeedSchemaError("METI RSSがHTTP成功なのに本文0 bytes")

    return r.content


def _error_fingerprint(exc: Exception) -> str:
    return hashlib.sha256(
        f"{type(exc).__name__}|{exc}".encode("utf-8", "replace")
    ).hexdigest()


def run(*, state_path: Path = STATE_PATH) -> int:
    now = datetime.now(timezone.utc)
    state = load_state(state_path)

    try:
        with requests.Session() as session:
            body = fetch_feed(session)

        entries = parse_entries(body)

    except Exception as exc:
        fp = _error_fingerprint(exc)

        # 同じ障害を15分ごとに何百行も増やさない。
        if state.get("last_error_fingerprint") != fp:
            append_dashboard_rows(
                [
                    system_dashboard_row(
                        "RSS監視エラー",
                        f"{type(exc).__name__}: {exc}",
                        now,
                    )
                ]
            )

        state.update(
            version=2,
            feed_url=FEED_URL,
            last_error_fingerprint=fp,
            last_error_type=type(exc).__name__,
            last_error_message=str(exc)[:1000],
        )
        save_state(state, state_path)

        _write_output("candidate", False)
        _write_output("candidate_count", 0)
        _write_output("sensor_ok", False)

        print(f"[ERROR] METI RSS: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    all_candidates = [c for e in entries if (c := classify(e)) is not None]
    current_candidate_ids = [c.entry_id for c in all_candidates]

    if not state.get("baseline_synced"):
        # デプロイ以前の記事を「新規検知」にしない。
        state = {
            "version": 2,
            "feed_url": FEED_URL,
            "baseline_synced": True,
            "seen_candidate_ids": current_candidate_ids[-1000:],
        }
        save_state(state, state_path)

        print(
            "[baseline] METI RSS: "
            f"feed {len(entries)}件 / 該当候補 {len(all_candidates)}件を既読化"
        )
        _write_output("candidate", False)
        _write_output("candidate_count", 0)
        _write_output("sensor_ok", True)
        return 0

    seen = set(state.get("seen_candidate_ids") or [])
    new_candidates = [c for c in all_candidates if c.entry_id not in seen]

    if state.get("last_error_fingerprint"):
        append_dashboard_rows(
            [
                system_dashboard_row(
                    "RSS監視復旧",
                    "前回のRSS監視エラーから正常取得へ復旧",
                    now,
                )
            ]
        )

    if new_candidates:
        append_dashboard_rows(
            [candidate_dashboard_row(c, now) for c in new_candidates]
        )

        merged = list(
            dict.fromkeys(
                list(state.get("seen_candidate_ids") or [])
                + [c.entry_id for c in new_candidates]
            )
        )[-1000:]

        state["seen_candidate_ids"] = merged
        state["last_candidate"] = {
            "entry_id": new_candidates[-1].entry_id,
            "title": new_candidates[-1].title,
            "url": new_candidates[-1].url,
            "published": new_candidates[-1].published,
            "detected_at": now.isoformat(timespec="seconds"),
        }

    # エラー復旧時、または候補追加時だけstateに意味のある変更が出る。
    state["version"] = 2
    state["feed_url"] = FEED_URL
    state["baseline_synced"] = True
    state.pop("last_error_fingerprint", None)
    state.pop("last_error_type", None)
    state.pop("last_error_message", None)
    save_state(state, state_path)

    print(
        f"[checked] METI RSS: feed {len(entries)}件 / "
        f"該当候補 {len(all_candidates)}件 / 新規候補 {len(new_candidates)}件"
    )

    _write_output("candidate", bool(new_candidates))
    _write_output("candidate_count", len(new_candidates))
    _write_output("sensor_ok", True)

    if new_candidates:
        newest = new_candidates[-1]
        _write_output("candidate_title", newest.title)
        _write_output("candidate_url", newest.url)
        _write_output("candidate_confidence", newest.confidence)

    return 0


def main() -> int:
    argparse.ArgumentParser().parse_args()
    from .meti_rss_audit import run_audited
    return run_audited()


if __name__ == "__main__":
    sys.exit(main())

