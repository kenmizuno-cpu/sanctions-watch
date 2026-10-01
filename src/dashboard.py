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
from collections import Counter
import gzip
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import ofac_removal_queue as ORQ
from .normalize import canonical_display_name, is_trailing_unknown_artifact
from .screening import secondary_screening_key

LOGGER = logging.getLogger(__name__)

JST = timezone(timedelta(hours=9))

DASH = Path("data") / "dashboard"
METI_MANUAL_STATE = Path("data") / "manual" / "meti" / "state.json"

STATUS_COLS = ["出所", "状態", "最終チェック", "最終更新", "件数", "内容ハッシュ"]
CHANGE_COLS = ["検知日時", "出所", "種別", "受取人名", "変更前", "変更後"]
LIST_COLS = ["受取人名", "リスクタイプ", "状態", "リスク度"]

SCREENING_COLS = [
    "match_key",
    "secondary_key",
    "受取人名",
    "出所",
    "カテゴリ",
    "リスクタイプ",
    "リスク度",
    "状態",
    "要確認",
]

# 変更履歴の保持行数。1行約80バイトなので5000行で約400KB。
MAX_CHANGES = 5000

SOURCE_LABEL = {
    "mof": "財務省",
    "meti": "経済産業省",
    "ofac_sdn": "OFAC SDN",
    "ofac_cons": "OFAC Consolidated",
    "mofa_catalog": "外務省（現行リスト）",
    "mofa_press": "外務省（報道発表）",
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

    # OFAC Party終了候補。取得・検証は成功し、承認までmasterは維持する。
    "review_required": "掲載終了候補・要レビュー",

    # 経産省: PDF等の更新を検出。手動取込対象。
    "updated": "更新あり（要手動確認）",

    # 経産省WAF等。想定される状態なので通常のシステム障害とは分離する。
    "blocked": "自動取得不可",

    # 取得失敗・書式変更などの異常。
    "error": "エラー",

    # 経産省: 自動接続を行わないmanual-first lifecycle。
    "manual_ok": "手動監視（正常）",
    "manual_pending": "要確認：正本取得待ち",
    "manual_review": "要レビュー",
    "manual_approved": "要確認：反映待ち",
    "manual_critical": "重大：更新候補未検証",
    "manual_blocked": "重大：正本解析BLOCKED",
    "manual_rejected": "要確認：取込却下",
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


def _load_meti_manual_state(root: Path) -> dict:
    path = root / METI_MANUAL_STATE
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("METI manual state must be a JSON object")
    return value


def _manual_status(meti_state: dict) -> str:
    lifecycle = str(meti_state.get("lifecycle_state", "")).strip()

    if lifecycle in {
        "CHECKED_NO_CHANGE",
        "APPLIED",
        "APPLIED_WITH_HOLDS",
    }:
        return "manual_ok"
    if lifecycle in {
        "DETECTED",
        "MANUAL_FETCH_REQUIRED",
        "FILE_RECEIVED",
        "VALIDATED",
    }:
        if meti_state.get("sla_breached_at"):
            return "manual_critical"
        return "manual_pending"
    if lifecycle in {"DIFFED", "REVIEW_REQUIRED"}:
        return "manual_review"
    if lifecycle == "APPROVED":
        return "manual_approved"
    if lifecycle == "BLOCKED":
        return "manual_blocked"
    if lifecycle == "REJECTED":
        return "manual_rejected"
    return ""


def build_status_rows(
    root: Path,
    hb: list[dict],
    st: dict,
    *,
    now: datetime | None = None,
    meti_state: dict | None = None,
    mofa_state: dict | None = None,
) -> list[list[str]]:
    """全監視ソースの現在状態を固定順で投影する。"""

    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must include a timezone")
    now_text = now.astimezone(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    latest = _latest_heartbeat_by_source(root)

    # atomic transactionでは正式heartbeatより先にin-memory行を投影する。
    # 同時刻なら今回渡された行を優先する。
    for supplied in hb:
        key = str(supplied.get("source", "")).strip()
        if key not in SOURCE_LABEL:
            continue

        current = dict(supplied)
        checked_at = str(current.get("checked_at", "")).strip() or now_text
        current["checked_at"] = checked_at
        previous = latest.get(key)
        if (
            previous is None
            or checked_at >= str(previous.get("checked_at", ""))
        ):
            latest[key] = current

    if meti_state is None:
        meti_state = _load_meti_manual_state(root)

    if mofa_state is None:
        mofa_path = root / 'data/mofa/state.json'
        if mofa_path.exists():
            from .mofa_documents import load_bundle
            mofa_state = load_bundle(root)[0]
        else:
            mofa_state = {}
    rows: list[list[str]] = []

    # 順序を毎回固定する。
    for key, source_label in SOURCE_LABEL.items():
        if key.startswith('mofa_'):
            family = (mofa_state or {}).get('families', {}).get(key, {})
            label = {'unchanged': '変更なし', 'document_updated': '資料更新・要レビュー',
                     'checking': '確認中', 'COVERAGE_GAP': '未確認期間あり',
                     'schema_changed': '構造異常', 'error': 'エラー'}.get(family.get('status'), '未確認')
            rows.append([source_label, label, _jst(family.get('last_success_at', '')),
                         _jst(family.get('last_document_change_at', '')), '', family.get('sha256', '')[:12]])
            continue
        entry = latest.get(key, {})
        previous_state = st.get(key, {})
        manual_active = (
            key == "meti"
            and bool(meti_state.get("lifecycle_state"))
        )

        if manual_active:
            pending = meti_state.get("pending_detection") or {}
            raw_status = _manual_status(meti_state)
            checked_at = str(
                meti_state.get("last_manual_check_at", "")
            )
            source_updated = str(
                meti_state.get("effective_date")
                or meti_state.get("publication_date")
                or pending.get("publication_at")
                or entry.get("source_updated")
                or _state_source_updated(key, previous_state)
                or ""
            )
            record_count = (
                meti_state.get("current_record_count")
                or meti_state.get("record_count")
                or previous_state.get("record_count", "")
            )
            content_hash = (
                meti_state.get("current_source_hash")
                or meti_state.get("source_hash")
                or previous_state.get("sha256")
                or ""
            )
        else:
            raw_status = str(entry.get("status", "")).strip()
            checked_at = str(entry.get("checked_at", ""))
            source_updated = str(
                entry.get("source_updated")
                or _state_source_updated(key, previous_state)
                or ""
            )
            record_count = entry.get("record_count")
            if record_count in ("", None):
                record_count = previous_state.get("record_count", "")
            content_hash = (
                entry.get("content_hash")
                or previous_state.get("sha256")
                or ""
            )

        status_label = (
            STATUS_LABEL.get(raw_status, raw_status)
            if raw_status
            else "未確認"
        )
        rows.append([
            source_label,
            status_label,
            _jst(checked_at),
            _http_date_to_jst(source_updated),
            str(record_count),
            str(content_hash)[:12],
        ])

    return rows


def write_status_rows(path: Path, rows: list[list[str]]) -> Path:
    """既に投影済みのstatus行を指定パスへ書く。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(STATUS_COLS)
        writer.writerows(rows)
    return path


def write_status(root: Path, hb: list[dict], st: dict) -> Path:
    """全監視ソースの最新稼働状況を status.csv に書き出す。

    以前は引数 hb（= 今回実行した監視対象）だけを書いていたため、

      watch-ofac → OFAC だけ
      watch-jp   → 財務省・経産省だけ

    と status.csv が交互に上書きされていた。

    heartbeat は変更なしでも毎回記録されるため、そこから各ソースの
    最新1行を取得することで、常に全ソースの状態を表示する。
    """
    path = root / DASH / "status.csv"
    rows = build_status_rows(root, hb, st)
    return write_status_rows(path, rows)


def prepend_change_rows(
    path: Path,
    rows: list[list],
    *,
    when: str,
) -> Path:
    """指定されたchanges.csvへ新しい履歴を先頭追加する。

    latest.csv は毎回上書きされるため、過去に何が起きたかがどこにも残らない。
    スプレッドシートで経過を追えるようにここへ蓄積する。
    現在のOFAC承認待ちを保護し、残りはMAX_CHANGES行まで保持する。
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    old: list[list] = []
    if path.exists():
        with path.open(encoding="utf-8", newline="") as f:
            existing_rows = list(csv.reader(f))
        existing = (
            existing_rows[1:]
            if existing_rows and existing_rows[0] == CHANGE_COLS
            else existing_rows
        )
        for row in existing:
            if len(row) >= 4:
                name = canonical_display_name(row[3])
                if is_trailing_unknown_artifact(name):
                    continue
                row[3] = name
            old.append(row)

    clean_rows = []
    for row in rows:
        row = list(row)
        if len(row) >= 3:
            name = canonical_display_name(row[2])
            if is_trailing_unknown_artifact(name):
                continue
            row[2] = name
        clean_rows.append(row)
    new = [[when] + row for row in clean_rows]
    # atomic_replace_manyの一時ファイルも正式changes.csvと同じディレクトリ。
    # 共通writerで保護し、METIの個別workflowでも承認待ちを落とさない。
    queue_path = path.parent.parent / "review" / "ofac_party_removal_queue.csv"
    keep = _keep_ofac_removal_reviews(new + old, ORQ.load(queue_path))

    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(CHANGE_COLS)
        w.writerows(keep)
    return path


def append_changes(root: Path, diff_rows: list[list], when: str = "") -> Path:
    """変更履歴に追記する。新しいものが上。"""

    stamp = when or datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
    path = root / DASH / "changes.csv"
    return prepend_change_rows(path, diff_rows, when=stamp)


def _ofac_removal_review_rows(queue_rows: list[dict]) -> list[list[str]]:
    """未承認Party終了の表示キーを初回検知時の情報から作る。

    Sheetsの対応状況は6列全体をキーに保持するため、最終確認時刻ではなく
    初回検知日時・event_id・FixedRef・検知snapshotを固定して使用する。
    """
    ORQ.pending_groups(queue_rows)  # 壊れたキューを正常な空一覧として公開しない。
    reviews = []
    for row in queue_rows:
        if row["status"] != ORQ.PENDING_REVIEW:
            continue
        detected = datetime.fromtimestamp(
            int(row["detected_at_ms"]) / 1000, tz=timezone.utc
        ).astimezone(JST).strftime("%Y-%m-%d %H:%M:%S")
        reviews.append([
            detected,
            "OFAC",
            "掲載終了候補（要確認）",
            canonical_display_name(row["party_name"]),
            f"制裁リスト（OFAC：{row['list']}）; FixedRef={row['party_id']}; "
            f"event_id={row['event_id']}; SHA256={row['snapshot_sha256']}",
            "削除承認待ち（名簿は有効のまま・削除未反映）",
        ])
    reviews.sort(key=lambda row: tuple(row), reverse=True)
    return reviews


def _keep_ofac_removal_reviews(rows: list[list], queue_rows: list[dict]) -> list[list]:
    """全履歴writerで同じ承認待ちを重複なく保護する。"""
    reviews = _ofac_removal_review_rows(queue_rows)
    review_keys = {tuple(row) for row in reviews}
    history = [row for row in rows if tuple(row) not in review_keys]
    return (reviews + history)[:max(MAX_CHANGES, len(reviews))]


def append_ofac_removal_reviews(root: Path, queue_rows: list[dict]) -> Path:
    """既存の差分履歴に現在の承認待ちを投影する。名簿は変更しない。"""
    path = root / DASH / "changes.csv"
    old = []
    if path.exists():
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.reader(handle)
            if next(reader, None) != CHANGE_COLS:
                raise ValueError("dashboard changes.csv header changed")
            old = list(reader)
    keep = _keep_ofac_removal_reviews(old, queue_rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(CHANGE_COLS)
        writer.writerows(keep)
    return path


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

def write_screening_list(root: Path, rows) -> Path:
    """Google Sheets の名簿検索専用一覧を生成する。

    dashboard/list.csv は master 全件との完全一致監査に使われているため、
    既存仕様を変更しない。

    このファイルは通常 screening 用なので:
      - 有効レコードだけを出力
      - canonical match_key をそのまま保持
      - screening 専用 secondary_key を別列で保持
      - 無効レコードや旧parser artifactは検索対象にしない

    secondary_key は master identity には使用しない。
    """

    values = (
        rows.values()
        if isinstance(rows, dict)
        else rows
    )

    d = root / DASH
    d.mkdir(
        parents=True,
        exist_ok=True,
    )

    p = d / "screening.csv"

    with p.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as f:

        w = csv.writer(
            f,
            lineterminator="\n",
        )

        w.writerow(
            SCREENING_COLS
        )

        for r in values:

            if str(
                r.get("status", "")
            ).strip() != "有効":
                continue

            name = canonical_display_name(
                r.get(
                    "display_name",
                    "",
                )
            )

            if not name:
                raise ValueError(
                    "active master rowに空のdisplay_name"
                )

            if is_trailing_unknown_artifact(
                name
            ):
                continue

            exact_key = str(
                r.get(
                    "match_key",
                    "",
                )
            ).strip()

            if not exact_key:
                raise ValueError(
                    f"active master rowに空match_key: {name}"
                )

            secondary_key = (
                secondary_screening_key(
                    name
                )
            )

            if not secondary_key:
                raise ValueError(
                    f"secondary_key生成失敗: {name}"
                )

            w.writerow([
                exact_key,
                secondary_key,
                name,
                r.get(
                    "sources",
                    "",
                ),
                r.get(
                    "categories",
                    "",
                ),
                r.get(
                    "risk_type",
                    "",
                ),
                r.get(
                    "risk_level",
                    "",
                ),
                r.get(
                    "status",
                    "",
                ),
                r.get(
                    "review_flag",
                    "",
                ),
            ])

    return p

def _expected_screening_rows(rows) -> Counter:
    """masterからscreening.csvの期待行集合を生成する。"""

    values = (
        rows.values()
        if isinstance(rows, dict)
        else rows
    )

    expected = Counter()

    for r in values:

        if str(
            r.get("status", "")
        ).strip() != "有効":
            continue

        name = canonical_display_name(
            r.get(
                "display_name",
                "",
            )
        )

        if not name:
            raise ValueError(
                "active master rowに空のdisplay_name"
            )

        if is_trailing_unknown_artifact(
            name
        ):
            continue

        exact_key = str(
            r.get(
                "match_key",
                "",
            )
        ).strip()

        if not exact_key:
            raise ValueError(
                f"active master rowに空match_key: {name}"
            )

        secondary_key = (
            secondary_screening_key(
                name
            )
        )

        if not secondary_key:
            raise ValueError(
                f"secondary_key生成失敗: {name}"
            )

        expected[
            (
                exact_key,
                secondary_key,
                name,
                str(
                    r.get(
                        "sources",
                        "",
                    )
                ),
                str(
                    r.get(
                        "categories",
                        "",
                    )
                ),
                str(
                    r.get(
                        "risk_type",
                        "",
                    )
                ),
                str(
                    r.get(
                        "risk_level",
                        "",
                    )
                ),
                str(
                    r.get(
                        "status",
                        "",
                    )
                ),
                str(
                    r.get(
                        "review_flag",
                        "",
                    )
                ),
            )
        ] += 1

    return expected


def assert_screening_gzip_consistent(
    rows,
    path: Path,
) -> None:
    """gzip内のscreeningデータがactive masterと完全一致することを検証する。"""

    expected = _expected_screening_rows(
        rows
    )

    try:
        with gzip.open(
            path,
            "rt",
            encoding="utf-8",
            newline="",
        ) as f:
            data = list(
                csv.reader(f)
            )

    except (
        OSError,
        EOFError,
        csv.Error,
        UnicodeError,
    ) as exc:
        raise ValueError(
            "screening.csv.gz読込失敗: "
            f"{exc}"
        ) from exc

    if not data:
        raise ValueError(
            "screening.csv.gzが空"
        )

    if data[0] != SCREENING_COLS:
        raise ValueError(
            "screening.csv.gzヘッダー不一致: "
            f"{data[0]!r}"
        )

    actual = Counter(
        tuple(row)
        for row in data[1:]
    )

    missing = expected - actual
    extra = actual - expected

    if missing or extra:
        raise ValueError(
            "master/screening gzip不一致: "
            f"expected={sum(expected.values())} "
            f"actual={sum(actual.values())} "
            f"missing={sum(missing.values())} "
            f"extra={sum(extra.values())}"
        )


def write_screening_gzip(root: Path, rows) -> Path:
    """active masterの検索用CSVを決定論的gzipとして安全に生成する。

    canonical match_key は変更しない。

    処理順:
      1. raw screening.csv生成
      2. temporary gzip生成
      3. masterとの完全一致検証
      4. 検証成功時のみ正式gzipへatomic replace

    同じ入力なら mtime=0 / filename="" により
    同一バイト列・同一SHA256となる。
    """

    stable_rows = (
        rows
        if isinstance(rows, dict)
        else list(rows)
    )

    csv_path = write_screening_list(
        root,
        stable_rows,
    )

    gz_path = csv_path.with_suffix(
        csv_path.suffix + ".gz"
    )

    tmp_path = gz_path.with_name(
        gz_path.name + ".tmp"
    )

    raw = csv_path.read_bytes()

    try:
        with tmp_path.open(
            "wb"
        ) as raw_out:

            with gzip.GzipFile(
                filename="",
                mode="wb",
                fileobj=raw_out,
                mtime=0,
                compresslevel=9,
            ) as gz:
                gz.write(raw)

        assert_screening_gzip_consistent(
            stable_rows,
            tmp_path,
        )

        tmp_path.replace(
            gz_path
        )

    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception as cleanup_error:
                LOGGER.error(
                    "screening gzip temp cleanup failed: %s: %s",
                    tmp_path,
                    cleanup_error,
                )

    return gz_path


MOFA_DOCUMENT_COLS = ['資料イベントID', '検知日時', '資料区分', 'タイトル', '公表日', '公表日時精度', '検知理由', '取得状態', 'レビュー状態', '発表URL', '資料URL', 'SHA256', '原本']

def build_mofa_document_rows(queue: list[dict]) -> list[list[str]]:
    keys = ['event_id','checked_at','role','title','publication_date','publication_precision','reason','fetch_status','review_status','notice_url','url','source_hash','raw_path']
    return [[_jst(row.get(k,'')) if k=='checked_at' else row.get(k,'') for k in keys]
            for row in queue if row.get('review_status') not in {'REVIEWED_DOCUMENT','CANCELLED_DOCUMENT'}]

def write_mofa_document_rows(path: Path, rows: list[list[str]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w',encoding='utf-8',newline='') as f:
        w=csv.writer(f,lineterminator='\n'); w.writerow(MOFA_DOCUMENT_COLS); w.writerows(rows)
    return path
