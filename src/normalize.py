"""
制裁リストマスターの正規化モジュール.

このモジュールは GitHub Actions 側でも再利用する.
  - match_key()   : 名寄せ用の正規化キー生成
  - clean_name()  : A列に出す表示名のクリーンアップ
  - validate()    : ゴミ行の判定
  - parse_remark(): 旧備考(113書式) -> (source, category) への分類
  - render_remark(): (source, category) の集合 -> G列文字列
"""
from __future__ import annotations

import re
import unicodedata

# ---------------------------------------------------------------- 正規化

# 見た目が同じで内部コードが違う記号を1つに寄せる
_PUNCT = str.maketrans({
    "\u2018": "'", "\u2019": "'",           # ‘ ’ -> '
    "\u201c": '"', "\u201d": '"',           # “ ” -> "
    "\u2010": "-", "\u2011": "-",           # ‐ ‑ -> -
    "\u2012": "-", "\u2013": "-", "\u2014": "-",  # ‒ – — -> -
    "\u2015": "-", "\u30fc": "-", "\uff0d": "-",  # ― ー －  -> -
    "\u00b4": "'", "\u0060": "'",           # ´ ` -> '
})

_WS = re.compile(r"[\s\u3000\u200b\ufeff]+")


def match_key(name: str) -> str:
    """名寄せ用キー. 表示には使わない.

    NFKC で全角英数/半角カナを統一し, 記号ゆれを吸収し,
    空白を全除去して casefold する.
    実データ検証では 83,733 件 -> 74,792 件に統合され, 誤統合は 0 件だった.
    """
    s = unicodedata.normalize("NFKC", str(name)).translate(_PUNCT)
    return _WS.sub("", s).casefold()


def clean_name(name: str) -> str:
    """A列に出す表示名. 原文をなるべく保つ最小限の掃除のみ.

    NFKC はかけない. 全角の団体名や Ⅰ/Ⅱ などのローマ数字を壊すため.
    """
    s = str(name).replace("\u200b", "").replace("\ufeff", "")
    s = s.replace("\u3000", " ")          # 全角スペース -> 半角
    s = re.sub(r"[\r\n\t]+", " ", s)      # 改行/タブ -> スペース
    s = re.sub(r" {2,}", " ", s)          # 連続スペースを1つに
    return s.strip()


# ---------------------------------------------------------------- 検証

# Excel の日付シリアル値が漏れたもの
_EXCEL_SERIAL = re.compile(r"^\s*[0-9]{5}\s*$")

# CJK を含むか（2文字でも正名たりうる。张伟 のような実在名を誤って落とさないため）
_HAS_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def validate(name: str) -> str | None:
    """投入不可なら理由文字列, 問題なければ None を返す.

    無効化は「どう解釈しても制裁対象名になりえない」ものに限定する.
    取りこぼし(偽陰性)は誤検知より重大なため, 判断が割れるものは
    needs_review() 側に回して人が見る.
    """
    s = clean_name(name)
    if not s:
        return "空文字または空白のみ"
    if _EXCEL_SERIAL.fullmatch(s):
        return f"Excelの日付シリアル値が名前として混入した疑い（{s}）"
    if re.fullmatch(r"[0-9]+", s):
        return f"数字のみで人名・団体名として成立しない（{s}）"
    if re.fullmatch(r"[\W_]+", s, flags=re.UNICODE):
        return "記号のみで構成されている"
    return None


def needs_review(name: str) -> str | None:
    """有効のまま残すが人の確認が要るもの. 理由文字列 or None."""
    s = clean_name(name)
    if _HAS_CJK.search(s):
        if len(s) <= 1:
            return f"1文字のため照合時に誤検知が多発する見込み（{s}）"
    elif len(s) <= 3:
        return f"{len(s)}文字と短く、照合時に誤検知が多発する見込み（{s}）"
    if _HAS_ALIAS_MARKER.search(s):
        return "別名マーカーを分解しきれなかった。手動で名前を切り出す必要がある"
    if len(s) > 120:
        return f"{len(s)}文字と異常に長い。複数名が1セルに連結された疑い"
    return None


