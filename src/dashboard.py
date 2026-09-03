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

from .normalize import canonical_display_name, is_trailing_unknown_artifact

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
    # OFAC: 元ファイルを新規取得できた状態。
    # 実質的な名簿差分があるとは限らないので「更新あり」と断定しない。
    "fetched": "取得あり",

    # 財務省: 正規化後のマスターにも実質差分あり。
    "changed": "更新あり",

    # 元ファイル自体は更新されたが、正規化後の名簿内容は同一。
    "no_effective_change": "元データ更新・実質変更なし",

    # ETag / Last-Modified / ハッシュ等で変更なし。
    "unchanged": "変更なし",

    # 経産省: PDF等の更新を検出。手動取込対象。
    "updated": "更新あり（要手動確認）",

    # 経産省WAF等。想定される状態なので通常のシステム障害とは分離する。
    "blocked": "自動取得不可",

    # 取得失敗・書式変更などの異常。
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


def _latest_heartbeat_by_source(root: Path) -> dict[str, dict]:
    """heartbeat 全履歴から、各ソースの最新1行だけを返す。

    status.csv は「今回実行したソース」ではなく、
    OFAC / 財務省 / 経産省の現在状態を常に一覧表示する必要がある。

    月替わり直後は当月CSVにまだ一部ソースしか存在しない可能性があるため、
    新しい月から過去へ遡って、全ソースが揃うまで読む。
    """
    hb_dir = root / "data" / "heartbeat"
    latest: dict[str, dict] = {}

    if not hb_dir.exists():
        return latest

    # YYYY-MM.csv なのでファイル名の逆順 = 新しい月から。
    files = sorted(hb_dir.glob("*.csv"), reverse=True)

    for p in files:
        try:
            with p.open(encoding="utf-8", newline="") as f:
                for row in csv.DictReader(f):
                    key = str(row.get("source", "")).strip()

                    # dashboardで管理しているソースだけを対象にする。
                    if key not in SOURCE_LABEL:
                        continue

                    checked_at = str(row.get("checked_at", "")).strip()
                    current = latest.get(key)

                    if (
                        current is None
                        or checked_at > str(current.get("checked_at", ""))
                    ):
                        latest[key] = dict(row)

        except (OSError, csv.Error):
            # 1ファイルの破損でダッシュボード生成全体を壊さない。
            # 本体の取得・監視異常は watch.py 側で別途失敗扱いになる。
            continue

        if len(latest) == len(SOURCE_LABEL):
            break

    return latest


def _state_source_updated(key: str, prev: dict) -> str:
    """state.json から元データの更新日時を可能な範囲で補完する。"""
    value = prev.get("source_updated")
    if value:
        return str(value)

    # 財務省は state.json 上では asof。
    value = prev.get("asof")
    if value:
        return str(value)

    # 経産省は dates の配列。
    dates = prev.get("dates")
    if isinstance(dates, list) and dates:
        return ";".join(str(x) for x in dates[:3])

    return ""


def write_status(root: Path, hb: list[dict], st: dict) -> Path:
    """全監視ソースの最新稼働状況を status.csv に書き出す。

    以前は引数 hb（= 今回実行した監視対象）だけを書いていたため、

      watch-ofac → OFAC だけ
      watch-jp   → 財務省・経産省だけ

    と status.csv が交互に上書きされていた。

    heartbeat は変更なしでも毎回記録されるため、そこから各ソースの
    最新1行を取得することで、常に全ソースの状態を表示する。
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    latest = _latest_heartbeat_by_source(root)

    # 通常は S.heartbeat() が先に実行されるので latest に今回分も入っている。
    # 単体テストや手動呼出しなど heartbeat 未書込のケースだけ hb で補完する。
    for e in hb:
        key = str(e.get("source", "")).strip()
        if key not in SOURCE_LABEL or key in latest:
            continue

        current = dict(e)
        current["checked_at"] = now
        latest[key] = current

    d = root / DASH
    d.mkdir(parents=True, exist_ok=True)
    p = d / "status.csv"

    with p.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(STATUS_COLS)

        # 順序を毎回固定する。
        for key, source_label in SOURCE_LABEL.items():
            e = latest.get(key, {})
            prev = st.get(key, {})

            raw_status = str(e.get("status", "")).strip()
            status_label = (
                STATUS_LABEL.get(raw_status, raw_status)
                if raw_status
                else "未確認"
            )

            source_updated = (
                e.get("source_updated")
                or _state_source_updated(key, prev)
                or ""
            )

            record_count = e.get("record_count")
            if record_count in ("", None):
                record_count = prev.get("record_count", "")

            content_hash = (
                e.get("content_hash")
                or prev.get("sha256")
                or ""
            )

            w.writerow([
                source_label,
                status_label,
                _jst(str(e.get("checked_at", ""))),
                _http_date_to_jst(str(source_updated)),
                record_count,
                str(content_hash)[:12],
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
        existing = rows[1:] if rows and rows[0] == CHANGE_COLS else rows
        for row in existing:
            if len(row) >= 4:
                name = canonical_display_name(row[3])
                if is_trailing_unknown_artifact(name):
                    continue
                row[3] = name
            old.append(row)

    clean_rows = []
    for row in diff_rows:
        row = list(row)
        if len(row) >= 3:
            name = canonical_display_name(row[2])
            if is_trailing_unknown_artifact(name):
                continue
            row[2] = name
        clean_rows.append(row)
    new = [[stamp] + row for row in clean_rows]
    keep = (new + old)[:MAX_CHANGES]

    with p.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
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
        w = csv.writer(f, lineterminator="\n")
        w.writerow(LIST_COLS)
        for r in values:
            name = canonical_display_name(r.get("display_name", ""))
            if is_trailing_unknown_artifact(name):
                continue
            w.writerow([name, r.get("risk_type", ""),
                        r.get("status", ""), r.get("risk_level", "")])
    return p
