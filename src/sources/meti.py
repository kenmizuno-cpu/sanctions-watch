"""経産省 外国ユーザーリスト（更新監視）。

**自動監視は成立しない。** 2026年8月時点で、経産省サイトは WAF により
自動アクセスを拒否している。ページも PDF も本文を返さない。

    curl -sL https://www.meti.go.jp/policy/anpo/law09.html | wc -c   # -> 0
    curl -sL https://www.meti.go.jp/files/900018298.pdf   | wc -c   # -> 0

WAF を迂回する実装はしない。相手が明示的に拒否している以上いずれ壊れるし、
政府サイト相手にやることではない。

そのため「取れたら比較、弾かれたら記録だけ」に徹し、弾かれてもワークフローは
落とさない。更新は年1〜3回（直近は2023年12月->2025年9月で1年9ヶ月空いた）なので
監視が止まっても実害はないが、この監視のせいで毎回ワークフローが赤くなると
財務省や OFAC の本当の障害を見逃す。そちらのほうがはるかに危険。

**運用上の代替:** 経産省のメール配信サービス（対外経済カテゴリ）を購読する。
改正時にプレスリリースが配信される。人間宛の通知なので WAF とは無関係で公式。
届いたら PDF を手作業で取り込む。 https://www.meti.go.jp/main/mail.html
"""
from __future__ import annotations

import re
from urllib.parse import urljoin

from ..fetch import fetch
from ..normalize import SRC_METI

NAME = "経産省 外国ユーザーリスト（更新監視）"
SOURCE = SRC_METI
KEY = "meti"

# 最新の外国ユーザーリストが載るページ。
# law05.html は「申請、相談に関する通達」の別ページなので注意。
INDEX_URL = "https://www.meti.go.jp/policy/anpo/law09.html"

# WAF を騙しに行かない。素性を明かした上で拒否されるならそれを受け入れる。
UA = "sanctions-watch/1.0 (compliance list monitor; contact via repository issues)"

PDF_RE = re.compile(r'href="([^"]*\.pdf)"', re.I)
DATE_RE = re.compile(r"令和\s*[0-9０-９]+\s*年\s*[0-9０-９]+\s*月\s*[0-9０-９]+\s*日")

_STRIP = [
    re.compile(r"<script.*?</script>", re.S | re.I),
    re.compile(r"<style.*?</style>", re.S | re.I),
    re.compile(r"<!--.*?-->", re.S),
]


class Blocked(RuntimeError):
    """WAF に弾かれた。異常ではなく想定内の状態。"""


def signature(html: str) -> str:
    """更新判定用の署名。改正のたびに PDF のファイルIDが変わるのが合図。

    アクセスカウンタ等まで含めると毎回変わって誤検知するので、
    PDFリンクと日付表記だけを見る。
    """
    s = html
    for pat in _STRIP:
        s = pat.sub(" ", s)
    return "|".join(sorted(set(PDF_RE.findall(s))) + sorted(set(DATE_RE.findall(s))))


def check(session=None) -> dict:
    """告知ページを取得して署名を返す。弾かれたら Blocked を投げる。"""
    f = fetch(INDEX_URL, session=session, allow_conditional=False, user_agent=UA)
    html = f.text

    # 202 を返して本文ゼロ、が WAF に弾かれたときの典型
    if not html.strip():
        raise Blocked("経産省サイトが本文を返さない（WAFによる自動アクセス拒否）。"
                      "更新はメール配信サービスで把握し、PDFは手作業で取り込むこと")
    sig = signature(html)
    if not sig:
        raise Blocked("ページは取得できたが PDF リンクも日付表記も見つからない。"
                      "ページ構成が変わった可能性がある")
    return dict(
        signature=sig,
        pdfs=[urljoin(INDEX_URL, u) for u in sorted(set(PDF_RE.findall(html)))],
        dates=sorted(set(DATE_RE.findall(html))),
        fetched=f,
    )