# ---------------------------------------------------------------- 別名の分解

SEP = "\x00"  # 分解用の内部区切り

# OFAC の船舶エントリ末尾: (呼出符号) 船種 ... flag
_VESSEL_TAIL = re.compile(
    r"\s*[（(][A-Z0-9]{4,8}[)）]\s*"
    r"(?:Crude Oil|Products|Chemical|Bulk|LPG|LNG|Oil|Cargo|Container|Refrigerated|General|Vehicle)?\s*"
    r"(?:Tanker|Carrier|Vessel|Ship|Cargo)\b.*?\bflag\s*$", re.I)

_PREFIX_ALIAS_NO = re.compile(r"^\s*[（(]\s*別名\s*[0-9０-９]*\s*[)）]?\s*")
_MARK_BESSHOU = re.compile(r"[（(]?\s*(?:別称|別名|旧称)\s*[、,]?\s*")
# ドット必須。ドット無しの `aka` を許すと Abu[baka]r のような語中に誤爆する。
# 実際 `Abdifatah Abubakar Abdi` が3分割される事故が起きた。
_MARK_AKA = re.compile(
    r"[（(]?\s*(?<![A-Za-z])(?:a\.\s?k\.\s?a|f\.\s?k\.\s?a|n\.\s?k\.\s?a)\.?\s*[.:：]?\s*(?![A-Za-z])",
    re.I)

_HAS_ALIAS_MARKER = re.compile(
    r"別名|別称|旧称|(?<![A-Za-z])(?:a\.\s?k\.\s?a|f\.\s?k\.\s?a|n\.\s?k\.\s?a)\.?(?![A-Za-z])", re.I)


def _orphan_closers_to_sep(s: str) -> str:
    """対応する開き括弧を失った閉じ括弧を区切りに変える.

    別名マーカーを区切りに置換すると、その括弧の閉じだけが残るため。
    """
    depth, out = 0, []
    for ch in s:
        if ch in "(（":
            depth += 1
            out.append(ch)
        elif ch in ")）":
            if depth > 0:
                depth -= 1
                out.append(ch)
            else:
                out.append(SEP)
        else:
            out.append(ch)
    return "".join(out)


def split_aliases(name: str) -> list[str]:
    """1セルに詰め込まれた複数名を個別の名前に分解する.

    実データで確認した混入パターン:
      （別名1）A、B、C                     -> A, B, C
      （別称、IAP RAS）IAP RAS             -> IAP RAS
      X (a.k.a. Y; a.k.a. Z)               -> X, Y, Z
      Chelyabinsk-70， a.k.a. Y            -> Chelyabinsk-70, Y
      MS ANGIA (a.k.a. GATHER VIEW) (T7AX8) Crude Oil Tanker San Marino flag
                                           -> MS ANGIA, GATHER VIEW
    分解できなければ元の名前1件だけを返す(取りこぼしを避けるため)。
    """
    s = clean_name(name)
    if not s:
        return []

    stripped = _VESSEL_TAIL.sub("", s).strip()

    # 別名マーカーが無い行は絶対に分割しない。
    # `“ズベイル、アブ”` のような姓名区切りの読点を別名と誤認すると
    # 存在しない名前を作り出してしまうため。括弧外しだけ行う。
    if not _HAS_ALIAS_MARKER.search(stripped):
        unwrapped, _ = strip_wrapping_parens(stripped)
        return [unwrapped] if unwrapped else [s]

    s = _PREFIX_ALIAS_NO.sub("", stripped)
    s = _MARK_BESSHOU.sub(SEP, s)
    s = _MARK_AKA.sub(SEP, s)
    s = _orphan_closers_to_sep(s)

    parts = re.split(rf"[{SEP}、，;；]", s)

    # 括弧が閉じていない断片は、括弧の位置で更に割る
    # `Igor Chayka (Chaika` -> `Igor Chayka`, `Chaika`
    expanded: list[str] = []
    for p in parts:
        if p.count("(") + p.count("（") > p.count(")") + p.count("）"):
            expanded += re.split(r"[（(]", p)
        else:
            expanded.append(p)

    seen, res = set(), []
    for p in expanded:
        p = clean_name(p).strip("　 ,;；、（(")
        if p.count("(") + p.count("（") < p.count(")") + p.count("）"):
            p = p.rstrip(")）").strip()
        p = clean_name(p)
        if p and p not in seen:
            seen.add(p)
            res.append(p)
    return res or [clean_name(name)]


