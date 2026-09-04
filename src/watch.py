"""制裁リスト監視のエントリポイント。

  python -m src.watch --sources ofac          # OFAC のみ (毎時)
  python -m src.watch --sources mof meti      # 財務省・経産省 (6時間ごと)
  python -m src.watch --sources all --dry-run

終了コード:
  0 = 正常 (差分の有無を問わない)
  1 = 取得失敗・書式変更などの異常。ワークフローを失敗させて通知を出す。
"""
from __future__ import annotations

import argparse
import os
import sys
import traceback
from pathlib import Path

import requests

from . import dashboard as D
from . import master as M
from . import ofac_index as OI
from . import state as S
from . import source_audit as A
from .fetch import archive, fetch, prune_raw, read_raw
from .sources import meti, mof, ofac

ROOT = Path(__file__).resolve().parent.parent
MASTER = ROOT / "data" / "master" / "master.csv"
DIFF_MD = ROOT / "data" / "diff" / "latest.md"
DIFF_CSV = ROOT / "data" / "diff" / "latest.csv"
OFAC_INDEX = ROOT / "data" / "master" / "ofac_alias_history.csv"

# Advanced XML / Party indexをmainへ導入しても、
# 明示的に解禁するまでmaster/社内取込には反映しない。
# 初回はParty/Alias baselineだけを確立する。
OFAC_ADVANCED_MASTER_ENABLED = True


def log(status: str, name: str, msg: str = "") -> None:
    print(f"[{status:>20}] {name}" + (f": {msg}" if msg else ""), flush=True)


# ------------------------------------------------------------------ 財務省

def run_mof(session, st, rows, hb, opts=None) -> list[M.Diff]:
    opts = opts or {}
    audit = opts.setdefault("audit", [])
    prev = st.get("mof", {})

    # 一覧ページ自体も一次ソースの一部。
    # CSVリンク消失を「変更なし」にしない。
    url, asof, idx = mof.discover(session=session)

    audit.append(
        A.entry(
            source="mof",
            document_role="index_page",
            status="fetched",
            fetched=idx,
            source_updated=asof,
        )
    )

    f = fetch(url, prev=prev, session=session)

    if f.not_modified or f.sha256 == prev.get("sha256"):
        log(
            "unchanged",
            mof.NAME,
            "変更なし" + (" (304)" if f.not_modified else ""),
        )

        hb.append(
            dict(
                source="mof",
                status="unchanged",
                content_hash=prev.get("sha256", ""),
                source_updated=prev.get("asof", asof),
                record_count=prev.get("record_count", ""),
            )
        )

        audit.append(
            A.entry(
                source="mof",
                document_role="list_file",
                status="unchanged",
                fetched=f,
                source_updated=prev.get("asof", asof),
                record_count=prev.get("record_count", ""),
            )
        )

        return []

    archive(f, "mof", ROOT)
    prune_raw("mof", ROOT)

    try:
        records = mof.parse(f)
    except mof.SchemaError as exc:
        audit.append(
            A.entry(
                source="mof",
                document_role="list_file",
                status="schema_error",
                fetched=f,
                source_updated=asof,
                fetch_failed=False,
                schema_changed=True,
                error=exc,
            )
        )
        raise

    # 区分番号の繰り下がり検査。
    baseline = bool(prev.get("baseline_synced"))
    problems = mof.detect_drift(records, rows)

    if problems:
        for p_ in problems:
            log("DRIFT", mof.NAME, p_)

        if baseline and not opts.get("ignore_drift"):
            exc = mof.SchemaError(
                "区分番号のずれを検出した。data/kubun_map.json を更新すること:\n  "
                + "\n  ".join(problems)
            )

            audit.append(
                A.entry(
                    source="mof",
                    document_role="list_file",
                    status="schema_error",
                    fetched=f,
                    source_updated=asof,
                    record_count=len(records),
                    fetch_failed=False,
                    schema_changed=True,
                    error=exc,
                )
            )

            raise exc

    d = M.merge(
        rows,
        records,
        mof.SOURCE,
        delist=baseline and not opts.get("no_delist"),
    )

    st["mof"] = dict(
        sha256=f.sha256,
        etag=f.etag,
        last_modified=f.last_modified,
        filename=f.filename,
        url=url,
        asof=asof,
        record_count=len(records),
        baseline_synced=True,
    )

    hb.append(
        dict(
            source="mof",
            status="changed" if d else "no_effective_change",
            content_hash=f.sha256,
            source_updated=asof,
            record_count=len(records),
            raw_path=f.raw_path,
        )
    )

    audit.append(
        A.entry(
            source="mof",
            document_role="list_file",
            status="changed" if d else "no_effective_change",
            fetched=f,
            source_updated=asof,
            record_count=len(records),
            diff_counts=d.counts if d else {
                "追加": 0,
                "削除": 0,
                "変更": 0,
            },
        )
    )

    log(
        "changed" if d else "no_effective_change",
        mof.NAME,
        (
            " ".join(f"{k}{v}" for k, v in d.counts.items())
            if d
            else "ファイルは更新されたが正規化後の内容は同一"
        ),
    )

    return [d] if d else []


