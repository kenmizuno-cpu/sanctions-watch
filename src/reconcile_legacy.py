"""旧Excel由来の行を、現行パーサーと同じ整形で財務省リストに突き合わせ直す。

初回の財務省同期で 2,432 行が「掲載終了」と判定されたが、実際には公表側は
1件も減っていない。原因は旧Excelの名前が別名マーカーや断片を含んだままで
match_key が現行パーサーの結果と一致しないこと。

  python -m src.reconcile_legacy --dry-run
  python -m src.reconcile_legacy

一度きりの移行。実行前に --dry-run で件数を目視確認すること。
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import re
from pathlib import Path

from . import master as M
from .fetch import Fetched
from .normalize import clean_name, match_key, split_aliases
from .sources import mof

ROOT = Path(__file__).resolve().parent.parent

# 「（a)」「(ａ)」のような列挙マーカー。ここで名前が連結されている。
MARKER = re.compile(r"[（(]\s*[ａ-ｚA-Za-zＡ-Ｚ]\s*[)）]")
# 末尾の括弧。英字別名が入っていることが多く、公表側では別レコード。
PAREN_TAIL = re.compile(r"[（(]([^（()）]+)[)）]\s*$")
EDGE = "(（)）-–—:：;；.,、。'\"「」『』 \u3000"
LEAD_NUM = re.compile(r"^\s*[0-9０-９]+[\s.．、]+")


def _variants(s: str) -> set[str]:
    t = s.strip(EDGE)
    return {x for x in (s, t, LEAD_NUM.sub("", t)) if x}


def candidates(raw: str) -> list[str]:
    """旧Excelの1セルから、突合に使う名前の候補を全て作る。"""
    s = clean_name(raw)
    parts = [p for p in MARKER.split(s) if p.strip()] if MARKER.search(s) else [s]
    out: list[str] = []
    for p in parts:
        for q in mof.split_top_level(p):
            for a in (split_aliases(clean_name(q)) or [clean_name(q)]):
                a = clean_name(a)
                for cand in _variants(a) | _variants(mof.strip_descriptor(a)):
                    if len(cand) > 1:
                        out.append(cand)
                    m = PAREN_TAIL.search(cand)
                    if m:
                        for x in (clean_name(cand[:m.start()]),
                                  clean_name(m.group(1))):
                            if len(x) > 1:
                                out.append(x)
    seen, res = set(), []
    for n in out:
        k = match_key(n)
        if k and k not in seen:
            seen.add(k)
            res.append(n)
    return res


def latest_raw(d: Path) -> Path:
    files = sorted(d.glob("*.csv.gz"))
    if not files:
        raise SystemExit(f"財務省の生データが無い: {d}")
    return files[-1]


def official(path: Path) -> dict[str, dict]:
    """現行の財務省リストを match_key -> {name, cats} で返す。"""
    recs = mof.parse(Fetched(url=str(path), body=gzip.open(path, "rb").read()))
    out: dict[str, dict] = {}
    for r in recs:
        k = match_key(r["name"])
        e = out.setdefault(k, dict(name=r["name"], cats=set()))
        if r.get("category"):
            e["cats"].add(r["category"])
    return out


def reconcile(rows: list[dict], cur: dict[str, dict]) -> dict:
    by_key = {r["match_key"]: r for r in rows}
    targets = [r for r in rows
               if r["invalid_reason"] == M.DELISTED and not r["sources"]]
    stat = dict(対象=len(targets), 復活=0, 統合=0, 未解決=0)
    drop: set[str] = set()

    for r in targets:
        keys = [match_key(c) for c in candidates(r["display_name"])]
        hit = next((k for k in keys if k and k in cur and k != r["match_key"]),
                   None)
        if not hit:
            stat["未解決"] += 1
            continue

        variants = set(json.loads(r["variants"] or "[]")) | {r["display_name"]}
        dst = by_key.get(hit)

        if dst is not None and dst is not r:
            # 正しい形の行が既にある。登録時間と別名だけ引き継いで旧行は落とす。
            dst["first_seen_ms"] = str(min(int(dst["first_seen_ms"] or 0),
                                           int(r["first_seen_ms"] or 0)))
            dst["variants"] = json.dumps(
                sorted(set(json.loads(dst["variants"] or "[]")) | variants),
                ensure_ascii=False)
            drop.add(r["match_key"])
            stat["統合"] += 1
            continue

        # 正しい形の行が無い。この行のキーと名前を差し替えて有効に戻す。
        e = cur[hit]
        r["match_key"] = hit
        r["display_name"] = e["name"]
        r["status"] = M.STATUS_ACTIVE
        r["invalid_reason"] = ""
        r["sources"] = "財務省"
        r["categories"] = ";".join(sorted(f"財務省:{c}" for c in e["cats"]))
        r["variants"] = json.dumps(sorted(variants | {e["name"]}),
                                   ensure_ascii=False)
        M._recompute(r)
        by_key[hit] = r
        stat["復活"] += 1

    stat["rows"] = [r for r in rows if r["match_key"] not in drop]
    return stat


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--master", default=str(ROOT / "data" / "master" / "master.csv"))
    ap.add_argument("--raw", default=str(ROOT / "data" / "raw" / "mof"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    p = Path(args.master)
    with p.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    raw = latest_raw(Path(args.raw))
    cur = official(raw)
    print(f"照合元: {raw.name} ({len(cur)} 名)")

    st = reconcile(rows, cur)
    out = st.pop("rows")
    print(" / ".join(f"{k} {v}" for k, v in st.items()))
    print(f"マスター: {len(rows)} 行 -> {len(out)} 行")

    if args.dry_run:
        print("--dry-run のため書き込みなし")
        return
    with p.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=M.FIELDS)
        w.writeheader()
        w.writerows({c: r.get(c, "") for c in M.FIELDS} for r in out)
    print(f"wrote {p}")


if __name__ == "__main__":
    main()
