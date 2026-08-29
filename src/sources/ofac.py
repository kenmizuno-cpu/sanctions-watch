"""OFAC SDN / Consolidated リスト。

Sanctions List Service (SLS) が現行の配信基盤。
旧 treasury.gov/ofac/downloads/ はここへリダイレクトされるが、
新ホストは User-Agent 必須で、付けないと 403 になる (fetch.py で対応)。

SDN.CSV は主名称、ALT.CSV は別名。ent_num で結合する。
"""
from __future__ import annotations

import csv
import io

from ..fetch import Fetched
from ..normalize import SRC_OFAC, clean_name, split_aliases

SOURCE = SRC_OFAC
BASE = "https://sanctionslistservice.ofac.treas.gov/api/download/"

# SLS のファイル名。提供元が変えたら env で差し替えられるようにしておく。
LISTS = {
    "ofac_sdn": dict(
        name="OFAC SDN リスト", label="SDN",
        prim=BASE + "SDN.CSV", alt=BASE + "ALT.CSV"),
    "ofac_cons": dict(
        name="OFAC Consolidated リスト", label="Consolidated",
        prim=BASE + "CONS_PRIM.CSV", alt=BASE + "CONS_ALT.CSV"),
}

# classic CSV はヘッダー行が無く、欠損は "-0-" で表される。
PRIM_COLS = ["ent_num", "name", "sdn_type", "program", "title",
             "call_sign", "vess_type", "tonnage", "grt", "vess_flag",
             "vess_owner", "remarks"]
ALT_COLS = ["ent_num", "alt_num", "alt_type", "alt_name", "alt_remarks"]
NULL = {"-0-", "", "-0- "}


class SchemaError(RuntimeError):
    pass


def _rows(text: str, cols: list[str], label: str) -> list[dict]:
    rdr = csv.reader(io.StringIO(text))
    out = []
    for i, row in enumerate(rdr):
        if not row or all(c.strip() in NULL for c in row):
            continue
        if len(row) < 2:
            continue
        if len(row) > len(cols):
            # 末尾の余剰は remarks の続きとして畳む
            row = row[:len(cols) - 1] + [",".join(row[len(cols) - 1:])]
        d = dict(zip(cols, [c.strip() for c in row]))
        # 先頭行がヘッダーだった場合(SLSが将来付けてきた場合)は捨てる
        if i == 0 and d.get("ent_num", "").lower() in {"ent_num", "entnum", "id"}:
            continue
        out.append(d)
    if not out:
        raise SchemaError(f"OFAC {label} から行を抽出できなかった。書式変更を疑う")
    return out


def parse(prim: Fetched, alt: Fetched | None, label: str) -> list[dict]:
    """SDN.CSV と ALT.CSV を ent_num で結合し、1名前1レコードに展開する。"""
    prim_rows = _rows(prim.text, PRIM_COLS, label)

    programs: dict[str, str] = {}
    out: list[dict] = []
    for r in prim_rows:
        ent = r["ent_num"]
        prog = r.get("program", "")
        prog = "" if prog in NULL else prog
        programs[ent] = prog
        raw = r.get("name", "")
        if raw in NULL:
            continue
        for n in split_aliases(clean_name(raw)):
            out.append(dict(source=SOURCE, category=f"{label}", name=n,
                            source_id=ent, program=prog))

    if alt is not None and alt.body is not None:
        for r in _rows(alt.text, ALT_COLS, f"{label} ALT"):
            raw = r.get("alt_name", "")
            if raw in NULL:
                continue
            ent = r["ent_num"]
            for n in split_aliases(clean_name(raw)):
                out.append(dict(source=SOURCE, category=f"{label}", name=n,
                                source_id=ent, program=programs.get(ent, "")))

    if not out:
        raise SchemaError(f"OFAC {label} から名前を1件も抽出できなかった")
    return out
