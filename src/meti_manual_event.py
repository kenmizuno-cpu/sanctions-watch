"""Pure lifecycle model for METI manual-first monitoring.

This module validates operator-provided metadata and updates in-memory state.
It deliberately performs no network or filesystem I/O.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Tuple
from urllib.parse import urlparse, urlunparse


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
