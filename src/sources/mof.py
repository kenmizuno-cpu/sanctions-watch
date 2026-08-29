"""財務省 資産凍結等対象者リスト。

ファイル名に日付が入って毎回変わる (shisantouketsu20260828.csv) ため、
固定URLを叩き続けると更新を永遠に取り逃す。一覧ページを取得して
正規表現でファイル名を拾う。
"""
from __future__ import annotations

import csv
import io
import re
from urllib.parse import urljoin

from ..fetch import Fetched, fetch
from ..normalize import SRC_MOF, canonical_category, clean_name, split_aliases

NAME = "財務省 資産凍結等対象者一覧"
SOURCE = SRC_MOF
KEY = "mof"

INDEX_URL = ("https://www.mof.go.jp/policy/international_policy/gaitame_kawase/"
             "gaitame/economic_sanctions/list.html")
FILE_RE = re.compile(r'href="([^"]*?shisantouketsu(\d{8})\.csv)"', re.I)
# ページ冒頭の「令和8年8月28日現在」表記。更新判定の補助に使う。
ASOF_RE = re.compile(r"(令和\s*\d+\s*年\s*\d+\s*月\s*\d+\s*日)\s*現在")

# 想定する列名。ここに無い列が来たら例外を投げて止める。
COL_CATEGORY = ("区分",)
COL_ID = ("番号",)
COL_TYPE = ("個人・団体", "個人・団体の別", "個人団体")
COL_NAME_JA = ("氏名（日本語）", "氏名・団体名（日本語）", "名称（日本語）")
COL_NAME_EN = ("氏名（英語）", "氏名・団体名（英語）", "名称（英語）")
COL_ALIAS = ("別名", "別称", "別名（日本語）", "別名（英語）")


class SchemaError(RuntimeError):
    """提供元が列構成を変えたときに投げる。壊れたデータで静かに上書きしない。"""


def discover(session=None, prev=None) -> tuple[str, str, Fetched]:
    """一覧ページから最新CSVのURLと基準日を得る。"""
    idx = fetch(INDEX_URL, session=session, allow_conditional=False)
    html = idx.text
    m = FILE_RE.search(html)
    if not m:
        raise SchemaError(
            "財務省の一覧ページから shisantouketsuYYYYMMDD.csv のリンクを見つけられなかった。"
            "ページ構成が変わった可能性がある")
    url = urljoin(INDEX_URL, m.group(1))
    asof = ASOF_RE.search(html)
    return url, (asof.group(1) if asof else m.group(2)), idx


def _pick(header: list[str], candidates: tuple[str, ...], required: bool = True) -> str | None:
    norm = {re.sub(r"\s", "", h): h for h in header}
    for c in candidates:
        key = re.sub(r"\s", "", c)
        if key in norm:
            return norm[key]
    if required:
        raise SchemaError(
            f"財務省CSVに想定した列が無い。探した列={candidates} / 実際の列={header}")
    return None


def parse(f: Fetched) -> list[dict]:
    """正規化済みレコードのリストを返す。1名前1レコードに展開する。"""
    rows = list(csv.DictReader(io.StringIO(f.text)))
    if not rows:
        raise SchemaError("財務省CSVが空だった")

    header = list(rows[0].keys())
    c_cat = _pick(header, COL_CATEGORY)
    c_id = _pick(header, COL_ID)
    c_ja = _pick(header, COL_NAME_JA, required=False)
    c_en = _pick(header, COL_NAME_EN, required=False)
    if not (c_ja or c_en):
        raise SchemaError(f"財務省CSVに氏名列が無い。実際の列={header}")
    alias_cols = [h for h in header if any(a in h for a in COL_ALIAS)]

    out: list[dict] = []
    seen_ids: set[str] = set()
    for r in rows:
        rid = clean_name(r.get(c_id, ""))
        # 同一人物が複数の告示に載るため、番号の重複自体は正常。
        # ただし同じ番号で氏名が違えば列ズレを疑う。
        cat = canonical_category(re.sub(r"^\s*[0-9０-９]+\s*[.．]\s*", "",
                                        clean_name(r.get(c_cat, ""))))
        names: list[str] = []
        for col in ([c_ja, c_en] + alias_cols):
            if not col:
                continue
            raw = clean_name(r.get(col, ""))
            if not raw or raw in {"-", "‐", "―", "なし"}:
                continue
            for part in re.split(r"[;；]", raw):
                names.extend(split_aliases(part))

        for n in names:
            out.append(dict(source=SOURCE, category=cat, name=n,
                            source_id=rid, listed=clean_name(r.get("告示日付", ""))))
        seen_ids.add(rid)

    if not out:
        raise SchemaError("財務省CSVから名前を1件も抽出できなかった。列構成の変更を疑う")
    return out
