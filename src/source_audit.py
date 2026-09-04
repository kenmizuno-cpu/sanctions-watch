"""一次ソース監視の詳細監査台帳。

既存の data/heartbeat はダッシュボード・稼働確認用として維持する。
このモジュールでは金融業務向けの監査証跡として、取得時のHTTP情報、
ETag / Last-Modified / SHA256、取得URL、原本、件数、差分、
取得失敗、スキーマ変更等を月別CSVへ保存する。

重要:
    「取得できない」
    「URLが消えた」
    「構造が変わった」

を単なる unchanged として扱わない。
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path


AUDIT_DIR = "data/source_audit"

AUDIT_COLS = [
    "checked_at",
    "source",
    "document_role",
    "status",
    "http_status",
    "etag",
    "last_modified",
    "content_hash",
    "source_updated",
    "url",
    "final_url",
    "fetched_file",
    "raw_path",
    "record_count",
    "diff_added",
    "diff_removed",
    "diff_changed",
    "fetch_failed",
    "schema_changed",
    "error_type",
    "error_message",
]


class AuditSchemaError(RuntimeError):
    """監査台帳自体の列構造が想定外の場合に停止する。"""


def _flag(value: bool) -> str:
    return "1" if value else "0"


def _clean_error(value) -> str:
    if value is None:
        return ""
    # CSV内で巨大なtracebackや改行が増殖しないよう1行化する。
    return " ".join(str(value).replace("\x00", "").splitlines())[:4000]


def entry(
    source: str,
    document_role: str,
    status: str,
    *,
    fetched=None,
    source_updated="",
    record_count="",
    diff_counts: dict | None = None,
    fetch_failed: bool = False,
    schema_changed: bool = False,
    error=None,
    url: str = "",
    content_hash: str = "",
    fetched_file: str = "",
    raw_path: str = "",
) -> dict:
    """監査台帳1行分を生成する。

    fetched は src.fetch.Fetched を想定するが、循環importを避けるため
    duck typing にしている。
    """
    row = {c: "" for c in AUDIT_COLS}

    row.update(
        source=source,
        document_role=document_role,
        status=status,
        source_updated=source_updated,
        record_count=record_count,
        fetch_failed=_flag(fetch_failed),
        schema_changed=_flag(schema_changed),
    )

    if fetched is not None:
        row["http_status"] = getattr(fetched, "http_status", "")
        row["etag"] = getattr(fetched, "etag", "") or ""
        row["last_modified"] = getattr(fetched, "last_modified", "") or ""
        row["content_hash"] = getattr(fetched, "sha256", "") or ""
        row["url"] = getattr(fetched, "url", "") or ""
        row["final_url"] = getattr(fetched, "final_url", "") or ""
        row["fetched_file"] = getattr(fetched, "filename", "") or ""
        row["raw_path"] = getattr(fetched, "raw_path", "") or ""

    # 明示指定値を優先。
    if url:
        row["url"] = url
    if content_hash:
        row["content_hash"] = content_hash
    if fetched_file:
        row["fetched_file"] = fetched_file
    if raw_path:
        row["raw_path"] = raw_path

    counts = diff_counts or {}
    row["diff_added"] = counts.get(
        "追加", counts.get("added", "")
    )
    row["diff_removed"] = counts.get(
        "削除", counts.get("removed", "")
    )
    row["diff_changed"] = counts.get(
        "変更", counts.get("changed", "")
    )

    if error is not None:
        row["error_type"] = type(error).__name__
        row["error_message"] = _clean_error(error)

    return row


def error_entry(
    source: str,
    document_role: str,
    error,
    *,
    url: str = "",
    status: str = "error",
    fetch_failed: bool = True,
    schema_changed: bool = False,
) -> dict:
    """例外から可能な限りHTTP情報まで回収して監査行を作る。"""

    # 一部の独自例外は、取得済みFetchedを保持できる。
    fetched = getattr(error, "fetched", None)

    row = entry(
        source,
        document_role,
        status,
        fetched=fetched,
        fetch_failed=fetch_failed,
        schema_changed=schema_changed,
        error=error,
        url=url,
    )

    # requests.HTTPError 等。
    response = getattr(error, "response", None)

    # requests例外では「実際に要求したURL」を最優先する。
    # 呼出側から渡す url はあくまでfallback。
    request = getattr(error, "request", None)

    if response is not None:
        row["http_status"] = getattr(response, "status_code", "") or ""

        headers = getattr(response, "headers", {}) or {}
        row["etag"] = headers.get("ETag", "") or ""
        row["last_modified"] = headers.get("Last-Modified", "") or ""

        response_url = getattr(response, "url", "") or ""
        if response_url:
            row["final_url"] = response_url

        if request is None:
            request = getattr(response, "request", None)

        request_url = (
            getattr(request, "url", "")
            if request is not None
            else ""
        ) or ""

        if request_url:
            row["url"] = request_url
        elif response_url:
            row["url"] = response_url

    elif request is not None:
        request_url = getattr(request, "url", "") or ""
        if request_url:
            row["url"] = request_url

    return row


def write(root: Path, entries: list[dict]) -> Path | None:
    """月別CSVへ追記する。

    既存CSVのヘッダーが想定外なら追記せず停止する。
    列ずれした監査証跡を正常扱いしないため。
    """
    if not entries:
        return None

    now = datetime.now(timezone.utc)

    d = root / AUDIT_DIR
    d.mkdir(parents=True, exist_ok=True)

    p = d / f"{now:%Y-%m}.csv"

    if p.exists() and p.stat().st_size:
        with p.open("r", encoding="utf-8", newline="") as f:
            reader = csv.reader(f)
            actual = next(reader, [])

        if actual != AUDIT_COLS:
            raise AuditSchemaError(
                "Source Audit Ledger の列構造が想定と異なる。"
                f" expected={AUDIT_COLS} actual={actual}"
            )

    new = not p.exists() or p.stat().st_size == 0

    checked_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    with p.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=AUDIT_COLS,
            extrasaction="ignore",
            lineterminator="\n",
        )

        if new:
            writer.writeheader()

        for source_row in entries:
            row = {c: "" for c in AUDIT_COLS}
            row.update(source_row)
            row["checked_at"] = checked_at
            writer.writerow(row)

    return p
