"""経産省 外国ユーザーリストの人手レビュー / 承認ゲート。

このモジュールは master へ一切反映しない。
役割は以下に限定する。

1. 手動取得・解析済みsnapshotの完全性を再検証
2. reviewer / decision / note を Review Ledger へ記録
3. state を REVIEW_REQUIRED -> APPROVED / REJECTED に遷移
4. Source Audit / Dashboard へレビュー結果を記録

重要:
- source hash を明示指定し、現在snapshotと一致しなければ停止する。
- raw / report / records / evidence / diff の整合性が崩れていれば承認不可。
- APPROVED でも applied=False のまま。master反映は別工程。
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import source_audit
from .meti_manual_import import (
    EVIDENCE_PATH,
    ROOT,
    STATE_PATH,
    append_dashboard_row,
    sha256_file,
)

REVIEW_DIR = ROOT / "data" / "review"
REVIEW_LEDGER = REVIEW_DIR / "meti_foreign_user_list.csv"
REVIEW_ARTIFACT_DIR = ROOT / "data" / "manual" / "meti" / "reviews"

REVIEW_COLS = [
    "review_id",
    "reviewed_at",
    "source",
    "document_role",
    "source_hash",
    "source_url",
    "decision",
    "reviewer",
    "note",
    "record_count",
    "diff_added",
    "diff_changed",
    "diff_removed",
    "report_path",
    "records_path",
    "diff_path",
    "raw_path",
    "evidence_path",
]

DECISION_APPROVED = "APPROVED"
DECISION_REJECTED = "REJECTED"


class ReviewError(RuntimeError):
    """承認を止めるべき整合性異常。"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None = None) -> str:
    return (dt or _now()).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stamp(dt: datetime | None = None) -> str:
    return (dt or _now()).strftime("%Y%m%dT%H%M%SZ")


def _load_json(path: Path, label: str) -> dict:
    if not path.exists():
        raise ReviewError(f"{label}が存在しない: {path}")
    if not path.is_file():
        raise ReviewError(f"{label}が通常ファイルではない: {path}")
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ReviewError(f"{label}をJSONとして読めない: {exc}") from exc
    if not isinstance(obj, dict):
        raise ReviewError(f"{label}がobjectではない")
    return obj


def _resolve_repo_path(value: str, label: str) -> Path:
    raw = str(value or "").strip()
    if not raw:
        raise ReviewError(f"{label}が空")
    path = Path(raw)
    if not path.is_absolute():
        path = ROOT / path
    path = path.resolve()
    root = ROOT.resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ReviewError(
            f"{label}がrepository外を指している: {path}"
        ) from exc
    if not path.exists():
        raise ReviewError(f"{label}が存在しない: {path}")
    if not path.is_file():
        raise ReviewError(f"{label}が通常ファイルではない: {path}")
    return path


def _relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT.resolve()))
    except ValueError:
        return str(path)


def _load_csv(path: Path, label: str) -> tuple[list[str], list[dict]]:
    if not path.exists():
        raise ReviewError(f"{label}が存在しない: {path}")
    if not path.is_file():
        raise ReviewError(f"{label}が通常ファイルではない: {path}")
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames or []
        rows = list(reader)
    if not fields:
        raise ReviewError(f"{label}のヘッダーがない")
    return fields, rows


def _expected_diff_counts(report: dict) -> dict[str, int]:
    raw = report.get("diff") or {}
    if not isinstance(raw, dict):
        raise ReviewError("report.diffがobjectではない")
    try:
        return {
            "追加": int(raw.get("追加", 0)),
            "変更": int(raw.get("変更", 0)),
            "削除": int(raw.get("削除", 0)),
        }
    except (TypeError, ValueError) as exc:
        raise ReviewError(f"report.diff件数が数値ではない: {raw}") from exc


def _actual_diff_counts(rows: list[dict]) -> dict[str, int]:
    counts = {"追加": 0, "変更": 0, "削除": 0}
    for row in rows:
        action = str(row.get("action", "")).strip()
        if not action:
            continue
        if action not in counts:
            raise ReviewError(f"未知のdiff actionを検出: {action!r}")
        counts[action] += 1
    return counts


