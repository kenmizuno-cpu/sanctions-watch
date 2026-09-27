"""Lifecycle model and local operator boundary for METI manual monitoring.

The core transition functions are pure.  The CLI persists repository-local
evidence atomically and deliberately contains no network client or URL opener.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse, urlunparse

from . import dashboard, persistence, source_audit
from . import state as state_store


EVENT_COLS = [
    "event_id",
    "event_at",
    "state",
    "detection_id",
    "source_url",
    "source_hash",
    "operator",
    "detail",
]

ALLOWED_TRANSITIONS = {
    "": {"CHECKED_NO_CHANGE", "DETECTED"},
    "CHECKED_NO_CHANGE": {"CHECKED_NO_CHANGE", "DETECTED"},
    "DETECTED": {"MANUAL_FETCH_REQUIRED"},
    "MANUAL_FETCH_REQUIRED": {"FILE_RECEIVED", "BLOCKED"},
    "FILE_RECEIVED": {"VALIDATED", "BLOCKED"},
    "VALIDATED": {"DIFFED", "BLOCKED"},
    "DIFFED": {"REVIEW_REQUIRED", "BLOCKED"},
    "REVIEW_REQUIRED": {"APPROVED", "REJECTED", "BLOCKED"},
    "APPROVED": {"APPLIED", "APPLIED_WITH_HOLDS", "BLOCKED"},
    "REJECTED": {"CHECKED_NO_CHANGE", "DETECTED"},
    "APPLIED": {"CHECKED_NO_CHANGE", "DETECTED"},
    "APPLIED_WITH_HOLDS": {"CHECKED_NO_CHANGE", "DETECTED"},
    "BLOCKED": {"FILE_RECEIVED", "REJECTED"},
}

_TERMINAL_STATES = {"REJECTED", "APPLIED", "APPLIED_WITH_HOLDS"}
_OFFICIAL_HOSTS = {"meti.go.jp", "www.meti.go.jp"}
ROOT = Path(__file__).resolve().parent.parent


class LifecycleError(ValueError):
    """Raised when lifecycle input or a requested transition is invalid."""


def _timestamp(value: datetime) -> str:
    if not isinstance(value, datetime):
        raise LifecycleError("timestamp must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise LifecycleError("timestamp must include a timezone")
    return value.astimezone(timezone.utc).replace(microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _compact_json(value: Dict[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _detail_with_attempt(detail: str, attempt: int) -> str:
    payload: Dict[str, Any]
    if detail:
        try:
            parsed = json.loads(detail)
        except (TypeError, ValueError):
            payload = {"detail": detail}
        else:
            payload = parsed if isinstance(parsed, dict) else {"detail": detail}
    else:
        payload = {}
    payload["attempt"] = attempt
    return _compact_json(payload)


def validate_notice_url(value: str) -> str:
    """Return a canonical official METI URL without contacting the host."""

    if not isinstance(value, str) or not value.strip():
        raise LifecycleError("METI notice URL is required")

    candidate = value.strip()
    parsed = urlparse(candidate)
    try:
        port = parsed.port
    except ValueError as exc:
        raise LifecycleError("METI notice URL has an invalid port") from exc

    if parsed.scheme.lower() != "https":
        raise LifecycleError("METI notice URL must use HTTPS")
    if parsed.hostname not in _OFFICIAL_HOSTS:
        raise LifecycleError("METI notice URL must use an official METI host")
    if "@" in parsed.netloc or parsed.username is not None or parsed.password is not None:
        raise LifecycleError("METI notice URL must not contain user information")
    if port is not None or parsed.netloc.lower() != parsed.hostname:
        raise LifecycleError("METI notice URL must not contain an explicit port")
    if parsed.fragment:
        raise LifecycleError("METI notice URL must not contain a fragment")

    return urlunparse(
        (
            "https",
            parsed.hostname,
            parsed.path,
            parsed.params,
            parsed.query,
            "",
        )
    )


def _existing_event(events: List[dict], event_id: str) -> Any:
    for event in events:
        if event.get("event_id") == event_id:
            return event
    return None


def advance(
    state: dict,
    events: List[dict],
    *,
    new_state: str,
    event_at: datetime,
    operator: str,
    source_url: str = "",
    source_hash: str = "",
    detail: str = "",
    detection_id: str = "",
) -> Tuple[dict, List[dict], dict]:
    """Advance the in-memory lifecycle and append one immutable event."""

    next_state = deepcopy(state)
    next_events = deepcopy(events)
    current = str(next_state.get("lifecycle_state", ""))
    event_at_text = _timestamp(event_at)
    operator = operator.strip()
    if not operator:
        raise LifecycleError("operator is required")
    if new_state not in {value for values in ALLOWED_TRANSITIONS.values() for value in values}:
        raise LifecycleError("unknown lifecycle state: %s" % new_state)

    pending = next_state.get("pending_detection") or {}
    pending_id = str(pending.get("detection_id", ""))
    if new_state == "CHECKED_NO_CHANGE":
        effective_detection_id = ""
    else:
        effective_detection_id = detection_id or pending_id or str(
            next_state.get("detection_id", "")
        )
    if pending_id and detection_id and detection_id != pending_id:
        raise LifecycleError("detection ID does not match the pending detection")
    if new_state != "CHECKED_NO_CHANGE" and not effective_detection_id:
        raise LifecycleError("detection ID is required for this transition")

    try:
        attempt = int(pending.get("attempt", 0))
    except (TypeError, ValueError) as exc:
        raise LifecycleError("pending detection attempt is invalid") from exc

    if new_state == "FILE_RECEIVED" and current in {
        "MANUAL_FETCH_REQUIRED",
        "BLOCKED",
    }:
        attempt += 1

    if new_state == "CHECKED_NO_CHANGE":
        semantic_key = "|".join(
            (new_state, source_url, event_at_text, operator)
        )
    else:
        semantic_key = "|".join(
            (effective_detection_id, new_state, source_hash, str(attempt))
        )
    event_id = _sha256(semantic_key)
    existing = _existing_event(next_events, event_id)
    if existing is not None:
        return next_state, next_events, existing

    allowed = ALLOWED_TRANSITIONS.get(current, set())
    if new_state not in allowed:
        raise LifecycleError(
            "invalid lifecycle transition: %s -> %s" % (current or "<initial>", new_state)
        )

    event_detail = detail
    if new_state != "CHECKED_NO_CHANGE":
        event_detail = _detail_with_attempt(detail, attempt)
    event = {
        "event_id": event_id,
        "event_at": event_at_text,
        "state": new_state,
        "detection_id": effective_detection_id,
        "source_url": source_url,
        "source_hash": source_hash,
        "operator": operator,
        "detail": event_detail,
    }
    next_events.append(event)
    next_state["lifecycle_state"] = new_state
    if effective_detection_id:
        next_state["detection_id"] = effective_detection_id
    if new_state == "FILE_RECEIVED":
        updated_pending = deepcopy(pending)
        updated_pending["attempt"] = attempt
        next_state["pending_detection"] = updated_pending
    if new_state == "DIFFED":
        next_state["diffed_at"] = event_at_text
    if new_state in _TERMINAL_STATES:
        next_state["pending_detection"] = {}

    return next_state, next_events, event


def open_detection(
    state: dict,
    events: List[dict],
    *,
    operator: str,
    notice_url: str,
    title: str,
    detected_at: datetime,
    publication_at: str = "",
    note: str = "",
) -> Tuple[dict, List[dict], str]:
    """Open one manual intake without fetching the official notice."""

    canonical_url = validate_notice_url(notice_url)
    normalized_title = title.strip()
    if not normalized_title:
        raise LifecycleError("notice title is required")

    pending = state.get("pending_detection") or {}
    if pending:
        if (
            pending.get("notice_url") == canonical_url
            and pending.get("title") == normalized_title
        ):
            existing_id = str(pending.get("detection_id", ""))
            if not existing_id:
                raise LifecycleError("pending detection has no detection ID")
            return deepcopy(state), deepcopy(events), existing_id
        raise LifecycleError("another METI detection is already pending")

    detected_at_text = _timestamp(detected_at)
    detection_id = _sha256(
        "|".join((canonical_url, normalized_title, detected_at_text))
    )
    if any(
        event.get("detection_id") == detection_id
        for event in events
    ):
        return deepcopy(state), deepcopy(events), detection_id

    next_state = deepcopy(state)
    next_state.update(
        {
            "pending_detection": {
                "detection_id": detection_id,
                "notice_url": canonical_url,
                "title": normalized_title,
                "publication_at": publication_at,
                "note": note,
                "attempt": 0,
            },
            "detection_id": detection_id,
            "detected_at": detected_at_text,
            "sla_due_at": _timestamp(detected_at + timedelta(minutes=60)),
            "diffed_at": "",
            "sla_breached_at": "",
            "sla_recovered_at": "",
        }
    )
    detected_detail = _compact_json(
        {
            "note": note,
            "publication_at": publication_at,
            "title": normalized_title,
        }
    )
    next_state, next_events, _ = advance(
        next_state,
        events,
        new_state="DETECTED",
        event_at=detected_at,
        operator=operator,
        source_url=canonical_url,
        detail=detected_detail,
        detection_id=detection_id,
    )
    next_state, next_events, _ = advance(
        next_state,
        next_events,
        new_state="MANUAL_FETCH_REQUIRED",
        event_at=detected_at,
        operator=operator,
        source_url=canonical_url,
        detection_id=detection_id,
    )
    return next_state, next_events, detection_id


def cancel_blocked_detection(
    state: dict,
    events: List[dict],
    *,
    detection_id: str,
    operator: str,
    cancelled_at: datetime,
    note: str,
) -> Tuple[dict, List[dict]]:
    """Cancel one matching blocked intake without changing applied data."""

    detection_id = str(detection_id or "").strip()
    if not detection_id:
        raise LifecycleError("detection ID is required for cancellation")
    note = str(note or "").strip()
    if not note:
        raise LifecycleError("cancellation note is required")

    for event in reversed(events):
        if (
            event.get("detection_id") == detection_id
            and event.get("state") == "REJECTED"
        ):
            try:
                detail = json.loads(str(event.get("detail") or "{}"))
            except (TypeError, ValueError):
                detail = {}
            if detail.get("action") == "cancel_blocked_detection":
                return deepcopy(state), deepcopy(events)

    pending = state.get("pending_detection") or {}
    if str(state.get("lifecycle_state") or "") != "BLOCKED":
        raise LifecycleError("only a BLOCKED detection can be cancelled")
    if str(pending.get("detection_id") or "") != detection_id:
        raise LifecycleError("detection ID does not match the blocked detection")

    detail = _compact_json(
        {
            "action": "cancel_blocked_detection",
            "note": note,
        }
    )
    next_state, next_events, _ = advance(
        state,
        events,
        new_state="REJECTED",
        event_at=cancelled_at,
        operator=operator,
        source_url=str(pending.get("notice_url") or ""),
        detail=detail,
        detection_id=detection_id,
    )
    return next_state, next_events


def record_no_change(
    state: dict,
    events: List[dict],
    *,
    operator: str,
    source_url: str,
    checked_at: datetime,
    note: str = "",
) -> Tuple[dict, List[dict]]:
    """Record a completed manual check only when no intake is pending."""

    if state.get("pending_detection"):
        raise LifecycleError("cannot record no change while a detection is pending")
    canonical_url = validate_notice_url(source_url)
    next_state, next_events, _ = advance(
        state,
        events,
        new_state="CHECKED_NO_CHANGE",
        event_at=checked_at,
        operator=operator,
        source_url=canonical_url,
        detail=note,
    )
    next_state["last_manual_check_at"] = _timestamp(checked_at)
    next_state["last_manual_check_by"] = operator.strip()
    return next_state, next_events


@dataclass(frozen=True)
class LifecyclePaths:
    """Every mutable file in one manual-lifecycle generation."""

    root: Path
    state: Path
    events: Path
    heartbeat: Path
    status: Path
    changes: Path
    audit: Path

    @classmethod
    def for_root(cls, root: Path, now: datetime) -> "LifecyclePaths":
        normalized = _normalize_now(now)
        root = Path(root)
        month = normalized.strftime("%Y-%m")
        return cls(
            root=root,
            state=root / "data/manual/meti/state.json",
            events=root / "data/manual/meti/events.csv",
            heartbeat=root / f"data/heartbeat/{month}.csv",
            status=root / "data/dashboard/status.csv",
            changes=root / "data/dashboard/changes.csv",
            audit=root / f"data/source_audit/{month}.csv",
        )

    def transaction_targets(self) -> Tuple[Path, ...]:
        return (
            self.state,
            self.events,
            self.heartbeat,
            self.status,
            self.changes,
            self.audit,
        )


def _normalize_now(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise LifecycleError("now must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise LifecycleError("now must include a timezone")
    return value.astimezone(timezone.utc)


def _parse_utc(value: str, field: str) -> datetime:
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise LifecycleError(
            "%s must use UTC YYYY-MM-DDTHH:MM:SSZ" % field
        ) from exc
    return parsed.replace(tzinfo=timezone.utc)


def _load_manual_state(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LifecycleError("failed to load METI manual state") from exc
    if not isinstance(value, dict):
        raise LifecycleError("METI manual state must be a JSON object")
    return value


def load_events(path: Path) -> List[dict]:
    """Load and validate the append-only lifecycle event ledger."""

    if not path.exists() or path.stat().st_size == 0:
        return []
    try:
        with path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames != EVENT_COLS:
                raise LifecycleError(
                    "METI lifecycle event columns do not match: %s"
                    % (reader.fieldnames,)
                )
            return [dict(row) for row in reader]
    except csv.Error as exc:
        raise LifecycleError("failed to parse METI lifecycle events") from exc


def _append_event_rows(path: Path, events: List[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists() or path.stat().st_size == 0
    if not new:
        load_events(path)

    with path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=EVENT_COLS,
            extrasaction="ignore",
            lineterminator="\n",
        )
        if new:
            writer.writeheader()
        for event in events:
            row = {column: "" for column in EVENT_COLS}
            row.update(event)
            writer.writerow(row)
    return path


def persist_lifecycle(
    *,
    state: dict,
    events: List[dict],
    heartbeat_status: str,
    dashboard_event: Optional[List[str]],
    audit_row: Optional[dict],
    now: datetime,
    paths: LifecyclePaths,
) -> None:
    """Commit state, ledger, heartbeat, dashboard, and audit as one generation."""

    now = _normalize_now(now)
    if dashboard_event is not None and len(dashboard_event) != 5:
        raise LifecycleError("dashboard event must contain five columns")

    existing_events = load_events(paths.events)
    if events[:len(existing_events)] != existing_events:
        raise LifecycleError("event ledger history cannot be rewritten")
    new_events = events[len(existing_events):]

    pending = state.get("pending_detection") or {}
    heartbeat_entry = {
        "source": "meti",
        "status": heartbeat_status,
        "content_hash": (
            state.get("current_source_hash")
            or state.get("source_hash")
            or ""
        ),
        "source_updated": (
            state.get("effective_date")
            or state.get("publication_date")
            or pending.get("publication_at")
            or ""
        ),
        "record_count": (
            state.get("current_record_count")
            or state.get("record_count")
            or ""
        ),
        "raw_path": state.get("current_raw_path", ""),
    }
    repository_state = state_store.load_state(paths.root)
    status_rows = dashboard.build_status_rows(
        paths.root,
        [heartbeat_entry],
        repository_state,
        now=now,
        meti_state=state,
    )
    change_rows = [dashboard_event] if dashboard_event is not None else []
    audit_rows = [audit_row] if audit_row is not None else []
    changed_at = now.astimezone(dashboard.JST).strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    persistence.atomic_replace_many([
        persistence.FileWrite(
            target=paths.state,
            writer=lambda path: state_store.write_state(path, state),
        ),
        persistence.FileWrite(
            target=paths.events,
            writer=lambda path: _append_event_rows(path, new_events),
            seed_existing=True,
        ),
        persistence.FileWrite(
            target=paths.heartbeat,
            writer=lambda path: state_store.append_heartbeat(
                path,
                [heartbeat_entry],
                now=now,
            ),
            seed_existing=True,
        ),
        persistence.FileWrite(
            target=paths.status,
            writer=lambda path: dashboard.write_status_rows(
                path,
                status_rows,
            ),
        ),
        persistence.FileWrite(
            target=paths.changes,
            writer=lambda path: dashboard.prepend_change_rows(
                path,
                change_rows,
                when=changed_at,
            ),
            seed_existing=True,
        ),
        persistence.FileWrite(
            target=paths.audit,
            writer=lambda path: source_audit.append_rows(
                path,
                audit_rows,
                now=now,
            ),
            seed_existing=True,
        ),
    ])


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Record METI manual monitoring without network access",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    check = commands.add_parser("check")
    check.add_argument("--operator", required=True)
    check.add_argument("--source-url", required=True)
    check.add_argument("--note", default="")
    check.add_argument("--checked-at", default="")

    detect = commands.add_parser("detect")
    detect.add_argument("--operator", required=True)
    detect.add_argument("--notice-url", required=True)
    detect.add_argument("--title", required=True)
    detect.add_argument("--publication-at", default="")
    detect.add_argument("--note", default="")
    detect.add_argument("--detected-at", default="")

    cancel = commands.add_parser("cancel")
    cancel.add_argument("--operator", required=True)
    cancel.add_argument("--detection-id", required=True)
    cancel.add_argument("--note", required=True)
    cancel.add_argument("--cancelled-at", default="")
    return parser


def main(
    argv: Optional[List[str]] = None,
    *,
    paths: Optional[LifecyclePaths] = None,
    now: Optional[datetime] = None,
) -> int:
    """Run the local-only check/detect/cancel operator command."""

    args = _parser().parse_args(argv)
    base_now = _normalize_now(now or datetime.now(timezone.utc))

    try:
        if args.command == "check":
            event_at = (
                _parse_utc(args.checked_at, "checked-at")
                if args.checked_at
                else base_now
            )
        elif args.command == "detect":
            event_at = (
                _parse_utc(args.detected_at, "detected-at")
                if args.detected_at
                else base_now
            )
        else:
            event_at = (
                _parse_utc(args.cancelled_at, "cancelled-at")
                if args.cancelled_at
                else base_now
            )

        paths = paths or LifecyclePaths.for_root(ROOT, event_at)
        old_state = _load_manual_state(paths.state)
        old_events = load_events(paths.events)
        before = str(old_state.get("lifecycle_state", ""))

        if args.command == "check":
            state, events = record_no_change(
                old_state,
                old_events,
                operator=args.operator,
                source_url=args.source_url,
                checked_at=event_at,
                note=args.note,
            )
            changed = len(events) != len(old_events)
            if not changed:
                return 0
            dashboard_event = (
                [
                    "経済産業省",
                    "手動監視確認",
                    "外国ユーザーリスト",
                    before,
                    "変更なし",
                ]
                if changed
                else None
            )
            audit_row = source_audit.entry(
                "meti_manual",
                "official_page_check",
                "checked_no_change",
                url=args.source_url,
            )
            persist_lifecycle(
                state=state,
                events=events,
                heartbeat_status="manual_ok",
                dashboard_event=dashboard_event,
                audit_row=audit_row,
                now=event_at,
                paths=paths,
            )
            return 0

        if args.command == "cancel":
            state, events = cancel_blocked_detection(
                old_state,
                old_events,
                detection_id=args.detection_id,
                operator=args.operator,
                cancelled_at=event_at,
                note=args.note,
            )
            changed = len(events) != len(old_events)
            if not changed:
                return 0
            pending = old_state.get("pending_detection") or {}
            persist_lifecycle(
                state=state,
                events=events,
                heartbeat_status="manual_rejected",
                dashboard_event=[
                    "経済産業省",
                    "手動取込取消",
                    "外国ユーザーリスト",
                    "BLOCKED",
                    "REJECTED",
                ],
                audit_row=source_audit.entry(
                    "meti_manual",
                    "foreign_user_list_pdf",
                    "cancelled",
                    url=str(pending.get("notice_url") or ""),
                ),
                now=event_at,
                paths=paths,
            )
            print("CANCELLED_DETECTION_ID=%s" % args.detection_id)
            return 0

        publication_at = ""
        if args.publication_at:
            publication_at = _timestamp(
                _parse_utc(args.publication_at, "publication-at")
            )
        state, events, detection_id = open_detection(
            old_state,
            old_events,
            operator=args.operator,
            notice_url=args.notice_url,
            title=args.title,
            detected_at=event_at,
            publication_at=publication_at,
            note=args.note,
        )
        changed = len(events) != len(old_events)
        if not changed:
            print("DETECTION_ID=%s" % detection_id)
            return 0
        dashboard_event = (
            [
                "経済産業省",
                "更新候補検知",
                "外国ユーザーリスト",
                before,
                "正本取得待ち",
            ]
            if changed
            else None
        )
        audit_row = source_audit.entry(
            "meti_manual",
            "official_notice",
            "manual_fetch_required",
            url=args.notice_url,
            source_updated=publication_at,
        )
        persist_lifecycle(
            state=state,
            events=events,
            heartbeat_status="manual_pending",
            dashboard_event=dashboard_event,
            audit_row=audit_row,
            now=event_at,
            paths=paths,
        )
        print("DETECTION_ID=%s" % detection_id)
        return 0
    except LifecycleError as error:
        print("ERROR: %s" % error, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
