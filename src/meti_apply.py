"""Apply an approved METI safe manifest to master.csv with hard gates.

Two commands are provided:

verify
    Re-check the approved snapshot, plan/legacy hashes, current master hash,
    action semantics, HOLD exclusions, and a full in-memory merge.  It writes
    nothing to the repository and prints the deterministic post-apply master
    SHA256.

apply
    Runs the same verification again and requires the operator to explicitly
    confirm the plan SHA256, master-before SHA256, and master-after SHA256.
    Only READY_ADD and READY_TAG_EXISTING are merged.  NOOP and every HOLD
    action are excluded.  Legacy METI rows are never delisted.

The apply path uses an atomic master replacement, a runtime transaction
journal, and best-effort rollback of every repository file it touches if a
normal Python exception occurs.  A leftover runtime journal blocks a new apply
and signals manual recovery after an abrupt process/machine failure.
"""
from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from . import master as M
from . import meti_apply_plan as P
from . import meti_review as R
from . import source_audit
from .meti_manual_import import (
    CHANGES_PATH,
    STATE_PATH,
    append_dashboard_row,
    save_state,
)
from .normalize import SRC_METI, match_key

ROOT = Path(__file__).resolve().parent.parent
MASTER_PATH = ROOT / "data" / "master" / "master.csv"
APPLICATION_DIR = (
    ROOT / "data" / "manual" / "meti" / "applications"
)
APPLY_LEDGER = ROOT / "data" / "review" / "meti_apply_ledger.csv"
RUNTIME_DIR = ROOT / ".runtime"
JOURNAL_PATH = RUNTIME_DIR / "meti_apply_transaction.json"

READY_ACTIONS = {
    P.ACTION_READY_ADD,
    P.ACTION_READY_TAG,
}
HOLD_ACTIONS = {
    P.ACTION_HOLD_WEAK,
    P.ACTION_HOLD_REVIEW,
    P.ACTION_HOLD_INVALID,
    P.ACTION_HOLD_COLLISION,
}
ALL_ACTIONS = READY_ACTIONS | {
    P.ACTION_NOOP,
} | HOLD_ACTIONS

APPLY_LEDGER_COLS = [
    "application_id",
    "applied_at",
    "operator",
    "source_hash",
    "source_url",
    "plan_path",
    "plan_sha256",
    "legacy_hold_path",
    "legacy_hold_sha256",
    "master_before_sha256",
    "master_after_sha256",
    "master_before_count",
    "master_after_count",
    "added",
    "changed",
    "removed",
    "noop",
    "held_weak_alias",
    "held_multi_party",
    "held_review",
    "held_invalid",
    "legacy_unresolved",
    "status",
]


class ApplyError(RuntimeError):
    """A safety gate failed; do not modify master."""


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(
            lambda: f.read(1024 * 1024),
            b"",
        ):
            h.update(chunk)
    return h.hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None = None) -> str:
    return (dt or _now()).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _stamp(dt: datetime | None = None) -> str:
    return (dt or _now()).strftime(
        "%Y%m%dT%H%M%SZ"
    )


