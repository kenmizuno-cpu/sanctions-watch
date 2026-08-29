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

from . import master as M
from . import state as S
from .fetch import archive, fetch, prune_raw
from .sources import meti, mof, ofac

ROOT = Path(__file__).resolve().parent.parent
MASTER = ROOT / "data" / "master" / "master.csv"
DIFF_MD = ROOT / "data" / "diff" / "latest.md"
DIFF_CSV = ROOT / "data" / "diff" / "latest.csv"


def log(status: str, name: str, msg: str = "") -> None:
    print(f"[{status:>20}] {name}" + (f": {msg}" if msg else ""), flush=True)


# ------------------------------------------------------------------ 財務省

def run_mof(session, st, rows, hb, opts=None) -> list[M.Diff]:
    opts = opts or {}
    prev = st.get("mof", {})
    url, asof, _ = mof.discover(session=session)
    f = fetch(url, prev=prev, session=session)

    if f.not_modified or f.sha256 == prev.get("sha256"):
        log("unchanged", mof.NAME, "変更なし" + (" (304)" if f.not_modified else ""))
        hb.append(dict(source="mof", status="unchanged", content_hash=prev.get("sha256", ""),
                       source_updated=prev.get("asof", asof),
                       record_count=prev.get("record_count", "")))
        return []

    archive(f, "mof", ROOT)
    prune_raw("mof", ROOT)
    records = mof.parse(f)

    # 区分番号の繰り下がり検査。初回はマスターが別パイプライン由来で
    # カテゴリ表記が揃っていないため警告に留め、2回目以降は止める。
    baseline = bool(prev.get("baseline_synced"))
    problems = mof.detect_drift(records, rows)
    if problems:
        for p_ in problems:
            log("DRIFT", mof.NAME, p_)
        if baseline and not opts.get("ignore_drift"):
            raise mof.SchemaError(
                "区分番号のずれを検出した。data/kubun_map.json を更新すること:\n  "
                + "\n  ".join(problems))

    d = M.merge(rows, records, mof.SOURCE,
                delist=baseline and not opts.get("no_delist"))
    st["mof"] = dict(sha256=f.sha256, etag=f.etag, last_modified=f.last_modified,
                     filename=f.filename, url=url, asof=asof,
                     record_count=len(records), baseline_synced=True)
    hb.append(dict(source="mof", status="changed" if d else "no_effective_change",
                   content_hash=f.sha256, source_updated=asof,
                   record_count=len(records), raw_path=f.raw_path))
    log("changed" if d else "no_effective_change", mof.NAME,
        " ".join(f"{k}{v}" for k, v in d.counts.items()) if d
        else "ファイルは更新されたが正規化後の内容は同一")
    return [d] if d else []


# ------------------------------------------------------------------ OFAC

def run_ofac(session, st, rows, hb, opts=None) -> list:
    """SDN と Consolidated をまとめて1回でマージする。

    どちらも source は "OFAC" なので、別々に merge を呼ぶと
    「SDN に無い OFAC 行」を消した直後に「Consolidated に無い行」を
    消すことになり、互いの分を削除し合う。実データで
    Consolidated の削除がマスターの OFAC 全件を超えて発覚した。
    """
    opts = opts or {}
    records: list = []
    fetched_any = False
    baseline = True

    for key, cfg in ofac.LISTS.items():
        prev = st.get(key, {})
        baseline = baseline and bool(prev.get("baseline_synced"))
        prim = fetch(cfg["prim"], prev=prev, session=session)

        if prim.not_modified or prim.sha256 == prev.get("sha256"):
            log("unchanged", cfg["name"],
                "変更なし" + (" — 304 Not Modified（ダウンロードなし）"
                            if prim.not_modified else ""))
            hb.append(dict(source=key, status="unchanged",
                           content_hash=prev.get("sha256", ""),
                           source_updated=prev.get("source_updated", ""),
                           record_count=prev.get("record_count", "")))
            # 変更が無くても、片方だけ更新された場合に他方を
            # 掲載終了と誤判定しないよう、保存済みの生ファイルから読み直す。
            cached = _latest_raw(key, cfg)
            if cached:
                records += cached
            continue

        fetched_any = True
        alt = fetch(cfg["alt"], session=session, allow_conditional=False)
        archive(prim, key, ROOT)
        archive(alt, key, ROOT)
        prune_raw(key, ROOT)

        part = ofac.parse(prim, alt, cfg["label"])
        records += part
        st[key] = dict(sha256=prim.sha256, etag=prim.etag,
                       last_modified=prim.last_modified,
                       source_updated=prim.last_modified,
                       record_count=len(part), baseline_synced=True,
                       raw_prim=prim.raw_path, raw_alt=alt.raw_path)
        hb.append(dict(source=key, status="fetched", content_hash=prim.sha256,
                       source_updated=prim.last_modified,
                       record_count=len(part), raw_path=prim.raw_path))

    if not fetched_any:
        return []

    # OFAC は classic CSV (SDN.CSV + ALT.CSV) から取っているが、既存マスターは
    # 別名がより充実した Advanced XML 由来とみられ、約11,000件の粒度差がある。
    # SDN+ALT から取れる名前は39,468件、マスターのOFAC分は50,566件。
    # この差を掲載終了として無効化すると制裁対象の別名が照合から消える。
    # 取りこぼしは誤検知よりはるかに重大なので、削除は報告のみに留める。
    # Advanced XML パーサに移行したら delist を有効化してよい。
    d = M.merge(rows, records, ofac.SOURCE, delist=False)
    log("changed" if d else "no_effective_change", "OFAC",
        " ".join(f"{k}{v}" for k, v in d.counts.items()) if d else "実質変更なし")
    return [d] if d else []


