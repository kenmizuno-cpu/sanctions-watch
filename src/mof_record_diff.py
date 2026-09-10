"""財務省の原本CSVをレコード単位で比較し、情報改訂を監視リストへ出す。

既存の master 差分は「スクリーニング用名称が増減・属性(source/category)変更したか」
を見る。一方、財務省CSVには生年月日、出生地、国籍、旅券番号、身分証番号、住所、
国連参照番号等があり、名称が同じままこれらだけ改訂されることがある。

このモジュールはその隙間を埋めるため、data/raw/mof の連続する原本スナップショットを
32列すべてで比較する。結果は:
  - data/source_diff/mof_latest.csv : 原本レコード差分の全明細
  - data/dashboard/changes.csv      : Google Sheets監視リスト向け情報改訂
  - data/source_record_state/mof.json: 最終処理済み原本
へ保存する。

重要:
  * master / internal_import の差分とは分離する。属性改訂だけで社内取込Excelを再生成しない。
  * 初回導入時は最新2世代だけを比較し、直近更新をバックフィルする。
  * 以後は最終処理済み原本から最新までを連続比較する。
  * 列構造変更、番号重複、履歴断絶は「変更なし」にせず異常終了する。
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import os
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
SOURCE = "財務省"
MAX_CHANGES = 5000

EXPECTED_COLUMNS = [
    "区分", "番号", "告示日付", "告示番号", "個人・団体",
    "氏名（日本語）", "氏名（英語）",
    "別名・別称（日本語）", "別名・別称（英語）",
    "旧称（日本語）", "旧称（英語）",
    "確定に十分でない別名（日本語）", "確定に十分でない別名（英語）",
    "称号（日本語）", "称号（英語）", "役職（日本語）", "役職（英語）",
    "生年月日", "出生地（日本語）", "出生地（英語）",
    "国籍（日本語）", "国籍（英語）", "旅券番号", "身分証番号",
    "住所・所在地（国）（日本語）", "住所・所在地（都市その他の情報）（日本語）",
    "住所・所在地（国）（英語）", "住所・所在地（都市その他の情報）（英語）",
    "国連参照番号", "リスト掲載日", "その他の情報", "外務省告示情報",
]

# スクリーニングの照合材料そのものではない管理・法的メタデータ。
# ただし変更を捨てず、監視リストには「管理情報改訂」として明示する。
ADMIN_FIELDS = {
    "区分", "番号", "告示日付", "告示番号", "リスト掲載日",
}

# この列は他の各列を結合した告示本文で、個別項目と同じ変更をもう一度含む。
# 原本監査には残すが、監視リストでは二重計上しない。
AGGREGATE_FIELDS = {"外務省告示情報"}

# 文章全体の変更は重要だが、直ちにスクリーニングキーへ反映する項目ではない。
SUPPLEMENTAL_FIELDS = {"その他の情報"}

# 日付のゼロ埋め・全角数字だけの差は同じ日付として扱う。
DATE_FIELDS = {"生年月日", "告示日付", "リスト掲載日"}

# 地名・住所ではカンマ有無だけで実質変更にしない。
LOCATION_FIELDS = {
    "出生地（日本語）", "出生地（英語）",
    "住所・所在地（国）（日本語）", "住所・所在地（都市その他の情報）（日本語）",
    "住所・所在地（国）（英語）", "住所・所在地（都市その他の情報）（英語）",
}

# 財務省側の様式変更で、従来は告示本文等に入っていた既知情報が
# 専用列へ移っただけのケース。原本監査には残すが、新規情報として通知しない。
# Alias列は Weak→Strong の品質変更が重要なので対象外。
STRUCTURE_ECHO_FIELDS = {
    "称号（日本語）", "称号（英語）", "役職（日本語）", "役職（英語）",
    "生年月日", "出生地（日本語）", "出生地（英語）",
    "国籍（日本語）", "国籍（英語）", "旅券番号", "身分証番号",
    "住所・所在地（国）（日本語）", "住所・所在地（都市その他の情報）（日本語）",
    "住所・所在地（国）（英語）", "住所・所在地（都市その他の情報）（英語）",
    "国連参照番号",
}

# 大文字小文字はスクリーニング上同一として扱う英字系フィールド。
CASE_INSENSITIVE_FIELDS = {
    "氏名（英語）", "別名・別称（英語）", "旧称（英語）",
    "確定に十分でない別名（英語）", "称号（英語）", "役職（英語）",
    "出生地（英語）", "国籍（英語）",
    "住所・所在地（国）（英語）",
    "住所・所在地（都市その他の情報）（英語）",
}

NAME_COLUMNS = [
    "氏名（日本語）", "氏名（英語）",
    "別名・別称（日本語）", "別名・別称（英語）",
    "旧称（日本語）", "旧称（英語）",
]
NULLISH = {"", "-", "‐", "―", "ー", "なし", "不明", "N/A", "n/a"}
RAW_STAMP = re.compile(r"^(\d{8}T\d{6}Z)__")

CHANGE_COLS = ["検知日時", "出所", "種別", "受取人名", "変更前", "変更後"]
SOURCE_DIFF_COLS = [
    "検知日時", "前回原本", "今回原本", "種別", "番号", "国連参照番号",
    "受取人名", "項目", "変更前", "変更後", "影響区分",
]


class RecordDiffError(RuntimeError):
    """原本レコード差分を安全に確定できない場合。"""


def _clean(value) -> str:
    """意味を壊さず、CSV由来の改行・前後空白だけ正規化する。"""
    if value is None:
        return ""
    s = str(value).replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(part.strip() for part in s.strip().split("\n"))


def _semantic_value(field_name: str, value: str) -> str:
    """表記だけの差を吸収した比較値を返す。

    原文は必ず別途保持する。ここでは差分アラートのノイズ除去だけを行う。
    NFKCで全角英数・記号を統一し、区切り前後の空白や連続空白を吸収する。
    英字系の氏名/別名等は大文字小文字も同一視する。
    """
    s = unicodedata.normalize("NFKC", _clean(value))
    s = s.replace("\u00a0", " ")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\s*;\s*", ";", s)
    s = re.sub(r"\s*,\s*", ",", s)
    s = re.sub(r"\s*:\s*", ":", s)

    if field_name in DATE_FIELDS:
        # 05 と ５ を同じ「5」として扱う。
        s = re.sub(r"\d+", lambda m: str(int(m.group(0))), s)

    if field_name in LOCATION_FIELDS:
        # 地名のカンマ挿入/末尾カンマは表示上の差で、場所自体は同じ。
        s = re.sub(r"[,，]+", " ", s)
        s = re.sub(r"[ \t]+", " ", s).strip()

    if field_name in SUPPLEMENTAL_FIELDS:
        # 日本語本文に機械的に空白が挿入される版があるため、本文比較では除去する。
        s = re.sub(r"\s+", "", s)
    else:
        s = "\n".join(part.strip() for part in s.split("\n")).strip()

    if field_name in CASE_INSENSITIVE_FIELDS:
        s = s.casefold()
    return s


def _evidence_token(value: str) -> str:
    """別列に同じ情報が既に存在したかを見るための保守的な検索値。

    財務省原文では全角記号、NBSP、ゼロ幅文字、和文の区切り記号が混ざる。
    "QDi.431" が本文側では "QDi．431" のように見える場合でも
    同一情報として追えるよう、検索専用の値では記号と空白を落とす。
    原文そのものは一切変更しない。
    """
    s = unicodedata.normalize("NFKC", _clean(value)).casefold()
    s = re.sub(r"[\u200b-\u200d\ufeff\s]+", "", s)
    return re.sub(r"[^0-9a-z_\u0080-\uffff]+", "", s).strip()


def _known_elsewhere(value: str, row: dict[str, str], field_name: str) -> bool:
    token = _evidence_token(value)
    if len(token) < 2:
        return False
    for col, other in row.items():
        if col == field_name:
            continue
        if token in _evidence_token(other):
            return True
    return False


_ID_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(?=[A-Za-z0-9.-]*[A-Za-z])"
    r"(?=[A-Za-z0-9.-]*\d)"
    r"[A-Za-z][A-Za-z0-9.-]{3,}"
    r"(?![A-Za-z0-9])"
)

_DOCUMENT_DETAIL_FIELDS = {"旅券番号"}
_DOCUMENT_DETAIL_MARKERS = (
    "発行", "失効", "有効", "issued", "expires", "expired", "expiry",
)


def _identifier_tokens(value: str) -> set[str]:
    """旅券番号等に含まれる英数字識別子だけを比較用に抽出する。"""
    s = unicodedata.normalize("NFKC", _clean(value))
    return {m.group(0).casefold() for m in _ID_TOKEN_RE.finditer(s)}


def _document_detail_moved_elsewhere(
    field_name: str,
    before: str,
    after: str,
    after_row: dict[str, str],
) -> bool:
    """識別子は同じで、付帯情報だけ別列へ移ったケースを検出する。

    例:
      旅券番号: A00195974（2029年12月16日に失効）
        -> A00195974

    これを "旅券番号削除" と通知すると誤解を生む。一方、別列・集約本文にも
    失効情報が消えているなら本当の情報欠落なので material のまま残す。
    """
    if field_name not in _DOCUMENT_DETAIL_FIELDS:
        return False

    before_ids = _identifier_tokens(before)
    after_ids = _identifier_tokens(after)
    if not before_ids or before_ids != after_ids:
        return False

    # 値が短くなっていないなら、単なる再配置とはみなさない。
    before_sem = _evidence_token(before)
    after_sem = _evidence_token(after)
    if not after_sem or len(after_sem) >= len(before_sem):
        return False

    # 付帯情報らしい要素が旧値にあり、新値から落ちたことを確認。
    b_cf = unicodedata.normalize("NFKC", _clean(before)).casefold()
    a_cf = unicodedata.normalize("NFKC", _clean(after)).casefold()
    dropped_markers = [m for m in _DOCUMENT_DETAIL_MARKERS if m in b_cf and m not in a_cf]
    if not dropped_markers:
        return False

    # 新スナップショットの別列に同じ識別子＋付帯情報が残っている場合だけ
    # "列再構成" とする。見つからなければ安全側で material 扱い。
    for col, other in after_row.items():
        if col == field_name:
            continue
        other_cf = unicodedata.normalize("NFKC", _clean(other)).casefold()
        other_ids = _identifier_tokens(other)
        if before_ids <= other_ids and any(m in other_cf for m in dropped_markers):
            return True

    return False


def _structure_echo(field_name: str, before: str, after: str,
                    before_row: dict[str, str], after_row: dict[str, str]) -> bool:
    """値が別列から専用列へ移っただけなら True。Alias品質変更は除外する。"""
    if field_name not in STRUCTURE_ECHO_FIELDS:
        return False

    if not before and after:
        return _known_elsewhere(after, before_row, field_name)
    if before and not after:
        return _known_elsewhere(before, after_row, field_name)

    # 旅券番号の番号自体は同じで、発行日/失効日など説明だけが
    # 集約本文へ残ったケースも "列再構成" とする。
    if before and after:
        return _document_detail_moved_elsewhere(
            field_name,
            before,
            after,
            after_row,
        )

    return False


def _decode(body: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "cp932", "shift_jis"):
        try:
            return body.decode(enc)
        except UnicodeDecodeError:
            continue
    raise RecordDiffError("財務省CSVの文字コードを判定できない")


def _read_raw(path: Path) -> bytes:
    body = path.read_bytes()
    if body[:2] == b"\x1f\x8b":
        try:
            return gzip.decompress(body)
        except OSError as exc:
            raise RecordDiffError(f"gzip展開に失敗: {path}: {exc}") from exc
    return body


def _sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


@dataclass(frozen=True)
class Snapshot:
    path: Path
    content_hash: str
    rows: dict[str, dict[str, str]]
    header: list[str]


@dataclass(frozen=True)
class FieldChange:
    before_id: str
    after_id: str
    un_reference: str
    name: str
    field_name: str
    before: str
    after: str
    structure_echo: bool = False

    @property
    def impact(self) -> str:
        if self.field_name in AGGREGATE_FIELDS:
            return "集約原文（重複）"
        if self.structure_echo:
            return "列再構成（既知情報）"
        if _semantic_value(self.field_name, self.before) == _semantic_value(self.field_name, self.after):
            return "表記差"
        if self.field_name in ADMIN_FIELDS:
            return "管理"
        if self.field_name in SUPPLEMENTAL_FIELDS:
            return "補足情報・要レビュー"
        return "識別・スクリーニング"

    @property
    def material(self) -> bool:
        return self.impact not in {"表記差", "集約原文（重複）", "列再構成（既知情報）"}

    @property
    def kind(self) -> str:
        if self.field_name in ADMIN_FIELDS:
            prefix = "管理情報改訂"
        elif self.field_name in SUPPLEMENTAL_FIELDS:
            prefix = "補足情報改訂"
        else:
            prefix = "情報改訂"
        return f"{prefix}（{self.field_name}）"


@dataclass
class SourceDiff:
    before_path: Path
    after_path: Path
    before_count: int
    after_count: int
    added: list[dict[str, str]] = field(default_factory=list)
    removed: list[dict[str, str]] = field(default_factory=list)
    amended: list[FieldChange] = field(default_factory=list)

    @property
    def amended_record_ids(self) -> set[str]:
        """実質変更があるレコードID。表記差・集約原文の重複差は除く。"""
        return {c.after_id or c.before_id for c in self.amended if c.material}

    @property
    def raw_amended_record_ids(self) -> set[str]:
        return {c.after_id or c.before_id for c in self.amended}

    @property
    def material_amended(self) -> list[FieldChange]:
        return [c for c in self.amended if c.material]

    @property
    def cosmetic_amended(self) -> list[FieldChange]:
        return [c for c in self.amended if c.impact == "表記差"]

    @property
    def aggregate_amended(self) -> list[FieldChange]:
        return [c for c in self.amended if c.impact == "集約原文（重複）"]

    @property
    def structure_amended(self) -> list[FieldChange]:
        return [c for c in self.amended if c.impact == "列再構成（既知情報）"]

    @property
    def changed(self) -> bool:
        return bool(self.added or self.removed or self.amended)


def _parse_csv(path: Path) -> Snapshot:
    body = _read_raw(path)
    text = _decode(body)
    reader = csv.DictReader(io.StringIO(text))
    actual = [_clean(x) for x in (reader.fieldnames or [])]

    if actual != EXPECTED_COLUMNS:
        missing = [c for c in EXPECTED_COLUMNS if c not in actual]
        extra = [c for c in actual if c not in EXPECTED_COLUMNS]
        raise RecordDiffError(
            "財務省CSVの列構造が変更された。"
            f" missing={missing} extra={extra} actual={actual}"
        )

    rows: dict[str, dict[str, str]] = {}
    for line_no, raw in enumerate(reader, start=2):
        row = {col: _clean(raw.get(col, "")) for col in EXPECTED_COLUMNS}
        rid = row["番号"]
        if not rid:
            raise RecordDiffError(f"財務省CSVの番号が空: {path.name}:{line_no}")
        if rid in rows:
            raise RecordDiffError(
                f"財務省CSVで番号が重複: {rid} ({path.name}:{line_no})"
            )
        rows[rid] = row

    if not rows:
        raise RecordDiffError(f"財務省CSVのレコード件数が0: {path}")

    return Snapshot(path=path, content_hash=_sha256(body), rows=rows, header=actual)


def _display_name(row: dict[str, str]) -> str:
    for col in NAME_COLUMNS:
        value = _clean(row.get(col, ""))
        if value not in NULLISH:
            # 主名称は原文を保持。主名称が空で別名を表示に使う場合だけ先頭候補に絞る。
            if col not in ("氏名（日本語）", "氏名（英語）"):
                for sep in ("；", ";"):
                    if sep in value:
                        value = value.split(sep, 1)[0].strip()
            return value
    rid = _clean(row.get("番号", ""))
    return f"番号 {rid}" if rid else "（名称不明）"


def _unique_index(rows: dict[str, dict[str, str]], ids: set[str], field_name: str) -> dict[str, str]:
    """空値・重複値を除き、field value -> record id の一意索引を返す。"""
    buckets: dict[str, list[str]] = {}
    for rid in ids:
        value = _clean(rows[rid].get(field_name, ""))
        if not value or value in NULLISH:
            continue
        buckets.setdefault(value, []).append(rid)
    return {value: members[0] for value, members in buckets.items() if len(members) == 1}


def _name_identity(row: dict[str, str]) -> str:
    # source IDが変わったケースの補助照合。完全一致のみで、曖昧一致はしない。
    ja = _clean(row.get("氏名（日本語）", ""))
    en = _clean(row.get("氏名（英語）", ""))
    if ja in NULLISH:
        ja = ""
    if en in NULLISH:
        en = ""
    return "\u241f".join((ja, en)) if (ja or en) else ""


def _unique_name_index(rows: dict[str, dict[str, str]], ids: set[str]) -> dict[str, str]:
    buckets: dict[str, list[str]] = {}
    for rid in ids:
        value = _name_identity(rows[rid])
        if not value:
            continue
        buckets.setdefault(value, []).append(rid)
    return {value: members[0] for value, members in buckets.items() if len(members) == 1}


def _pair_records(before: Snapshot, after: Snapshot) -> tuple[list[tuple[str, str]], set[str], set[str]]:
    """番号→国連参照番号→主名称の順で保守的に同一レコードを対応付ける。"""
    b_ids = set(before.rows)
    a_ids = set(after.rows)
    pairs: list[tuple[str, str]] = []

    for rid in sorted(b_ids & a_ids):
        pairs.append((rid, rid))
    b_left = b_ids - a_ids
    a_left = a_ids - b_ids

    # 番号そのものが改訂された場合でも、国連参照番号が一意なら同一Partyとして追跡。
    b_un = _unique_index(before.rows, b_left, "国連参照番号")
    a_un = _unique_index(after.rows, a_left, "国連参照番号")
    for key in sorted(set(b_un) & set(a_un)):
        b_id, a_id = b_un[key], a_un[key]
        pairs.append((b_id, a_id))
        b_left.discard(b_id)
        a_left.discard(a_id)

    # 国連参照番号が無い対象は、日英主名称の完全一致かつ一意な場合だけ補助照合。
    b_name = _unique_name_index(before.rows, b_left)
    a_name = _unique_name_index(after.rows, a_left)
    for key in sorted(set(b_name) & set(a_name)):
        b_id, a_id = b_name[key], a_name[key]
        pairs.append((b_id, a_id))
        b_left.discard(b_id)
        a_left.discard(a_id)

    return pairs, b_left, a_left


def compare_snapshots(before: Snapshot, after: Snapshot) -> SourceDiff:
    if before.header != after.header:
        raise RecordDiffError("財務省CSVの前回・今回で列構造が一致しない")

    diff = SourceDiff(
        before_path=before.path,
        after_path=after.path,
        before_count=len(before.rows),
        after_count=len(after.rows),
    )
    pairs, removed_ids, added_ids = _pair_records(before, after)

    for rid in sorted(added_ids):
        diff.added.append(after.rows[rid])
    for rid in sorted(removed_ids):
        diff.removed.append(before.rows[rid])

    for before_id, after_id in sorted(pairs):
        b = before.rows[before_id]
        a = after.rows[after_id]
        name = _display_name(a) if _display_name(a) != "（名称不明）" else _display_name(b)
        for col in EXPECTED_COLUMNS:
            if b[col] == a[col]:
                continue
            diff.amended.append(
                FieldChange(
                    before_id=before_id,
                    after_id=after_id,
                    un_reference=a.get("国連参照番号", "") or b.get("国連参照番号", ""),
                    name=name,
                    field_name=col,
                    before=b[col],
                    after=a[col],
                    structure_echo=_structure_echo(col, b[col], a[col], b, a),
                )
            )

    return diff


def _raw_paths(root: Path) -> list[Path]:
    raw_dir = root / "data" / "raw" / "mof"
    if not raw_dir.exists():
        return []

    grouped: dict[str, list[Path]] = {}
    for p in raw_dir.iterdir():
        if not p.is_file():
            continue
        m = RAW_STAMP.match(p.name)
        if not m:
            continue
        if ".csv" not in p.name.lower():
            continue
        grouped.setdefault(m.group(1), []).append(p)

    paths: list[Path] = []
    for stamp in sorted(grouped):
        members = grouped[stamp]
        if len(members) != 1:
            raise RecordDiffError(
                f"財務省rawの同一世代に複数CSVが存在: {stamp}: {[p.name for p in members]}"
            )
        paths.append(members[0])
    return paths


def _state_path(root: Path) -> Path:
    return root / "data" / "source_record_state" / "mof.json"


def _load_state(root: Path) -> dict:
    p = _state_path(root)
    if not p.exists():
        return {}
    try:
        value = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RecordDiffError(f"財務省source record stateを読めない: {exc}") from exc
    if not isinstance(value, dict):
        raise RecordDiffError("財務省source record stateがJSON objectではない")
    return value


def _rel(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def plan_transitions(root: Path, state: dict) -> tuple[list[tuple[Path, Path]], Path | None]:
    paths = _raw_paths(root)
    if not paths:
        return [], None

    latest = paths[-1]
    processed = _clean(state.get("processed_raw", ""))

    if not processed:
        # 導入時は過去全履歴を大量に再掲せず、最新更新1回だけバックフィル。
        return ([(paths[-2], latest)] if len(paths) >= 2 else []), latest

    lookup = {_rel(root, p): i for i, p in enumerate(paths)}
    if processed not in lookup:
        raise RecordDiffError(
            "最終処理済み財務省rawが保持履歴に存在しない。"
            f" processed_raw={processed}。履歴断絶のため自動差分を停止する"
        )

    start = lookup[processed]
    if start == len(paths) - 1:
        return [], latest

    return [(paths[i], paths[i + 1]) for i in range(start, len(paths) - 1)], latest


def _write_source_diff(root: Path, diffs: list[SourceDiff], stamp: str) -> Path:
    d = root / "data" / "source_diff"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "mof_latest.csv"

    with p.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(SOURCE_DIFF_COLS)
        for diff in diffs:
            bname = diff.before_path.name
            aname = diff.after_path.name
            for row in diff.added:
                w.writerow([stamp, bname, aname, "原本レコード追加", row["番号"],
                            row.get("国連参照番号", ""), _display_name(row), "", "", "", ""])
            for row in diff.removed:
                w.writerow([stamp, bname, aname, "原本レコード削除", row["番号"],
                            row.get("国連参照番号", ""), _display_name(row), "", "", "", ""])
            for c in diff.amended:
                w.writerow([stamp, bname, aname, "情報改訂", c.after_id or c.before_id,
                            c.un_reference, c.name, c.field_name, c.before, c.after,
                            c.impact])
    return p


def _append_dashboard(root: Path, diffs: list[SourceDiff], stamp: str) -> int:
    rows: list[list[str]] = []
    for diff in diffs:
        for c in diff.material_amended:
            rows.append([stamp, SOURCE, c.kind, c.name, c.before, c.after])

    if not rows:
        return 0

    p = root / "data" / "dashboard" / "changes.csv"
    p.parent.mkdir(parents=True, exist_ok=True)
    old: list[list[str]] = []
    if p.exists():
        with p.open(encoding="utf-8", newline="") as f:
            existing = list(csv.reader(f))
        if existing and existing[0] != CHANGE_COLS:
            raise RecordDiffError(
                f"changes.csvの列構造が想定外: expected={CHANGE_COLS} actual={existing[0]}"
            )
        old = existing[1:] if existing else []

    combined = (rows + old)[:MAX_CHANGES]
    with p.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(CHANGE_COLS)
        w.writerows(combined)
    return len(rows)


def _save_state(root: Path, latest: Path, snapshot: Snapshot, summary: dict) -> Path:
    p = _state_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "processed_raw": _rel(root, latest),
        "processed_content_sha256": snapshot.content_hash,
        "processed_at": datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S%z"),
        "record_count": len(snapshot.rows),
        "last_diff": summary,
    }
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(p)
    return p


def run(root: Path, *, dry_run: bool = False, details: bool = False) -> dict:
    root = root.resolve()
    state = _load_state(root)
    transitions, latest = plan_transitions(root, state)

    empty = {
        "source_changed": False,
        "added": 0,
        "removed": 0,
        "amended": 0,
        "field_changes": 0,
        "dashboard_rows": 0,
        "transitions": 0,
    }

    if latest is None:
        print("財務省rawなし: 原本レコード差分をスキップ")
        return empty

    # 既に最新まで処理済み。
    if not transitions and state.get("processed_raw"):
        print("財務省 原本レコード差分: 処理済み（新しいrawなし）")
        return empty

    diffs: list[SourceDiff] = []
    for before_path, after_path in transitions:
        before = _parse_csv(before_path)
        after = _parse_csv(after_path)
        diffs.append(compare_snapshots(before, after))

    latest_snapshot = _parse_csv(latest)
    added = sum(len(d.added) for d in diffs)
    removed = sum(len(d.removed) for d in diffs)
    amended_ids = {
        (d.after_path.name, rid)
        for d in diffs
        for rid in d.amended_record_ids
    }
    raw_amended_ids = {
        (d.after_path.name, rid)
        for d in diffs
        for rid in d.raw_amended_record_ids
    }
    field_changes = sum(len(d.material_amended) for d in diffs)
    raw_field_changes = sum(len(d.amended) for d in diffs)
    cosmetic_changes = sum(len(d.cosmetic_amended) for d in diffs)
    aggregate_changes = sum(len(d.aggregate_amended) for d in diffs)
    structure_changes = sum(len(d.structure_amended) for d in diffs)
    summary = {
        "source_changed": bool(added or removed or raw_field_changes),
        "material_changed": bool(added or removed or field_changes),
        "added": added,
        "removed": removed,
        "amended": len(amended_ids),
        "raw_amended": len(raw_amended_ids),
        "field_changes": field_changes,
        "raw_field_changes": raw_field_changes,
        "cosmetic_changes": cosmetic_changes,
        "aggregate_changes": aggregate_changes,
        "structure_changes": structure_changes,
        "dashboard_rows": field_changes,
        "transitions": len(transitions),
    }

    print(
        "財務省 原本レコード差分: "
        f"追加={added} 削除={removed} 情報改訂={len(amended_ids)} "
        f"実質変更項目={field_changes} "
        f"表記差={cosmetic_changes} 列再構成={structure_changes} "
        f"集約原文差={aggregate_changes} 世代={len(transitions)}"
    )

    if details:
        for diff in diffs:
            for row in diff.added:
                print(f"  [追加] {row['番号']} {row.get('国連参照番号', '')} {_display_name(row)}")
            for row in diff.removed:
                print(f"  [削除] {row['番号']} {row.get('国連参照番号', '')} {_display_name(row)}")
            for c in diff.amended:
                ref = f" {c.un_reference}" if c.un_reference else ""
                label = (
                    "改訂" if c.material
                    else (
                        "表記差" if c.impact == "表記差"
                        else ("列再構成" if c.impact == "列再構成（既知情報）" else "集約差")
                    )
                )
                print(
                    f"  [{label}] {c.after_id or c.before_id}{ref} {c.name} / "
                    f"{c.field_name} [{c.impact}]: "
                    f"{c.before or '—'} -> {c.after or '—'}"
                )

    if dry_run:
        return summary

    stamp = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
    _write_source_diff(root, diffs, stamp)
    dashboard_rows = _append_dashboard(root, diffs, stamp)
    summary["dashboard_rows"] = dashboard_rows
    _save_state(root, latest, latest_snapshot, summary)
    return summary


def _github_output(summary: dict) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as f:
        for key in (
            "source_changed", "material_changed", "added", "removed", "amended",
            "raw_amended", "field_changes", "raw_field_changes", "cosmetic_changes",
            "structure_changes", "aggregate_changes", "dashboard_rows",
        ):
            value = summary.get(key, "")
            if isinstance(value, bool):
                value = "true" if value else "false"
            f.write(f"{key}={value}\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        default=str(Path(__file__).resolve().parent.parent),
        help="sanctions-watch repository root",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--details", action="store_true", help="変更項目の前後値を標準出力へ表示")
    args = parser.parse_args()

    try:
        summary = run(Path(args.root), dry_run=args.dry_run, details=args.details)
    except RecordDiffError as exc:
        print(f"[FAILED] 財務省 原本レコード差分: {exc}")
        return 1

    _github_output(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
