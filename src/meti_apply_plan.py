"""METI approved snapshot -> safe pre-apply manifest.

This module never writes master.csv and never sets applied=True.

Safety principles:
- Approved snapshot integrity is re-verified through meti_review.
- The current master SHA256 is pinned into the plan.
- Weak short aliases are held and never become standalone screening names.
- The same normalized name attached to multiple METI party numbers is held.
- Legacy METI-tagged names absent from the current official snapshot are held.
- A small snapshot-specific whitelist may release long aliases only after
  direct verification against the official source PDF.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from . import master as M
from . import meti_review as MR
from .meti_manual_import import load_records
from .normalize import (
    SRC_METI,
    canonical_display_name,
    match_key,
    needs_review,
    validate,
)

ROOT = Path(__file__).resolve().parent.parent
MASTER_PATH = ROOT / "data" / "master" / "master.csv"
PLAN_DIR = ROOT / "data" / "manual" / "meti" / "apply_plans"

ACTION_READY_ADD = "READY_ADD"
ACTION_READY_TAG = "READY_TAG_EXISTING"
ACTION_NOOP = "NOOP_ALREADY_METI"
ACTION_HOLD_WEAK = "HOLD_WEAK_ALIAS"
ACTION_HOLD_REVIEW = "HOLD_REVIEW"
ACTION_HOLD_INVALID = "HOLD_INVALID"
ACTION_HOLD_COLLISION = "HOLD_MULTI_PARTY_COLLISION"

PLAN_COLS = [
    "action",
    "match_key",
    "name",
    "record_no",
    "record_primary",
    "kind",
    "party_nos",
    "supporting_names",
    "source_record_ids",
    "review_reason",
    "resolution",
    "existing_master_sources",
    "existing_master_display_name",
]

LEGACY_COLS = [
    "action",
    "match_key",
    "display_name",
    "sources",
    "status",
    "remark",
]

# These four aliases were directly checked against the exact official METI
# 2025-09-29 PDF snapshot.  They are long because the official list itself
# contains a long English/Japanese or organizational form, not because the
# parser merged separate rows.
SOURCE_VERIFIED_LONG_ALIASES = {
    "c632d657f9c13b1250d688394eb7c99956a994527cb56f4a77c86ed61ff1dbda": {
        (
            "China Aerospace Science and Industry Corporation(CASIC) "
            "Second Academy 23rd Research Institute "
            "中国航天科学技術工業集団有限公司第二研究院第二十 三研究所"
        ),
        (
            "No. 33 Research Institute of the Third Academy of "
            "China Aerospace Science and Industry Corporation (CASIC) "
            "(中国航天科工集団第三研究院三十三研究所)"
        ),
        (
            "811th Research Institute, 8th Academy, China Aerospace "
            "Science and Technology Corporation (CASC) "
            "(中国航天科技集団公司第八研究院第八一一研究所)"
        ),
        (
            "FEDERAL STATE BUDGETARY ESTABLISHMENT 33 CENTRAL "
            "SCIENTIFIC RESEARCH TEST INSTITUTE OF THE MINISTRY "
            "OF DEFENSE OF THE RUSSIAN"
        ),
    }
}


class ApplyPlanError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _split_sources(row: dict) -> set[str]:
    return {
        x
        for x in str(row.get("sources", "")).split(";")
        if x
    }


def _is_weak_reason(reason: str) -> bool:
    s = str(reason or "")
    return "短く" in s and "誤検知" in s


def _source_verified_long(
    *,
    source_hash: str,
    name: str,
    reason: str,
) -> bool:
    if "異常に長い" not in str(reason or ""):
        return False
    allowed = SOURCE_VERIFIED_LONG_ALIASES.get(
        source_hash,
        set(),
    )
    return name in allowed


def _candidate_priority(c: dict) -> tuple:
    # Prefer PRIMARY.  Then prefer a representation that did not need an
    # override.  Remaining order is deterministic.
    return (
        0 if c["kind"] == "PRIMARY" else 1,
        0 if not c["review_reason"] else 1,
        int(c["record_no"]),
        c["name"].casefold(),
    )


def classify_key(
    *,
    source_hash: str,
    key: str,
    candidates: list[dict],
    master_row: dict | None,
) -> dict:
    party_nos = sorted({
        int(c["record_no"])
        for c in candidates
    })

    supporting_names = list(dict.fromkeys(
        c["name"] for c in candidates
    ))
    source_record_ids = list(dict.fromkeys(
        c["source_record_id"]
        for c in candidates
    ))

    if len(party_nos) > 1:
        chosen = sorted(
            candidates,
            key=_candidate_priority,
        )[0]
        return {
            "action": ACTION_HOLD_COLLISION,
            "chosen": chosen,
            "party_nos": party_nos,
            "supporting_names": supporting_names,
            "source_record_ids": source_record_ids,
            "review_reason": (
                "同一match_keyが複数のMETI No.に紐づく。"
                "party-aware管理まで単独master反映しない"
            ),
            "resolution": "",
        }

    safe: list[dict] = []

    for c in candidates:
        if c["invalid_reason"]:
            continue
        if not c["review_reason"]:
            safe.append(c)
            continue
        if _source_verified_long(
            source_hash=source_hash,
            name=c["name"],
            reason=c["review_reason"],
        ):
            copy_c = dict(c)
            copy_c["resolution"] = (
                "SOURCE_VERIFIED_LONG_ALIAS"
            )
            safe.append(copy_c)

    if safe:
        chosen = sorted(
            safe,
            key=_candidate_priority,
        )[0]

        if master_row is None:
            action = ACTION_READY_ADD
        elif SRC_METI in _split_sources(master_row):
            action = ACTION_NOOP
        else:
            action = ACTION_READY_TAG

        return {
            "action": action,
            "chosen": chosen,
            "party_nos": party_nos,
            "supporting_names": supporting_names,
            "source_record_ids": source_record_ids,
            "review_reason": (
                chosen.get("review_reason", "")
            ),
            "resolution": (
                chosen.get("resolution", "")
            ),
        }

    invalids = [
        c for c in candidates
        if c["invalid_reason"]
    ]
    reviews = [
        c for c in candidates
        if c["review_reason"]
    ]

    chosen = sorted(
        candidates,
        key=_candidate_priority,
    )[0]

    if invalids and len(invalids) == len(candidates):
        action = ACTION_HOLD_INVALID
        reason = " / ".join(dict.fromkeys(
            c["invalid_reason"]
            for c in invalids
            if c["invalid_reason"]
        ))
    elif reviews and all(
        _is_weak_reason(c["review_reason"])
        for c in reviews
    ):
        action = ACTION_HOLD_WEAK
        reason = " / ".join(dict.fromkeys(
            c["review_reason"]
            for c in reviews
            if c["review_reason"]
        ))
    else:
        action = ACTION_HOLD_REVIEW
        reason = " / ".join(dict.fromkeys(
            (
                c["invalid_reason"]
                or c["review_reason"]
            )
            for c in candidates
            if (
                c["invalid_reason"]
                or c["review_reason"]
            )
        ))

    return {
        "action": action,
        "chosen": chosen,
        "party_nos": party_nos,
        "supporting_names": supporting_names,
        "source_record_ids": source_record_ids,
        "review_reason": reason,
        "resolution": "",
    }


def build_plan(
    *,
    expected_hash: str,
) -> dict:
    verified = MR.verify_snapshot(
        expected_hash=expected_hash,
    )
    state = verified["state"]

    if state.get("review_status") != "APPROVED":
        raise ApplyPlanError(
            "review_statusがAPPROVEDではない"
        )
    if state.get("approved") is not True:
        raise ApplyPlanError(
            "approved=Trueではない"
        )
    if state.get("applied") is not False:
        raise ApplyPlanError(
            "既にapplied=True、またはstateが不正"
        )

    if not MASTER_PATH.exists():
        raise ApplyPlanError(
            f"masterが存在しない: {MASTER_PATH}"
        )
    if not MASTER_PATH.is_file():
        raise ApplyPlanError(
            f"masterが通常ファイルではない: {MASTER_PATH}"
        )

    master_hash = _sha256(MASTER_PATH)
    master = M.load(MASTER_PATH)

    records = load_records(
        verified["records_path"]
    )

    by_key: dict[str, list[dict]] = defaultdict(list)

    for rec in records:
        raw_items = [
            ("PRIMARY", rec.company),
            *[
                (f"ALIAS:{i}", alias)
                for i, alias in enumerate(
                    rec.aliases,
                    1,
                )
            ],
        ]

        for kind, raw_name in raw_items:
            name = canonical_display_name(
                raw_name
            )
            if not name:
                continue

            key = match_key(name)
            if not key:
                raise ApplyPlanError(
                    f"No.{rec.no} {kind} match_keyが空"
                )

            invalid_reason = validate(name) or ""
            review_reason = (
                needs_review(name)
                if not invalid_reason
                else ""
            ) or ""

            by_key[key].append({
                "record_no": rec.no,
                "record_primary": rec.company,
                "kind": kind,
                "name": name,
                "source_record_id": (
                    f"{expected_hash}:{rec.no}:{kind}"
                ),
                "invalid_reason": invalid_reason,
                "review_reason": review_reason,
            })

    plan_rows: list[dict] = []

    for key in sorted(by_key):
        master_row = master.get(key)

        result = classify_key(
            source_hash=expected_hash,
            key=key,
            candidates=by_key[key],
            master_row=master_row,
        )

        chosen = result["chosen"]

        plan_rows.append({
            "action": result["action"],
            "match_key": key,
            "name": chosen["name"],
            "record_no": chosen["record_no"],
            "record_primary": (
                chosen["record_primary"]
            ),
            "kind": chosen["kind"],
            "party_nos": ";".join(
                map(str, result["party_nos"])
            ),
            "supporting_names": json.dumps(
                result["supporting_names"],
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            "source_record_ids": ";".join(
                result["source_record_ids"]
            ),
            "review_reason": result[
                "review_reason"
            ],
            "resolution": result["resolution"],
            "existing_master_sources": (
                master_row.get("sources", "")
                if master_row
                else ""
            ),
            "existing_master_display_name": (
                master_row.get(
                    "display_name",
                    "",
                )
                if master_row
                else ""
            ),
        })

    existing_meti = {
        key
        for key, row in master.items()
        if SRC_METI in _split_sources(row)
    }

    legacy_rows = []

    for key in sorted(
        existing_meti - set(by_key)
    ):
        row = master[key]
        legacy_rows.append({
            "action": (
                "HOLD_LEGACY_NOT_IN_CURRENT"
            ),
            "match_key": key,
            "display_name": row.get(
                "display_name",
                "",
            ),
            "sources": row.get(
                "sources",
                "",
            ),
            "status": row.get(
                "status",
                "",
            ),
            "remark": row.get(
                "remark",
                "",
            ),
        })

    counts = Counter(
        row["action"]
        for row in plan_rows
    )

    # Snapshot-invariant safety expectations established by the read-only
    # triage.  If these change for the exact same PDF hash, stop rather than
    # silently producing a different plan.
    if expected_hash == (
        "c632d657f9c13b1250d688394eb7c99956a994527cb56f4a77c86ed61ff1dbda"
    ):
        if len(records) != 835:
            raise ApplyPlanError(
                "expected 835 METI records"
            )
        if len(by_key) != 2232:
            raise ApplyPlanError(
                "expected 2232 unique match_key"
            )
        if counts[ACTION_HOLD_WEAK] != 22:
            raise ApplyPlanError(
                "expected 22 weak aliases"
            )
        if counts[ACTION_HOLD_COLLISION] != 2:
            raise ApplyPlanError(
                "expected 2 cross-party collisions"
            )
        if counts[ACTION_HOLD_INVALID] != 0:
            raise ApplyPlanError(
                "expected 0 invalid names"
            )
        if counts[ACTION_HOLD_REVIEW] != 0:
            raise ApplyPlanError(
                "source-verified long aliases以外の"
                "未解決reviewが残っている"
            )

    return {
        "verified": verified,
        "state": state,
        "source_hash": expected_hash,
        "master_hash": master_hash,
        "master_count": len(master),
        "record_count": len(records),
        "raw_name_count": sum(
            len(v)
            for v in by_key.values()
        ),
        "unique_key_count": len(by_key),
        "plan_rows": plan_rows,
        "legacy_rows": legacy_rows,
        "counts": dict(counts),
    }


def _write_csv(
    path: Path,
    fields: list[str],
    rows: list[dict],
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    tmp = path.with_suffix(
        path.suffix + ".tmp"
    )
    with tmp.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as f:
        w = csv.DictWriter(
            f,
            fieldnames=fields,
            extrasaction="ignore",
            lineterminator="\n",
        )
        w.writeheader()
        w.writerows(rows)
    tmp.replace(path)


def write_plan(plan: dict) -> dict:
    source_hash = plan["source_hash"]
    master_hash = plan["master_hash"]
    base = (
        f"{source_hash[:12]}"
        f"__master_{master_hash[:12]}"
    )

    plan_csv = PLAN_DIR / (
        base + "__plan.csv"
    )
    legacy_csv = PLAN_DIR / (
        base + "__legacy_hold.csv"
    )
    summary_json = PLAN_DIR / (
        base + "__summary.json"
    )

    _write_csv(
        plan_csv,
        PLAN_COLS,
        plan["plan_rows"],
    )
    _write_csv(
        legacy_csv,
        LEGACY_COLS,
        plan["legacy_rows"],
    )

    plan_csv_hash = _sha256(plan_csv)
    legacy_csv_hash = _sha256(
        legacy_csv
    )

    summary = {
        "version": 1,
        "generated_at": _iso_now(),
        "status": "PREAPPLY_READY",
        "apply_executed": False,
        "source_hash": source_hash,
        "source_url": plan["verified"][
            "source_url"
        ],
        "master_sha256": master_hash,
        "master_record_count": plan[
            "master_count"
        ],
        "meti_record_count": plan[
            "record_count"
        ],
        "raw_name_count": plan[
            "raw_name_count"
        ],
        "unique_match_key_count": plan[
            "unique_key_count"
        ],
        "counts": plan["counts"],
        "legacy_unresolved_count": len(
            plan["legacy_rows"]
        ),
        "plan_path": str(
            plan_csv.relative_to(ROOT)
        ),
        "plan_sha256": plan_csv_hash,
        "legacy_hold_path": str(
            legacy_csv.relative_to(ROOT)
        ),
        "legacy_hold_sha256": (
            legacy_csv_hash
        ),
        "approved": True,
        "applied": False,
    }

    tmp = summary_json.with_suffix(
        ".json.tmp"
    )
    tmp.write_text(
        json.dumps(
            summary,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )
    tmp.replace(summary_json)

    summary["summary_path"] = str(
        summary_json.relative_to(ROOT)
    )
    return summary


def print_summary(summary: dict) -> None:
    counts = summary["counts"]

    print(
        "===== METI SAFE PRE-APPLY PLAN ====="
    )
    print(
        "source_hash             :",
        summary["source_hash"],
    )
    print(
        "master_sha256           :",
        summary["master_sha256"],
    )
    print(
        "master_record_count     :",
        summary["master_record_count"],
    )
    print(
        "METI records            :",
        summary["meti_record_count"],
    )
    print(
        "raw names               :",
        summary["raw_name_count"],
    )
    print(
        "unique match_key        :",
        summary["unique_match_key_count"],
    )
    print("")
    print(
        "READY_ADD               :",
        counts.get(
            ACTION_READY_ADD,
            0,
        ),
    )
    print(
        "READY_TAG_EXISTING      :",
        counts.get(
            ACTION_READY_TAG,
            0,
        ),
    )
    print(
        "NOOP_ALREADY_METI       :",
        counts.get(
            ACTION_NOOP,
            0,
        ),
    )
    print(
        "HOLD_WEAK_ALIAS         :",
        counts.get(
            ACTION_HOLD_WEAK,
            0,
        ),
    )
    print(
        "HOLD_MULTI_PARTY        :",
        counts.get(
            ACTION_HOLD_COLLISION,
            0,
        ),
    )
    print(
        "HOLD_REVIEW             :",
        counts.get(
            ACTION_HOLD_REVIEW,
            0,
        ),
    )
    print(
        "HOLD_INVALID            :",
        counts.get(
            ACTION_HOLD_INVALID,
            0,
        ),
    )
    print(
        "legacy unresolved       :",
        summary[
            "legacy_unresolved_count"
        ],
    )
    print("")
    print(
        "plan                    :",
        summary["plan_path"],
    )
    print(
        "plan SHA256             :",
        summary["plan_sha256"],
    )
    print(
        "legacy hold             :",
        summary["legacy_hold_path"],
    )
    print(
        "legacy hold SHA256      :",
        summary["legacy_hold_sha256"],
    )
    print(
        "summary                 :",
        summary["summary_path"],
    )
    print("")
    print(
        "PREAPPLY_PLAN_STATUS    : PASS"
    )
    print(
        "MASTER_FILE_WRITTEN     : NO"
    )
    print(
        "APPLIED_STATE_CHANGED   : NO"
    )


def main(
    argv: list[str] | None = None,
) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--hash",
        required=True,
    )
    args = ap.parse_args(argv)

    try:
        plan = build_plan(
            expected_hash=args.hash,
        )
        summary = write_plan(plan)
        print_summary(summary)
        return 0
    except (
        MR.ReviewError,
        ApplyPlanError,
    ) as exc:
        print(
            f"[BLOCKED] "
            f"{type(exc).__name__}: {exc}"
        )
        print(
            "MASTER_FILE_WRITTEN   = NO"
        )
        print(
            "APPLIED_STATE_CHANGED = NO"
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

