"""既存の受取人名リスト(xlsx)からマスターをブートストラップする。

初回のみ実行する。ローカルで走らせて件数を目視確認してからコミットすること。
いきなり Actions で回すと、パース結果がおかしくてもそのまま
マスターとして確定してしまう。

  python -m src.import_legacy --src path/to/black_receiver_name_all.xlsx
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import pandas as pd

from . import master as M
from .normalize import (canonical_display_name, clean_name,
                        is_trailing_unknown_artifact, match_key, needs_review,
                        parse_remark_multi, render_remark, split_aliases, validate)

ROOT = Path(__file__).resolve().parent.parent
LOG_COLS = ["行", "種別", "元の値", "変更後", "理由"]


def build(src_xlsx: str, ts: int) -> tuple[list[dict], list[dict]]:
    df = pd.read_excel(src_xlsx, dtype=str).fillna("")
    log: list[dict] = []
    rows: list[dict] = []

    for i, r in df.iterrows():
        raw = r["受取人名"]
        excel_row = i + 2
        pairs = parse_remark_multi(r["備考"])
        was_invalid = str(r["有効なのか"]).strip() == "無効"
        pieces = split_aliases(raw)

        if len(pieces) > 1:
            log.append(dict(行=excel_row, 種別="別名分解", 元の値=raw,
                            変更後=" / ".join(pieces),
                            理由="1セルに複数名または別名マーカーが混入していたため分解。"
                                 "無効化すると制裁対象を照合から取りこぼすため分割して全て残す"))
        elif pieces and pieces[0] != clean_name(raw):
            log.append(dict(行=excel_row, 種別="書式整形", 元の値=raw, 変更後=pieces[0],
                            理由="余分な括弧・船舶メタデータ・空白を除去"))
        elif clean_name(raw) != raw:
            log.append(dict(行=excel_row, 種別="空白整形", 元の値=repr(raw),
                            変更後=clean_name(raw),
                            理由="前後の空白・全角スペース・連続空白を整理"))

        for name in pieces:
            display = canonical_display_name(name)
            if display != name:
                log.append(dict(
                    行=excel_row, 種別="引用符除去", 元の値=name, 変更後=display,
                    理由="外側の引用符は別名を示す書式であり、名称の一部ではないため除去"))
            name = display
            if is_trailing_unknown_artifact(name):
                log.append(dict(
                    行=excel_row, 種別="削除", 元の値=name, 変更後="（削除）",
                    理由="original script等のメタ情報「不明」が名称末尾に混入した旧データ"))
                continue
            reason = validate(name)
            review = None if reason else needs_review(name)
            if reason:
                log.append(dict(行=excel_row, 種別="無効化", 元の値=name,
                                変更後="（無効化）", 理由=reason))
            elif review:
                log.append(dict(行=excel_row, 種別="要確認", 元の値=name,
                                変更後="（有効のまま）", 理由=review))
            rows.append(dict(
                key=match_key(name), name=name, orig=raw, pairs=pairs,
                first=str(r["登録時間"]).strip(), last=str(r["更新時間"]).strip(),
                invalid=bool(reason) or was_invalid,
                reason=reason or ("既存データで無効指定" if was_invalid else ""),
                review=review or "", split=len(pieces) > 1, order=i))

    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(row["key"], []).append(row)

    out: list[dict] = []
    for key, mem in groups.items():
        mem.sort(key=lambda m: (int(m["first"] or 0), m["order"]))
        head = mem[0]
        first = min(int(m["first"] or 0) for m in mem)
        variants = sorted({m["name"] for m in mem})
        invalid = any(m["invalid"] for m in mem)
        reason = next((m["reason"] for m in mem if m["reason"]), "")
        changed = (len(mem) > 1 or invalid or any(m["split"] for m in mem)
                   or head["name"] != head["orig"])

        if len(mem) > 1:
            log.append(dict(行="", 種別="名寄せ統合", 元の値=" / ".join(variants),
                            変更後=head["name"],
                            理由=f"表記ゆれ（大文字小文字・記号・全角半角）で同一と判定し"
                                 f"{len(mem)}行を統合。出所: "
                                 f"{'、'.join(sorted({s for m in mem for s, _ in m['pairs']}))}"))

        # categories は master.py の規約に合わせ "出所:カテゴリ" で持つ
        cats = {f"{s}:{c}" for m in mem for s, c in m["pairs"] if c}
        out.append(dict(
            match_key=key, display_name=head["name"],
            status=M.STATUS_INACTIVE if invalid else M.STATUS_ACTIVE,
            risk_type=M.RISK_TYPE, risk_level=M.RISK_LEVEL,
            first_seen_ms=first, last_updated_ms=ts if changed else int(head["last"] or first),
            sources=";".join(sorted({s for m in mem for s, _ in m["pairs"]})),
            categories=";".join(sorted(cats)),
            remark=render_remark([p for m in mem for p in m["pairs"]]),
            invalid_reason=reason,
            review_flag=next((m["review"] for m in mem if m["review"]), ""),
            variants=json.dumps(variants, ensure_ascii=False),
        ))
    out.sort(key=lambda d: d["match_key"])
    return out, log


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", default=str(ROOT / "data" / "master" / "master.csv"))
    ap.add_argument("--log", default=str(ROOT / "data" / "master" / "cleanup_log.csv"))
    args = ap.parse_args()

    ts = M.now_ms()
    rows, log = build(args.src, ts)

    p = Path(args.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=M.FIELDS)
        w.writeheader()
        w.writerows(rows)

    lp = Path(args.log)
    with lp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LOG_COLS)
        w.writeheader()
        w.writerows(log)

    active = sum(1 for r in rows if r["status"] == M.STATUS_ACTIVE)
    review = sum(1 for r in rows if r["review_flag"])
    print(f"マスター {len(rows)} 行 (有効 {active} / 無効 {len(rows)-active} / 要確認 {review})")
    print(f"クリーンアップ {len(log)} 件 -> {lp}")
    srcs: dict[str, int] = {}
    for r in rows:
        for s in r["sources"].split(";"):
            if s:
                srcs[s] = srcs.get(s, 0) + 1
    for k, v in sorted(srcs.items(), key=lambda x: -x[1]):
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