# ------------------------------------------------------------------ OFAC

def _ofac_master_rollout_pending(st: dict, enabled=None) -> bool:
    """Advanced baselineはあるがmaster同期が未完了ならTrue。

    gateをONにした直後にOFACが304でも、次のリスト更新を待たず
    保存済みbaseline rawから現在snapshotを再生してmasterへ反映する。
    """
    if enabled is None:
        enabled = OFAC_ADVANCED_MASTER_ENABLED

    return bool(
        enabled
        and any(
            not st.get(key, {}).get(
                "advanced_master_synced"
            )
            for key in ofac.LISTS
        )
    )


def run_ofac(session, st, rows, hb, opts=None) -> list:
    """SDN と Consolidated の Advanced XML をまとめて1回でマージする。

    Classic primary CSV:
        - ETag / Last-Modified による更新検知
        - DistinctParty.FixedRef との Party ID 完全一致監査

    Advanced XML:
        - 名称・別名・非Latin名称の正式な取込元

    どちらか一方しか取得できない状態では OFAC 全体を merge しない。
    不完全スナップショットで大量の偽「掲載終了候補」を出さないため。
    """
    opts = opts or {}
    audit = opts.setdefault("audit", [])
    records: list = []
    unchanged: list[tuple[str, dict]] = []
    fetched_any = False

    for key, cfg in ofac.LISTS.items():
        prev = st.get(key, {})

        # Advanced XML 導入前の state には advanced_* が無い。
        # Classic が304でも初回だけ強制的にAdvanced XMLを取得する。
        bootstrap_advanced = not (
            prev.get("advanced_sha256")
            and prev.get("raw_advanced")
            and prev.get("advanced_baseline_synced")
        )

        prim = fetch(
            cfg["prim"],
            prev=prev,
            session=session,
        )

        classic_unchanged = (
            prim.not_modified
            or (
                bool(prev.get("sha256"))
                and prim.sha256 == prev.get("sha256")
            )
        )

        if classic_unchanged and not bootstrap_advanced:
            log(
                "unchanged",
                cfg["name"],
                "変更なし"
                + (
                    " — 304 Not Modified（ダウンロードなし）"
                    if prim.not_modified
                    else ""
                ),
            )

            hb.append(
                dict(
                    source=key,
                    status="unchanged",
                    content_hash=(
                        prev.get("advanced_sha256")
                        or prev.get("sha256", "")
                    ),
                    source_updated=prev.get(
                        "source_updated", ""
                    ),
                    record_count=prev.get(
                        "record_count", ""
                    ),
                )
            )

            audit.append(
                A.entry(
                    source=key,
                    document_role="classic_primary",
                    status="unchanged",
                    fetched=prim,
                    source_updated=prev.get("source_updated", ""),
                    record_count=prev.get("record_count", ""),
                )
            )

            unchanged.append((key, prev))
            continue

        fetched_any = True

        # 304 で body が無いが Advanced bootstrap が必要な場合。
        # 最新ClassicのParty ID照合が必要なので無条件GETを1回だけ行う。
        if prim.not_modified or prim.body is None:
            prim = fetch(
                cfg["prim"],
                session=session,
                allow_conditional=False,
            )

        alt = fetch(
            cfg["alt"],
            session=session,
            allow_conditional=False,
        )

        advanced = fetch(
            cfg["advanced"],
            session=session,
            allow_conditional=False,
        )

        # HTTP取得に成功した時点の証跡。
        # 後段のパース/coverage検証で失敗した場合でも、
        # 「何を取得したか」がAudit Ledgerに残る。
        audit.extend([
            A.entry(
                source=key,
                document_role="classic_primary",
                status="fetched",
                fetched=prim,
            ),
            A.entry(
                source=key,
                document_role="classic_alias",
                status="fetched",
                fetched=alt,
            ),
            A.entry(
                source=key,
                document_role="advanced_xml",
                status="fetched",
                fetched=advanced,
            ),
        ])

        # Classic と Advanced は別ディレクトリで世代管理する。
        # 121MB級XMLをClassicのprim/alt世代と混ぜると、
        # prune時の「1世代」の意味が壊れるため。
        archive(prim, key, ROOT)
        archive(alt, key, ROOT)
        archive(advanced, f"{key}_advanced", ROOT)

        prune_raw(key, ROOT)
        prune_raw(f"{key}_advanced", ROOT)

        classic_ids = ofac.classic_party_ids(
            prim,
            cfg["label"],
        )

        part, advanced_ids = ofac.parse_advanced(
            advanced,
            cfg["label"],
        )

        # Classic CSVもOFAC公式の現行名称なので、
        # Advancedを正式詳細ソースとしつつ検索互換名としてunionする。
        classic_part = ofac.parse(
            prim,
            alt,
            cfg["label"],
        )

        for r in classic_part:
            r["format"] = "classic_csv"

        # ここが自動掲載終了より前の最重要安全弁。
        # ClassicとAdvancedのParty IDが1件でも違えば停止する。
        ofac.validate_party_coverage(
            classic_ids,
            advanced_ids,
            cfg["label"],
        )

        records += part
        records += classic_part

        source_updated = (
            advanced.last_modified
            or prim.last_modified
        )

        st[key] = dict(
            sha256=prim.sha256,
            etag=prim.etag,
            last_modified=prim.last_modified,
            filename=prim.filename,
            url=cfg["prim"],
            source_updated=source_updated,
            record_count=len(part) + len(classic_part),
            advanced_record_count=len(part),
            classic_record_count=len(classic_part),
            party_count=len(advanced_ids),
            classic_party_count=len(classic_ids),
            baseline_synced=True,
            advanced_baseline_synced=True,
            advanced_sha256=advanced.sha256,
            advanced_etag=advanced.etag,
            advanced_last_modified=advanced.last_modified,
            advanced_url=cfg["advanced"],
            raw_prim=prim.raw_path,
            raw_alt=alt.raw_path,
            raw_advanced=advanced.raw_path,
        )

        hb.append(
            dict(
                source=key,
                status="fetched",
                content_hash=advanced.sha256,
                source_updated=source_updated,
                record_count=len(part) + len(classic_part),
                raw_path=advanced.raw_path,
            )
        )

        audit.append(
            A.entry(
                source=key,
                document_role="validated_snapshot",
                status="validated",
                fetched=advanced,
                source_updated=source_updated,
                record_count=len(part) + len(classic_part),
            )
        )

        log(
            "fetched",
            cfg["name"],
            (
                f"Party {len(advanced_ids)}件 / "
                f"Advanced名称 {len(part)}件 / "
                f"Classic名称 {len(classic_part)}件 / "
                "Classic ID coverage完全一致"
            ),
        )

    rollout_pending = _ofac_master_rollout_pending(
        st
    )

    if not fetched_any and not rollout_pending:
        return []

    if not fetched_any and rollout_pending:
        log(
            "rollout-cache",
            "OFAC",
            (
                "Advanced master初回同期: "
                "ソース変更なしのため保存済みbaseline rawを再検証して使用"
            ),
        )

    # 片方だけ更新された場合、もう片方の最新Advanced XMLを
    # stateで記録したrawから復元する。
    #
    # 読めない場合は部分スナップショットでmergeせず、
    # workflow自体を失敗させる。
    for key, prev in unchanged:
        cached = _latest_advanced(
            key,
            ofac.LISTS[key],
            prev,
        )

        if cached is None:
            raise ofac.SchemaError(
                f"{ofac.LISTS[key]['name']} の保存済みAdvanced XMLを"
                "読み戻せない。不完全なOFACスナップショットでの"
                "掲載終了判定を防ぐため処理を停止する"
            )

        part, party_count = cached
        records += part

        log(
            "cached",
            ofac.LISTS[key]["name"],
            f"Party {party_count}件 / 名称 {len(part)}件",
        )

    # OFAC Party/Alias履歴。
    # 全alias（Weak含む）をFixedRef付きで保存する。
    history = OI.load(
        OFAC_INDEX,
    )

    index_diff = OI.update(
        history,
        records,
        M.now_ms(),
    )

    # PartyそのもののFixedRef消失は、
    # name差分とは比較にならない重要イベント。
    #
    # 現段階では自動解除せずfail-closed。
    # workflow失敗通知を発生させ、人手監査する。
    if index_diff.removed_parties:
        sample = sorted(
            index_diff.removed_parties
        )[:20]

        raise ofac.SchemaError(
            "OFAC Party FixedRef消失を検出: "
            f"{len(index_diff.removed_parties)}件 "
            f"sample={sample}。"
            "自動無効化せず処理を停止する"
        )

    # failure時には保存しないよう、
    # Party終了検査を通過してからoptsへ渡す。
    opts["ofac_index_rows"] = history
    opts["ofac_index_diff"] = index_diff

    screening_records = (
        ofac.screening_records(
            records
        )
    )

    weak_record_count = (
        len(records)
        - len(screening_records)
    )

    log(
        "party-index",
        "OFAC",
        (
            f"baseline={index_diff.baseline} / "
            f"Party追加={len(index_diff.added_parties)} / "
            f"Party終了={len(index_diff.removed_parties)} / "
            f"Alias新規={index_diff.added_aliases} / "
            f"Party継続中Alias非current="
            f"{index_diff.inactive_aliases_active_party} / "
            f"Weak records={weak_record_count} / "
            f"screening records={len(screening_records)}"
        ),
    )

    # 初回baselineではmasterを変更しない。
    # これによりmainへコードを入れても大量差分・Release更新・
    # 社内取込ファイル生成が自動発生しない。
    if not OFAC_ADVANCED_MASTER_ENABLED:
        log(
            "safe-mode",
            "OFAC",
            (
                "Advanced XML rollout gate OFF: "
                "Party/Alias indexのみ監査し、"
                "master・社内取込へは未反映"
            ),
        )

        return []

    # 通常スクリーニングはStrong名称のみ。
    #
    # Weak AKAはParty indexに残るが、
    # 高リスク名称としてmasterへ直接投入しない。
    d = M.merge(
        rows,
        screening_records,
        ofac.SOURCE,
        delist=False,
        report_missing=False,
    )

    # mergeまで正常完了したsnapshotだけmaster同期済みとする。
    # main() はmaster保存後にstateを保存するため、途中失敗時は
    # 次回もう一度安全にrolloutを再実行できる。
    for key in ofac.LISTS:
        state_row = st.get(key)

        if not state_row:
            raise ofac.SchemaError(
                f"OFAC master同期state欠落: {key}"
            )

        state_row["advanced_master_synced"] = True

    if rollout_pending:
        log(
            "rollout-state",
            "OFAC",
            "Advanced XML Strong名称のmaster同期完了",
        )

    audit.append(
        A.entry(
            source="ofac",
            document_role="aggregate_merge",
            status="changed" if d else "no_effective_change",
            record_count=len(screening_records),
            diff_counts=d.counts if d else {
                "追加": 0,
                "削除": 0,
                "変更": 0,
            },
        )
    )

    log(
        "changed" if d else "no_effective_change",
        "OFAC",
        (
            " ".join(
                f"{k}{v}"
                for k, v in d.counts.items()
            )
            if d
            else "実質変更なし"
        ),
    )

    return [d] if d else []