def strip_wrapping_parens(name: str) -> tuple[str, bool]:
    """名前全体が括弧で囲まれている場合に外す. (結果, 外したか) を返す."""
    s = clean_name(name)
    m = re.fullmatch(r"[（(]\s*(.+?)\s*[)）]", s)
    if m and not re.search(r"[（(]", m.group(1)):
        return m.group(1), True
    return s, False


# ---------------------------------------------------------------- 備考の分類

SRC_MOF = "財務省"
SRC_OFAC = "OFAC"
SRC_METI = "経産省"
SRC_MOFA = "外務省"
SRC_UK = "UK FCDO"
SRC_UNKNOWN = "出所不明"

# 先頭の "N." を落とす. 財務省は新カテゴリ挿入のたびに以降を繰り下げるため,
# 番号を保持すると再採番のたびに数千行が偽の「変更」判定になる.
_LEADING_NO = re.compile(r"^\s*[0-9０-９]+\s*[.．]\s*")
_MULTI_NO = re.compile(r"^\s*(?:[0-9０-９]+\s*[.．]\s*)+")


def _strip_category_number(cat: str) -> str:
    """先頭および複合カテゴリの各要素から番号を落とす.

    `タリバーン関係者等、3.テロリスト等 (1)` のように読点で連結された
    複合カテゴリでは2つ目以降にも番号が残るため、要素ごとに処理する.
    """
    parts = [p for p in re.split(r"[、,]", cat)]
    out = []
    for p in parts:
        p = _MULTI_NO.sub("", p)
        p = _LEADING_NO.sub("", p).strip()
        if p:
            out.append(p)
    return "、".join(out)


def _balance_parens(s: str) -> str:
    """括弧の不整合を直す.

    実データには開き括弧が半角 `(` で閉じが全角 `）` という混在がある
    (`30.ロシア連邦(特定銀行）`). 全角半角を区別せず対応を取る.
    """
    opens = s.count("(") + s.count("（")
    closes = s.count(")") + s.count("）")
    if opens > closes:
        s += ")" * (opens - closes)
    elif closes > opens:
        s = "(" * (closes - opens) + s
    return s


# 同一カテゴリの表記ゆれ. 左を右に寄せる.
_CATEGORY_ALIASES = {
    "タリバーン関係者": "タリバーン関係者等",
    "タリバーン関係者等リストの改正": "タリバーン関係者等",
    "ハイチ": "ハイチ共和国",
    "ハイチにおける平和等を脅かす行為等に関与した者等": "ハイチ共和国",
}


def canonical_category(cat: str) -> str:
    """カテゴリ名の表記を揃える. 番号除去後に適用する."""
    s = str(cat).strip()
    if not s:
        return ""
    # 括弧は半角に統一 (（個人） -> (個人))。ローマ数字は NFKC で潰さないよう温存
    s = s.replace("（", "(").replace("）", ")")
    s = s.replace(";", "；").replace("；", "；")
    s = _WS.sub(" ", s).strip()
    s = re.sub(r"\s*\(\s*", "(", s)      # `テロリスト等 (2)` -> `テロリスト等(2)`
    s = re.sub(r"\s*\)\s*", ")", s)
    s = re.sub(r"\s*？\s*", "；", s)
    s = re.sub(r"\s*；\s*", "；", s)      # `(協調 ； 団体)` -> `(協調；団体)`
    s = _balance_parens(s)
    parts = [_CATEGORY_ALIASES.get(p.strip(), p.strip())
             for p in s.split("、") if p.strip()]
    # 重複除去（順序保持）
    seen, out = set(), []
    for p in parts:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return "、".join(out)


