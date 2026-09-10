"""財務省の原本情報改訂から、再スクリーニング対象を対象者単位で生成する。

入力:
  data/source_diff/mof_latest.csv

出力:
  data/dashboard/re_review.csv

方針:
  * 管理情報、表記差、列再構成、集約原文差だけでは再審査に出さない。
  * 主名称・Strong Alias・旧称・旅券/IDの変更は高優先度。
  * DOB/出生地/国籍/住所の実質変更は中優先度。
  * Weak Alias単独変更では再スクリーニングを要求しない。
  * Weak Alias -> Strong Alias昇格は明示的に再審査理由へ出す。
  * 1対象者1更新世代=1案件に集約し、安定した再審査IDで重複登録を防ぐ。
  * re_review.csv は履歴型。新規案件を先頭へ追加し、既存案件は保持する。
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

SOURCE = "財務省"
MOF_SOURCE_URL = (
    "https://www.mof.go.jp/policy/international_policy/"
    "gaitame_kawase/gaitame/economic_sanctions/list.html"
)
MAX_RE_REVIEW = 2000

SOURCE_DIFF_COLS = [
    "検知日時", "前回原本", "今回原本", "種別", "番号", "国連参照番号",
    "受取人名", "項目", "変更前", "変更後", "影響区分",
]

RE_REVIEW_COLS = [
    "再審査ID", "検知日時", "出所", "対象者", "番号", "国連参照番号",
    "優先度", "再審査理由", "変更項目", "変更概要", "原本", "一次ソースURL",
]

HIGH_TRIGGER_FIELDS = {
    "氏名（日本語）", "氏名（英語）",
    "別名・別称（日本語）", "別名・別称（英語）",
    "旧称（日本語）", "旧称（英語）",
    "旅券番号", "身分証番号",
}

MEDIUM_TRIGGER_FIELDS = {
    "生年月日",
    "出生地（日本語）", "出生地（英語）",
    "国籍（日本語）", "国籍（英語）",
    "住所・所在地（国）（日本語）", "住所・所在地（都市その他の情報）（日本語）",
    "住所・所在地（国）（英語）", "住所・所在地（都市その他の情報）（英語）",
}

WEAK_ALIAS_FIELDS = {
    "確定に十分でない別名（日本語）",
    "確定に十分でない別名（英語）",
}

STRONG_ALIAS_FIELDS = {
    "別名・別称（日本語）",
    "別名・別称（英語）",
}

PRIMARY_NAME_FIELDS = {"氏名（日本語）", "氏名（英語）"}
OLD_NAME_FIELDS = {"旧称（日本語）", "旧称（英語）"}
ADDRESS_FIELDS = {
    "住所・所在地（国）（日本語）", "住所・所在地（都市その他の情報）（日本語）",
    "住所・所在地（国）（英語）", "住所・所在地（都市その他の情報）（英語）",
}


class ReReviewError(RuntimeError):
    pass


@dataclass(frozen=True)
class Candidate:
    case_id: str
    detected_at: str
    source: str
    name: str
    record_id: str
    un_reference: str
    priority: str
    reasons: str
    fields: str
    detail: str
    source_document: str
    source_url: str = MOF_SOURCE_URL

    def as_row(self) -> list[str]:
        return [
            self.case_id, self.detected_at, self.source, self.name,
            self.record_id, self.un_reference, self.priority, self.reasons,
            self.fields, self.detail, self.source_document, self.source_url,
        ]


def _clean(value) -> str:
    if value is None:
        return ""
    return str(value).replace("\r\n", "\n").replace("\r", "\n").strip()


def _norm(value: str) -> str:
    return unicodedata.normalize("NFKC", _clean(value)).casefold().strip()


def _split_aliases(value: str) -> set[str]:
    s = unicodedata.normalize("NFKC", _clean(value))
    if not s or s in {"-", "—", "―"}:
        return set()
    return {_norm(x) for x in re.split(r"[;；]", s) if _norm(x)}


def _read_csv(path: Path, expected: list[str]) -> list[dict[str, str]]:
    if not path.exists():
        return []
    try:
        with path.open(encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            actual = [_clean(x) for x in (reader.fieldnames or [])]
            if actual != expected:
                raise ReReviewError(
                    f"CSV列構造が想定外: {path} expected={expected} actual={actual}"
                )
            return [
                {k: _clean(v) for k, v in row.items()}
                for row in reader
                if any(_clean(v) for v in row.values())
            ]
    except csv.Error as exc:
        raise ReReviewError(f"CSV読込失敗: {path}: {exc}") from exc


def _case_id(after_raw: str, record_id: str) -> str:
    m = re.search(r"shisantouketsu(\d{8})", after_raw)
    date = m.group(1) if m else "UNKNOWN"
    digest = hashlib.sha256(f"{after_raw}|{record_id}".encode("utf-8")).hexdigest()[:8]
    return f"MOF-{date}-{record_id}-{digest}"


def _promotion_detected(rows: list[dict[str, str]], strong_field: str, weak_field: str) -> bool:
    strong = next((r for r in rows if r["項目"] == strong_field), None)
    weak = next((r for r in rows if r["項目"] == weak_field), None)
    if not strong or not weak:
        return False
    strong_added = _split_aliases(strong["変更後"]) - _split_aliases(strong["変更前"])
    weak_removed = _split_aliases(weak["変更前"]) - _split_aliases(weak["変更後"])
    return bool(strong_added & weak_removed)


def _reason_labels(rows: list[dict[str, str]], trigger_fields: set[str]) -> list[str]:
    reasons: list[str] = []

    if (
        _promotion_detected(rows, "別名・別称（日本語）", "確定に十分でない別名（日本語）")
        or _promotion_detected(rows, "別名・別称（英語）", "確定に十分でない別名（英語）")
    ):
        reasons.append("Weak Alias→Strong Alias昇格")
    elif trigger_fields & STRONG_ALIAS_FIELDS:
        reasons.append("強い別名変更")

    if trigger_fields & PRIMARY_NAME_FIELDS:
        reasons.append("主名称変更")
    if trigger_fields & OLD_NAME_FIELDS:
        reasons.append("旧称変更")
    if "旅券番号" in trigger_fields:
        reasons.append("旅券番号変更")
    if "身分証番号" in trigger_fields:
        reasons.append("身分証番号変更")
    if "生年月日" in trigger_fields:
        reasons.append("生年月日変更")
    if trigger_fields & {"出生地（日本語）", "出生地（英語）"}:
        reasons.append("出生地変更")
    if trigger_fields & {"国籍（日本語）", "国籍（英語）"}:
        reasons.append("国籍変更")
    if trigger_fields & ADDRESS_FIELDS:
        reasons.append("住所変更")

    return reasons or ["スクリーニング識別情報変更"]


def build_candidates(source_rows: list[dict[str, str]]) -> list[Candidate]:
    groups: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)

    for row in source_rows:
        if row.get("種別") != "情報改訂":
            continue
        after_raw = row.get("今回原本", "")
        record_id = row.get("番号", "")
        if not after_raw or not record_id:
            raise ReReviewError("情報改訂行に今回原本または番号がない")
        groups[(after_raw, record_id)].append(row)

    out: list[Candidate] = []
    for (after_raw, record_id), rows in sorted(groups.items()):
        screening_rows = [r for r in rows if r.get("影響区分") == "識別・スクリーニング"]
        trigger_rows = [
            r for r in screening_rows
            if r.get("項目") in HIGH_TRIGGER_FIELDS | MEDIUM_TRIGGER_FIELDS
        ]
        if not trigger_rows:
            # 国連参照番号やWeak Alias単独変更などでは、既存顧客の再スクリーニングを要求しない。
            continue

        trigger_fields = {r["項目"] for r in trigger_rows}
        high = bool(trigger_fields & HIGH_TRIGGER_FIELDS)
        priority = "高" if high else "中"

        # Weak Aliasは単独ではトリガーにしないが、Strong昇格の証跡として同じ案件に含める。
        context_rows = list(trigger_rows)
        if trigger_fields & STRONG_ALIAS_FIELDS:
            context_rows.extend(r for r in screening_rows if r.get("項目") in WEAK_ALIAS_FIELDS)

        # 項目順を原本差分の出現順で維持しつつ重複除外。
        unique_rows: list[dict[str, str]] = []
        seen_fields: set[str] = set()
        for r in context_rows:
            field = r["項目"]
            if field in seen_fields:
                continue
            seen_fields.add(field)
            unique_rows.append(r)

        first = rows[0]
        reasons = _reason_labels(rows, trigger_fields)
        detail = "\n".join(
            f"{r['項目']}: {r['変更前'] or '—'} → {r['変更後'] or '—'}"
            for r in unique_rows
        )
        out.append(Candidate(
            case_id=_case_id(after_raw, record_id),
            detected_at=first.get("検知日時", ""),
            source=SOURCE,
            name=first.get("受取人名", "") or f"番号 {record_id}",
            record_id=record_id,
            un_reference=first.get("国連参照番号", ""),
            priority=priority,
            reasons=" / ".join(reasons),
            fields=" / ".join(r["項目"] for r in unique_rows),
            detail=detail,
            source_document=after_raw,
        ))

    return out


def write_queue(root: Path, candidates: list[Candidate]) -> tuple[Path, int]:
    p = root / "data" / "dashboard" / "re_review.csv"
    p.parent.mkdir(parents=True, exist_ok=True)

    old_rows = _read_csv(p, RE_REVIEW_COLS) if p.exists() else []
    existing_ids = {r["再審査ID"] for r in old_rows}
    new = [c.as_row() for c in candidates if c.case_id not in existing_ids]

    old = [[r[col] for col in RE_REVIEW_COLS] for r in old_rows]
    combined = (new + old)[:MAX_RE_REVIEW]

    with p.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(RE_REVIEW_COLS)
        w.writerows(combined)
    return p, len(new)


def run(root: Path, *, dry_run: bool = False, details: bool = False) -> dict:
    root = root.resolve()
    source = root / "data" / "source_diff" / "mof_latest.csv"
    if not source.exists():
        print("財務省 再審査: source_diffなし（スキップ）")
        return {"candidates": 0, "new_cases": 0}

    rows = _read_csv(source, SOURCE_DIFF_COLS)
    candidates = build_candidates(rows)

    if details:
        for c in candidates:
            print(
                f"  [再審査] {c.case_id} {c.un_reference} {c.name} "
                f"優先度={c.priority} 理由={c.reasons}"
            )
            for line in c.detail.splitlines():
                print(f"    - {line}")

    if dry_run:
        print(f"財務省 再審査: 候補={len(candidates)}（dry-run）")
        return {"candidates": len(candidates), "new_cases": len(candidates)}

    _, new_cases = write_queue(root, candidates)
    print(f"財務省 再審査: 候補={len(candidates)} 新規={new_cases}")
    return {"candidates": len(candidates), "new_cases": new_cases}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        default=str(Path(__file__).resolve().parent.parent),
        help="sanctions-watch repository root",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--details", action="store_true")
    args = parser.parse_args()
    try:
        run(Path(args.root), dry_run=args.dry_run, details=args.details)
    except ReReviewError as exc:
        print(f"[FAILED] 財務省 再審査: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