def verify_snapshot(
    *,
    expected_hash: str | None = None,
) -> dict:
    """現在snapshotを読み取り専用で完全性検証する。"""
    state = _load_json(STATE_PATH, "METI manual state")

    source_hash = str(state.get("current_source_hash") or "").strip()
    if not re.fullmatch(r"[0-9a-f]{64}", source_hash):
        raise ReviewError(
            f"stateのsource_hashが不正: {source_hash!r}"
        )

    if expected_hash is not None and source_hash != expected_hash:
        raise ReviewError(
            "指定hashと現在snapshotが一致しない: "
            f"expected={expected_hash} current={source_hash}"
        )

    if state.get("applied") is not False:
        raise ReviewError(
            "review対象snapshotが applied=False ではない"
        )

    report_path = _resolve_repo_path(
        state.get("current_report_path", ""),
        "current_report_path",
    )
    records_path = _resolve_repo_path(
        state.get("current_records_path", ""),
        "current_records_path",
    )
    diff_path = _resolve_repo_path(
        state.get("current_diff_path", ""),
        "current_diff_path",
    )
    raw_path = _resolve_repo_path(
        state.get("current_raw_path", ""),
        "current_raw_path",
    )

    report = _load_json(report_path, "METI manual report")

    if str(report.get("source_hash") or "") != source_hash:
        raise ReviewError("report.source_hashとstateが一致しない")

    if str(report.get("raw_path") or "") != _relative(raw_path):
        raise ReviewError("report.raw_pathとstate/raw実体が一致しない")

    if str(report.get("records_path") or "") != _relative(records_path):
        raise ReviewError("report.records_pathとstateが一致しない")

    if str(report.get("diff_path") or "") != _relative(diff_path):
        raise ReviewError("report.diff_pathとstateが一致しない")

    state_url = str(state.get("source_url") or "").strip()
    report_url = str(report.get("source_url") or "").strip()
    if not state_url or state_url != report_url:
        raise ReviewError("state/reportのsource_urlが一致しない")

    if "](" in state_url or state_url.startswith("["):
        raise ReviewError("source_urlにMarkdown形式が混入している")

    raw_hash = sha256_file(raw_path)
    if raw_hash != source_hash:
        raise ReviewError(
            "保存原本SHA256がstateと一致しない: "
            f"raw={raw_hash} state={source_hash}"
        )

    _, records = _load_csv(records_path, "records CSV")
    record_count = len(records)

    try:
        state_count = int(state.get("current_record_count"))
        report_count = int(report.get("record_count"))
        expected_count = int(report.get("expected_count"))
    except (TypeError, ValueError) as exc:
        raise ReviewError(
            "state/reportの件数フィールドが数値ではない"
        ) from exc

    if not (
        record_count == state_count
        == report_count
        == expected_count
    ):
        raise ReviewError(
            "records/state/report/expected件数が不一致: "
            f"records={record_count} state={state_count} "
            f"report={report_count} expected={expected_count}"
        )

    if record_count <= 0:
        raise ReviewError("recordsが0件")

    nos: list[int] = []
    keys: list[str] = []

    for i, row in enumerate(records, 1):
        try:
            no = int(row.get("no", ""))
        except ValueError as exc:
            raise ReviewError(
                f"records行{i}のnoが数値ではない"
            ) from exc

        nos.append(no)

        company = str(row.get("company") or "").strip()
        country = str(row.get("country") or "").strip()
        key = str(row.get("match_key") or "").strip()

        if not company:
            raise ReviewError(f"No.{no} companyが空")
        if not country:
            raise ReviewError(f"No.{no} countryが空")
        if not key:
            raise ReviewError(f"No.{no} match_keyが空")
        keys.append(key)

    if nos != list(range(1, record_count + 1)):
        raise ReviewError(
            "recordsのNo.連番が崩れている"
        )

    if len(set(keys)) != record_count:
        raise ReviewError(
            "recordsのmatch_keyが一意ではない"
        )

    _, evidence = _load_csv(
        EVIDENCE_PATH,
        "METI Source Evidence Ledger",
    )
    current = [
        row for row in evidence
        if str(row.get("current") or "") == "1"
    ]

    if len(current) != record_count:
        raise ReviewError(
            "current evidence件数がrecordsと一致しない: "
            f"evidence={len(current)} records={record_count}"
        )

    evidence_keys = [
        str(row.get("match_key") or "").strip()
        for row in current
    ]
    if any(not x for x in evidence_keys):
        raise ReviewError("evidenceに空match_keyがある")
    if len(set(evidence_keys)) != record_count:
        raise ReviewError("current evidenceのmatch_keyが一意ではない")

    if set(evidence_keys) != set(keys):
        raise ReviewError(
            "recordsとcurrent evidenceのmatch_key集合が一致しない"
        )

    for row in current:
        if str(row.get("source_hash") or "") != source_hash:
            raise ReviewError("current evidenceのsource_hashが不一致")
        if str(row.get("source_url") or "") != state_url:
            raise ReviewError("current evidenceのsource_urlが不一致")
        source_record_id = str(
            row.get("source_record_id") or ""
        )
        if not source_record_id.startswith(source_hash + ":"):
            raise ReviewError(
                "current evidenceのsource_record_idが不正"
            )

    _, diff_rows = _load_csv(diff_path, "diff CSV")
    expected_diff = _expected_diff_counts(report)
    actual_diff = _actual_diff_counts(diff_rows)

    if expected_diff != actual_diff:
        raise ReviewError(
            "report.diffとdiff CSVが一致しない: "
            f"report={expected_diff} actual={actual_diff}"
        )

    baseline = bool(report.get("baseline"))
    if baseline and any(actual_diff.values()):
        raise ReviewError(
            "baselineなのにdiffが0ではない"
        )

    if str(report.get("status") or "") != "REVIEW_REQUIRED":
        raise ReviewError(
            "report.statusがREVIEW_REQUIREDではない"
        )
    if report.get("review_required") is not True:
        raise ReviewError(
            "report.review_requiredがtrueではない"
        )
    if str(report.get("auto_import") or "") != "BLOCKED":
        raise ReviewError(
            "report.auto_importがBLOCKEDではない"
        )

    return {
        "state": state,
        "report": report,
        "source_hash": source_hash,
        "source_url": state_url,
        "record_count": record_count,
        "diff_counts": actual_diff,
        "baseline": baseline,
        "report_path": report_path,
        "records_path": records_path,
        "diff_path": diff_path,
        "raw_path": raw_path,
        "evidence_path": EVIDENCE_PATH.resolve(),
    }


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(
            state,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )
    tmp.replace(STATE_PATH)


