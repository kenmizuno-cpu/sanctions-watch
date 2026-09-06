"""財務省 資産凍結等対象者リスト。

実データ (shisantouketsu20260828.csv, 32列2866行) に合わせて実装。

踏んだ落とし穴:
  - ファイル名に日付が入って毎回変わるため、固定URLでは更新を取り逃す
  - 別名の区切りは全角の `；`。しかも括弧の内側にも `；` が出る
        ムハマド・イブラヒム・マッカウィ(生年月日1960/4/11； 1963/4/11、…)
    素朴に分割すると名前が真っ二つになるので、括弧の深さ0でだけ切る
  - 別名に `（生年月日…、出生地…、国籍…）` の説明文が付く。名前ではないので落とす
  - `区分` は番号だけでカテゴリ名が無い。しかも財務省は新カテゴリを挿入するたび
    以降を繰り下げる (中央アフリカ 34->37、イエメン 36->39 を実データで確認)。
    対応表は data/kubun_map.json で管理し、ずれたら detect_drift() で止める
"""
from __future__ import annotations

import csv
import io
import json
import re
from collections import defaultdict
from pathlib import Path
from urllib.parse import urljoin

from ..fetch import Fetched, fetch
from ..normalize import SRC_MOF, canonical_category, clean_name, match_key

NAME = "財務省 資産凍結等対象者一覧"
SOURCE = SRC_MOF
KEY = "mof"

INDEX_URL = ("https://www.mof.go.jp/policy/international_policy/gaitame_kawase/"
             "gaitame/economic_sanctions/list.html")
FILE_RE = re.compile(r'href="([^"]*?shisantouketsu(\d{8})\.csv)"', re.I)
ASOF_RE = re.compile(r"(令和\s*\d+\s*年\s*\d+\s*月\s*\d+\s*日)\s*現在")

KUBUN_MAP = Path(__file__).resolve().parent.parent.parent / "data" / "kubun_map.json"

COL_KUBUN = "区分"
COL_ID = "番号"
COL_NOTICE = "告示日付"
COL_MOFA_NOTICE = "外務省告示情報"

# 名前として採用する列。称号(ムラー/ハッジ)と役職は敬称・肩書きであって
# 名前ではないため、意図的に除外している。
NAME_COLS = [
    "氏名（日本語）", "氏名（英語）",
    "別名・別称（日本語）", "別名・別称（英語）",
    "旧称（日本語）", "旧称（英語）",
    "確定に十分でない別名（日本語）", "確定に十分でない別名（英語）",
]

# 名前の後ろに付く説明文。これが入っている括弧は丸ごと落とす。
_DESCRIPTOR = re.compile(
    r"生年月日|出生地|国籍|旅券|passport|original\s*script|"
    r"\bDOB\b|\bPOB\b|Nationality|\d{4}\s*年\s*\d+\s*月\s*\d+\s*日\s*生",
    re.I)

NULL_NAMES = {"-", "‐", "―", "ー", "なし", "不明", "N/A", "n/a"}

# 財務省CSVの「外務省告示情報」には、国連等の一次情報から転記された
# 原綴りが `original script:` として明示される。
#
# これは Arabic / Cyrillic 等による実スクリーニングに重要なので、
# 自由文全体を名前として解析するのではなく、この明示ラベルだけを
# 保守的に抽出する。
_ORIGINAL_SCRIPT_RE = re.compile(
    r"original\s*(?:script|scipt)\s*[：:]\s*",
    re.I,
)

_ORIGINAL_SCRIPT_FIELD_MARKERS = (
    "(a.k.a", "（a.k.a",
    "(f.k.a", "（f.k.a",
    "(n.k.a", "（n.k.a",
    "称号：", "称号:",
    "役職：", "役職:",
    "生年月日：", "生年月日:",
    "出生地：", "出生地:",
    "国籍：", "国籍:",
    "旅券番号：", "旅券番号:",
    "ＩＤ番号：", "ＩＤ番号:",
    "所在地：", "所在地:",
    "国連制裁委員会",
)


def _has_native_script(value: str) -> bool:
    """original script として採用可能な非Latin原綴りを含むか。"""
    for ch in value:
        o = ord(ch)

        # Arabic / Arabic supplement / presentation forms
        if (
            0x0600 <= o <= 0x06FF
            or 0x0750 <= o <= 0x077F
            or 0x08A0 <= o <= 0x08FF
            or 0xFB50 <= o <= 0xFDFF
            or 0xFE70 <= o <= 0xFEFF
        ):
            return True

        # Cyrillic
        if (
            0x0400 <= o <= 0x052F
            or 0x2DE0 <= o <= 0x2DFF
            or 0xA640 <= o <= 0xA69F
        ):
            return True

        # CJK / Kana
        if (
            0x3040 <= o <= 0x30FF
            or 0x3400 <= o <= 0x4DBF
            or 0x4E00 <= o <= 0x9FFF
            or 0xF900 <= o <= 0xFAFF
        ):
            return True

        # Hangul
        if (
            0xAC00 <= o <= 0xD7AF
            or 0x1100 <= o <= 0x11FF
        ):
            return True

        # Hebrew
        if 0x0590 <= o <= 0x05FF:
            return True

        # Devanagari
        if 0x0900 <= o <= 0x097F:
            return True

        # Greek
        if 0x0370 <= o <= 0x03FF:
            return True

    return False


