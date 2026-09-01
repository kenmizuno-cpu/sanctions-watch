"""スプレッドシート取込用のCSVを書き出す。

Googleスプレッドシートの IMPORTDATA から raw.githubusercontent.com 経由で
直接読ませることを想定している。そのため:

  - 列見出しは日本語。シート上でそのまま見出しになる
  - 日時は JST の文字列。スプレッドシート側で時差を考えなくてよい
  - 小さく保つ。IMPORTDATA のサイズ上限は Google が公開しておらず、
    数MBで落ちたという報告がある。status と changes は数十〜数百KBに収める

master.csv (15MB) や latest.csv は大きくなりうるので直接読ませない。
ここで絞ったものを別途出す。
"""
from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))

DASH = Path("data") / "dashboard"

STATUS_COLS = ["出所", "状態", "最終チェック", "最終更新", "件数", "内容ハッシュ"]
CHANGE_COLS = ["検知日時", "出所", "種別", "受取人名", "変更前", "変更後"]
LIST_COLS = ["受取人名", "リスクタイプ", "状態", "リスク度"]

# 変更履歴の保持行数。1行約80バイトなので5000行で約400KB。
MAX_CHANGES = 5000

SOURCE_LABEL = {
    "mof": "財務省",
    "meti": "経済産業省",
    "ofac_sdn": "OFAC SDN",
    "ofac_cons": "OFAC Consolidated",
}

STATUS_LABEL = {
    "fetched": "更新あり",
    "unchanged": "変更なし",
    "error": "エラー",
}


def _jst(iso_utc: str) -> str:
    """UTCのISO文字列をJSTの表示用文字列に直す。空なら空のまま。"""
    if not iso_utc:
        return ""
    try:
        dt = datetime.strptime(iso_utc, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return iso_utc
    return dt.replace(tzinfo=timezone.utc).astimezone(JST).strftime("%Y-%m-%d %H:%M:%S")


def _http_date_to_jst(value: str) -> str:
    """Last-Modified 形式をJSTに直す。パースできなければ原文をそのまま返す。"""
    if not value:
        return ""
    for fmt in ("%a, %d %b %Y %H:%M:%S GMT", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            dt = datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        return dt.astimezone(JST).strftime("%Y-%m-%d %H:%M:%S")
    return value


def write_status(root: Path, hb: list[dict], st: dict) -> Path:
    """各ソースの稼働状況。1ソース1行なので数百バイトにしかならない。

    「動いているか」を見るためのシートなので、チェックできた時刻を必ず出す。
    変更が無い回でも heartbeat には行が入るため、ここが古いままなら
    Actions が止まっていると判断できる。
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    d = root / DASH
    d.mkdir(parents=True, exist_ok=True)
    p = d / "status.csv"

    with p.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(STATUS_COLS)
        for e in hb:
            key = e.get("source", "")
            prev = st.get(key, {})
            w.writerow([
                SOURCE_LABEL.get(key, key),
                STATUS_LABEL.get(e.get("status", ""), e.get("status", "")),
                _jst(now),
                _http_date_to_jst(str(e.get("source_updated")
                                      or prev.get("source_updated") or "")),
                e.get("record_count", "") or prev.get("record_count", ""),
                (str(e.get("content_hash") or prev.get("sha256") or ""))[:12],
            ])
    return p


def append_changes(root: Path, diff_rows: list[list], when: str = "") -> Path:
    """変更履歴に追記する。新しいものが上。

    latest.csv は毎回上書きされるため、過去に何が起きたかがどこにも残らない。
    スプレッドシートで経過を追えるようにここへ蓄積する。
    MAX_CHANGES 行で打ち切り、古いものから落とす。
    """
    stamp = when or datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
    d = root / DASH
    d.mkdir(parents=True, exist_ok=True)
    p = d / "changes.csv"

    old: list[list] = []
    if p.exists():
        with p.open(encoding="utf-8", newline="") as f:
            rows = list(csv.reader(f))
        old = rows[1:] if rows and rows[0] == CHANGE_COLS else rows

    new = [[stamp] + list(r) for r in diff_rows]
    keep = (new + old)[:MAX_CHANGES]

    with p.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(CHANGE_COLS)
        w.writerows(keep)
    return p


def write_list(root: Path, rows) -> Path:
    """照合用の一覧。列を4つに絞る。

    master.csv は13列15MBあり IMPORTDATA では読めない。名寄せに要る列だけに
    落とすと約2.6MB。これでも上限に触れる可能性があるので、シート側で
    読めない場合は Apps Script の UrlFetchApp を使うことになる。

    M.load() は match_key をキーにした dict を返すが、list を渡されても
    動くようにしておく。
    """
    values = rows.values() if isinstance(rows, dict) else rows
    d = root / DASH
    d.mkdir(parents=True, exist_ok=True)
    p = d / "list.csv"
    with p.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(LIST_COLS)
        for r in values:
            w.writerow([r.get("display_name", ""), r.get("risk_type", ""),
                        r.get("status", ""), r.get("risk_level", "")])
    return p