def _summary_merge_ts_ms(summary: dict) -> int:
    # plan生成時刻をmaster merge用の固定timestampへ変換する。
    # M.merge() は ts 未指定だと現在時刻を使うため、verifyのたびに
    # first_seen_ms / last_updated_ms が変わり、同一入力でも
    # master_after SHA256 が変化する。commit済みplan summaryの
    # generated_at を固定時刻として使い、再現性を保証する。
    raw = str(
        summary.get("generated_at") or ""
    ).strip()

    if not raw:
        raise ApplyError(
            "summary.generated_atが空"
        )

    try:
        dt = datetime.fromisoformat(
            raw.replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise ApplyError(
            "summary.generated_atの形式が不正: "
            f"{raw!r}"
        ) from exc

    if dt.tzinfo is None:
        raise ApplyError(
            "summary.generated_atにtimezoneがない"
        )

    dt = dt.astimezone(timezone.utc)
    ts_ms = int(dt.timestamp() * 1000)

    if ts_ms <= 0:
        raise ApplyError(
            "summary.generated_atから得たtimestampが不正"
        )

    return ts_ms


def _relative(path: Path) -> str:
    try:
        return str(
            path.resolve().relative_to(
                ROOT.resolve()
            )
        )
    except ValueError:
        return str(path)


def _resolve_repo_file(
    value: str | Path,
    label: str,
) -> Path:
    raw = str(value or "").strip()
    if not raw:
        raise ApplyError(f"{label}が空")

    p = Path(raw)
    if not p.is_absolute():
        p = ROOT / p
    p = p.resolve()

    try:
        p.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise ApplyError(
            f"{label}がrepository外: {p}"
        ) from exc

    if not p.exists():
        raise ApplyError(
            f"{label}が存在しない: {p}"
        )
    if not p.is_file():
        raise ApplyError(
            f"{label}が通常ファイルではない: {p}"
        )

    return p


def _load_json(
    path: Path,
    label: str,
) -> dict:
    if not path.exists():
        raise ApplyError(
            f"{label}が存在しない: {path}"
        )
    try:
        obj = json.loads(
            path.read_text(encoding="utf-8")
        )
    except Exception as exc:
        raise ApplyError(
            f"{label}をJSONとして読めない: {exc}"
        ) from exc

    if not isinstance(obj, dict):
        raise ApplyError(
            f"{label}がobjectではない"
        )
    return obj


def _load_csv_exact(
    path: Path,
    expected_fields: list[str],
    label: str,
) -> list[dict]:
    if not path.exists():
        raise ApplyError(
            f"{label}が存在しない: {path}"
        )
    if not path.is_file():
        raise ApplyError(
            f"{label}が通常ファイルではない: {path}"
        )

    with path.open(
        encoding="utf-8",
        newline="",
    ) as f:
        reader = csv.DictReader(f)
        actual = reader.fieldnames or []
        if actual != expected_fields:
            raise ApplyError(
                f"{label}列構造が不一致: "
                f"expected={expected_fields} "
                f"actual={actual}"
            )
        return list(reader)


def _sources(row: dict) -> set[str]:
    return {
        x
        for x in str(
            row.get("sources", "")
        ).split(";")
        if x
    }


def _string_row(row: dict) -> dict:
    return {
        f: str(row.get(f, ""))
        for f in M.FIELDS
    }


def _assert_plan_semantics(
    *,
    rows: list[dict],
    legacy_rows: list[dict],
    master: dict[str, dict],
) -> dict:
    if not rows:
        raise ApplyError("planが0行")

    actions = Counter()
    keys: set[str] = set()

    for row in rows:
        action = str(
            row.get("action") or ""
        )
        key = str(
            row.get("match_key") or ""
        )
        name = str(
            row.get("name") or ""
        )

        if action not in ALL_ACTIONS:
            raise ApplyError(
                f"未知のplan action: {action!r}"
            )

        if not key:
            raise ApplyError(
                "planに空match_keyがある"
            )

        if key in keys:
            raise ApplyError(
                f"plan match_key重複: {key}"
            )
        keys.add(key)

        if match_key(name) != key:
            raise ApplyError(
                "plan nameから再生成したmatch_keyが"
                f"一致しない: {name!r}"
            )

        master_row = master.get(key)
        srcs = (
            _sources(master_row)
            if master_row
            else set()
        )

        if action == P.ACTION_READY_ADD:
            if master_row is not None:
                raise ApplyError(
                    "READY_ADDなのにmasterに存在: "
                    f"{name!r}"
                )

        elif action == P.ACTION_READY_TAG:
            if master_row is None:
                raise ApplyError(
                    "READY_TAG_EXISTINGなのに"
                    f"masterに存在しない: {name!r}"
                )
            if SRC_METI in srcs:
                raise ApplyError(
                    "READY_TAG_EXISTINGなのに"
                    f"既にMETIタグあり: {name!r}"
                )

        elif action == P.ACTION_NOOP:
            if master_row is None:
                raise ApplyError(
                    "NOOPなのにmasterに存在しない: "
                    f"{name!r}"
                )
            if SRC_METI not in srcs:
                raise ApplyError(
                    "NOOPなのにMETIタグがない: "
                    f"{name!r}"
                )

        elif action == P.ACTION_HOLD_WEAK:
            kind = str(
                row.get("kind") or ""
            )
            if not kind.startswith("ALIAS:"):
                raise ApplyError(
                    "HOLD_WEAK_ALIASがAliasではない: "
                    f"{name!r}"
                )

        elif action == P.ACTION_HOLD_COLLISION:
            party_nos = [
                x
                for x in str(
                    row.get("party_nos") or ""
                ).split(";")
                if x
            ]
            if len(set(party_nos)) < 2:
                raise ApplyError(
                    "HOLD_MULTI_PARTY_COLLISIONなのに"
                    "複数Party番号がない"
                )

        if (
            action in READY_ACTIONS
            | {P.ACTION_NOOP}
            and row.get("review_reason")
            and row.get("resolution")
            != "SOURCE_VERIFIED_LONG_ALIAS"
        ):
            raise ApplyError(
                "review_reason付きREADY/NOOPが"
                "source-verified解決されていない: "
                f"{name!r}"
            )

        actions[action] += 1

    if actions[P.ACTION_HOLD_REVIEW]:
        raise ApplyError(
            "HOLD_REVIEWが残っている"
        )
    if actions[P.ACTION_HOLD_INVALID]:
        raise ApplyError(
            "HOLD_INVALIDが残っている"
        )

    legacy_keys: set[str] = set()

    for row in legacy_rows:
        action = str(
            row.get("action") or ""
        )
        key = str(
            row.get("match_key") or ""
        )

        if action != "HOLD_LEGACY_NOT_IN_CURRENT":
            raise ApplyError(
                "legacy holdに未知action: "
                f"{action!r}"
            )
        if not key:
            raise ApplyError(
                "legacy holdに空match_key"
            )
        if key in legacy_keys:
            raise ApplyError(
                f"legacy hold key重複: {key}"
            )
        legacy_keys.add(key)

        if key in keys:
            raise ApplyError(
                "legacy hold keyがcurrent planにも"
                f"存在する: {key}"
            )

        master_row = master.get(key)
        if master_row is None:
            raise ApplyError(
                "legacy hold keyがmasterにない: "
                f"{key}"
            )
        if SRC_METI not in _sources(
            master_row
        ):
            raise ApplyError(
                "legacy holdなのにmasterへ"
                f"METIタグがない: {key}"
            )

    return {
        "counts": dict(actions),
        "plan_keys": keys,
        "legacy_keys": legacy_keys,
    }


def _simulate(
    *,
    rows: list[dict],
    legacy_rows: list[dict],
    master: dict[str, dict],
    merge_ts_ms: int,
) -> dict:
    semantic = _assert_plan_semantics(
        rows=rows,
        legacy_rows=legacy_rows,
        master=master,
    )
    counts = semantic["counts"]

    ready_rows = [
        row
        for row in rows
        if row["action"] in READY_ACTIONS
    ]

    merge_records = [
        {
            "source": SRC_METI,
            "category": "",
            "name": row["name"],
            "source_id": row[
                "source_record_ids"
            ],
        }
        for row in ready_rows
    ]

    before = copy.deepcopy(master)
    after = copy.deepcopy(master)

    diff = M.merge(
        after,
        merge_records,
        SRC_METI,
        ts=merge_ts_ms,
        delist=False,
        report_missing=False,
    )

    expected_add_keys = {
        row["match_key"]
        for row in rows
        if row["action"]
        == P.ACTION_READY_ADD
    }
    expected_change_keys = {
        row["match_key"]
        for row in rows
        if row["action"]
        == P.ACTION_READY_TAG
    }

    actual_add_keys = {
        x["key"] for x in diff.added
    }
    actual_change_keys = {
        x["key"] for x in diff.changed
    }

    if actual_add_keys != expected_add_keys:
        raise ApplyError(
            "merge追加keyがplanと一致しない: "
            f"expected={len(expected_add_keys)} "
            f"actual={len(actual_add_keys)}"
        )

    if (
        actual_change_keys
        != expected_change_keys
    ):
        raise ApplyError(
            "merge変更keyがplanと一致しない: "
            f"expected={len(expected_change_keys)} "
            f"actual={len(actual_change_keys)}"
        )

    if diff.removed:
        raise ApplyError(
            "safe applyで削除が発生した"
        )

    if len(after) != (
        len(before)
        + len(expected_add_keys)
    ):
        raise ApplyError(
            "master件数増分がREADY_ADDと"
            "一致しない"
        )

    changed_existing = {
        key
        for key in before
        if _string_row(before[key])
        != _string_row(after[key])
    }

    if (
        changed_existing
        != expected_change_keys
    ):
        unexpected = sorted(
            changed_existing
            - expected_change_keys
        )[:20]
        missing = sorted(
            expected_change_keys
            - changed_existing
        )[:20]
        raise ApplyError(
            "plan外の既存master行が変更された: "
            f"unexpected={unexpected} "
            f"missing={missing}"
        )

    for key in semantic["legacy_keys"]:
        if _string_row(before[key]) != (
            _string_row(after[key])
        ):
            raise ApplyError(
                "legacy hold行が変更された: "
                f"{key}"
            )

    hold_keys = {
        row["match_key"]
        for row in rows
        if row["action"] in HOLD_ACTIONS
    }

    for key in hold_keys & set(before):
        if _string_row(before[key]) != (
            _string_row(after[key])
        ):
            raise ApplyError(
                "HOLD行が変更された: "
                f"{key}"
            )

    return {
        "before": before,
        "after": after,
        "diff": diff,
        "counts": counts,
        "ready_rows": ready_rows,
        "expected_add_keys": expected_add_keys,
        "expected_change_keys": (
            expected_change_keys
        ),
        "legacy_keys": semantic[
            "legacy_keys"
        ],
    }


def verify_executor(
    *,
    summary_path: Path,
    expected_source_hash: str,
) -> dict:
    summary_path = _resolve_repo_file(
        summary_path,
        "summary",
    )
    summary = _load_json(
        summary_path,
        "apply plan summary",
    )

    if summary.get("status") != (
        "PREAPPLY_READY"
    ):
        raise ApplyError(
            "summary.statusがPREAPPLY_READY"
            "ではない"
        )
    if (
        summary.get("apply_executed")
        is not False
    ):
        raise ApplyError(
            "summary.apply_executedがFalse"
            "ではない"
        )
    if summary.get("approved") is not True:
        raise ApplyError(
            "summary.approvedがTrueではない"
        )
    if summary.get("applied") is not False:
        raise ApplyError(
            "summary.appliedがFalseではない"
        )

    source_hash = str(
        summary.get("source_hash") or ""
    )

    if source_hash != expected_source_hash:
        raise ApplyError(
            "summary source_hash不一致: "
            f"{source_hash}"
        )

    verified = R.verify_snapshot(
        expected_hash=expected_source_hash,
    )
    state = verified["state"]

    if state.get("review_status") != (
        "APPROVED"
    ):
        raise ApplyError(
            "state.review_statusがAPPROVED"
            "ではない"
        )
    if state.get("approved") is not True:
        raise ApplyError(
            "state.approvedがTrueではない"
        )
    if state.get("applied") is not False:
        raise ApplyError(
            "state.appliedがFalseではない"
        )

    plan_path = _resolve_repo_file(
        summary.get("plan_path", ""),
        "plan",
    )
    legacy_path = _resolve_repo_file(
        summary.get(
            "legacy_hold_path",
            "",
        ),
        "legacy hold",
    )

    actual_plan_hash = _sha256(
        plan_path
    )
    actual_legacy_hash = _sha256(
        legacy_path
    )

    if actual_plan_hash != str(
        summary.get("plan_sha256") or ""
    ):
        raise ApplyError(
            "plan SHA256不一致"
        )

    if actual_legacy_hash != str(
        summary.get(
            "legacy_hold_sha256"
        ) or ""
    ):
        raise ApplyError(
            "legacy hold SHA256不一致"
        )

    current_master_hash = _sha256(
        MASTER_PATH
    )
    expected_master_hash = str(
        summary.get("master_sha256") or ""
    )

    if (
        current_master_hash
        != expected_master_hash
    ):
        raise ApplyError(
            "masterがplan作成後に変化した: "
            f"expected={expected_master_hash} "
            f"actual={current_master_hash}"
        )

    master = M.load(MASTER_PATH)

    try:
        expected_master_count = int(
            summary.get(
                "master_record_count"
            )
        )
    except (TypeError, ValueError) as exc:
        raise ApplyError(
            "summary master_record_count不正"
        ) from exc

    if len(master) != expected_master_count:
        raise ApplyError(
            "master record count不一致: "
            f"summary={expected_master_count} "
            f"actual={len(master)}"
        )

    rows = _load_csv_exact(
        plan_path,
        P.PLAN_COLS,
        "plan CSV",
    )
    legacy_rows = _load_csv_exact(
        legacy_path,
        P.LEGACY_COLS,
        "legacy hold CSV",
    )

    actual_counts = Counter(
        row["action"]
        for row in rows
    )
    summary_counts = {
        str(k): int(v)
        for k, v in (
            summary.get("counts") or {}
        ).items()
    }

    if dict(actual_counts) != (
        summary_counts
    ):
        raise ApplyError(
            "summary countsとplan CSVが"
            "一致しない"
        )

    if len(legacy_rows) != int(
        summary.get(
            "legacy_unresolved_count"
        )
    ):
        raise ApplyError(
            "legacy unresolved件数が"
            "summaryと一致しない"
        )

    merge_ts_ms = _summary_merge_ts_ms(
        summary
    )

    sim = _simulate(
        rows=rows,
        legacy_rows=legacy_rows,
        master=master,
        merge_ts_ms=merge_ts_ms,
    )

    with tempfile.TemporaryDirectory() as td:
        candidate = (
            Path(td) / "master_after.csv"
        )
        M.save(
            sim["after"],
            candidate,
        )
        after_hash = _sha256(
            candidate
        )
        reloaded = M.load(candidate)

    if set(reloaded) != set(
        sim["after"]
    ):
        raise ApplyError(
            "post-master roundtripでkey集合が"
            "一致しない"
        )

    for key, row in sim["after"].items():
        if _string_row(
            reloaded[key]
        ) != _string_row(row):
            raise ApplyError(
                "post-master roundtripで"
                f"内容不一致: {key}"
            )

    # Exact baseline expectations for the approved 2025-09-29 snapshot.
    if expected_source_hash == (
        "c632d657f9c13b1250d688394eb7c999"
        "56a994527cb56f4a77c86ed61ff1dbda"
    ):
        expected = {
            P.ACTION_READY_ADD: 314,
            P.ACTION_READY_TAG: 432,
            P.ACTION_NOOP: 1462,
            P.ACTION_HOLD_WEAK: 22,
            P.ACTION_HOLD_COLLISION: 2,
        }

        for action, value in expected.items():
            if actual_counts[action] != value:
                raise ApplyError(
                    f"{action}件数がbaseline"
                    f"期待値と不一致: "
                    f"{actual_counts[action]} != "
                    f"{value}"
                )

        if actual_counts[
            P.ACTION_HOLD_REVIEW
        ] != 0:
            raise ApplyError(
                "baseline HOLD_REVIEW != 0"
            )
        if actual_counts[
            P.ACTION_HOLD_INVALID
        ] != 0:
            raise ApplyError(
                "baseline HOLD_INVALID != 0"
            )
        if len(legacy_rows) != 2967:
            raise ApplyError(
                "baseline legacy unresolved"
                " != 2967"
            )

    return {
        "summary_path": summary_path,
        "summary": summary,
        "verified": verified,
        "plan_path": plan_path,
        "legacy_path": legacy_path,
        "rows": rows,
        "legacy_rows": legacy_rows,
        "master_before_sha256": (
            current_master_hash
        ),
        "master_after_sha256": after_hash,
        "master_merge_timestamp_ms": merge_ts_ms,
        "master_before_count": len(master),
        "master_after_count": len(
            sim["after"]
        ),
        "simulation": sim,
        "counts": dict(actual_counts),
    }


def print_verify(result: dict) -> None:
    c = Counter(result["counts"])

    print(
        "===== METI APPLY EXECUTOR VERIFY ====="
    )
    print(
        "source_hash          :",
        result["summary"]["source_hash"],
    )
    print(
        "plan_sha256          :",
        result["summary"]["plan_sha256"],
    )
    print(
        "master_before_sha256 :",
        result["master_before_sha256"],
    )
    print(
        "master_after_sha256  :",
        result["master_after_sha256"],
    )
    print(
        "merge_timestamp_ms   :",
        result["master_merge_timestamp_ms"],
    )
    print(
        "master_before_count  :",
        result["master_before_count"],
    )
    print(
        "master_after_count   :",
        result["master_after_count"],
    )
    print("")
    print(
        "READY_ADD            :",
        c[P.ACTION_READY_ADD],
    )
    print(
        "READY_TAG_EXISTING   :",
        c[P.ACTION_READY_TAG],
    )
    print(
        "NOOP_ALREADY_METI    :",
        c[P.ACTION_NOOP],
    )
    print(
        "HOLD_WEAK_ALIAS      :",
        c[P.ACTION_HOLD_WEAK],
    )
    print(
        "HOLD_MULTI_PARTY     :",
        c[P.ACTION_HOLD_COLLISION],
    )
    print(
        "HOLD_REVIEW          :",
        c[P.ACTION_HOLD_REVIEW],
    )
    print(
        "HOLD_INVALID         :",
        c[P.ACTION_HOLD_INVALID],
    )
    print(
        "legacy unresolved    :",
        len(result["legacy_rows"]),
    )
    print("")
    print(
        "EXECUTOR_VERIFY      : PASS"
    )
    print(
        "MASTER_FILE_WRITTEN  : NO"
    )
    print(
        "APPLIED_STATE_CHANGED: NO"
    )


def _git_clean() -> None:
    try:
        cp = subprocess.run(
            [
                "git",
                "status",
                "--porcelain",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
    except Exception as exc:
        raise ApplyError(
            f"git status確認失敗: {exc}"
        ) from exc

    if cp.stdout.strip():
        raise ApplyError(
            "apply前のworking treeがclean"
            "ではない。RSS等を停止し、"
            "未コミット差分を解消すること"
        )


def _read_optional(path: Path) -> bytes | None:
    if path.exists():
        return path.read_bytes()
    return None


def _atomic_write_bytes(
    path: Path,
    data: bytes,
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    tmp = path.with_name(
        path.name + ".rollback.tmp"
    )
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _restore(
    snapshots: dict[Path, bytes | None],
) -> None:
    errors: list[str] = []

    for path, data in snapshots.items():
        try:
            if data is None:
                path.unlink(
                    missing_ok=True
                )
            else:
                _atomic_write_bytes(
                    path,
                    data,
                )
        except Exception as exc:
            errors.append(
                f"{path}: {exc}"
            )

    if errors:
        raise ApplyError(
            "ROLLBACK FAILED: "
            + " | ".join(errors)
        )


def _append_apply_ledger(
    row: dict,
) -> None:
    APPLY_LEDGER.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    if (
        APPLY_LEDGER.exists()
        and APPLY_LEDGER.stat().st_size
    ):
        with APPLY_LEDGER.open(
            encoding="utf-8",
            newline="",
        ) as f:
            actual = next(
                csv.reader(f),
                [],
            )
        if actual != APPLY_LEDGER_COLS:
            raise ApplyError(
                "METI apply ledger列構造が"
                "想定外"
            )

    new = (
        not APPLY_LEDGER.exists()
        or APPLY_LEDGER.stat().st_size
        == 0
    )

    with APPLY_LEDGER.open(
        "a",
        encoding="utf-8",
        newline="",
    ) as f:
        w = csv.DictWriter(
            f,
            fieldnames=APPLY_LEDGER_COLS,
            extrasaction="ignore",
            lineterminator="\n",
        )
        if new:
            w.writeheader()
        w.writerow({
            k: row.get(k, "")
            for k in APPLY_LEDGER_COLS
        })


def _write_json_atomic(
    path: Path,
    obj: dict,
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    tmp = path.with_suffix(
        path.suffix + ".tmp"
    )
    tmp.write_text(
        json.dumps(
            obj,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


def apply_verified(
    *,
    result: dict,
    operator: str,
    confirm_plan_sha256: str,
    confirm_master_before_sha256: str,
    confirm_master_after_sha256: str,
) -> dict:
    operator = str(operator or "").strip()
    if not operator:
        raise ApplyError(
            "operatorは必須"
        )

    summary = result["summary"]

    if (
        confirm_plan_sha256
        != summary["plan_sha256"]
    ):
        raise ApplyError(
            "confirm plan SHA256不一致"
        )
    if (
        confirm_master_before_sha256
        != result[
            "master_before_sha256"
        ]
    ):
        raise ApplyError(
            "confirm master-before SHA256"
            "不一致"
        )
    if (
        confirm_master_after_sha256
        != result[
            "master_after_sha256"
        ]
    ):
        raise ApplyError(
            "confirm master-after SHA256"
            "不一致"
        )

    _git_clean()

    if JOURNAL_PATH.exists():
        raise ApplyError(
            "未完了METI apply transaction"
            f" journalが存在する: "
            f"{JOURNAL_PATH}. "
            "新規applyを停止。manual recovery"
            " required"
        )

    # Re-run every gate immediately before any write.
    result = verify_executor(
        summary_path=result[
            "summary_path"
        ],
        expected_source_hash=summary[
            "source_hash"
        ],
    )

    if (
        result["master_after_sha256"]
        != confirm_master_after_sha256
    ):
        raise ApplyError(
            "直前再検証でmaster-after SHAが"
            "変化した"
        )

    now = _now()
    application_id = (
        f"{_stamp(now)}__"
        f"{summary['source_hash'][:12]}__apply"
    )
    application_path = (
        APPLICATION_DIR
        / f"{application_id}.json"
    )

    audit_path = (
        ROOT
        / source_audit.AUDIT_DIR
        / f"{now:%Y-%m}.csv"
    )

    touched = {
        MASTER_PATH,
        STATE_PATH,
        CHANGES_PATH,
        audit_path,
        APPLY_LEDGER,
        application_path,
    }

    snapshots = {
        p: _read_optional(p)
        for p in touched
    }

    RUNTIME_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    journal = {
        "version": 1,
        "status": "PREPARED",
        "application_id": application_id,
        "prepared_at": _iso(now),
        "source_hash": summary[
            "source_hash"
        ],
        "plan_sha256": summary[
            "plan_sha256"
        ],
        "master_before_sha256": result[
            "master_before_sha256"
        ],
        "master_after_sha256": result[
            "master_after_sha256"
        ],
    }
    _write_json_atomic(
        JOURNAL_PATH,
        journal,
    )

    try:
        master_tmp = MASTER_PATH.with_name(
            MASTER_PATH.name
            + ".meti-apply.tmp"
        )
        master_tmp.unlink(
            missing_ok=True
        )

        M.save(
            result["simulation"]["after"],
            master_tmp,
        )

        tmp_hash = _sha256(
            master_tmp
        )
        if (
            tmp_hash
            != result[
                "master_after_sha256"
            ]
        ):
            raise ApplyError(
                "master temp SHA256がverify"
                "結果と一致しない"
            )

        os.replace(
            master_tmp,
            MASTER_PATH,
        )

        if _sha256(
            MASTER_PATH
        ) != result[
            "master_after_sha256"
        ]:
            raise ApplyError(
                "master atomic replace後SHA"
                "不一致"
            )

        counts = Counter(
            result["counts"]
        )

        state = copy.deepcopy(
            result["verified"]["state"]
        )
        state.update({
            "applied": True,
            "apply_status": (
                "APPLIED_WITH_HOLDS"
            ),
            "applied_at": _iso(now),
            "applied_by": operator,
            "applied_source_hash": (
                summary["source_hash"]
            ),
            "apply_plan_path": (
                summary["plan_path"]
            ),
            "apply_plan_sha256": (
                summary["plan_sha256"]
            ),
            "master_before_sha256": result[
                "master_before_sha256"
            ],
            "master_after_sha256": result[
                "master_after_sha256"
            ],
            "master_merge_timestamp_ms": result[
                "master_merge_timestamp_ms"
            ],
            "applied_add_count": counts[
                P.ACTION_READY_ADD
            ],
            "applied_change_count": counts[
                P.ACTION_READY_TAG
            ],
            "applied_noop_count": counts[
                P.ACTION_NOOP
            ],
            "held_weak_alias_count": counts[
                P.ACTION_HOLD_WEAK
            ],
            "held_multi_party_count": counts[
                P.ACTION_HOLD_COLLISION
            ],
            "held_review_count": counts[
                P.ACTION_HOLD_REVIEW
            ],
            "held_invalid_count": counts[
                P.ACTION_HOLD_INVALID
            ],
            "legacy_unresolved_count": len(
                result["legacy_rows"]
            ),
            "application_id": (
                application_id
            ),
            "application_path": (
                _relative(
                    application_path
                )
            ),
        })
        save_state(state)

        app = {
            "version": 1,
            "status": "APPLIED_WITH_HOLDS",
            "apply_executed": True,
            "application_id": application_id,
            "applied_at": _iso(now),
            "operator": operator,
            "source_hash": summary[
                "source_hash"
            ],
            "source_url": summary[
                "source_url"
            ],
            "plan_path": summary[
                "plan_path"
            ],
            "plan_sha256": summary[
                "plan_sha256"
            ],
            "legacy_hold_path": summary[
                "legacy_hold_path"
            ],
            "legacy_hold_sha256": summary[
                "legacy_hold_sha256"
            ],
            "master_before_sha256": result[
                "master_before_sha256"
            ],
            "master_after_sha256": result[
                "master_after_sha256"
            ],
            "master_merge_timestamp_ms": result[
                "master_merge_timestamp_ms"
            ],
            "master_before_count": result[
                "master_before_count"
            ],
            "master_after_count": result[
                "master_after_count"
            ],
            "added": counts[
                P.ACTION_READY_ADD
            ],
            "changed": counts[
                P.ACTION_READY_TAG
            ],
            "removed": 0,
            "noop": counts[
                P.ACTION_NOOP
            ],
            "held_weak_alias": counts[
                P.ACTION_HOLD_WEAK
            ],
            "held_multi_party": counts[
                P.ACTION_HOLD_COLLISION
            ],
            "held_review": counts[
                P.ACTION_HOLD_REVIEW
            ],
            "held_invalid": counts[
                P.ACTION_HOLD_INVALID
            ],
            "legacy_unresolved": len(
                result["legacy_rows"]
            ),
        }

        _write_json_atomic(
            application_path,
            app,
        )
        _append_apply_ledger(
            app,
        )

        report = result[
            "verified"
        ]["report"]
        raw_path = result[
            "verified"
        ]["raw_path"]

        source_audit.write(
            ROOT,
            [
                source_audit.entry(
                    "meti_manual",
                    "foreign_user_list_pdf",
                    "applied_with_holds",
                    url=summary[
                        "source_url"
                    ],
                    content_hash=summary[
                        "source_hash"
                    ],
                    fetched_file=(
                        raw_path.name
                    ),
                    raw_path=_relative(
                        raw_path
                    ),
                    source_updated=str(
                        report.get(
                            "effective_date",
                            "",
                        )
                        or ""
                    ),
                    record_count=result[
                        "verified"
                    ]["record_count"],
                    diff_counts={
                        "追加": counts[
                            P.ACTION_READY_ADD
                        ],
                        "変更": counts[
                            P.ACTION_READY_TAG
                        ],
                        "削除": 0,
                    },
                    fetch_failed=False,
                    schema_changed=False,
                )
            ],
        )

        append_dashboard_row(
            "手動正本反映完了（保留あり）",
            "外国ユーザーリスト",
            (
                "APPROVED / "
                "applied=False"
            ),
            (
                "APPLIED / "
                f"追加{counts[P.ACTION_READY_ADD]} "
                f"変更{counts[P.ACTION_READY_TAG]} / "
                f"Weak保留{counts[P.ACTION_HOLD_WEAK]} / "
                f"Party保留{counts[P.ACTION_HOLD_COLLISION]} / "
                f"Legacy保留{len(result['legacy_rows'])} / "
                f"SHA256 {summary['source_hash'][:12]}"
            ),
        )

        journal["status"] = "COMMITTED"
        journal["committed_at"] = _iso()
        _write_json_atomic(
            JOURNAL_PATH,
            journal,
        )
        JOURNAL_PATH.unlink(
            missing_ok=True
        )

        return {
            "application": app,
            "application_path": (
                application_path
            ),
            "state": state,
        }

    except Exception as exc:
        try:
            _restore(
                snapshots
            )
            JOURNAL_PATH.unlink(
                missing_ok=True
            )
        except Exception as rollback_exc:
            raise ApplyError(
                "APPLY FAILED AND ROLLBACK "
                "FAILED. MANUAL RECOVERY "
                "REQUIRED. "
                f"apply_error={exc}; "
                f"rollback_error={rollback_exc}"
            ) from rollback_exc

        raise ApplyError(
            "apply失敗。repository filesは"
            "rollback済み: "
            f"{type(exc).__name__}: {exc}"
        ) from exc


def print_apply(result: dict) -> None:
    app = result["application"]

    print(
        "===== METI APPLY COMPLETE ====="
    )
    print(
        "application_id       :",
        app["application_id"],
    )
    print(
        "operator             :",
        app["operator"],
    )
    print(
        "source_hash          :",
        app["source_hash"],
    )
    print(
        "master_before_sha256 :",
        app["master_before_sha256"],
    )
    print(
        "master_after_sha256  :",
        app["master_after_sha256"],
    )
    print(
        "master_before_count  :",
        app["master_before_count"],
    )
    print(
        "master_after_count   :",
        app["master_after_count"],
    )
    print(
        "added                :",
        app["added"],
    )
    print(
        "changed              :",
        app["changed"],
    )
    print(
        "removed              :",
        app["removed"],
    )
    print(
        "noop                 :",
        app["noop"],
    )
    print(
        "held weak alias      :",
        app["held_weak_alias"],
    )
    print(
        "held multi-party     :",
        app["held_multi_party"],
    )
    print(
        "legacy unresolved    :",
        app["legacy_unresolved"],
    )
    print(
        "application report   :",
        _relative(
            result[
                "application_path"
            ]
        ),
    )
    print("")
    print(
        "APPLY_STATUS         : "
        "APPLIED_WITH_HOLDS"
    )
    print(
        "APPLIED_STATE        : TRUE"
    )


def main(
    argv: list[str] | None = None,
) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(
        dest="command",
        required=True,
    )

    verify = sub.add_parser(
        "verify"
    )
    verify.add_argument(
        "--summary",
        required=True,
    )
    verify.add_argument(
        "--source-hash",
        required=True,
    )

    apply_p = sub.add_parser(
        "apply"
    )
    apply_p.add_argument(
        "--summary",
        required=True,
    )
    apply_p.add_argument(
        "--source-hash",
        required=True,
    )
    apply_p.add_argument(
        "--operator",
        required=True,
    )
    apply_p.add_argument(
        "--confirm-plan-sha256",
        required=True,
    )
    apply_p.add_argument(
        "--confirm-master-before-sha256",
        required=True,
    )
    apply_p.add_argument(
        "--confirm-master-after-sha256",
        required=True,
    )

    args = ap.parse_args(argv)

    try:
        result = verify_executor(
            summary_path=Path(
                args.summary
            ),
            expected_source_hash=(
                args.source_hash
            ),
        )

        if args.command == "verify":
            print_verify(result)
            return 0

        applied = apply_verified(
            result=result,
            operator=args.operator,
            confirm_plan_sha256=(
                args.confirm_plan_sha256
            ),
            confirm_master_before_sha256=(
                args.confirm_master_before_sha256
            ),
            confirm_master_after_sha256=(
                args.confirm_master_after_sha256
            ),
        )
        print_apply(applied)
        return 0

    except (
        ApplyError,
        R.ReviewError,
    ) as exc:
        print(
            f"[BLOCKED] "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        print(
            "MASTER_WRITE = BLOCKED"
        )
        print(
            "APPLIED_STATE = UNCHANGED"
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