def _original_script_stop(tail: str) -> tuple[int, str]:
    """original script 値の安全な終端を返す。"""
    stops: list[tuple[int, str]] = []

    for closer in (")", "）"):
        pos = tail.find(closer)
        if pos >= 0:
            stops.append((pos, "close_parenthesis"))

    low = tail.casefold()

    for marker in _ORIGINAL_SCRIPT_FIELD_MARKERS:
        pos = low.find(marker.casefold())
        if pos >= 0:
            stops.append((pos, "field_marker"))

    if not stops:
        return len(tail), "no_explicit_terminator"

    return min(stops, key=lambda x: x[0])


def extract_original_script_names(row: dict) -> list[str]:
    """外務省告示情報の明示 `original script:` 原綴りだけを抽出する。

    安全条件:
      - original script / original scipt の明示ラベル
      - 非Latin native script を含む
      - 2〜200文字
      - 閉じ括弧または既知フィールドマーカーによる明示終端

    条件を満たさない自由文は名前として取り込まない。
    """
    notice = clean_name(row.get(COL_MOFA_NOTICE, ""))

    if not notice:
        return []

    out: list[str] = []

    for marker in _ORIGINAL_SCRIPT_RE.finditer(notice):
        tail = notice[marker.end():].lstrip()

        end, termination = _original_script_stop(tail)

        # 終端不明の自由文は fail-closed。
        if termination == "no_explicit_terminator":
            continue

        value = clean_name(tail[:end])

        value = value.strip(
            " \t\r\n"
            ";；"
            ",，、"
            ":："
        )

        # original script: (xxx) のような外側ラッパのみ除去。
        value = value.lstrip("(（").strip()
        value = value.rstrip(")）").strip()

        # 財務省の一部レコードには
        #
        #   originalscript: （original script: 原綴り）
        #
        # のような二重ラベルが存在する。
        #
        # 外側markerから抽出した値が再びoriginal-script markerで
        # 始まる場合、その値自体は名前ではない。
        # 内側markerは正規表現iteratorが別途処理するのでここでは捨てる。
        if _ORIGINAL_SCRIPT_RE.match(value):
            continue

        if not value:
            continue

        if not (2 <= len(value) <= 200):
            continue

        if not _has_native_script(value):
            continue

        if value in NULL_NAMES:
            continue

        out.append(value)

    # 同一source row内でのみmatch_key重複排除。
    seen: set[str] = set()
    result: list[str] = []

    for name in out:
        key = match_key(name)

        if key and key not in seen:
            seen.add(key)
            result.append(name)

    return result


class SchemaError(RuntimeError):
    """提供元が列構成や区分番号を変えたときに投げる。

    壊れたデータで静かにマスターを上書きするのが一番怖いので、
    疑わしければ止める。

    fetched:
        構造変更を検知するまでに取得できていた一次ソース。
        HTTP Status / URL / SHA256等の監査証跡を失わないため保持する。
    """

    def __init__(self, message: str, *, fetched=None):
        super().__init__(message)
        self.fetched = fetched


# ---------------------------------------------------------------- 取得

def discover(session=None) -> tuple[str, str, Fetched]:
    idx = fetch(INDEX_URL, session=session, allow_conditional=False)
    html = idx.text
    m = FILE_RE.search(html)
    if not m:
        raise SchemaError(
            "財務省の一覧ページから shisantouketsuYYYYMMDD.csv のリンクを見つけられなかった。"
            "ページ構成が変わった可能性がある",
            fetched=idx,
        )
    url = urljoin(INDEX_URL, m.group(1))
    asof = ASOF_RE.search(html)
    return url, (asof.group(1) if asof else m.group(2)), idx


# ---------------------------------------------------------------- 名前の分解

def split_top_level(s: str) -> list[str]:
    """括弧の深さ0にある `；` `;` でだけ分割する。

    括弧の内側の `；` は生年月日の区切りとして使われているため、
    深さを見ないと名前が壊れる。
    """
    out, buf, depth = [], [], 0
    for ch in s:
        if ch in "(（":
            depth += 1
        elif ch in ")）":
            depth = max(0, depth - 1)
        if ch in ";；" and depth == 0:
            out.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    out.append("".join(buf))
    return [x for x in (p.strip() for p in out) if x]