class _Raw:
    """保存済み生ファイルを Fetched と同じインターフェースで読ませる殻。"""

    def __init__(self, path: Path):
        self.body = read_raw(path)

    @property
    def text(self) -> str:
        for enc in ("utf-8-sig", "utf-8", "cp1252"):
            try:
                return self.body.decode(enc)
            except UnicodeDecodeError:
                continue
        return self.body.decode("utf-8", errors="replace")



def _latest_advanced(
    key: str,
    cfg: dict,
    prev: dict,
) -> tuple[list, int] | None:
    """保存済みAdvanced XMLをClassic Party IDで再検証して読み戻す。"""
    raw_advanced = prev.get("raw_advanced", "")
    raw_prim = prev.get("raw_prim", "")

    if not raw_advanced or not raw_prim:
        return None

    advanced_path = ROOT / raw_advanced
    prim_path = ROOT / raw_prim

    if not advanced_path.exists() or not prim_path.exists():
        return None

    try:
        advanced = _Raw(advanced_path)
        prim = _Raw(prim_path)

        part, advanced_ids = ofac.parse_advanced(
            advanced,
            cfg["label"],
        )

        prim_path2, alt_path = resolve_raw(
            key,
            prev,
        )

        if (
            prim_path2 is None
            or alt_path is None
        ):
            return None

        prim = _Raw(prim_path2)
        alt = _Raw(alt_path)

        classic_ids = ofac.classic_party_ids(
            prim,
            cfg["label"],
        )

        ofac.validate_party_coverage(
            classic_ids,
            advanced_ids,
            cfg["label"],
        )

        classic_part = ofac.parse(
            prim,
            alt,
            cfg["label"],
        )

        for r in classic_part:
            r["format"] = "classic_csv"

        return (
            part + classic_part,
            len(advanced_ids),
        )

    except Exception as exc:  # noqa: BLE001
        print(
            f"  警告: {key} のAdvanced XML再パースに失敗: "
            f"{exc}",
            flush=True,
        )
        return None

