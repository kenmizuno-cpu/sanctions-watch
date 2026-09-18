"""Human-approved OFAC Party removal handling.

The watcher detects Party removals by OFAC DistinctParty FixedRef.  This
module validates a repository-controlled approval ledger before a removal is
allowed to progress beyond the existing fail-closed gate.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from . import master as M
from . import ofac_index as OI
from .normalize import canonical_display_name


APPROVAL_FIELDS = [
    "list",
    "snapshot_sha256",
    "party_id",
    "party_name",
    "decision",
    "approved_by",
    "approved_at",
    "official_url",
    "notes",
]

AUDIT_FIELDS = [
    "applied_at",
    "list",
    "snapshot_sha256",
    "party_id",
    "party_name",
    "approved_by",
    "approved_at",
    "official_url",
    "notes",
    "effect_count",
    "effects_json",
    "master_before_sha256",
    "master_after_sha256",
    "index_before_sha256",
    "index_after_sha256",
]

_SHA256_RE = re.compile(r"[0-9a-f]{64}")


class ApprovalError(RuntimeError):
    """The detected OFAC removal is not covered by a valid approval."""


class AuditError(RuntimeError):
    """The OFAC removal application audit cannot be written safely."""


@dataclass(frozen=True)
class Approval:
    list_name: str
    snapshot_sha256: str
    party_id: str
    party_name: str
    approved_by: str
    approved_at: str
    official_url: str
    notes: str


def _validate_timestamp(value: str, row_number: int) -> None:
    try:
        parsed = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )
    except ValueError as error:
        raise ApprovalError(
            f"承認台帳 {row_number}行目 approved_at が不正: {value!r}"
        ) from error

    if parsed.tzinfo is None:
        raise ApprovalError(
            f"承認台帳 {row_number}行目 approved_at にtimezoneが無い"
        )


def _validate_official_url(value: str, row_number: int) -> None:
    try:
        parsed = urlsplit(value)
    except ValueError as error:
        raise ApprovalError(
            f"承認台帳 {row_number}行目 Treasury公式URLが不正"
        ) from error

    hostname = (parsed.hostname or "").lower()
    official_host = (
        hostname == "treasury.gov"
        or hostname.endswith(".treasury.gov")
        or hostname == "sanctionslistservice.ofac.treas.gov"
    )

    if parsed.scheme != "https" or not official_host:
        raise ApprovalError(
            f"承認台帳 {row_number}行目 Treasury公式URLが不正: {value!r}"
        )


def _load(path: Path) -> list[Approval]:
    if not path.is_file():
        raise ApprovalError(
            f"OFAC Party削除の承認台帳が存在しない: {path}"
        )

    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)

        if reader.fieldnames != APPROVAL_FIELDS:
            raise ApprovalError(
                "OFAC Party削除承認台帳の列構造が不一致: "
                f"expected={APPROVAL_FIELDS} actual={reader.fieldnames}"
            )

        approvals: list[Approval] = []
        seen: set[tuple[str, str, str]] = set()

        for row_number, row in enumerate(reader, start=2):
            list_name = str(row.get("list") or "").strip()
            snapshot_sha256 = str(
                row.get("snapshot_sha256") or ""
            ).strip()
            party_id = str(row.get("party_id") or "").strip()
            party_name = canonical_display_name(
                row.get("party_name") or ""
            )
            decision = str(row.get("decision") or "").strip()
            approved_by = str(row.get("approved_by") or "").strip()
            approved_at = str(row.get("approved_at") or "").strip()
            official_url = str(row.get("official_url") or "").strip()
            notes = str(row.get("notes") or "").strip()

            required = {
                "list": list_name,
                "party_id": party_id,
                "party_name": party_name,
                "approved_by": approved_by,
                "approved_at": approved_at,
                "official_url": official_url,
            }
            missing = [key for key, value in required.items() if not value]
            if missing:
                raise ApprovalError(
                    f"承認台帳 {row_number}行目の必須項目が空: {missing}"
                )

            if not _SHA256_RE.fullmatch(snapshot_sha256):
                raise ApprovalError(
                    f"承認台帳 {row_number}行目 snapshot_sha256 が不正"
                )

            if decision != "APPROVED":
                raise ApprovalError(
                    f"承認台帳 {row_number}行目 decision はAPPROVED必須: "
                    f"{decision!r}"
                )

            _validate_timestamp(approved_at, row_number)
            _validate_official_url(official_url, row_number)

            key = (list_name, snapshot_sha256, party_id)
            if key in seen:
                raise ApprovalError(
                    "OFAC Party削除承認が重複: "
                    f"list={list_name} hash={snapshot_sha256} "
                    f"party_id={party_id}"
                )
            seen.add(key)

            approvals.append(
                Approval(
                    list_name=list_name,
                    snapshot_sha256=snapshot_sha256,
                    party_id=party_id,
                    party_name=party_name,
                    approved_by=approved_by,
                    approved_at=approved_at,
                    official_url=official_url,
                    notes=notes,
                )
            )

    return approvals


def load_and_authorize(
    path: Path,
    removed_parties: set[tuple[str, str]],
    snapshot_hashes: dict[str, str],
    history: dict[tuple[str, str, str], dict],
) -> list[Approval]:
    """Return approvals only when every per-list removal set matches exactly."""
    if not removed_parties:
        return []

    approvals = _load(path)
    removals_by_list: dict[str, set[str]] = {}

    for list_name, party_id in removed_parties:
        removals_by_list.setdefault(str(list_name), set()).add(
            str(party_id)
        )

    authorized: list[Approval] = []

    for list_name in sorted(removals_by_list):
        snapshot_sha256 = str(
            snapshot_hashes.get(list_name) or ""
        ).strip()
        if not _SHA256_RE.fullmatch(snapshot_sha256):
            raise ApprovalError(
                f"{list_name} Advanced XML snapshot SHA256が不正または欠落"
            )

        matching = [
            item
            for item in approvals
            if (
                item.list_name == list_name
                and item.snapshot_sha256 == snapshot_sha256
            )
        ]
        approved_ids = {item.party_id for item in matching}
        detected_ids = removals_by_list[list_name]

        if approved_ids != detected_ids:
            raise ApprovalError(
                f"{list_name} 承認FixedRef集合が不一致: "
                f"detected={sorted(detected_ids)} "
                f"approved={sorted(approved_ids)} "
                f"snapshot_sha256={snapshot_sha256}"
            )

        for item in matching:
            known_names = {
                canonical_display_name(row.get("name") or "")
                for row in history.values()
                if (
                    str(row.get("list") or "") == item.list_name
                    and str(row.get("party_id") or "") == item.party_id
                )
            }

            if item.party_name not in known_names:
                raise ApprovalError(
                    "OFAC Party削除承認のparty_nameがFixedRef履歴と不一致: "
                    f"list={item.list_name} party_id={item.party_id} "
                    f"party_name={item.party_name!r} "
                    f"history_names={sorted(known_names)}"
                )

        authorized.extend(matching)

    return sorted(
        authorized,
        key=lambda item: (item.list_name, item.party_id),
    )


def _logical_state_sha256(
    rows: dict,
    fields: list[str] | tuple[str, ...],
    sort_fields: tuple[str, ...],
) -> str:
    normalized = [
        {
            field: str(row.get(field, "") or "")
            for field in fields
        }
        for row in rows.values()
    ]
    normalized.sort(
        key=lambda row: tuple(row.get(field, "") for field in sort_fields)
    )
    payload = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def master_state_sha256(rows: dict[str, dict]) -> str:
    """Hash the persisted master fields as a deterministic logical state."""
    return _logical_state_sha256(
        rows,
        M.FIELDS,
        ("match_key",),
    )


def index_state_sha256(history: dict[tuple[str, str, str], dict]) -> str:
    """Hash the persisted OFAC index fields as a deterministic logical state."""
    return _logical_state_sha256(
        history,
        OI.FIELDS,
        ("list", "party_id", "name"),
    )


def _split(value: str) -> list[str]:
    return [item for item in str(value or "").split(";") if item]


def _join(values) -> str:
    return ";".join(sorted({str(value) for value in values if str(value)}))


def _master_audit_values(row: dict) -> dict:
    return {
        field: str(row.get(field, "") or "")
        for field in M.DIFF_AUDIT_FIELDS
    }


def apply_to_master(
    rows: dict[str, dict],
    history: dict[tuple[str, str, str], dict],
    removed_parties: set[tuple[str, str]],
    *,
    ts: int,
) -> tuple[M.Diff, list[dict]]:
    """Remove OFAC only for names solely backed by approved removed parties."""
    removed = {
        (str(list_name), str(party_id))
        for list_name, party_id in removed_parties
    }
    candidate_parties: dict[str, set[tuple[str, str]]] = {}

    for item in history.values():
        party = (
            str(item.get("list") or ""),
            str(item.get("party_id") or ""),
        )
        key = str(item.get("match_key") or "").strip()
        if party in removed and key:
            candidate_parties.setdefault(key, set()).add(party)

    current_strong_keys = {
        str(item.get("match_key") or "").strip()
        for item in history.values()
        if (
            item.get("party_current") == "1"
            and item.get("alias_current") == "1"
            and item.get("low_quality") != "1"
            and str(item.get("match_key") or "").strip()
        )
    }

    diff = M.Diff(source="OFAC")
    effects: list[dict] = []

    for key in sorted(candidate_parties):
        parties = [
            {"list": list_name, "party_id": party_id}
            for list_name, party_id in sorted(candidate_parties[key])
        ]
        row = rows.get(key)

        if row is None:
            effects.append({
                "match_key": key,
                "display_name": "",
                "removed_parties": parties,
                "action": "master_row_absent",
                "before": {},
                "after": {},
            })
            continue

        before = _master_audit_values(row)
        display_name = str(row.get("display_name") or "")

        if key in current_strong_keys:
            effects.append({
                "match_key": key,
                "display_name": display_name,
                "removed_parties": parties,
                "action": "kept_current_ofac_party",
                "before": before,
                "after": before,
            })
            continue

        sources = _split(row.get("sources", ""))
        if "OFAC" not in sources:
            effects.append({
                "match_key": key,
                "display_name": display_name,
                "removed_parties": parties,
                "action": "ofac_source_absent",
                "before": before,
                "after": before,
            })
            continue

        remaining_sources = [source for source in sources if source != "OFAC"]
        remaining_categories = [
            category
            for category in _split(row.get("categories", ""))
            if not category.startswith("OFAC:")
        ]

        row["sources"] = _join(remaining_sources)
        row["categories"] = _join(remaining_categories)

        if not remaining_sources:
            row["status"] = M.STATUS_INACTIVE
            row["invalid_reason"] = M.DELISTED
            action = "deactivated"
        else:
            action = "removed_ofac_source"

        M._recompute(row)
        row["last_updated_ms"] = str(ts)
        after = _master_audit_values(row)

        diff.removed.append({
            "key": key,
            "name": display_name,
            "before": before,
            "after": after,
            "delisted": True,
        })
        effects.append({
            "match_key": key,
            "display_name": display_name,
            "removed_parties": parties,
            "action": action,
            "before": before,
            "after": after,
        })

    return diff, effects


def refresh_effects(
    rows: dict[str, dict],
    effects: list[dict],
) -> list[dict]:
    """Refresh effect ``after`` values after later reconciliation steps."""
    for effect in effects:
        key = str(effect.get("match_key") or "")
        row = rows.get(key)

        if row is not None:
            effect["after"] = _master_audit_values(row)

    return effects


def build_audit_rows(
    approvals: list[Approval],
    effects: list[dict],
    *,
    master_before_sha256: str,
    master_after_sha256: str,
    index_before_sha256: str,
    index_after_sha256: str,
) -> list[dict]:
    """Build one inspectable application-audit row per approved Party."""
    hashes = {
        "master_before_sha256": master_before_sha256,
        "master_after_sha256": master_after_sha256,
        "index_before_sha256": index_before_sha256,
        "index_after_sha256": index_after_sha256,
    }
    invalid_hashes = [
        name
        for name, value in hashes.items()
        if not _SHA256_RE.fullmatch(str(value or ""))
    ]
    if invalid_hashes:
        raise AuditError(
            f"OFAC Party削除適用監査の状態hashが不正: {invalid_hashes}"
        )

    rows = []

    for approval in sorted(
        approvals,
        key=lambda item: (item.list_name, item.party_id),
    ):
        selected_effects = [
            effect
            for effect in effects
            if {
                "list": approval.list_name,
                "party_id": approval.party_id,
            } in effect.get("removed_parties", [])
        ]
        selected_effects.sort(
            key=lambda item: (
                str(item.get("match_key") or ""),
                str(item.get("action") or ""),
            )
        )

        row = {field: "" for field in AUDIT_FIELDS}
        row.update({
            "list": approval.list_name,
            "snapshot_sha256": approval.snapshot_sha256,
            "party_id": approval.party_id,
            "party_name": approval.party_name,
            "approved_by": approval.approved_by,
            "approved_at": approval.approved_at,
            "official_url": approval.official_url,
            "notes": approval.notes,
            "effect_count": str(len(selected_effects)),
            "effects_json": json.dumps(
                selected_effects,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            **hashes,
        })
        rows.append(row)

    return rows


def append_audit(
    path: Path,
    rows: list[dict],
    *,
    applied_at: datetime,
) -> None:
    """Append application rows, rejecting a pre-existing schema mismatch."""
    if not rows:
        return

    if applied_at.tzinfo is None:
        raise AuditError("OFAC Party削除適用日時にtimezoneが無い")

    path.parent.mkdir(parents=True, exist_ok=True)
    has_content = path.exists() and path.stat().st_size > 0

    if has_content:
        with path.open("r", encoding="utf-8", newline="") as handle:
            actual = next(csv.reader(handle), [])

        if actual != AUDIT_FIELDS:
            raise AuditError(
                "OFAC Party削除適用監査の列構造が不一致: "
                f"expected={AUDIT_FIELDS} actual={actual}"
            )

    timestamp = applied_at.astimezone(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=AUDIT_FIELDS,
            extrasaction="ignore",
            lineterminator="\n",
        )
        if not has_content:
            writer.writeheader()

        for source_row in rows:
            row = {field: "" for field in AUDIT_FIELDS}
            row.update(source_row)
            row["applied_at"] = timestamp
            writer.writerow(row)