def _append_review_ledger(row: dict) -> None:
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)

    if REVIEW_LEDGER.exists() and REVIEW_LEDGER.stat().st_size:
        with REVIEW_LEDGER.open(
            encoding="utf-8",
            newline="",
        ) as f:
            actual = next(csv.reader(f), [])
        if actual != REVIEW_COLS:
            raise ReviewError(
                "METI Review Ledgerの列構造が想定と異なる: "
                f"expected={REVIEW_COLS} actual={actual}"
            )

    new = (
        not REVIEW_LEDGER.exists()
        or REVIEW_LEDGER.stat().st_size == 0
    )

    with REVIEW_LEDGER.open(
        "a",
        encoding="utf-8",
        newline="",
    ) as f:
        w = csv.DictWriter(
            f,
            fieldnames=REVIEW_COLS,
            extrasaction="ignore",
            lineterminator="\n",
        )
        if new:
            w.writeheader()
        w.writerow(
            {c: row.get(c, "") for c in REVIEW_COLS}
        )


def _write_review_artifact(row: dict) -> Path:
    REVIEW_ARTIFACT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )
    p = REVIEW_ARTIFACT_DIR / (
        f"{row['review_id']}__review.json"
    )
    p.write_text(
        json.dumps(
            row,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )
    return p


def _decision_row(
    verified: dict,
    *,
    decision: str,
    reviewer: str,
    note: str,
    now: datetime,
) -> dict:
    source_hash = verified["source_hash"]
    stamp = _stamp(now)
    review_id = (
        f"{stamp}__{source_hash[:12]}__"
        f"{decision.lower()}"
    )
    counts = verified["diff_counts"]

    return {
        "review_id": review_id,
        "reviewed_at": _iso(now),
        "source": "経済産業省",
        "document_role": "foreign_user_list_pdf",
        "source_hash": source_hash,
        "source_url": verified["source_url"],
        "decision": decision,
        "reviewer": reviewer,
        "note": note,
        "record_count": verified["record_count"],
        "diff_added": counts["追加"],
        "diff_changed": counts["変更"],
        "diff_removed": counts["削除"],
        "report_path": _relative(
            verified["report_path"]
        ),
        "records_path": _relative(
            verified["records_path"]
        ),
        "diff_path": _relative(
            verified["diff_path"]
        ),
        "raw_path": _relative(
            verified["raw_path"]
        ),
        "evidence_path": _relative(
            verified["evidence_path"]
        ),
    }


def decide(
    *,
    expected_hash: str,
    decision: str,
    reviewer: str,
    note: str,
) -> dict:
    reviewer = str(reviewer or "").strip()
    note = str(note or "").strip()

    if not reviewer:
        raise ReviewError("reviewerは必須")

    if decision not in {
        DECISION_APPROVED,
        DECISION_REJECTED,
    }:
        raise ReviewError(
            f"未知のdecision: {decision}"
        )

    if decision == DECISION_REJECTED and not note:
        raise ReviewError(
            "REJECTEDではnoteが必須"
        )

    verified = verify_snapshot(
        expected_hash=expected_hash,
    )
    state = verified["state"]

    current_status = str(
        state.get("review_status") or ""
    )

    if current_status == decision:
        if decision == DECISION_APPROVED:
            if state.get("approved") is True:
                return {
                    "idempotent": True,
                    "verified": verified,
                    "state": state,
                }
        if decision == DECISION_REJECTED:
            if state.get("approved") is False:
                return {
                    "idempotent": True,
                    "verified": verified,
                    "state": state,
                }

    if current_status != "REVIEW_REQUIRED":
        raise ReviewError(
            "現在stateはレビュー待ちではない: "
            f"review_status={current_status!r}"
        )

    if state.get("approved") is not False:
        raise ReviewError(
            "レビュー前stateのapprovedがFalseではない"
        )

    now = _now()
    row = _decision_row(
        verified,
        decision=decision,
        reviewer=reviewer,
        note=note,
        now=now,
    )

    # Ledgerと個別artifactを先に確定。
    _append_review_ledger(row)
    artifact_path = _write_review_artifact(row)

    state["review_status"] = decision
    state["approved"] = (
        decision == DECISION_APPROVED
    )
    state["applied"] = False
    state["reviewed_at"] = row["reviewed_at"]
    state["reviewed_by"] = reviewer
    state["review_note"] = note
    state["review_id"] = row["review_id"]
    state["review_artifact_path"] = _relative(
        artifact_path
    )
    _save_state(state)

    status = (
        "approved"
        if decision == DECISION_APPROVED
        else "rejected"
    )

    source_audit.write(
        ROOT,
        [
            source_audit.entry(
                "meti_manual",
                "foreign_user_list_pdf",
                status,
                url=verified["source_url"],
                content_hash=verified["source_hash"],
                fetched_file=verified[
                    "raw_path"
                ].name,
                raw_path=_relative(
                    verified["raw_path"]
                ),
                source_updated=str(
                    verified["report"].get(
                        "effective_date",
                        "",
                    )
                    or ""
                ),
                record_count=verified[
                    "record_count"
                ],
                diff_counts=verified[
                    "diff_counts"
                ],
                fetch_failed=False,
                schema_changed=False,
            )
        ],
    )

    if decision == DECISION_APPROVED:
        append_dashboard_row(
            "手動正本レビュー承認",
            "外国ユーザーリスト",
            (
                f"REVIEW_REQUIRED / "
                f"{verified['record_count']}件"
            ),
            (
                f"APPROVED / reviewer={reviewer} / "
                f"SHA256 {verified['source_hash'][:12]}"
            ),
        )
    else:
        append_dashboard_row(
            "手動正本レビュー却下",
            "外国ユーザーリスト",
            (
                f"REVIEW_REQUIRED / "
                f"{verified['record_count']}件"
            ),
            (
                f"REJECTED / reviewer={reviewer} / "
                f"{note}"
            ),
        )

    return {
        "idempotent": False,
        "verified": verified,
        "state": state,
        "review_row": row,
        "artifact_path": artifact_path,
    }


def print_status(
    *,
    expected_hash: str | None = None,
) -> int:
    verified = verify_snapshot(
        expected_hash=expected_hash,
    )
    state = verified["state"]
    counts = verified["diff_counts"]

    print("===== METI REVIEW STATUS =====")
    print(
        "source_hash      :",
        verified["source_hash"],
    )
    print(
        "record_count     :",
        verified["record_count"],
    )
    print(
        "baseline         :",
        verified["baseline"],
    )
    print(
        "diff             :",
        f"追加{counts['追加']} "
        f"変更{counts['変更']} "
        f"削除{counts['削除']}",
    )
    print(
        "review_status    :",
        state.get("review_status", ""),
    )
    print(
        "approved         :",
        state.get("approved", ""),
    )
    print(
        "applied          :",
        state.get("applied", ""),
    )
    print(
        "source_url       :",
        verified["source_url"],
    )
    if state.get("reviewed_at"):
        print(
            "reviewed_at     :",
            state.get("reviewed_at"),
        )
        print(
            "reviewed_by     :",
            state.get("reviewed_by"),
        )
        print(
            "review_note     :",
            state.get("review_note"),
        )
    print("")
    print("SNAPSHOT_INTEGRITY: PASS")
    print("MASTER_APPLIED    : NO")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(
        dest="command",
        required=True,
    )

    status = sub.add_parser("status")
    status.add_argument("--hash", default=None)

    approve = sub.add_parser("approve")
    approve.add_argument("--hash", required=True)
    approve.add_argument("--reviewer", required=True)
    approve.add_argument("--note", default="")

    reject = sub.add_parser("reject")
    reject.add_argument("--hash", required=True)
    reject.add_argument("--reviewer", required=True)
    reject.add_argument("--note", required=True)

    args = ap.parse_args(argv)

    try:
        if args.command == "status":
            return print_status(
                expected_hash=args.hash,
            )

        decision = (
            DECISION_APPROVED
            if args.command == "approve"
            else DECISION_REJECTED
        )

        result = decide(
            expected_hash=args.hash,
            decision=decision,
            reviewer=args.reviewer,
            note=args.note,
        )

        if result.get("idempotent"):
            print(
                f"[idempotent] already {decision}"
            )
            print("MASTER_APPLIED = NO")
            return 0

        verified = result["verified"]
        state = result["state"]
        counts = verified["diff_counts"]

        print("===== METI REVIEW DECISION =====")
        print("decision         :", decision)
        print("reviewer         :", args.reviewer)
        print(
            "source_hash      :",
            verified["source_hash"],
        )
        print(
            "record_count     :",
            verified["record_count"],
        )
        print(
            "diff             :",
            f"追加{counts['追加']} "
            f"変更{counts['変更']} "
            f"削除{counts['削除']}",
        )
        print(
            "review_status    :",
            state["review_status"],
        )
        print(
            "approved         :",
            state["approved"],
        )
        print(
            "applied          :",
            state["applied"],
        )
        print(
            "review_artifact  :",
            _relative(
                result["artifact_path"]
            ),
        )
        print("")
        print("MASTER_APPLIED = NO")
        return 0

    except ReviewError as exc:
        print(
            f"[BLOCKED] {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        print("REVIEW DECISION = NOT RECORDED")
        print("MASTER_APPLIED   = NO")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