def parse_remark(remark: str) -> tuple[str, str]:
    """旧G列の値を (source, category) に分類する.

    実データ 113 種を対象に設計. category が空文字なら分類名なし.
    """
    v = str(remark).strip()
    if not v:
        return SRC_UNKNOWN, ""

    u = v.upper()

    # 明示的な出所表記 (2025年の一括取込分)
    if "OFAC" in u or "OFCA" in u or "OFAC" in u.replace("OFAC", "OFAC"):
        return SRC_OFAC, ""
    if "FCDO" in u:
        return SRC_UK, ""
    if "METI" in u or "経産" in v:
        return SRC_METI, ""
    if "外務省告示" in v:
        return SRC_MOFA, canonical_category(v)
    if "誤登録" in v:
        return SRC_UNKNOWN, "誤登録"
    # 「財務諸表　タリバーン関係者」は「財務省」のタイポ
    if "財務省" in v or "財務諸表" in v:
        cat = re.sub(r"財務諸表|財務省", "", v).strip("　 ")
        return SRC_MOF, canonical_category(_strip_category_number(cat))

    # 制裁リスト（...) 書式 (2022年世代)
    m = re.fullmatch(r"制裁リスト\s*[（(]\s*(.*?)\s*[)）]?\s*", v)
    if m:
        return SRC_MOF, canonical_category(_strip_category_number(m.group(1)))

    # 先頭が番号のカテゴリ書式 (2023-24年世代)
    if re.match(r"^\s*[0-9０-９]+\s*[.．]", v):
        return SRC_MOF, canonical_category(_strip_category_number(v))

    # 外為法の日付表記 — 財務省・経産省いずれの告示か備考からは特定できない
    if "外為法" in v:
        return SRC_UNKNOWN, v

    # 番号なしのカテゴリ名 (2025年世代)
    return SRC_MOF, canonical_category(v)


# ---------------------------------------------------------------- 出力書式

_SRC_ORDER = {SRC_MOF: 0, SRC_METI: 1, SRC_MOFA: 2, SRC_OFAC: 3,
              SRC_UK: 4, SRC_UNKNOWN: 9}

KNOWN_SOURCES = set(_SRC_ORDER)

# 自分で生成した G列 を読み戻すためのパターン。
# 制裁リスト（財務省：タリバーン関係者等／OFAC：SDN） のような形。
_CANONICAL = re.compile(r"^\s*制裁リスト\s*[（(]\s*(.+?)\s*[)）]\s*$")


def parse_remark_multi(remark: str) -> list[tuple[str, str]]:
    """G列を (source, category) のリストに戻す。

    まず自分が生成した正規書式かを判定し、そうでなければ旧書式として
    parse_remark に回す。生成した Excel を再取り込みできないと、
    配布物からマスターを作り直せなくなる。
    """
    v = str(remark).strip()
    m = _CANONICAL.match(v)
    if m:
        parts = [p.strip() for p in m.group(1).split("／") if p.strip()]
        out: list[tuple[str, str]] = []
        ok = bool(parts)
        for p in parts:
            src, _, cats = p.partition("：")
            src = src.strip()
            if src not in KNOWN_SOURCES:
                ok = False
                break
            if cats.strip():
                out += [(src, canonical_category(c))
                        for c in cats.split("、") if c.strip()]
            else:
                out.append((src, ""))
        if ok:
            return out
    return [parse_remark(v)]


def render_remark(pairs) -> str:
    """(source, category) の集合を G列文字列にする.

    例: 制裁リスト（財務省：タリバーン関係者等／OFAC）
    番号は入れない. 財務省の区分番号は年単位でズレるため.
    """
    bucket: dict[str, list[str]] = {}
    for src, cat in pairs:
        bucket.setdefault(src, [])
        if cat and cat not in bucket[src]:
            bucket[src].append(cat)

    parts = []
    for src in sorted(bucket, key=lambda s: (_SRC_ORDER.get(s, 8), s)):
        cats = bucket[src]
        parts.append(f"{src}：{'、'.join(cats)}" if cats else src)
    return f"制裁リスト（{'／'.join(parts)}）" if parts else "制裁リスト"