def resolve_raw(key: str, prev: dict) -> tuple[Path | None, Path | None]:
    """保存済み生ファイルの (prim, alt) を特定する。

    まず state.json が記録している raw_prim / raw_alt をそのまま使う。
    以前はディレクトリを `*SDN.CSV` で glob していたが、SLS は
    Content-Disposition で小文字のファイル名を返すため実ファイルは
    `..__sdn.csv` になり、大文字小文字を区別する Linux では
    **常に何もマッチしなかった**。glob は state が無い場合の保険として残し、
    大文字小文字と .gz を無視して照合する。
    """
    prim = alt = None
    for field_, slot in (("raw_prim", "prim"), ("raw_alt", "alt")):
        rel = prev.get(field_)
        if not rel:
            continue
        p = ROOT / rel
        if p.exists():
            if slot == "prim":
                prim = p
            else:
                alt = p

    if prim and alt:
        return prim, alt

    d = ROOT / "data" / "raw" / key
    if not d.exists():
        return prim, alt
    files = sorted((p for p in d.iterdir() if p.is_file()),
                   key=lambda p: p.name, reverse=True)

    def pick(*needles: str) -> Path | None:
        for p in files:
            name = p.name.lower()
            if any(n in name for n in needles):
                return p
        return None

    return prim or pick("sdn.csv", "prim.csv"), alt or pick("alt.csv")