def strip_descriptor(name: str) -> str:
    """名前の後ろに付く説明的な括弧を落とす。

        サイフ・アル・アドル（生年月日1963/4/11、出生地エジプト、国籍エジプト）
          -> サイフ・アル・アドル
    """
    s = clean_name(name)
    prev = None
    while prev != s and s:
        prev = s
        m = re.search(r"[（(]([^（()）]*)[)）]?\s*$", s)
        if m and _DESCRIPTOR.search(m.group(1)):
            s = clean_name(s[:m.start()])
    return s


def extract_names(row: dict) -> list[str]:
    """1レコードからscreening nameを全て取り出す。"""
    out: list[str] = []

    # 構造化された氏名・別名・旧称等。
    for col in NAME_COLS:
        raw = clean_name(row.get(col, ""))
        if not raw or raw in NULL_NAMES:
            continue
        for part in split_top_level(raw):
            n = strip_descriptor(part)
            if n and n not in NULL_NAMES and len(n) > 1:
                out.append(n)

    # 外務省告示情報に明示された非Latin original script。
    #
    # 自由文からの推測抽出は禁止し、
    # extract_original_script_names() の安全条件を満たすものだけ。
    out.extend(extract_original_script_names(row))

    seen, res = set(), []

    for n in out:
        k = match_key(n)
        if k and k not in seen:
            seen.add(k)
            res.append(n)

    return res


# ---------------------------------------------------------------- 区分

def load_kubun_map(path=None) -> dict:
    p = Path(path) if path else KUBUN_MAP
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8")).get("map", {})


def detect_drift(records: list, master: dict,
                 min_samples: int = 8, threshold: float = 0.5) -> list:
    """区分番号の繰り下がりを検出する。

    マスターに既にある名前の財務省カテゴリと、今回の対応表が付けた
    カテゴリを突き合わせ、多数派が食い違えば番号がずれたと判断する。
    黙って全件を別カテゴリに書き換えるより、止めて人が直すほうが安全。
    """
    agree = defaultdict(lambda: [0, 0])
    labels = {}
    for r in records:
        row = master.get(match_key(r["name"]))
        if not row:
            continue
        existing = [c.split(":", 1)[1]
                    for c in str(row.get("categories", "")).split(";")
                    if c.startswith(SOURCE + ":")]
        if not existing:
            continue
        k = r["kubun"]
        labels[k] = r["category"]
        agree[k][1] += 1
        if r["category"] and r["category"] in existing:
            agree[k][0] += 1

    problems = []
    for k in sorted(agree, key=int):
        ok, total = agree[k]
        if total >= min_samples and ok / total < threshold:
            problems.append(
                "区分{}（対応表では「{}」）: マスターの既存カテゴリと一致したのは "
                "{}/{} 件のみ。財務省が区分番号を繰り下げた可能性がある".format(
                    k, labels.get(k) or "未定義", ok, total))
    return problems


# ---------------------------------------------------------------- パース

def parse(f: Fetched, kubun_map=None) -> list:
    """正規化済みレコードのリストを返す。1名前1レコードに展開する。"""
    rows = list(csv.DictReader(io.StringIO(f.text)))
    if not rows:
        raise SchemaError("財務省CSVが空だった")

    header = [h.strip() for h in rows[0].keys() if h]
    need = [COL_KUBUN, COL_ID, COL_MOFA_NOTICE] + NAME_COLS[:2]
    missing = [c for c in need if c not in header]
    if missing:
        raise SchemaError(
            "財務省CSVに想定した列が無い。欠けている列={} / 実際の列={}".format(
                missing, header))

    kmap = kubun_map if kubun_map is not None else load_kubun_map()
    out = []
    unknown = set()

    for r in rows:
        kubun = clean_name(r.get(COL_KUBUN, ""))
        cat = canonical_category(kmap.get(kubun, ""))
        if kubun and kubun not in kmap:
            unknown.add(kubun)
        rid = clean_name(r.get(COL_ID, ""))
        for n in extract_names(r):
            out.append(dict(source=SOURCE, category=cat, name=n,
                            source_id=rid, kubun=kubun,
                            listed=clean_name(r.get(COL_NOTICE, ""))))

    if not out:
        raise SchemaError("財務省CSVから名前を1件も抽出できなかった。列構成の変更を疑う")
    if unknown:
        # 新設カテゴリ。全体は止めず、カテゴリ名なしで取り込んで通知する。
        print("  警告: 対応表に無い区分 {} を検出。data/kubun_map.json の更新が必要"
              .format(sorted(unknown, key=int)), flush=True)
    return out
