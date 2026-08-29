"""経産省 外国ユーザーリスト。

PDF でしか配布されておらず、機械可読な形式が存在しない。
更新は年1〜3回程度なので、PDFの自動パースは投資対効果が悪い。
告知ページとPDF自体のハッシュだけ監視し、変わったら人に通知して
手作業で取り込む。マスターの経産省分はそれまで据え置く。
"""
from __future__ import annotations

import re
from urllib.parse import urljoin

from ..fetch import Fetched, fetch
from ..normalize import SRC_METI

NAME = "経産省 外国ユーザーリスト（更新監視）"
SOURCE = SRC_METI
KEY = "meti"

INDEX_URL = "https://www.meti.go.jp/policy/anpo/law05.html"
PDF_RE = re.compile(r'href="([^"]*(?:user_list|yuza|enduser)[^"]*\.pdf)"', re.I)
DATE_RE = re.compile(r"(令和\s*\d+\s*年\s*\d+\s*月\s*\d+\s*日)")

# 監視対象はページ本文のうち更新に関係する部分だけ。
# アクセスカウンタや広告枠まで含めると毎回ハッシュが変わって誤検知する。
STRIP = [
    re.compile(r"<script.*?</script>", re.S | re.I),
    re.compile(r"<style.*?</style>", re.S | re.I),
    re.compile(r"<!--.*?-->", re.S),
    re.compile(r"\s+"),
]


def signature(html: str) -> str:
    """ページから更新判定用の安定した文字列を作る。"""
    s = html
    for pat in STRIP[:-1]:
        s = pat.sub(" ", s)
    links = sorted(set(PDF_RE.findall(s)))
    dates = sorted(set(DATE_RE.findall(s)))
    return "|".join(links + dates)


def check(session=None) -> dict:
    """告知ページを取得して、PDFリンクと日付表記の署名を返す。"""
    idx = fetch(INDEX_URL, session=session, allow_conditional=False)
    html = idx.text
    sig = signature(html)
    pdfs = [urljoin(INDEX_URL, u) for u in sorted(set(PDF_RE.findall(html)))]
    dates = sorted(set(DATE_RE.findall(html)))
    return dict(signature=sig, pdfs=pdfs, dates=dates, fetched=idx)
