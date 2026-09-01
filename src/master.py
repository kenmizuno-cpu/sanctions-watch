"""マスターの読み書きと、ソース単位のマージ・差分抽出。

重要な前提: マスターには自動再取得できない行が含まれる。
  経産省(PDFのみ) / 外務省告示 / UK FCDO / 出所を特定できない旧取込分
そのため突合は必ず「ソース単位」で行う。OFAC を取得したときに
OFAC 由来の行だけを照合し、他ソースの行には一切触れない。
全件を入れ替える実装にすると、これらが毎回消える。
"""
from __future__ import annotations

import csv
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from .normalize import match_key, needs_review, render_remark, validate

FIELDS = [
    "match_key", "display_name", "status", "risk_type", "risk_level",
    "first_seen_ms", "last_updated_ms", "sources", "categories",
    "remark", "invalid_reason", "review_flag", "variants",
]

STATUS_ACTIVE = "有効"
STATUS_INACTIVE = "無効"
RISK_TYPE = "制裁リスト"
RISK_LEVEL = "高"

# 制裁解除された行に付ける理由。行は消さずに無効化する。
# 消すと登録時間が失われ、過去時点での照合状況を説明できなくなる。
DELISTED = "全ての出所から掲載が無くなったため無効化（制裁解除または統廃合）"


def now_ms() -> int:
    return int(time.time() * 1000)


