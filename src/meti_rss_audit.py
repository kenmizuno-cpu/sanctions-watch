"""METI公式ニュースリリースRSSの金融業務向け監査ラッパー。

15分ごとの通常成功はローカルruntimeへ保存し、GitHubへ毎回commitしない。
GitHubへ残すのは、本文変更・候補検知・新規異常・復旧など意味のあるイベントだけ。
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests

from . import meti_rss as mr
from . import source_audit
from .fetch import Fetched, archive

DEFAULT_STALE_HOURS = 24 * 7

RUNTIME_HB_COLS = [
    "checked_at", "source", "document_role", "status", "http_status",
    "etag", "last_modified", "content_hash", "record_count",
    "candidate_count", "latest_entry_updated", "entry_age_hours",
    "fetch_failed", "schema_changed", "url", "final_url",
    "error_type", "error_message",
]


class FeedStaleError(RuntimeError):
    """HTTP取得は成功しても、RSSの最終entryが古すぎる場合。"""


def _utc_text(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_dt(value: str) -> datetime | None:
    s = str(value or "").strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = parsedate_to_datetime(s)
        except (TypeError, ValueError):
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def latest_entry_updated(entries: list[mr.Entry]) -> str:
    parsed: list[datetime] = []
    for entry in entries:
        dt = _parse_dt(entry.published)
        if dt is None:
            raise mr.FeedSchemaError(
                f"RSS項目の日時を解析できない: title={entry.title!r} value={entry.published!r}"
            )
        parsed.append(dt)
    if not parsed:
        raise mr.FeedSchemaError("RSS最終entry日時を判定できない")
    return _utc_text(max(parsed))


def entry_age_hours(latest_updated: str, now: datetime) -> float:
    dt = _parse_dt(latest_updated)
    if dt is None:
        raise mr.FeedSchemaError(f"RSS最終entry日時を解析できない: {latest_updated!r}")
    return max(0.0, (now.astimezone(timezone.utc) - dt).total_seconds() / 3600.0)


def stale_threshold_hours() -> int:
    raw = os.environ.get("METI_RSS_STALE_HOURS", str(DEFAULT_STALE_HOURS)).strip()
    try:
        hours = int(raw)
    except ValueError as exc:
        raise mr.FeedSchemaError(f"METI_RSS_STALE_HOURS が整数ではない: {raw!r}") from exc
    if hours < 24:
        raise mr.FeedSchemaError("METI_RSS_STALE_HOURS は24時間以上にしてください")
    return hours


def runtime_dir() -> Path:
    raw = os.environ.get("METI_RSS_RUNTIME_DIR", "").strip()
    if raw:
        return Path(raw).expanduser()
    return mr.ROOT / ".runtime" / "meti_rss"


def fetch_feed(session: requests.Session, prev: dict | None = None) -> Fetched:
    prev = prev or {}
    headers = {
        "User-Agent": mr.UA,
        "Accept": "application/atom+xml, application/rss+xml, application/xml, text/xml;q=0.9",
        "Cache-Control": "no-cache",
    }
    if prev.get("etag"):
        headers["If-None-Match"] = str(prev["etag"])
    if prev.get("last_modified"):
        headers["If-Modified-Since"] = str(prev["last_modified"])

    response = session.get(mr.FEED_URL, headers=headers, timeout=45)
    if response.status_code == 304:
        return Fetched(
            url=mr.FEED_URL,
            body=None,
            sha256=str(prev.get("content_hash") or ""),
            not_modified=True,
            etag=response.headers.get("ETag", "") or str(prev.get("etag") or ""),
            last_modified=(response.headers.get("Last-Modified", "") or str(prev.get("last_modified") or "")),
            filename="ml_index_release_atom.xml",
            http_status=304,
            final_url=str(response.url or mr.FEED_URL),
            headers=dict(response.headers),
        )

    response.raise_for_status()
    body = response.content
    return Fetched(
        url=mr.FEED_URL,
        body=body,
        sha256=hashlib.sha256(body).hexdigest(),
        not_modified=False,
        etag=response.headers.get("ETag", "") or "",
        last_modified=response.headers.get("Last-Modified", "") or "",
        filename="ml_index_release_atom.xml",
        http_status=response.status_code,
        final_url=str(response.url or mr.FEED_URL),
        headers=dict(response.headers),
    )


def _runtime_row(*, now: datetime, status: str, fetched: Fetched | None,
                 record_count: int | str = "", candidate_count: int | str = "",
                 latest_updated: str = "", age_hours: float | str = "",
                 fetch_failed: bool = False, schema_changed: bool = False,
                 error: Exception | None = None) -> dict:
    row = {c: "" for c in RUNTIME_HB_COLS}
    row.update(
        checked_at=_utc_text(now),
        source="meti_rss",
        document_role="METI_NEWS_RELEASE_RSS_SENSOR",
        status=status,
        record_count=record_count,
        candidate_count=candidate_count,
        latest_entry_updated=latest_updated,
        entry_age_hours=(f"{age_hours:.1f}" if isinstance(age_hours, float) else age_hours),
        fetch_failed="1" if fetch_failed else "0",
        schema_changed="1" if schema_changed else "0",
        url=mr.FEED_URL,
    )
    if fetched is not None:
        row.update(
            http_status=fetched.http_status,
            etag=fetched.etag,
            last_modified=fetched.last_modified,
            content_hash=fetched.sha256,
            final_url=fetched.final_url,
        )
    if error is not None:
        response = getattr(error, "response", None)
        if response is not None:
            row["http_status"] = getattr(response, "status_code", "") or ""
            headers = getattr(response, "headers", {}) or {}
            row["etag"] = headers.get("ETag", "") or ""
            row["last_modified"] = headers.get("Last-Modified", "") or ""
            row["final_url"] = str(getattr(response, "url", "") or mr.FEED_URL)
        row["error_type"] = type(error).__name__
        row["error_message"] = " ".join(str(error).splitlines())[:2000]
    return row


def write_runtime_heartbeat(row: dict, *, healthy: bool, fetch_success: bool,
                            base_dir: Path | None = None) -> None:
    base = base_dir or runtime_dir()
    hb_dir = base / "heartbeat"
    hb_dir.mkdir(parents=True, exist_ok=True)

    checked = _parse_dt(str(row.get("checked_at") or "")) or datetime.now(timezone.utc)
    hb_path = hb_dir / f"{checked:%Y-%m}.csv"
    new = not hb_path.exists() or hb_path.stat().st_size == 0
    with hb_path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=RUNTIME_HB_COLS, lineterminator="\n")
        if new:
            writer.writeheader()
        writer.writerow({c: row.get(c, "") for c in RUNTIME_HB_COLS})

    status_path = base / "status.json"
    previous: dict = {}
    if status_path.exists():
        try:
            previous = json.loads(status_path.read_text(encoding="utf-8"))
        except Exception:
            previous = {}

    status = {
        "version": 1,
        "last_checked_at": row.get("checked_at", ""),
        "status": row.get("status", ""),
        "http_status": row.get("http_status", ""),
        "etag": row.get("etag", ""),
        "last_modified": row.get("last_modified", ""),
        "content_hash": row.get("content_hash", ""),
        "record_count": row.get("record_count", ""),
        "candidate_count": row.get("candidate_count", ""),
        "latest_entry_updated": row.get("latest_entry_updated", ""),
        "entry_age_hours": row.get("entry_age_hours", ""),
        "fetch_failed": row.get("fetch_failed", ""),
        "schema_changed": row.get("schema_changed", ""),
        "url": row.get("url", ""),
        "final_url": row.get("final_url", ""),
        "error_type": row.get("error_type", ""),
        "error_message": row.get("error_message", ""),
        "last_fetch_success_at": previous.get("last_fetch_success_at", ""),
        "last_healthy_at": previous.get("last_healthy_at", ""),
    }
    if fetch_success:
        status["last_fetch_success_at"] = row.get("checked_at", "")
    if healthy:
        status["last_healthy_at"] = row.get("checked_at", "")

    base.mkdir(parents=True, exist_ok=True)
    tmp = status_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(status, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(status_path)


def _audit_event(*, status: str, fetched: Fetched | None,
                 record_count: int | str, latest_updated: str,
                 fetch_failed: bool = False, schema_changed: bool = False,
                 error: Exception | None = None) -> None:
    if fetched is not None:
        row = source_audit.entry(
            "meti_rss", "METI_NEWS_RELEASE_RSS_SENSOR", status,
            fetched=fetched, source_updated=latest_updated,
            record_count=record_count, fetch_failed=fetch_failed,
            schema_changed=schema_changed, error=error,
        )
    elif error is not None:
        row = source_audit.error_entry(
            "meti_rss", "METI_NEWS_RELEASE_RSS_SENSOR", error,
            url=mr.FEED_URL, status=status,
            fetch_failed=fetch_failed, schema_changed=schema_changed,
        )
        row["source_updated"] = latest_updated
        row["record_count"] = record_count
    else:
        row = source_audit.entry(
            "meti_rss", "METI_NEWS_RELEASE_RSS_SENSOR", status,
            source_updated=latest_updated, record_count=record_count,
            fetch_failed=fetch_failed, schema_changed=schema_changed,
            url=mr.FEED_URL,
        )
    source_audit.write(mr.ROOT, [row])


def _issue_fingerprint(kind: str, message: str) -> str:
    # stale の age=...h は15分ごとに増えるため、そのままfingerprintへ
    # 入れると同じ継続異常を毎回「新規異常」と誤判定する。
    stable_message = str(message)

    if kind == "stale":
        stable_message = re.sub(
            r"age=[0-9]+(?:\.[0-9]+)?h",
            "age=<dynamic>",
            stable_message,
        )

    return hashlib.sha256(
        f"{kind}|{stable_message}".encode("utf-8", "replace")
    ).hexdigest()


def _record_issue(*, state: dict, state_path: Path, now: datetime,
                  kind: str, dashboard_type: str, message: str,
                  fetched: Fetched | None, record_count: int | str,
                  candidate_count: int | str, latest_updated: str,
                  age_hours: float | str, fetch_failed: bool,
                  schema_changed: bool, error: Exception) -> int:
    fingerprint = _issue_fingerprint(kind, message)
    is_new = state.get("last_issue_fingerprint") != fingerprint

    if is_new:
        mr.append_dashboard_rows([mr.system_dashboard_row(dashboard_type, message, now)])
        _audit_event(
            status=kind, fetched=fetched, record_count=record_count,
            latest_updated=latest_updated, fetch_failed=fetch_failed,
            schema_changed=schema_changed, error=error,
        )
        state["last_issue_fingerprint"] = fingerprint
        state["last_issue_type"] = kind
        state["last_issue_message"] = message[:1000]
        state["last_issue_at"] = now.isoformat(timespec="seconds")
        mr.save_state(state, state_path)

    runtime = _runtime_row(
        now=now, status=kind, fetched=fetched,
        record_count=record_count, candidate_count=candidate_count,
        latest_updated=latest_updated, age_hours=age_hours,
        fetch_failed=fetch_failed, schema_changed=schema_changed,
        error=error,
    )
    write_runtime_heartbeat(
        runtime, healthy=False,
        fetch_success=(fetched is not None and not fetch_failed),
    )

    mr._write_output("candidate", False)
    mr._write_output("candidate_count", 0)
    mr._write_output("sensor_ok", False)
    print(f"[ERROR] METI RSS {kind}: {message}", file=sys.stderr)
    return 2 if kind == "stale" else 1


def run_audited(*, state_path: Path = mr.STATE_PATH) -> int:
    now = datetime.now(timezone.utc)
    state = mr.load_state(state_path)

    fetched: Fetched | None = None
    entries: list[mr.Entry] = []
    all_candidates: list[mr.Candidate] = []
    latest_updated = str(state.get("latest_entry_updated") or "")
    record_count = int(state.get("record_count") or 0)
    candidate_count = int(state.get("current_candidate_count") or 0)
    age_hours: float | str = ""

    try:
        with requests.Session() as session:
            fetched = fetch_feed(session, state)

        if fetched.not_modified:
            if not state.get("audit_baseline_synced"):
                raise mr.FeedSchemaError("304 Not Modifiedだが監査baselineが未確立")
            if not latest_updated or not record_count:
                raise mr.FeedSchemaError("304 Not Modifiedだが前回件数/最終entry日時がない")
        else:
            entries = mr.parse_entries(fetched.body or b"")
            record_count = len(entries)
            all_candidates = [c for e in entries if (c := mr.classify(e)) is not None]
            candidate_count = len(all_candidates)
            latest_updated = latest_entry_updated(entries)
            age_hours = entry_age_hours(latest_updated, now)

            previous_hash = str(state.get("content_hash") or "")
            evidence_baseline = not bool(state.get("audit_baseline_synced"))
            content_changed = bool(previous_hash and previous_hash != fetched.sha256)
            first_hash = not previous_hash

            if evidence_baseline or content_changed or first_hash:
                archive(fetched, "meti_rss", mr.ROOT, compress=True)

            state.update(
                version=3,
                feed_url=mr.FEED_URL,
                audit_baseline_synced=True,
                etag=fetched.etag,
                last_modified=fetched.last_modified,
                content_hash=fetched.sha256,
                record_count=record_count,
                current_candidate_count=candidate_count,
                latest_entry_updated=latest_updated,
            )

            if evidence_baseline:
                _audit_event(status="baseline", fetched=fetched,
                             record_count=record_count, latest_updated=latest_updated)
            elif content_changed:
                _audit_event(status="source_updated", fetched=fetched,
                             record_count=record_count, latest_updated=latest_updated)

            mr.save_state(state, state_path)

        if age_hours == "":
            age_hours = entry_age_hours(latest_updated, now)

        threshold = stale_threshold_hours()
        if float(age_hours) > threshold:
            exc = FeedStaleError(
                f"RSS最終entryが古すぎる: latest={latest_updated}, "
                f"age={float(age_hours):.1f}h, threshold={threshold}h"
            )
            return _record_issue(
                state=state, state_path=state_path, now=now,
                kind="stale", dashboard_type="RSS鮮度異常",
                message=str(exc), fetched=fetched,
                record_count=record_count, candidate_count=candidate_count,
                latest_updated=latest_updated, age_hours=float(age_hours),
                fetch_failed=False, schema_changed=False, error=exc,
            )

        if fetched.not_modified:
            new_candidates: list[mr.Candidate] = []
        else:
            if not state.get("baseline_synced"):
                state["baseline_synced"] = True
                state["seen_candidate_ids"] = [c.entry_id for c in all_candidates][-1000:]
                new_candidates = []
                print(f"[baseline] METI RSS: feed {record_count}件 / 該当候補 {candidate_count}件を既読化")
            else:
                seen = set(state.get("seen_candidate_ids") or [])
                new_candidates = [c for c in all_candidates if c.entry_id not in seen]
                if new_candidates:
                    mr.append_dashboard_rows([mr.candidate_dashboard_row(c, now) for c in new_candidates])
                    merged = list(dict.fromkeys(
                        list(state.get("seen_candidate_ids") or [])
                        + [c.entry_id for c in new_candidates]
                    ))[-1000:]
                    state["seen_candidate_ids"] = merged
                    newest = new_candidates[-1]
                    state["last_candidate"] = {
                        "entry_id": newest.entry_id,
                        "title": newest.title,
                        "url": newest.url,
                        "published": newest.published,
                        "detected_at": now.isoformat(timespec="seconds"),
                    }
                    _audit_event(status="candidate_detected", fetched=fetched,
                                 record_count=record_count, latest_updated=latest_updated)

        if state.get("last_issue_fingerprint"):
            previous_issue = str(state.get("last_issue_type") or "unknown")
            mr.append_dashboard_rows([
                mr.system_dashboard_row(
                    "RSS監視復旧",
                    f"前回のRSS異常({previous_issue})から正常状態へ復旧",
                    now,
                )
            ])
            _audit_event(status="recovered", fetched=fetched,
                         record_count=record_count, latest_updated=latest_updated)
            for key in ("last_issue_fingerprint", "last_issue_type",
                        "last_issue_message", "last_issue_at"):
                state.pop(key, None)

        for key in ("last_error_fingerprint", "last_error_type", "last_error_message"):
            state.pop(key, None)

        state["version"] = 3
        state["feed_url"] = mr.FEED_URL
        state["baseline_synced"] = True
        mr.save_state(state, state_path)

        runtime = _runtime_row(
            now=now,
            status="unchanged" if fetched.not_modified else "ok",
            fetched=fetched,
            record_count=record_count,
            candidate_count=candidate_count,
            latest_updated=latest_updated,
            age_hours=float(age_hours),
        )
        write_runtime_heartbeat(runtime, healthy=True, fetch_success=True)

        new_count = len(new_candidates)
        print(
            f"[checked] METI RSS: HTTP {fetched.http_status} / feed {record_count}件 / "
            f"該当候補 {candidate_count}件 / 新規候補 {new_count}件 / "
            f"latest {latest_updated} / age {float(age_hours):.1f}h"
        )
        mr._write_output("candidate", bool(new_candidates))
        mr._write_output("candidate_count", new_count)
        mr._write_output("sensor_ok", True)
        if new_candidates:
            newest = new_candidates[-1]
            mr._write_output("candidate_title", newest.title)
            mr._write_output("candidate_url", newest.url)
            mr._write_output("candidate_confidence", newest.confidence)
        return 0

    except mr.FeedSchemaError as exc:
        return _record_issue(
            state=state, state_path=state_path, now=now,
            kind="schema_error", dashboard_type="RSSスキーマ異常",
            message=str(exc), fetched=fetched,
            record_count=record_count, candidate_count=candidate_count,
            latest_updated=latest_updated, age_hours=age_hours,
            fetch_failed=False, schema_changed=True, error=exc,
        )
    except Exception as exc:
        return _record_issue(
            state=state, state_path=state_path, now=now,
            kind="fetch_error", dashboard_type="RSS監視エラー",
            message=f"{type(exc).__name__}: {exc}", fetched=fetched,
            record_count=record_count, candidate_count=candidate_count,
            latest_updated=latest_updated, age_hours=age_hours,
            fetch_failed=True, schema_changed=False, error=exc,
        )
