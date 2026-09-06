"""METI 外国ユーザーリストの正式な end-to-end 照合監査。

監査経路:

公式PDF
  -> parsed records
  -> Source Evidence Ledger
  -> apply plan
  -> apply ledger
  -> current master

目的:
- 公式record_noが欠落していない
- Primary/Aliasがplan生成途中で黙って消えていない
- 各source tokenに一意な処理結果がある
- READY/NOOP/HOLD件数がapply ledgerと一致する
- actionable行がcurrent masterに存在し、METI sourceタグを保持する
- 原本SHA256とEvidence provenanceを検証する

通常実行はREAD ONLY。
--write-report 指定時のみ監査結果JSON/CSVを出力する。
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from . import meti_apply_plan as P
from .normalize import SRC_METI


ROOT = Path(__file__).resolve().parent.parent

APPLY_LEDGER = (
    ROOT / "data" / "review" / "meti_apply_ledger.csv"
)
REVIEW_LEDGER = (
    ROOT / "data" / "review" / "meti_foreign_user_list.csv"
)
MASTER_PATH = (
    ROOT / "data" / "master" / "master.csv"
)
REPORT_DIR = (
    ROOT
    / "data"
    / "manual"
    / "meti"
    / "reconciliation_reports"
)

ACTIONABLE_ACTIONS = {
    P.ACTION_READY_ADD,
    P.ACTION_READY_TAG,
    P.ACTION_NOOP,
}

HOLD_ACTIONS = {
    P.ACTION_HOLD_WEAK,
    P.ACTION_HOLD_COLLISION,
    P.ACTION_HOLD_REVIEW,
    P.ACTION_HOLD_INVALID,
}


class ReconciliationError(RuntimeError):
    """METI正式照合に失敗した。"""


def _load_csv(path: Path) -> list[dict]:
    if not path.exists():
        raise ReconciliationError(
            f"CSVが存在しない: {path}"
        )

    with path.open(
        encoding="utf-8",
        newline="",
    ) as f:
        reader = csv.DictReader(f)

        if not reader.fieldnames:
            raise ReconciliationError(
                f"CSV headerがない: {path}"
            )

        return list(reader)


def _sha256(path: Path) -> str:
    if not path.exists():
        raise ReconciliationError(
            f"ファイルが存在しない: {path}"
        )

    h = hashlib.sha256()

    with path.open("rb") as f:
        for chunk in iter(
            lambda: f.read(1024 * 1024),
            b"",
        ):
            h.update(chunk)

    return h.hexdigest()


def _repo_path(
    root: Path,
    value: str,
) -> Path:
    raw = str(value or "").strip()

    if not raw:
        raise ReconciliationError(
            "repository pathが空"
        )

    p = Path(raw)

    if not p.is_absolute():
        p = root / p

    p = p.resolve()

    try:
        p.relative_to(
            root.resolve()
        )
    except ValueError as exc:
        raise ReconciliationError(
            f"repository外のpath: {p}"
        ) from exc

    return p


def _int(
    value,
    label: str,
) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ReconciliationError(
            f"{label}が整数ではない: {value!r}"
        ) from exc


def _sources(row: dict) -> set[str]:
    return {
        x
        for x in str(
            row.get("sources", "")
        ).split(";")
        if x
    }


def _latest_apply(
    root: Path,
) -> dict:
    rows = _load_csv(
        root
        / "data"
        / "review"
        / "meti_apply_ledger.csv"
    )

    if not rows:
        raise ReconciliationError(
            "METI apply ledgerが空"
        )

    row = rows[-1]

    if row.get("status") not in {
        "APPLIED",
        "APPLIED_WITH_HOLDS",
    }:
        raise ReconciliationError(
            "最新METI applyが適用済みではない: "
            f"{row.get('status')!r}"
        )

    return row


def _approved_review(
    root: Path,
    source_hash: str,
) -> dict:
    rows = [
        row
        for row in _load_csv(
            root
            / "data"
            / "review"
            / "meti_foreign_user_list.csv"
        )
        if (
            row.get("source_hash")
            == source_hash
            and row.get("decision")
            == "APPROVED"
        )
    ]

    if len(rows) != 1:
        raise ReconciliationError(
            "source_hashに対応するAPPROVED reviewが"
            "1件ではない: "
            f"{len(rows)}"
        )

    return rows[0]


def _verify_source_and_evidence(
    *,
    root: Path,
    apply: dict,
    review: dict,
) -> dict:
    source_hash = apply["source_hash"]

    evidence_path = _repo_path(
        root,
        review["evidence_path"],
    )
    raw_path = _repo_path(
        root,
        review["raw_path"],
    )
    records_path = _repo_path(
        root,
        review["records_path"],
    )

    if _sha256(raw_path) != source_hash:
        raise ReconciliationError(
            "公式原本PDF SHA256が"
            "apply source_hashと一致しない"
        )

    evidence_rows = _load_csv(
        evidence_path
    )
    parsed_rows = _load_csv(
        records_path
    )

    review_count = _int(
        review.get("record_count"),
        "review record_count",
    )

    if len(evidence_rows) != review_count:
        raise ReconciliationError(
            "Evidence行数とreview件数が不一致: "
            f"{len(evidence_rows)} != "
            f"{review_count}"
        )

    if len(parsed_rows) != review_count:
        raise ReconciliationError(
            "parsed records件数とreview件数が不一致: "
            f"{len(parsed_rows)} != "
            f"{review_count}"
        )

    parsed_by_no: dict[int, dict] = {}

    for row in parsed_rows:
        no = _int(
            row.get("no"),
            "parsed record no",
        )

        if no in parsed_by_no:
            raise ReconciliationError(
                f"parsed record_no重複: {no}"
            )

        parsed_by_no[no] = row

    expected_nos = set(
        range(
            1,
            review_count + 1,
        )
    )

    if set(parsed_by_no) != expected_nos:
        raise ReconciliationError(
            "parsed record_noが完全連番ではない"
        )

    evidence_by_no: dict[int, dict] = {}
    expected_tokens: set[str] = set()
    details: list[dict] = []
    source_documents: set[str] = set()
    source_urls: set[str] = set()

    prefix = source_hash + ":"

    for row in evidence_rows:
        if row.get("source_hash") != source_hash:
            raise ReconciliationError(
                "Evidence source_hash不一致"
            )

        if str(row.get("current")) != "1":
            raise ReconciliationError(
                "Evidence current != 1: "
                f"{row.get('source_record_id')}"
            )

        sid = str(
            row.get("source_record_id")
            or ""
        )

        if not sid.startswith(prefix):
            raise ReconciliationError(
                "source_record_id形式不正: "
                f"{sid!r}"
            )

        no = _int(
            sid[len(prefix):],
            "Evidence record_no",
        )

        if no in evidence_by_no:
            raise ReconciliationError(
                f"Evidence record_no重複: {no}"
            )

        try:
            evidence_json = json.loads(
                row.get("evidence")
                or ""
            )
        except Exception as exc:
            raise ReconciliationError(
                f"Evidence JSON不正: record_no={no}"
            ) from exc

        if _int(
            evidence_json.get("record_no"),
            "Evidence JSON record_no",
        ) != no:
            raise ReconciliationError(
                "Evidence JSON record_no不一致: "
                f"{no}"
            )

        aliases = evidence_json.get(
            "aliases",
            [],
        )

        if not isinstance(
            aliases,
            list,
        ):
            raise ReconciliationError(
                f"aliasesがlistではない: {no}"
            )

        parsed = parsed_by_no.get(no)

        if parsed is None:
            raise ReconciliationError(
                "Evidenceに対応するparsed recordなし: "
                f"{no}"
            )

        if (
            str(parsed.get("company") or "")
            != str(
                row.get("canonical_record")
                or ""
            )
        ):
            raise ReconciliationError(
                "parsed companyとEvidence canonical_record"
                f"不一致: record_no={no}"
            )

        if (
            str(parsed.get("match_key") or "")
            != str(
                row.get("match_key")
                or ""
            )
        ):
            raise ReconciliationError(
                "parsed/Evidence match_key不一致: "
                f"record_no={no}"
            )

        primary_token = (
            f"{source_hash}:{no}:PRIMARY"
        )

        expected_tokens.add(
            primary_token
        )

        for idx in range(
            1,
            len(aliases) + 1,
        ):
            expected_tokens.add(
                f"{source_hash}:{no}:ALIAS:{idx}"
            )

        source_documents.add(
            str(
                row.get(
                    "source_document"
                )
                or ""
            )
        )
        source_urls.add(
            str(
                row.get("source_url")
                or ""
            )
        )

        evidence_by_no[no] = row

        details.append(
            {
                "record_no": no,
                "canonical_record": (
                    row.get(
                        "canonical_record"
                    )
                    or ""
                ),
                "match_key": (
                    row.get("match_key")
                    or ""
                ),
                "alias_count": (
                    len(aliases)
                ),
                "expected_token_count": (
                    1 + len(aliases)
                ),
            }
        )

    if set(evidence_by_no) != expected_nos:
        raise ReconciliationError(
            "Evidence record_noが完全連番ではない"
        )

    if len(source_documents) != 1:
        raise ReconciliationError(
            "Evidence source_documentが"
            "1種類ではない"
        )

    if len(source_urls) != 1:
        raise ReconciliationError(
            "Evidence source_urlが"
            "1種類ではない"
        )

    only_document = next(
        iter(source_documents)
    )
    only_url = next(
        iter(source_urls)
    )

    if (
        _repo_path(
            root,
            only_document,
        )
        != raw_path
    ):
        raise ReconciliationError(
            "Evidence source_documentと"
            "review raw_pathが一致しない"
        )

    if only_url != apply["source_url"]:
        raise ReconciliationError(
            "Evidence source_urlと"
            "apply source_urlが一致しない"
        )

    return {
        "record_count": review_count,
        "evidence_rows": evidence_rows,
        "evidence_by_no": evidence_by_no,
        "parsed_rows": parsed_rows,
        "expected_tokens": expected_tokens,
        "details": details,
        "evidence_path": evidence_path,
        "records_path": records_path,
        "raw_path": raw_path,
        "raw_sha256": source_hash,
    }


def _verify_plan(
    *,
    root: Path,
    apply: dict,
    source: dict,
) -> dict:
    source_hash = apply["source_hash"]

    plan_path = _repo_path(
        root,
        apply["plan_path"],
    )

    if (
        _sha256(plan_path)
        != apply["plan_sha256"]
    ):
        raise ReconciliationError(
            "plan SHA256がapply ledgerと不一致"
        )

    rows = _load_csv(
        plan_path
    )

    token_counter: Counter = Counter()
    token_to_rows = defaultdict(list)

    for line_no, row in enumerate(
        rows,
        start=2,
    ):
        raw = str(
            row.get(
                "source_record_ids",
                ""
            )
            or ""
        )

        tokens = [
            x.strip()
            for x in raw.split(";")
            if x.strip()
        ]

        if not tokens:
            raise ReconciliationError(
                "plan source_record_idsが空: "
                f"line={line_no}"
            )

        for token in tokens:
            token_counter[token] += 1
            token_to_rows[token].append(
                row
            )

    expected_tokens = source[
        "expected_tokens"
    ]
    actual_tokens = set(
        token_counter
    )

    missing = (
        expected_tokens
        - actual_tokens
    )
    unexpected = (
        actual_tokens
        - expected_tokens
    )
    duplicated = {
        token
        for token, count
        in token_counter.items()
        if count != 1
    }

    if missing:
        raise ReconciliationError(
            "Planへ到達していないsource token: "
            f"{sorted(missing)[:20]}"
        )

    if unexpected:
        raise ReconciliationError(
            "Evidenceに存在しないsource token: "
            f"{sorted(unexpected)[:20]}"
        )

    if duplicated:
        raise ReconciliationError(
            "Plan source token重複: "
            f"{sorted(duplicated)[:20]}"
        )

    primary_actions = Counter()
    primary_holds: list[dict] = []

    for no in range(
        1,
        source["record_count"] + 1,
    ):
        token = (
            f"{source_hash}:{no}:PRIMARY"
        )

        matches = token_to_rows.get(
            token,
            [],
        )

        if len(matches) != 1:
            raise ReconciliationError(
                "PRIMARY outcomeが1件ではない: "
                f"record_no={no} "
                f"count={len(matches)}"
            )

        row = matches[0]
        action = row["action"]

        primary_actions[action] += 1

        if action in HOLD_ACTIONS:
            primary_holds.append(
                {
                    "record_no": no,
                    "action": action,
                    "name": row["name"],
                }
            )

        detail = source[
            "details"
        ][no - 1]

        detail.update(
            {
                "primary_action": action,
                "primary_plan_name": (
                    row.get("name") or ""
                ),
                "primary_plan_match_key": (
                    row.get("match_key")
                    or ""
                ),
            }
        )

    actions = Counter(
        row["action"]
        for row in rows
    )

    expected_actions = {
        P.ACTION_READY_ADD:
            _int(
                apply["added"],
                "apply added",
            ),
        P.ACTION_READY_TAG:
            _int(
                apply["changed"],
                "apply changed",
            ),
        P.ACTION_NOOP:
            _int(
                apply["noop"],
                "apply noop",
            ),
        P.ACTION_HOLD_WEAK:
            _int(
                apply["held_weak_alias"],
                "apply held_weak_alias",
            ),
        P.ACTION_HOLD_COLLISION:
            _int(
                apply[
                    "held_multi_party"
                ],
                "apply held_multi_party",
            ),
        P.ACTION_HOLD_REVIEW:
            _int(
                apply["held_review"],
                "apply held_review",
            ),
        P.ACTION_HOLD_INVALID:
            _int(
                apply["held_invalid"],
                "apply held_invalid",
            ),
    }

    for action, expected in (
        expected_actions.items()
    ):
        actual = actions[action]

        if actual != expected:
            raise ReconciliationError(
                "plan action件数とapply ledger不一致: "
                f"{action}: "
                f"{actual} != {expected}"
            )

    return {
        "plan_path": plan_path,
        "rows": rows,
        "token_count": len(
            actual_tokens
        ),
        "token_to_rows": (
            token_to_rows
        ),
        "action_counts": dict(
            actions
        ),
        "primary_action_counts": dict(
            primary_actions
        ),
        "primary_holds": (
            primary_holds
        ),
    }


def _verify_master(
    *,
    root: Path,
    apply: dict,
    plan: dict,
    source: dict,
) -> dict:
    rows = _load_csv(
        root
        / "data"
        / "master"
        / "master.csv"
    )

    master: dict[str, dict] = {}

    for row in rows:
        key = str(
            row.get("match_key")
            or ""
        )

        if not key:
            raise ReconciliationError(
                "masterに空match_key"
            )

        if key in master:
            raise ReconciliationError(
                f"master match_key重複: {key}"
            )

        master[key] = row

    actionable_rows = [
        row
        for row in plan["rows"]
        if row["action"]
        in ACTIONABLE_ACTIONS
    ]

    missing_master = []
    missing_source = []

    for row in actionable_rows:
        key = row["match_key"]
        current = master.get(key)

        if current is None:
            missing_master.append(
                key
            )
            continue

        if SRC_METI not in _sources(
            current
        ):
            missing_source.append(
                key
            )

    if missing_master:
        raise ReconciliationError(
            "actionable planがcurrent masterに"
            "存在しない: "
            f"{missing_master[:20]}"
        )

    if missing_source:
        raise ReconciliationError(
            "actionable planにMETI sourceタグが"
            "存在しない: "
            f"{missing_source[:20]}"
        )

    for detail in source["details"]:
        no = detail["record_no"]
        token = (
            f"{apply['source_hash']}:"
            f"{no}:PRIMARY"
        )

        plan_row = plan[
            "token_to_rows"
        ][token][0]

        key = plan_row[
            "match_key"
        ]

        current = master.get(key)

        detail[
            "primary_in_master"
        ] = current is not None

        detail[
            "primary_meti_tagged"
        ] = bool(
            current
            and SRC_METI
            in _sources(current)
        )

    current_sha = _sha256(
        root
        / "data"
        / "master"
        / "master.csv"
    )

    return {
        "current_count": len(
            rows
        ),
        "current_sha256": (
            current_sha
        ),
        "apply_count": _int(
            apply[
                "master_after_count"
            ],
            "master_after_count",
        ),
        "apply_sha256": apply[
            "master_after_sha256"
        ],
        "unchanged_since_apply": (
            current_sha
            == apply[
                "master_after_sha256"
            ]
        ),
        "actionable_count": len(
            actionable_rows
        ),
    }


def audit(
    *,
    root: Path = ROOT,
) -> dict:
    root = root.resolve()

    apply = _latest_apply(
        root
    )
    source_hash = apply[
        "source_hash"
    ]

    review = _approved_review(
        root,
        source_hash,
    )

    source = (
        _verify_source_and_evidence(
            root=root,
            apply=apply,
            review=review,
        )
    )

    plan = _verify_plan(
        root=root,
        apply=apply,
        source=source,
    )

    master = _verify_master(
        root=root,
        apply=apply,
        plan=plan,
        source=source,
    )

    return {
        "status": "PASS",
        "application_id": (
            apply["application_id"]
        ),
        "source_hash": source_hash,
        "source_url": (
            apply["source_url"]
        ),
        "apply_status": (
            apply["status"]
        ),
        "review_id": (
            review["review_id"]
        ),
        "record_count": (
            source["record_count"]
        ),
        "parsed_record_count": len(
            source["parsed_rows"]
        ),
        "evidence_record_count": len(
            source["evidence_rows"]
        ),
        "source_token_count": (
            plan["token_count"]
        ),
        "primary_outcome_count": (
            source["record_count"]
        ),
        "primary_action_counts": (
            plan[
                "primary_action_counts"
            ]
        ),
        "primary_hold_count": len(
            plan["primary_holds"]
        ),
        "action_counts": (
            plan["action_counts"]
        ),
        "actionable_master_count": (
            master[
                "actionable_count"
            ]
        ),
        "master_current_count": (
            master["current_count"]
        ),
        "master_apply_count": (
            master["apply_count"]
        ),
        "master_current_sha256": (
            master["current_sha256"]
        ),
        "master_apply_sha256": (
            master["apply_sha256"]
        ),
        "master_unchanged_since_apply": (
            master[
                "unchanged_since_apply"
            ]
        ),
        "raw_pdf_sha256": (
            source["raw_sha256"]
        ),
        "raw_path": str(
            source["raw_path"]
            .relative_to(root)
        ),
        "evidence_path": str(
            source["evidence_path"]
            .relative_to(root)
        ),
        "records_path": str(
            source["records_path"]
            .relative_to(root)
        ),
        "plan_path": str(
            plan["plan_path"]
            .relative_to(root)
        ),
        "details": (
            source["details"]
        ),
    }


def _stamp() -> str:
    return datetime.now(
        timezone.utc
    ).strftime(
        "%Y%m%dT%H%M%SZ"
    )


def write_report(
    result: dict,
    *,
    root: Path = ROOT,
) -> tuple[Path, Path]:
    root = root.resolve()

    report_dir = (
        root
        / "data"
        / "manual"
        / "meti"
        / "reconciliation_reports"
    )

    report_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    prefix = (
        f"{_stamp()}__"
        f"{result['source_hash'][:12]}"
        "__reconciliation"
    )

    json_path = (
        report_dir
        / f"{prefix}.json"
    )
    csv_path = (
        report_dir
        / f"{prefix}.csv"
    )

    json_obj = {
        k: v
        for k, v in result.items()
        if k != "details"
    }

    json_obj[
        "generated_at"
    ] = datetime.now(
        timezone.utc
    ).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    json_path.write_text(
        json.dumps(
            json_obj,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    fields = [
        "record_no",
        "canonical_record",
        "match_key",
        "alias_count",
        "expected_token_count",
        "primary_action",
        "primary_plan_name",
        "primary_plan_match_key",
        "primary_in_master",
        "primary_meti_tagged",
    ]

    with csv_path.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as f:
        writer = csv.DictWriter(
            f,
            fieldnames=fields,
            lineterminator="\n",
        )
        writer.writeheader()

        for row in result[
            "details"
        ]:
            writer.writerow(
                {
                    key: row.get(
                        key,
                        "",
                    )
                    for key in fields
                }
            )

    return (
        json_path,
        csv_path,
    )


def _print_summary(
    result: dict,
) -> None:
    print(
        "===== METI FORMAL "
        "RECONCILIATION ====="
    )
    print(
        "application :",
        result["application_id"],
    )
    print(
        "source hash :",
        result["source_hash"],
    )
    print(
        "records     :",
        result["record_count"],
    )
    print(
        "tokens      :",
        result[
            "source_token_count"
        ],
    )
    print(
        "primary     :",
        result[
            "primary_outcome_count"
        ],
    )
    print(
        "primary hold:",
        result[
            "primary_hold_count"
        ],
    )
    print(
        "actionable  :",
        result[
            "actionable_master_count"
        ],
    )
    print(
        "master count:",
        result[
            "master_current_count"
        ],
    )
    print(
        "master SHA unchanged:",
        result[
            "master_unchanged_since_apply"
        ],
    )
    print()
    print(
        "action counts:"
    )

    for key, value in sorted(
        result[
            "action_counts"
        ].items()
    ):
        print(
            " ",
            key,
            "=",
            value,
        )

    print()
    print(
        "METI_FORMAL_RECONCILIATION: PASS"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "METI外国ユーザーリスト"
            "正式照合監査"
        )
    )

    parser.add_argument(
        "--write-report",
        action="store_true",
        help=(
            "監査結果JSON/CSVを"
            "repositoryへ出力する"
        ),
    )

    args = parser.parse_args()

    result = audit()

    _print_summary(
        result
    )

    if args.write_report:
        json_path, csv_path = (
            write_report(
                result
            )
        )

        print()
        print(
            "JSON report:",
            json_path.relative_to(
                ROOT
            ),
        )
        print(
            "CSV report :",
            csv_path.relative_to(
                ROOT
            ),
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