def load(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8", newline="") as f:
        return {r["match_key"]: r for r in csv.DictReader(f)}


def save(rows: dict[str, dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for k in sorted(rows):
            w.writerow({c: rows[k].get(c, "") for c in FIELDS})


def _split(v: str) -> list[str]:
    return [x for x in str(v or "").split(";") if x]


def _join(vals) -> str:
    return ";".join(sorted(set(v for v in vals if v)))


def _pairs(row: dict) -> list[tuple[str, str]]:
    """sources と categories から (source, category) の組を復元する。"""
    srcs = _split(row.get("sources", ""))
    cats = _split(row.get("categories", ""))
    out = []
    for s in srcs:
        own = [c for c in cats if c.startswith(f"{s}:")]
        if own:
            out += [(s, c.split(":", 1)[1]) for c in own]
        else:
            out.append((s, ""))
    return out


def _recompute(row: dict) -> dict:
    """sources/categories から remark を作り直す。"""
    row["remark"] = render_remark(_pairs(row))
    return row


@dataclass
class Diff:
    source: str
    added: list[dict] = field(default_factory=list)
    removed: list[dict] = field(default_factory=list)
    changed: list[dict] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.added or self.removed or self.changed)

    @property
    def counts(self) -> dict:
        return dict(追加=len(self.added), 削除=len(self.removed),
                    変更=len(self.changed))


def merge(master: dict[str, dict], records: list[dict], source: str,
          ts: int | None = None, delist: bool = True) -> Diff:
    """1ソース分の取得結果をマスターに反映し、差分を返す。

    records: [{source, category, name, source_id, ...}, ...]
    """
    ts = ts or now_ms()
    diff = Diff(source=source)

    # 取得結果を match_key に畳む
    incoming: dict[str, dict] = {}
    for r in records:
        k = match_key(r["name"])
        if not k:
            continue
        e = incoming.setdefault(k, dict(name=r["name"], cats=set(), ids=set()))
        if r.get("category"):
            e["cats"].add(r["category"])
        if r.get("source_id"):
            e["ids"].add(str(r["source_id"]))

    for k, e in incoming.items():
        new_cats = {f"{source}:{c}" for c in e["cats"]}
        row = master.get(k)

        if row is None:
            reason = validate(e["name"])
            row = dict(
                match_key=k, display_name=e["name"],
                status=STATUS_INACTIVE if reason else STATUS_ACTIVE,
                risk_type=RISK_TYPE, risk_level=RISK_LEVEL,
                first_seen_ms=ts, last_updated_ms=ts,
                sources=source, categories=_join(new_cats),
                invalid_reason=reason or "",
                review_flag="" if reason else (needs_review(e["name"]) or ""),
                variants=json.dumps([e["name"]], ensure_ascii=False),
            )
            master[k] = _recompute(row)
            diff.added.append(dict(key=k, name=e["name"],
                                   remark=row["remark"]))
            continue

        before = {c: row.get(c, "") for c in ("status", "sources", "categories", "remark")}

        srcs = set(_split(row["sources"])) | {source}
        # このソース由来のカテゴリだけ差し替える。他ソース分は保持。
        cats = {c for c in _split(row["categories"])
                if not c.startswith(f"{source}:")} | new_cats
        row["sources"] = _join(srcs)
        row["categories"] = _join(cats)

        # 一度掲載が消えて再掲載された場合は有効に戻す
        if row.get("invalid_reason") == DELISTED:
            row["status"] = STATUS_ACTIVE
            row["invalid_reason"] = ""

        _recompute(row)
        after = {c: row.get(c, "") for c in ("status", "sources", "categories", "remark")}
        if before != after:
            row["last_updated_ms"] = ts
            diff.changed.append(dict(key=k, name=row["display_name"],
                                     before=before, after=after))

    # このソースに載っていたが、今回の取得結果から消えた行
    for k, row in master.items():
        if source not in _split(row["sources"]) or k in incoming:
            continue
        srcs = [s for s in _split(row["sources"]) if s != source]
        cats = [c for c in _split(row["categories"]) if not c.startswith(f"{source}:")]
        before = {c: row.get(c, "") for c in ("status", "sources", "categories", "remark")}
        if not delist:
            # 初回同期。掲載終了の可能性として報告だけして、行は触らない。
            diff.removed.append(dict(key=k, name=row["display_name"],
                                     before=before, after=before,
                                     delisted=False))
            continue
        row["sources"] = _join(srcs)
        row["categories"] = _join(cats)
        if not srcs:
            row["status"] = STATUS_INACTIVE
            row["invalid_reason"] = DELISTED
        _recompute(row)
        row["last_updated_ms"] = ts
        after = {c: row.get(c, "") for c in ("status", "sources", "categories", "remark")}
        diff.removed.append(dict(key=k, name=row["display_name"],
                                 before=before, after=after, delisted=True))

    return diff


def render_markdown(diffs: list[Diff]) -> str:
    """差分レポート。フィールド単位で変更前後を出す。"""
    if not any(diffs):
        return "変更なし\n"
    out = ["# 制裁リスト差分レポート", ""]
    for d in diffs:
        if not d:
            continue
        out.append(f"## {d.source}　"
                   f"追加 {len(d.added)} / 削除 {len(d.removed)} / 変更 {len(d.changed)}")
        out.append("")
        if d.added:
            out.append("### 追加")
            for a in d.added[:200]:
                out.append(f"- `{a['name']}` — {a['remark']}")
            if len(d.added) > 200:
                out.append(f"- …他 {len(d.added) - 200} 件")
            out.append("")
        if d.removed:
            held = any(not r.get("delisted", True) for r in d.removed)
            out.append("### 掲載終了の候補（初回のため無効化せず保留）" if held
                       else "### 掲載終了（行は無効化して残す）")
            for r in d.removed[:200]:
                out.append(f"- `{r['name']}` — {r['after']['status']}")
            if len(d.removed) > 200:
                out.append(f"- …他 {len(d.removed) - 200} 件")
            out.append("")
        if d.changed:
            out.append("### 変更")
            for c in d.changed[:100]:
                out.append(f"#### `{c['name']}`")
                out.append("| 項目 | 変更前 | 変更後 |")
                out.append("| --- | --- | --- |")
                for f_ in ("status", "sources", "categories", "remark"):
                    if c["before"][f_] != c["after"][f_]:
                        out.append(f"| {f_} | {c['before'][f_] or '—'} | "
                                   f"{c['after'][f_] or '—'} |")
                out.append("")
            if len(d.changed) > 100:
                out.append(f"…他 {len(d.changed) - 100} 件")
                out.append("")
    return "\n".join(out) + "\n"


def diff_rows(diffs: list[Diff]) -> list[list]:
    """差分を [出所, 種別, 受取人名, 変更前, 変更後] の行に展開する。

    write_diff_csv とダッシュボードの変更履歴で同じ行を使うため切り出した。
    片方だけ列が変わって食い違うのを防ぐ。
    """
    out: list[list] = []
    for d in diffs:
        for a in d.added:
            out.append([d.source, "追加", a["name"], "", a["remark"]])
        for r in d.removed:
            out.append([d.source, "掲載終了", r["name"],
                        r["before"]["remark"], r["after"]["remark"]])
        for c in d.changed:
            out.append([d.source, "変更", c["name"],
                        c["before"]["remark"], c["after"]["remark"]])
    return out


def write_diff_csv(diffs: list[Diff], path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = diff_rows(diffs)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["出所", "種別", "受取人名", "変更前", "変更後"])
        w.writerows(rows)
    return len(rows)