def _latest_raw(key: str, cfg: dict) -> list:
    """保存済みの生ファイルから再パースする。

    片方のリストだけが更新された回に、もう片方を取り直さずに済ませると
    そのリストの全件が「掲載終了」と判定される。304 のときは
    アーカイブから読み戻して突合対象に含める。
    """
    d = ROOT / "data" / "raw" / key
    if not d.exists():
        return []
    prim = sorted(d.glob("*SDN.CSV"), reverse=True) or \
        sorted(d.glob("*PRIM.CSV"), reverse=True)
    alt = sorted(d.glob("*ALT.CSV"), reverse=True)
    if not prim:
        return []

    class _Raw:
        def __init__(self, path):
            self.body = path.read_bytes()

        @property
        def text(self):
            for enc in ("utf-8-sig", "utf-8", "cp1252"):
                try:
                    return self.body.decode(enc)
                except UnicodeDecodeError:
                    continue
            return self.body.decode("utf-8", errors="replace")

    try:
        return ofac.parse(_Raw(prim[0]), _Raw(alt[0]) if alt else None,
                          cfg["label"])
    except Exception as e:  # noqa: BLE001
        print(f"  警告: {key} の生ファイル再パースに失敗: {e}", flush=True)
        return []


# ------------------------------------------------------------------ 経産省

def run_meti(session, st, rows, hb, opts=None) -> list[dict]:
    prev = st.get("meti", {})
    res = meti.check(session=session)
    sig = res["signature"]
    changed = sig != prev.get("signature")

    hb.append(dict(source="meti", status="updated" if changed else "unchanged",
                   content_hash=res["fetched"].sha256,
                   source_updated=";".join(res["dates"][:3])))

    if not changed:
        log("unchanged", meti.NAME, "変更なし")
        return []

    st["meti"] = dict(signature=sig, pdfs=res["pdfs"], dates=res["dates"],
                      sha256=res["fetched"].sha256)
    archive(res["fetched"], "meti", ROOT)
    prune_raw("meti", ROOT)
    log("updated", meti.NAME, "更新検出（PDFのため要手動取込）")
    # PDF はパースしない。マスターの経産省分は据え置き、人に通知だけする。
    return [dict(source=meti.SOURCE, pdfs=res["pdfs"], dates=res["dates"])]


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
    opts = dict(no_delist=args.no_delist, ignore_drift=args.ignore_drift)

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
            hb.append(dict(source=t, status="error", content_hash=""))

    if args.dry_run:
        print(M.render_markdown(diffs))
        return 1 if failed else 0

    S.heartbeat(ROOT, hb)
    if diffs:
        M.save(rows, MASTER)
        DIFF_MD.parent.mkdir(parents=True, exist_ok=True)
        DIFF_MD.write_text(M.render_markdown(diffs), encoding="utf-8")
        M.write_diff_csv(diffs, DIFF_CSV)
    S.save_state(ROOT, st)

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