def _latest_raw(key: str, cfg: dict, prev: dict | None = None) -> list:
    """保存済みの生ファイルから再パースする。

    片方のリストだけが更新された回に、もう片方を取り直さずに済ませると
    そのリストの全件が「掲載終了」と判定される。304 のときは
    アーカイブから読み戻して突合対象に含める。
    """
    prim, alt = resolve_raw(key, prev or {})
    if not prim:
        return []
    try:
        return ofac.parse(_Raw(prim), _Raw(alt) if alt else None, cfg["label"])
    except Exception as e:  # noqa: BLE001
        print(f"  警告: {key} の生ファイル再パースに失敗: {e}", flush=True)
        return []


# ------------------------------------------------------------------ 経産省

def run_meti(session, st, rows, hb, opts=None) -> list:
    """経産省の更新監視。

    WAF等による明示的な自動取得拒否だけは blocked として記録し、
    他ソースの監視を止めない。

    一方で、
      - ページ構造変更
      - HTTP 404/500
      - 想定外の例外

    は blocked と混同せず異常終了させる。
    """
    opts = opts or {}
    audit = opts.setdefault("audit", [])
    prev = st.get("meti", {})

    try:
        res = meti.check(session=session)

    except meti.Blocked as exc:
        log("blocked", meti.NAME, f"取得できず: {exc}")

        hb.append(
            dict(
                source="meti",
                status="blocked",
            )
        )

        audit.append(
            A.error_entry(
                source="meti",
                document_role="index_page",
                error=exc,
                url=meti.INDEX_URL,
                status="blocked",
                fetch_failed=True,
                schema_changed=False,
            )
        )

        return []

    except requests.HTTPError as exc:
        status_code = getattr(
            getattr(exc, "response", None),
            "status_code",
            None,
        )

        # METIのWAFが403で拒否するケースは「取得拒否」として分離。
        if status_code == 403:
            log("blocked", meti.NAME, f"HTTP 403: {exc}")

            hb.append(
                dict(
                    source="meti",
                    status="blocked",
                )
            )

            audit.append(
                A.error_entry(
                    source="meti",
                    document_role="index_page",
                    error=exc,
                    url=meti.INDEX_URL,
                    status="blocked",
                    fetch_failed=True,
                    schema_changed=False,
                )
            )

            return []

        raise

    except meti.SchemaError:
        raise

    sig = res["signature"]
    changed = sig != prev.get("signature")
    fetched = res["fetched"]

    hb.append(
        dict(
            source="meti",
            status="updated" if changed else "unchanged",
            content_hash=fetched.sha256,
            source_updated=";".join(res["dates"][:3]),
        )
    )

    audit.append(
        A.entry(
            source="meti",
            document_role="index_page",
            status="updated" if changed else "unchanged",
            fetched=fetched,
            source_updated=";".join(res["dates"][:3]),
        )
    )

    if not changed:
        log("unchanged", meti.NAME, "変更なし")
        return []

    st["meti"] = dict(
        signature=sig,
        pdfs=res["pdfs"],
        dates=res["dates"],
        sha256=fetched.sha256,
        etag=fetched.etag,
        last_modified=fetched.last_modified,
        url=fetched.url,
        filename=fetched.filename,
    )

    archive(fetched, "meti", ROOT)
    prune_raw("meti", ROOT)

    # archive後のraw_pathを含む確定証跡。
    audit.append(
        A.entry(
            source="meti",
            document_role="index_page_archived",
            status="updated",
            fetched=fetched,
            source_updated=";".join(res["dates"][:3]),
        )
    )

    log(
        "updated",
        meti.NAME,
        "更新検出（PDFのため要手動取込）",
    )

    return [
        dict(
            source=meti.SOURCE,
            pdfs=res["pdfs"],
            dates=res["dates"],
        )
    ]


