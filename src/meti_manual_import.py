"""経産省「外国ユーザーリスト」PDFの手動正本取込。

重要:
- METIのHTML/PDFは自動取得がWAFで拒否されるため、ブラウザで手動取得した
  公式PDFをこのモジュールへ渡す。
- 解析成功しても master へ自動反映しない。
- 解析不能・画像PDF・件数不一致・表構造変更は REVIEW REQUIRED / BLOCKED。
- 一次資料、SHA256、抽出レコード、差分、Source Evidence Ledger、監査証跡を保存する。

使用例:
  python3 -m src.meti_manual_import ~/Downloads/20250929_3.pdf \
    --source-url https://www.meti.go.jp/policy/anpo/20250929_3.pdf \
    --publication-date 2025-09-29 \
    --effective-date 2025-10-09 \
    --expected-count 835
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import pdfplumber

from . import source_audit
from .dashboard import CHANGE_COLS
from .normalize import canonical_display_name, match_key

ROOT = Path(__file__).resolve().parent.parent

RAW_DIR = ROOT / "data" / "raw" / "meti_manual"
QUARANTINE_DIR = ROOT / "data" / "quarantine" / "meti_manual"
REPORT_DIR = ROOT / "data" / "manual" / "meti" / "reports"
TEXT_DIR = ROOT / "data" / "manual" / "meti" / "text"
RECORD_DIR = ROOT / "data" / "manual" / "meti" / "records"
DIFF_DIR = ROOT / "data" / "manual" / "meti" / "diffs"
STATE_PATH = ROOT / "data" / "manual" / "meti" / "state.json"
EVIDENCE_PATH = ROOT / "data" / "evidence" / "meti_foreign_user_list.csv"
CHANGES_PATH = ROOT / "data" / "dashboard" / "changes.csv"

MIN_PDF_BYTES = 10_000
MIN_TEXT_CHARS = 2_000
MIN_RECORDS = 100
MAX_PAGES = 500

EVIDENCE_COLS = [
    "canonical_record",
    "match_key",
    "source",
    "source_record_id",
    "source_url",
    "source_document",
    "publication_date",
    "effective_date",
    "first_seen",
    "last_seen",
    "current",
    "source_hash",
    "evidence",
]

RECORD_COLS = [
    "no",
    "country",
    "company",
    "aliases",
    "wmd",
    "conventional_weapons",
    "match_key",
]

DIFF_COLS = [
    "action",
    "match_key",
    "old_no",
    "new_no",
    "old_company",
    "new_company",
    "old_country",
    "new_country",
    "old_aliases",
    "new_aliases",
    "old_wmd",
    "new_wmd",
    "old_conventional_weapons",
    "new_conventional_weapons",
]


class ManualImportError(RuntimeError):
    """手動正本取込を停止すべき異常。"""


class PdfValidationError(ManualImportError):
    """PDFそのものの検証に失敗。"""


class PdfStructureError(ManualImportError):
    """PDFは読めるが表構造・抽出構造が想定外。"""


@dataclass(frozen=True)
class Record:
    no: int
    country: str
    company: str
    aliases: tuple[str, ...]
    wmd: str
    conventional_weapons: str

    @property
    def key(self) -> str:
        return match_key(self.company)

    def comparable(self) -> tuple:
        return (
            _norm_compare(self.country),
            tuple(sorted(_norm_compare(x) for x in self.aliases if x.strip())),
            _norm_compare(self.wmd),
            _norm_compare(self.conventional_weapons),
        )


@dataclass
class DiffResult:
    added: list[Record]
    removed: list[Record]
    changed: list[tuple[Record, Record]]

    @property
    def counts(self) -> dict[str, int]:
        return {
            "追加": len(self.added),
            "削除": len(self.removed),
            "変更": len(self.changed),
        }


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(dt: datetime | None = None) -> str:
    dt = dt or _now()
    return dt.strftime("%Y%m%dT%H%M%SZ")


def _iso(dt: datetime | None = None) -> str:
    dt = dt or _now()
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def validate_pdf_file(path: Path) -> tuple[int, str]:
    if not path.exists():
        raise PdfValidationError(f"ファイルが存在しない: {path}")
    if not path.is_file():
        raise PdfValidationError(f"通常ファイルではない: {path}")

    size = path.stat().st_size
    digest = sha256_file(path)

    if size < MIN_PDF_BYTES:
        raise PdfValidationError(
            f"PDFとして小さすぎる: size={size} bytes"
        )

    with path.open("rb") as f:
        head = f.read(1024)

    if b"%PDF-" not in head[:32]:
        preview = head[:80].decode("utf-8", "replace").replace("\n", " ")
        raise PdfValidationError(
            "PDF magic header(%PDF-)がない。403/WAF HTML等の可能性: "
            f"head={preview!r}"
        )

    return size, digest


def _clean_cell(value) -> str:
    if value is None:
        return ""
    s = str(value).replace("\x00", "")
    s = s.replace("\u3000", " ")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\s*\n\s*", "\n", s)
    return s.strip()


def _flat(value) -> str:
    return re.sub(r"\s+", " ", _clean_cell(value)).strip()


def _norm_compare(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def _split_aliases(value: str) -> tuple[str, ...]:
    s = _clean_cell(value)
    if not s:
        return ()

    result: list[str] = []
    for line in s.splitlines():
        line = re.sub(r"^[・•●▪◦\-]\s*", "", line).strip()
        if not line:
            continue
        if line in {"別名", "Also Known As"}:
            continue
        if line not in result:
            result.append(line)
    return tuple(result)


def _is_header(row: list[str]) -> bool:
    joined = " ".join(_flat(x) for x in row).lower()
    has_company = (
        "company or organization" in joined
        or "企業名" in joined
        or "組織名" in joined
    )
    has_alias = "also known as" in joined or "別名" in joined
    return has_company and has_alias


def _header_map(row: list[str]) -> dict[str, int]:
    result: dict[str, int] = {}
    for i, cell in enumerate(row):
        t = _flat(cell).lower()
        if re.search(r"\bno\.?\b", t) or t == "no":
            result.setdefault("no", i)
        if "country or region" in t or "国名" in t or "地域名" in t:
            result.setdefault("country", i)
        if (
            "company or organization" in t
            or "企業名" in t
            or "組織名" in t
        ):
            result.setdefault("company", i)
        if "also known as" in t or "別名" in t:
            result.setdefault("aliases", i)
        if "type of wmd" in t or "懸念区分" in t:
            result.setdefault("wmd", i)
        if "conventional weapons" in t or "通常兵器" in t:
            result.setdefault("conventional", i)

    required = {"no", "country", "company", "aliases", "wmd"}
    if not required.issubset(result):
        raise PdfStructureError(
            f"外国ユーザーリスト表ヘッダーを判定できない: {result}"
        )
    return result


def _cell(row: list[str], idx: int | None) -> str:
    if idx is None or idx < 0 or idx >= len(row):
        return ""
    return _clean_cell(row[idx])


def records_from_tables(tables: Iterable[list[list]]) -> list[Record]:
    records: list[Record] = []
    current: dict | None = None
    active_map: dict[str, int] | None = None

    def flush() -> None:
        nonlocal current
        if current is None:
            return
        company = canonical_display_name(_flat(current["company"]))
        country = _flat(current["country"])
        if not company:
            raise PdfStructureError(
                f"No.{current['no']} の企業名・組織名が空"
            )
        if not country:
            raise PdfStructureError(
                f"No.{current['no']} の国名・地域名が空"
            )
        records.append(
            Record(
                no=int(current["no"]),
                country=country,
                company=company,
                aliases=_split_aliases(current["aliases"]),
                wmd=_flat(current["wmd"]),
                conventional_weapons=_flat(
                    current["conventional_weapons"]
                ),
            )
        )
        current = None

    for table in tables:
        if not table:
            continue

        for raw_row in table:
            row = [_clean_cell(x) for x in (raw_row or [])]
            if not any(row):
                continue

            if _is_header(row):
                flush()
                active_map = _header_map(row)
                continue

            if active_map is None:
                continue

            no_text = _flat(_cell(row, active_map.get("no")))
            m = re.fullmatch(r"(\d{1,4})", no_text)

            if m:
                flush()
                current = {
                    "no": int(m.group(1)),
                    "country": _cell(row, active_map.get("country")),
                    "company": _cell(row, active_map.get("company")),
                    "aliases": _cell(row, active_map.get("aliases")),
                    "wmd": _cell(row, active_map.get("wmd")),
                    "conventional_weapons": _cell(
                        row, active_map.get("conventional")
                    ),
                }
                continue

            # ページ/セル跨ぎの継続行。No.が空なら直前レコードへ連結。
            if current is not None and not no_text:
                for name, idx_name in (
                    ("country", "country"),
                    ("company", "company"),
                    ("aliases", "aliases"),
                    ("wmd", "wmd"),
                    ("conventional_weapons", "conventional"),
                ):
                    extra = _cell(row, active_map.get(idx_name))
                    if extra:
                        current[name] = (
                            f"{current[name]}\n{extra}".strip()
                        )

    flush()

    if not records:
        raise PdfStructureError("表からレコードを1件も抽出できない")

    by_no: dict[int, Record] = {}
    for rec in records:
        if rec.no in by_no:
            # 同一ページを別抽出戦略で重複投入した場合ではなく、
            # 1パス内の重複は構造異常として止める。
            raise PdfStructureError(f"No.{rec.no} が重複している")
        by_no[rec.no] = rec

    ordered = [by_no[n] for n in sorted(by_no)]
    expected = list(range(1, len(ordered) + 1))
    actual = [r.no for r in ordered]

    if actual != expected:
        missing = sorted(set(expected) - set(actual))[:20]
        raise PdfStructureError(
            "No.の連番が崩れている。PDF表構造変更または抽出欠落の可能性: "
            f"count={len(actual)} max={max(actual)} missing={missing}"
        )

    if len(ordered) < MIN_RECORDS:
        raise PdfStructureError(
            f"抽出件数が異常に少ない: {len(ordered)}件"
        )

    keys = [r.key for r in ordered]
    if any(not k for k in keys):
        raise PdfStructureError("空のmatch_keyを検出")

    # 同一名称が別Noで存在する可能性はあるため重複名称自体では止めない。
    return ordered


def extract_pdf(path: Path) -> tuple[list[Record], str, int]:
    texts: list[str] = []
    all_tables: list[list[list]] = []

    try:
        with pdfplumber.open(path) as pdf:
            pages = len(pdf.pages)
            if pages <= 0:
                raise PdfValidationError("PDFページ数が0")
            if pages > MAX_PAGES:
                raise PdfValidationError(
                    f"PDFページ数が異常: {pages}"
                )

            for page in pdf.pages:
                text = page.extract_text() or ""
                texts.append(text)
                tables = page.extract_tables() or []
                all_tables.extend(tables)
    except ManualImportError:
        raise
    except Exception as exc:
        raise PdfValidationError(
            f"PDFを開く/抽出できない: {type(exc).__name__}: {exc}"
        ) from exc

    full_text = "\n\n".join(texts)
    text_chars = len(re.sub(r"\s+", "", full_text))
    text_pages = sum(1 for t in texts if len(re.sub(r"\s+", "", t)) >= 20)

    if text_chars < MIN_TEXT_CHARS or text_pages < max(1, int(pages * 0.7)):
        raise PdfStructureError(
            "テキスト抽出量が少なすぎる。画像PDFまたは新しい資料形式の可能性: "
            f"pages={pages} text_pages={text_pages} chars={text_chars}"
        )

    compact = re.sub(r"\s+", "", full_text)
    if "外国ユーザーリスト" not in compact:
        raise PdfStructureError(
            "「外国ユーザーリスト」文言を確認できない。別資料の可能性"
        )

    records = records_from_tables(all_tables)
    return records, full_text, pages


def load_records(path: Path) -> list[Record]:
    result: list[Record] = []
    if not path.exists():
        return result
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            aliases = tuple(
                x for x in str(row.get("aliases", "")).split("\u241e") if x
            )
            result.append(
                Record(
                    no=int(row["no"]),
                    country=row.get("country", ""),
                    company=row.get("company", ""),
                    aliases=aliases,
                    wmd=row.get("wmd", ""),
                    conventional_weapons=row.get(
                        "conventional_weapons", ""
                    ),
                )
            )
    return result


def save_records(records: list[Record], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(
            f, fieldnames=RECORD_COLS, lineterminator="\n"
        )
        w.writeheader()
        for r in records:
            w.writerow(
                {
                    "no": r.no,
                    "country": r.country,
                    "company": r.company,
                    "aliases": "\u241e".join(r.aliases),
                    "wmd": r.wmd,
                    "conventional_weapons": r.conventional_weapons,
                    "match_key": r.key,
                }
            )


def diff_records(old: list[Record], new: list[Record]) -> DiffResult:
    old_map = {r.key: r for r in old}
    new_map = {r.key: r for r in new}

    added = [new_map[k] for k in sorted(new_map.keys() - old_map.keys())]
    removed = [old_map[k] for k in sorted(old_map.keys() - new_map.keys())]

    changed: list[tuple[Record, Record]] = []
    for key in sorted(old_map.keys() & new_map.keys()):
        if old_map[key].comparable() != new_map[key].comparable():
            changed.append((old_map[key], new_map[key]))

    return DiffResult(added=added, removed=removed, changed=changed)


def save_diff(diff: DiffResult, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(
            f, fieldnames=DIFF_COLS, lineterminator="\n"
        )
        w.writeheader()

        for action, old, new in (
            *[("追加", None, r) for r in diff.added],
            *[("削除", r, None) for r in diff.removed],
            *[("変更", a, b) for a, b in diff.changed],
        ):
            w.writerow(
                {
                    "action": action,
                    "match_key": (new or old).key,
                    "old_no": old.no if old else "",
                    "new_no": new.no if new else "",
                    "old_company": old.company if old else "",
                    "new_company": new.company if new else "",
                    "old_country": old.country if old else "",
                    "new_country": new.country if new else "",
                    "old_aliases": " | ".join(old.aliases) if old else "",
                    "new_aliases": " | ".join(new.aliases) if new else "",
                    "old_wmd": old.wmd if old else "",
                    "new_wmd": new.wmd if new else "",
                    "old_conventional_weapons": (
                        old.conventional_weapons if old else ""
                    ),
                    "new_conventional_weapons": (
                        new.conventional_weapons if new else ""
                    ),
                }
            )


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        value = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ManualImportError(
            f"METI manual state.jsonが壊れている: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise ManualImportError("METI manual state.jsonがobjectではない")
    return value


def save_state(value: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp.replace(STATE_PATH)


def append_dashboard_row(kind: str, subject: str, before: str, after: str) -> None:
    CHANGES_PATH.parent.mkdir(parents=True, exist_ok=True)
    old: list[list[str]] = []
    if CHANGES_PATH.exists():
        with CHANGES_PATH.open(encoding="utf-8", newline="") as f:
            rows = list(csv.reader(f))
        old = rows[1:] if rows and rows[0] == CHANGE_COLS else rows

    row = [
        datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S"),
        "経済産業省",
        kind,
        subject,
        before,
        after,
    ]

    # 同一イベントの完全重複を抑止。
    if row[1:] in [x[1:] for x in old if len(x) >= 6]:
        return

    with CHANGES_PATH.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(CHANGE_COLS)
        w.writerow(row)
        w.writerows(old[:4999])


def update_evidence(
    records: list[Record],
    *,
    source_hash: str,
    source_url: str,
    source_document: str,
    publication_date: str,
    effective_date: str,
    seen_at: str,
) -> None:
    EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)

    old_rows: list[dict] = []
    if EVIDENCE_PATH.exists():
        with EVIDENCE_PATH.open(encoding="utf-8", newline="") as f:
            old_rows = list(csv.DictReader(f))

    first_seen_by_key: dict[str, str] = {}
    for row in old_rows:
        key = row.get("match_key", "")
        first = row.get("first_seen", "")
        if key and first and key not in first_seen_by_key:
            first_seen_by_key[key] = first
        row["current"] = "0"

    new_rows: list[dict] = []
    for rec in records:
        evidence = {
            "record_no": rec.no,
            "country": rec.country,
            "aliases": list(rec.aliases),
            "wmd": rec.wmd,
            "conventional_weapons": rec.conventional_weapons,
        }
        new_rows.append(
            {
                "canonical_record": rec.company,
                "match_key": rec.key,
                "source": "経済産業省 外国ユーザーリスト",
                "source_record_id": f"{source_hash}:{rec.no}",
                "source_url": source_url,
                "source_document": source_document,
                "publication_date": publication_date,
                "effective_date": effective_date,
                "first_seen": first_seen_by_key.get(rec.key, seen_at),
                "last_seen": seen_at,
                "current": "1",
                "source_hash": source_hash,
                "evidence": json.dumps(
                    evidence, ensure_ascii=False, separators=(",", ":")
                ),
            }
        )

    with EVIDENCE_PATH.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(
            f, fieldnames=EVIDENCE_COLS, lineterminator="\n"
        )
        w.writeheader()
        w.writerows(old_rows)
        w.writerows(new_rows)


def write_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _archive_copy(src: Path, dest_dir: Path, digest: str, stamp: str) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", src.name)
    dest = dest_dir / f"{stamp}__{digest[:12]}__{safe_name}"
    shutil.copy2(src, dest)
    if sha256_file(dest) != digest:
        dest.unlink(missing_ok=True)
        raise ManualImportError("原本コピー後のSHA256が一致しない")
    return dest


def _relative(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", help="ブラウザで取得した経産省公式PDF")
    ap.add_argument(
        "--source-url",
        required=True,
        help="当該PDFの経産省公式URL",
    )
    ap.add_argument("--publication-date", default="")
    ap.add_argument("--effective-date", default="")
    ap.add_argument("--expected-count", type=int, default=None)
    args = ap.parse_args(argv)

    src = Path(args.pdf).expanduser().resolve()
    now = _now()
    stamp = _stamp(now)
    seen_at = _iso(now)

    # SHAはPDF妥当性判定前でも取得し、拒否HTML等を識別できるようにする。
    digest = sha256_file(src) if src.exists() and src.is_file() else ""
    report_path = REPORT_DIR / f"{stamp}__manual_import.json"

    try:
        size, digest = validate_pdf_file(src)
    except Exception as exc:
        quarantine = ""
        if digest and src.exists() and src.is_file():
            try:
                q = _archive_copy(src, QUARANTINE_DIR, digest, stamp)
                quarantine = _relative(q)
            except Exception:
                quarantine = ""

        report = {
            "version": 1,
            "status": "BLOCKED",
            "review_required": True,
            "auto_import": "BLOCKED",
            "reason": str(exc),
            "input_file": str(src),
            "source_url": args.source_url,
            "source_hash": digest,
            "quarantine_path": quarantine,
            "checked_at": seen_at,
        }
        write_report(report_path, report)

        source_audit.write(
            ROOT,
            [
                source_audit.entry(
                    "meti_manual",
                    "foreign_user_list_pdf",
                    "manual_input_invalid",
                    url=args.source_url,
                    content_hash=digest,
                    fetched_file=src.name,
                    raw_path=quarantine,
                    fetch_failed=False,
                    schema_changed=True,
                    error=exc,
                )
            ],
        )
        append_dashboard_row(
            "手動正本解析BLOCKED",
            "外国ユーザーリスト",
            str(exc),
            args.source_url,
        )
        print(f"[BLOCKED] {exc}", file=sys.stderr)
        print(f"report: {_relative(report_path)}")
        return 2

    state = load_state()

    if state.get("current_source_hash") == digest:
        print("[duplicate] 同一SHA256のPDFは既に取込済み")
        print(f"SHA256: {digest}")
        print(f"records: {state.get('current_record_count', '')}")
        print("AUTO IMPORT = BLOCKED / REVIEW REQUIRED")
        return 0

    raw_path = _archive_copy(src, RAW_DIR, digest, stamp)
    text_path = TEXT_DIR / f"{stamp}__{digest[:12]}.txt"
    record_path = RECORD_DIR / f"{stamp}__{digest[:12]}.csv"
    diff_path = DIFF_DIR / f"{stamp}__{digest[:12]}.csv"

    try:
        records, full_text, pages = extract_pdf(raw_path)

        if args.expected_count is not None and len(records) != args.expected_count:
            raise PdfStructureError(
                "公表件数とPDF抽出件数が一致しない: "
                f"expected={args.expected_count} actual={len(records)}"
            )

        TEXT_DIR.mkdir(parents=True, exist_ok=True)
        text_path.write_text(full_text, encoding="utf-8")

        previous_path = Path(state.get("current_records_path", ""))
        if previous_path and not previous_path.is_absolute():
            previous_path = ROOT / previous_path
        old_records = load_records(previous_path) if previous_path else []

        diff = (
            diff_records(old_records, records)
            if old_records
            else DiffResult([], [], [])
        )

        save_records(records, record_path)
        save_diff(diff, diff_path)

        update_evidence(
            records,
            source_hash=digest,
            source_url=args.source_url,
            source_document=_relative(raw_path),
            publication_date=args.publication_date,
            effective_date=args.effective_date,
            seen_at=seen_at,
        )

        baseline = not bool(old_records)
        report = {
            "version": 1,
            "status": "REVIEW_REQUIRED",
            "review_required": True,
            "auto_import": "BLOCKED",
            "baseline": baseline,
            "checked_at": seen_at,
            "source_url": args.source_url,
            "publication_date": args.publication_date,
            "effective_date": args.effective_date,
            "input_file": str(src),
            "raw_path": _relative(raw_path),
            "text_path": _relative(text_path),
            "records_path": _relative(record_path),
            "diff_path": _relative(diff_path),
            "source_hash": digest,
            "file_size": size,
            "page_count": pages,
            "record_count": len(records),
            "expected_count": args.expected_count,
            "diff": diff.counts,
            "previous_source_hash": state.get("current_source_hash", ""),
            "previous_record_count": state.get("current_record_count", ""),
        }
        write_report(report_path, report)

        state = {
            "version": 1,
            "current_source_hash": digest,
            "current_raw_path": _relative(raw_path),
            "current_records_path": _relative(record_path),
            "current_report_path": _relative(report_path),
            "current_diff_path": _relative(diff_path),
            "current_record_count": len(records),
            "publication_date": args.publication_date,
            "effective_date": args.effective_date,
            "source_url": args.source_url,
            "last_imported_at": seen_at,
            "review_status": "REVIEW_REQUIRED",
            "approved": False,
            "applied": False,
        }
        save_state(state)

        source_audit.write(
            ROOT,
            [
                source_audit.entry(
                    "meti_manual",
                    "foreign_user_list_pdf",
                    "baseline_review_required" if baseline else "diff_review_required",
                    url=args.source_url,
                    content_hash=digest,
                    fetched_file=src.name,
                    raw_path=_relative(raw_path),
                    source_updated=args.effective_date,
                    record_count=len(records),
                    diff_counts=diff.counts,
                    fetch_failed=False,
                    schema_changed=False,
                )
            ],
        )

        if baseline:
            append_dashboard_row(
                "手動正本baseline（要レビュー）",
                "外国ユーザーリスト",
                "",
                f"{len(records)}件 / SHA256 {digest[:12]} / {args.source_url}",
            )
        else:
            c = diff.counts
            append_dashboard_row(
                "手動正本差分（要レビュー）",
                "外国ユーザーリスト",
                f"前版 {len(old_records)}件",
                (
                    f"現版 {len(records)}件 / "
                    f"追加{c['追加']} 変更{c['変更']} 削除{c['削除']} / "
                    f"{args.source_url}"
                ),
            )

        print("===== METI MANUAL IMPORT =====")
        print(f"PDF             : OK")
        print(f"pages           : {pages}")
        print(f"SHA256          : {digest}")
        print(f"records         : {len(records)}")
        print(f"baseline        : {baseline}")
        print(
            "diff            : "
            f"追加{diff.counts['追加']} "
            f"変更{diff.counts['変更']} "
            f"削除{diff.counts['削除']}"
        )
        print(f"raw             : {_relative(raw_path)}")
        print(f"records         : {_relative(record_path)}")
        print(f"diff            : {_relative(diff_path)}")
        print(f"report          : {_relative(report_path)}")
        print(f"evidence        : {_relative(EVIDENCE_PATH)}")
        print("")
        print("AUTO IMPORT     = BLOCKED")
        print("REVIEW REQUIRED = YES")
        print("master反映      = 未実施")
        return 0

    except Exception as exc:
        schema_changed = isinstance(exc, PdfStructureError)
        report = {
            "version": 1,
            "status": "BLOCKED",
            "review_required": True,
            "auto_import": "BLOCKED",
            "reason": str(exc),
            "checked_at": seen_at,
            "source_url": args.source_url,
            "publication_date": args.publication_date,
            "effective_date": args.effective_date,
            "input_file": str(src),
            "raw_path": _relative(raw_path),
            "source_hash": digest,
            "file_size": size,
        }
        write_report(report_path, report)

        source_audit.write(
            ROOT,
            [
                source_audit.entry(
                    "meti_manual",
                    "foreign_user_list_pdf",
                    "parse_blocked",
                    url=args.source_url,
                    content_hash=digest,
                    fetched_file=src.name,
                    raw_path=_relative(raw_path),
                    fetch_failed=False,
                    schema_changed=schema_changed,
                    error=exc,
                )
            ],
        )
        append_dashboard_row(
            "手動正本解析BLOCKED",
            "外国ユーザーリスト",
            str(exc),
            args.source_url,
        )
        print(f"[BLOCKED] {type(exc).__name__}: {exc}", file=sys.stderr)
        print(f"raw: {_relative(raw_path)}")
        print(f"report: {_relative(report_path)}")
        print("AUTO IMPORT = BLOCKED / REVIEW REQUIRED")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

