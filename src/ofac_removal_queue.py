"""Persistent human-review queue for OFAC Party removals.

Detection is intentionally separate from screening deactivation.  A complete
OFAC snapshot may mark Party/Alias history inactive and create a queue event,
while the master stays active until an exact human approval is available.
"""
from __future__ import annotations

import copy
import csv
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, MutableSequence, Set, Tuple

from .normalize import canonical_display_name


FIELDS = [
    "event_id",
    "list",
    "snapshot_sha256",
    "party_id",
    "party_name",
    "status",
    "detected_at_ms",
    "last_seen_at_ms",
    "resolved_at_ms",
    "resolution",
]

PENDING_REVIEW = "PENDING_REVIEW"
APPLIED = "APPLIED"
CANCELLED_REAPPEARED = "CANCELLED_REAPPEARED"

STATUSES = {
    PENDING_REVIEW,
    APPLIED,
    CANCELLED_REAPPEARED,
}

_SHA256_RE = re.compile(r"[0-9a-f]{64}")

Party = Tuple[str, str]
ApprovalKey = Tuple[str, str, str]
PendingGroup = Tuple[str, str, str]


class QueueError(RuntimeError):
    """The OFAC removal review queue is malformed or unsafe to transition."""


@dataclass(frozen=True)
class QueueDiff:
    created_events: Set[str] = field(default_factory=set)
    cancelled_parties: Set[Party] = field(default_factory=set)


def _timestamp(value, field_name: str) -> str:
    rendered = str(value)
    if not rendered.isdigit() or int(rendered) < 0:
        raise QueueError(
            f"OFAC removal queue {field_name} must be a non-negative integer: "
            f"{value!r}"
        )
    return rendered