# ------------------------------------------------------------------ main

RUNNERS = {"mof": run_mof, "ofac": run_ofac, "meti": run_meti}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources", nargs="+", default=["all"],
                    choices=["all", "mof", "ofac", "meti"])
    ap.add_argument("--dry-run", action="store_true",
                    help="マスターとstateを書き換えずに差分だけ表示する")
    ap.add_argument("--no-delist", action="store_true",
                    help="取得結果から消えた行を無効化しない（初回同期用）")
    ap.add_argument("--ignore-drift", action="store_true",
                    help="区分番号のずれを検出しても止めない")
    args = ap.parse_args()
    opts = dict(
        no_delist=args.no_delist,
        ignore_drift=args.ignore_drift,
        dry_run=args.dry_run,
        audit=[],
    )

    targets = list(RUNNERS) if "all" in args.sources else args.sources

    if not MASTER.exists():
        print("マスターが存在しない。先に import_legacy.py でブートストラップすること",
              file=sys.stderr)
        return 1

    rows = M.load(MASTER)
    st = S.load_state(ROOT)
    session = requests.Session()
    hb: list[dict] = []
    diffs: list[M.Diff] = []
    meti_notice: list[dict] = []
    failed: list[str] = []

    for t in targets:
        try:
            out = RUNNERS[t](session, st, rows, hb, opts)
            if t == "meti":
                meti_notice += out
            else:
                diffs += out
        except Exception as e:  # noqa: BLE001
            failed.append(f"{t}: {e}")
            log("FAILED", t, str(e))
            traceback.print_exc()

            schema_changed = isinstance(
                e,
                (
                    mof.SchemaError,
                    ofac.SchemaError,
                    meti.SchemaError,
                ),
            )

            error_status = (
                "schema_error"
                if schema_changed
                else "error"
            )

            if t == "ofac":
                # OFACはSDN/Consolidatedを一体スナップショットとして扱う。
                # 片方の失敗でも全体mergeを停止するため、
                # dashboard上も両方を正常表示のまま残さない。
                for key, cfg in ofac.LISTS.items():
                    hb.append(
                        dict(
                            source=key,
                            status="error",
                            content_hash="",
                        )
                    )

                    opts["audit"].append(
                        A.error_entry(
                            source=key,
                            document_role="source_run",
                            error=e,
                            url=cfg.get("prim", ""),
                            status=error_status,
                            fetch_failed=not schema_changed,
                            schema_changed=schema_changed,
                        )
                    )
            else:
                hb.append(
                    dict(
                        source=t,
                        status="error",
                        content_hash="",
                    )
                )

                source_url = (
                    mof.INDEX_URL
                    if t == "mof"
                    else (
                        meti.INDEX_URL
                        if t == "meti"
                        else ""
                    )
                )

                opts["audit"].append(
                    A.error_entry(
                        source=t,
                        document_role="source_run",
                        error=e,
                        url=source_url,
                        status=error_status,
                        fetch_failed=not schema_changed,
                        schema_changed=schema_changed,
                    )
                )

    # dry-runでは既存仕様どおり永続ファイルを書かない。
    # 通常実行ではmaster/stateより先に監査証跡を保存する。
    # Audit Ledger自体が壊れているなら業務データ更新も止める。
    if not args.dry_run:
        try:
            A.write(ROOT, opts["audit"])
        except Exception as e:  # noqa: BLE001
            log("FAILED", "source_audit", str(e))
            traceback.print_exc()
            print(
                "Source Audit Ledger の保存に失敗したため、"
                "master/state更新を停止する",
                file=sys.stderr,
            )
            return 1

    if args.dry_run:
        print(M.render_markdown(diffs))
        return 1 if failed else 0

    if opts.get("ofac_index_rows") is not None:
        OI.save(
            opts["ofac_index_rows"],
            OFAC_INDEX,
        )

    S.heartbeat(ROOT, hb)
    if diffs:
        M.save(rows, MASTER)
        DIFF_MD.parent.mkdir(parents=True, exist_ok=True)
        DIFF_MD.write_text(M.render_markdown(diffs), encoding="utf-8")
        M.write_diff_csv(diffs, DIFF_CSV)
    S.save_state(ROOT, st)

    # スプレッドシート取込用。status は変更が無い回も必ず書く。
    # ここが古いままなら Actions が止まっていると判断できるため。
    D.write_status(ROOT, hb, st)

    # changes と list は差分が無くても、無ければ作る。差分が出るまで
    # ファイルが存在しないと、シート側の設定時に404で詰まる。
    # 内容が変わらなければ git 上は差分にならないので毎回書いてよい。
    D.append_changes(ROOT, M.diff_rows(diffs) if diffs else [])
    if diffs or not (ROOT / D.DASH / "list.csv").exists():
        D.write_list(ROOT, rows)

    # 後続ステップ用の出力
    gh = os.environ.get("GITHUB_OUTPUT")
    if gh:
        with open(gh, "a", encoding="utf-8") as f:
            f.write(f"has_diff={'true' if diffs else 'false'}\n")
            f.write(f"meti_updated={'true' if meti_notice else 'false'}\n")
            f.write(f"added={sum(len(d.added) for d in diffs)}\n")
            f.write(f"removed={sum(len(d.removed) for d in diffs)}\n")
            f.write(f"changed={sum(len(d.changed) for d in diffs)}\n")

    # 取得失敗はワークフローを失敗させる。黙って止まっているのが一番怖い。
    if failed:
        print("取得に失敗した対象があるため異常終了する:\n  " + "\n  ".join(failed),
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
