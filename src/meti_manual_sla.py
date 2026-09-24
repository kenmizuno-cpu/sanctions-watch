"""Enforce the repository-local METI manual-intake SLA.

The checker reads only committed lifecycle state.  It never contacts METI or
opens a URL, and it writes only when a breach or recovery is recorded for the
first time.
"""
from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

from . import dashboard, persistence
from . import state as state_store
from .meti_manual_event import LifecyclePaths


ROOT = Path(__file__).resolve().parent.parent

OPEN_STATES = {
    "DETECTED",
    "MANUAL_FETCH_REQUIRED",
    "FILE_RECEIVED",
    "VALIDATED",
}
DIFFED_STATES = {
    "DIFFED",
    "REVIEW_REQUIRED",
    "APPROVED",
}
CLOSED_STATES = {
    "CHECKED_NO_CHANGE",
    "REJECTED",
    "APPLIED",
    "APPLIED_WITH_HOLDS",
}
KNOWN_STATES = OPEN_STATES | DIFFED_STATES | CLOSED_STATES | {"BLOCKED"}
SLA_DURATION = timedelta(minutes=60)


class SlaStateError(ValueError):
    """Raised when the stored manual-SLA state cannot be evaluated."""


def _normalize_now(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise SlaStateError("now must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise SlaStateError("now must include a timezone")
    return value.astimezone(timezone.utc)


def _timestamp(value: datetime) -> str:
    return _normalize_now(value).replace(microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _parse_timestamp(state: dict, field: str) -> datetime:
    value = state.get(field)
    if not isinstance(value, str) or not value:
        raise SlaStateError("%s is required" % field)
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise SlaStateError(
            "%s must use UTC YYYY-MM-DDTHH:MM:SSZ" % field
        ) from exc
    return parsed.replace(tzinfo=timezone.utc)


def _optional_timestamp(state: dict, field: str) -> Optional[datetime]:
    value = state.get(field)
    if value in (None, ""):
        return None
    return _parse_timestamp(state, field)


def _validated_sla_history(
    state: dict,
    lifecycle: str,
    now: datetime,
) -> tuple[
    Optional[datetime],
    Optional[datetime],
    Optional[datetime],
    Optional[datetime],
    Optional[datetime],
]:
    fields = (
        "detected_at",
        "sla_due_at",
        "diffed_at",
        "sla_breached_at",
        "sla_recovered_at",
    )
    has_history = any(state.get(field) not in (None, "") for field in fields)
    if lifecycle in CLOSED_STATES and not has_history:
        return None, None, None, None, None
    if (
        lifecycle in DIFFED_STATES
        and state.get("diffed_at") in (None, "")
    ):
        raise SlaStateError("diffed_at is required for %s" % lifecycle)

    detected_at = _parse_timestamp(state, "detected_at")
    due_at = _parse_timestamp(state, "sla_due_at")
    diffed_at = _optional_timestamp(state, "diffed_at")
    breached_at = _optional_timestamp(state, "sla_breached_at")
    recovered_at = _optional_timestamp(state, "sla_recovered_at")

    if due_at != detected_at + SLA_DURATION:
        raise SlaStateError(
            "sla_due_at must be exactly 60 minutes after detected_at"
        )
    if lifecycle in OPEN_STATES and diffed_at is not None:
        raise SlaStateError(
            "diffed_at is invalid for an open lifecycle state"
        )
    if diffed_at is not None and diffed_at < detected_at:
        raise SlaStateError("diffed_at must not precede detected_at")
    if breached_at is not None and breached_at < due_at:
        raise SlaStateError("sla_breached_at must not precede sla_due_at")
    if breached_at is not None and diffed_at is not None:
        if diffed_at < due_at:
            raise SlaStateError(
                "sla_breached_at is invalid when diffed_at precedes sla_due_at"
            )
        if breached_at > diffed_at:
            raise SlaStateError("sla_breached_at must not follow diffed_at")
    if recovered_at is not None:
        if breached_at is None or diffed_at is None:
            raise SlaStateError(
                "sla_recovered_at requires breach and diff timestamps"
            )
        if recovered_at < breached_at or recovered_at < diffed_at:
            raise SlaStateError(
                "sla_recovered_at must not precede breach or diff"
            )
    for field, value in (
        ("diffed_at", diffed_at),
        ("sla_breached_at", breached_at),
        ("sla_recovered_at", recovered_at),
    ):
        if value is not None and value > now:
            raise SlaStateError("%s must not be in the future" % field)

    return detected_at, due_at, diffed_at, breached_at, recovered_at


def evaluate(state: dict, now: datetime) -> str:
    """Return the current SLA condition without reading or writing files."""

    now = _normalize_now(now)
    if not isinstance(state, dict):
        raise SlaStateError("METI manual state must be a JSON object")

    lifecycle = str(state.get("lifecycle_state", "")).strip()
    if lifecycle not in KNOWN_STATES:
        raise SlaStateError(
            "unknown or missing lifecycle state: %s"
            % (lifecycle or "<missing>")
        )

    if lifecycle == "BLOCKED":
        return "blocked"

    (
        detected_at,
        due_at,
        diffed_at,
        breached_at,
        recovered_at,
    ) = _validated_sla_history(state, lifecycle, now)

    if diffed_at is not None:
        if recovered_at is not None:
            return "healthy"
        if breached_at is not None or diffed_at >= due_at:
            return "recovered"
        return "healthy"

    if lifecycle in CLOSED_STATES:
        return "healthy"

    if lifecycle in OPEN_STATES:
        if breached_at is not None:
            return "breached"
        return "pending" if now < due_at else "breached"

    raise SlaStateError("unhandled lifecycle state: %s" % lifecycle)


def _load_state(path: Path) -> dict:
    if not path.exists():
        raise SlaStateError("METI manual state does not exist")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SlaStateError("failed to load METI manual state") from exc
    if not isinstance(value, dict):
        raise SlaStateError("METI manual state must be a JSON object")
    return value


def _heartbeat_status(state: dict) -> str:
    lifecycle = str(state.get("lifecycle_state", ""))
    if lifecycle in {"DIFFED", "REVIEW_REQUIRED"}:
        return "manual_review"
    if lifecycle == "APPROVED":
        return "manual_approved"
    if lifecycle == "REJECTED":
        return "manual_rejected"
    if lifecycle == "BLOCKED":
        return "manual_blocked"
    return "manual_ok"


def _heartbeat_entry(state: dict, status: str) -> dict:
    pending = state.get("pending_detection") or {}
    return {
        "source": "meti",
        "status": status,
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


def _persist_transition(
    *,
    state: dict,
    heartbeat_status: str,
    dashboard_events: List[List[str]],
    paths: LifecyclePaths,
    now: datetime,
) -> None:
    heartbeat_entry = _heartbeat_entry(state, heartbeat_status)
    repository_state = state_store.load_state(paths.root)
    status_rows = dashboard.build_status_rows(
        paths.root,
        [heartbeat_entry],
        repository_state,
        now=now,
        meti_state=state,
    )
    changed_at = now.astimezone(dashboard.JST).strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    persistence.atomic_replace_many([
        persistence.FileWrite(
            target=paths.state,
            writer=lambda path: state_store.write_state(path, state),
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
                dashboard_events,
                when=changed_at,
            ),
            seed_existing=True,
        ),
    ])


def run(*, paths: LifecyclePaths, now: datetime) -> int:
    """Evaluate and persist only first breach/recovery transitions."""

    try:
        now = _normalize_now(now)
        state = _load_state(paths.state)
        condition = evaluate(state, now)
    except SlaStateError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 1

    if condition in {"healthy", "pending"}:
        print("METI_MANUAL_SLA=%s" % condition.upper())
        return 0

    if condition == "blocked":
        print("METI_MANUAL_SLA=BLOCKED")
        return 2

    next_state = deepcopy(state)
    critical_event = [
        "経済産業省",
        "SLA超過",
        "外国ユーザーリスト",
        "要確認：正本取得待ち",
        "重大：更新候補未検証",
    ]
    if condition == "breached":
        if next_state.get("sla_breached_at"):
            print("METI_MANUAL_SLA=BREACHED")
            return 2
        next_state["sla_breached_at"] = _timestamp(
            _parse_timestamp(next_state, "sla_due_at")
        )
        heartbeat_status = "manual_critical"
        dashboard_events = [critical_event]
        exit_code = 2
    else:
        missed_breach = not next_state.get("sla_breached_at")
        if missed_breach:
            next_state["sla_breached_at"] = _timestamp(
                _parse_timestamp(next_state, "sla_due_at")
            )
        next_state["sla_recovered_at"] = _timestamp(now)
        heartbeat_status = _heartbeat_status(next_state)
        recovery_event = [
            "経済産業省",
            "SLA復旧",
            "外国ユーザーリスト",
            "重大：更新候補未検証",
            dashboard.STATUS_LABEL[heartbeat_status],
        ]
        dashboard_events = [recovery_event]
        if missed_breach:
            dashboard_events.append(critical_event)
        exit_code = 0

    _persist_transition(
        state=next_state,
        heartbeat_status=heartbeat_status,
        dashboard_events=dashboard_events,
        paths=paths,
        now=now,
    )
    print("METI_MANUAL_SLA=%s" % condition.upper())
    return exit_code


def main(
    argv: Optional[List[str]] = None,
    *,
    paths: Optional[LifecyclePaths] = None,
    now: Optional[datetime] = None,
) -> int:
    """Run the local-only SLA checker."""

    parser = argparse.ArgumentParser(
        description="Check the local METI manual-intake SLA",
    )
    parser.parse_args(argv)
    checked_at = now or datetime.now(timezone.utc)
    try:
        checked_at = _normalize_now(checked_at)
    except SlaStateError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 1
    paths = paths or LifecyclePaths.for_root(ROOT, checked_at)
    return run(paths=paths, now=checked_at)


if __name__ == "__main__":
    raise SystemExit(main())