def _event_id(
    list_name: str,
    snapshot_sha256: str,
    party_ids: Iterable[str],
) -> str:
    payload = json.dumps(
        {
            "list": list_name,
            "party_ids": sorted({str(item) for item in party_ids}),
            "snapshot_sha256": snapshot_sha256,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _normalized_row(row: Mapping[str, object], row_number: int) -> dict:
    normalized = {
        field_name: str(row.get(field_name, "") or "").strip()
        for field_name in FIELDS
    }

    required = [
        "event_id",
        "list",
        "snapshot_sha256",
        "party_id",
        "party_name",
        "status",
        "detected_at_ms",
        "last_seen_at_ms",
    ]
    missing = [field_name for field_name in required if not normalized[field_name]]
    if missing:
        raise QueueError(
            f"OFAC removal queue row {row_number} has empty fields: {missing}"
        )

    if not _SHA256_RE.fullmatch(normalized["event_id"]):
        raise QueueError(
            f"OFAC removal queue row {row_number} event_id is invalid"
        )
    if not _SHA256_RE.fullmatch(normalized["snapshot_sha256"]):
        raise QueueError(
            f"OFAC removal queue row {row_number} snapshot SHA256 is invalid"
        )
    if normalized["status"] not in STATUSES:
        raise QueueError(
            f"OFAC removal queue row {row_number} status is invalid: "
            f"{normalized['status']!r}"
        )

    normalized["party_name"] = canonical_display_name(
        normalized["party_name"]
    )
    normalized["detected_at_ms"] = _timestamp(
        normalized["detected_at_ms"],
        "detected_at_ms",
    )
    normalized["last_seen_at_ms"] = _timestamp(
        normalized["last_seen_at_ms"],
        "last_seen_at_ms",
    )

    if int(normalized["last_seen_at_ms"]) < int(normalized["detected_at_ms"]):
        raise QueueError(
            f"OFAC removal queue row {row_number} last_seen precedes detection"
        )

    if normalized["status"] == PENDING_REVIEW:
        if normalized["resolved_at_ms"] or normalized["resolution"]:
            raise QueueError(
                f"OFAC removal queue row {row_number} pending row is resolved"
            )
    else:
        normalized["resolved_at_ms"] = _timestamp(
            normalized["resolved_at_ms"],
            "resolved_at_ms",
        )
        if int(normalized["resolved_at_ms"]) < int(
            normalized["detected_at_ms"]
        ):
            raise QueueError(
                f"OFAC removal queue row {row_number} resolution precedes detection"
            )

        expected_resolution = (
            "HUMAN_APPROVED"
            if normalized["status"] == APPLIED
            else "REAPPEARED"
        )
        if normalized["resolution"] != expected_resolution:
            raise QueueError(
                f"OFAC removal queue row {row_number} resolution is invalid: "
                f"expected={expected_resolution!r} "
                f"actual={normalized['resolution']!r}"
            )

    return normalized


def _validated(rows: Iterable[Mapping[str, object]]) -> List[dict]:
    normalized = [
        _normalized_row(row, row_number)
        for row_number, row in enumerate(rows, start=2)
    ]
    seen_rows: Set[Tuple[str, str]] = set()
    open_parties: Set[Party] = set()
    events: Dict[str, List[dict]] = {}

    for row in normalized:
        row_key = (row["event_id"], row["party_id"])
        if row_key in seen_rows:
            raise QueueError(
                "OFAC removal queue has a duplicate event/party row: "
                f"event_id={row['event_id']} party_id={row['party_id']}"
            )
        seen_rows.add(row_key)
        events.setdefault(row["event_id"], []).append(row)

        if row["status"] == PENDING_REVIEW:
            party = (row["list"], row["party_id"])
            if party in open_parties:
                raise QueueError(
                    "OFAC removal queue has multiple open events for one party: "
                    f"list={party[0]} party_id={party[1]}"
                )
            open_parties.add(party)

    for event_id, event_rows in events.items():
        list_names = {row["list"] for row in event_rows}
        snapshots = {row["snapshot_sha256"] for row in event_rows}
        detected_at = {row["detected_at_ms"] for row in event_rows}
        if len(list_names) != 1 or len(snapshots) != 1 or len(detected_at) != 1:
            raise QueueError(
                f"OFAC removal queue event metadata is inconsistent: {event_id}"
            )
        transition_states = {
            (
                row["status"],
                row["resolved_at_ms"],
                row["resolution"],
            )
            for row in event_rows
        }
        if len(transition_states) != 1:
            raise QueueError(
                f"OFAC removal queue event transition is partial: {event_id}"
            )

        list_name = event_rows[0]["list"]
        snapshot = event_rows[0]["snapshot_sha256"]
        expected = _event_id(
            list_name,
            snapshot,
            (row["party_id"] for row in event_rows),
        )
        if event_id != expected:
            raise QueueError(
                "OFAC removal queue event_id does not match its exact party set: "
                f"event_id={event_id} expected={expected}"
            )

    normalized.sort(
        key=lambda row: (
            int(row["detected_at_ms"]),
            row["event_id"],
            row["party_id"],
        )
    )
    return normalized


def load(path: Path) -> List[dict]:
    """Load and strictly validate the queue; a missing file is an empty queue."""
    if not path.exists():
        return []

    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != FIELDS:
            raise QueueError(
                "OFAC removal queue schema mismatch: "
                f"expected={FIELDS} actual={reader.fieldnames}"
            )
        return _validated(reader)


def save(rows: Iterable[Mapping[str, object]], path: Path) -> None:
    """Rewrite the validated queue in deterministic order."""
    normalized = _validated(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(normalized)


def _party_name(
    history: Mapping[Tuple[str, str, str], Mapping[str, object]],
    party: Party,
) -> str:
    candidates = [
        row
        for row in history.values()
        if (
            str(row.get("list", "")) == party[0]
            and str(row.get("party_id", "")) == party[1]
            and canonical_display_name(row.get("name", ""))
        )
    ]
    if not candidates:
        raise QueueError(
            "OFAC removal queue cannot resolve party name from history: "
            f"list={party[0]} party_id={party[1]}"
        )

    candidates.sort(
        key=lambda row: (
            0 if str(row.get("primary", "")) == "1" else 1,
            0 if str(row.get("low_quality", "")) != "1" else 1,
            canonical_display_name(row.get("name", "")).casefold(),
            canonical_display_name(row.get("name", "")),
        )
    )
    return canonical_display_name(candidates[0].get("name", ""))


def reconcile(
    rows: MutableSequence[dict],
    removed_parties: Iterable[Party],
    current_parties: Iterable[Party],
    snapshot_hashes: Mapping[str, str],
    history: Mapping[Tuple[str, str, str], Mapping[str, object]],
    ts: int,
) -> QueueDiff:
    """Reconcile open events against one complete OFAC snapshot atomically."""
    timestamp = _timestamp(ts, "transition timestamp")
    staged = copy.deepcopy(_validated(rows))
    current = {
        (str(list_name), str(party_id))
        for list_name, party_id in current_parties
    }
    detected = {
        (str(list_name), str(party_id))
        for list_name, party_id in removed_parties
    }
    if current & detected:
        raise QueueError(
            "OFAC removal queue input marks the same party current and removed: "
            f"{sorted(current & detected)}"
        )

    normalized_hashes = {
        str(list_name): str(snapshot).strip()
        for list_name, snapshot in snapshot_hashes.items()
    }

    created_events: Set[str] = set()
    cancelled_parties: Set[Party] = set()
    requeue: Set[Party] = set()
    events: Dict[str, List[dict]] = {}
    for row in staged:
        events.setdefault(row["event_id"], []).append(row)

    for event_rows in events.values():
        pending = [row for row in event_rows if row["status"] == PENDING_REVIEW]
        if not pending:
            continue

        reappeared = {
            (row["list"], row["party_id"])
            for row in pending
            if (row["list"], row["party_id"]) in current
        }
        if reappeared:
            cancelled_parties.update(reappeared)
            for row in pending:
                party = (row["list"], row["party_id"])
                row["status"] = CANCELLED_REAPPEARED
                row["resolved_at_ms"] = timestamp
                row["resolution"] = "REAPPEARED"
                if party not in current:
                    requeue.add(party)
            continue

        for row in pending:
            if row["list"] in normalized_hashes:
                row["last_seen_at_ms"] = timestamp

    candidates = detected | requeue
    open_parties = {
        (row["list"], row["party_id"])
        for row in staged
        if row["status"] == PENDING_REVIEW
    }
    candidates -= open_parties

    by_list: Dict[str, Set[str]] = {}
    for list_name, party_id in candidates:
        by_list.setdefault(list_name, set()).add(party_id)

    for list_name in sorted(by_list):
        snapshot = normalized_hashes.get(list_name, "")
        if not _SHA256_RE.fullmatch(snapshot):
            raise QueueError(
                f"{list_name} removal queue snapshot SHA256 is invalid or missing"
            )

        party_ids = by_list[list_name]
        event_id = _event_id(list_name, snapshot, party_ids)
        if any(row["event_id"] == event_id for row in staged):
            raise QueueError(
                "OFAC removal queue would reuse an existing event_id: "
                f"{event_id}"
            )

        for party_id in sorted(party_ids):
            staged.append({
                "event_id": event_id,
                "list": list_name,
                "snapshot_sha256": snapshot,
                "party_id": party_id,
                "party_name": _party_name(
                    history,
                    (list_name, party_id),
                ),
                "status": PENDING_REVIEW,
                "detected_at_ms": timestamp,
                "last_seen_at_ms": timestamp,
                "resolved_at_ms": "",
                "resolution": "",
            })
        created_events.add(event_id)

    validated = _validated(staged)
    rows[:] = validated
    return QueueDiff(
        created_events=created_events,
        cancelled_parties=cancelled_parties,
    )


def pending_groups(
    rows: Iterable[Mapping[str, object]],
) -> Dict[PendingGroup, Set[str]]:
    """Return exact party sets for fully open pending events."""
    normalized = _validated(rows)
    grouped: Dict[PendingGroup, Set[str]] = {}
    for row in normalized:
        if row["status"] != PENDING_REVIEW:
            continue
        key = (
            row["event_id"],
            row["list"],
            row["snapshot_sha256"],
        )
        grouped.setdefault(key, set()).add(row["party_id"])
    return grouped


def mark_applied(
    rows: MutableSequence[dict],
    approved_keys: Iterable[ApprovalKey],
    ts: int,
) -> None:
    """Mark exact authorized queue rows applied, without partial mutation."""
    timestamp = _timestamp(ts, "transition timestamp")
    staged = copy.deepcopy(_validated(rows))
    requested = {
        (str(list_name), str(snapshot), str(party_id))
        for list_name, snapshot, party_id in approved_keys
    }
    available = {
        (row["list"], row["snapshot_sha256"], row["party_id"]): row
        for row in staged
        if row["status"] == PENDING_REVIEW
    }
    missing = requested - set(available)
    if missing:
        raise QueueError(
            "approval does not identify a pending queue row: "
            f"{sorted(missing)}"
        )

    groups = pending_groups(staged)
    for (_event_id_value, list_name, snapshot), party_ids in groups.items():
        selected = {
            party_id
            for selected_list, selected_snapshot, party_id in requested
            if selected_list == list_name and selected_snapshot == snapshot
        }
        if selected and selected != party_ids:
            raise QueueError(
                "approval would partially apply a pending queue event: "
                f"list={list_name} snapshot_sha256={snapshot} "
                f"pending={sorted(party_ids)} approved={sorted(selected)}"
            )

    for key in requested:
        row = available[key]
        row["status"] = APPLIED
        row["resolved_at_ms"] = timestamp
        row["resolution"] = "HUMAN_APPROVED"

    rows[:] = _validated(staged)
