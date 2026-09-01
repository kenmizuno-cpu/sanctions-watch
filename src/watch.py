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
from .fetch import archive, fetch, prune_raw, read_raw
from .sources import meti, mof, ofac

ROOT = Path(__file__).resolve().parent.parent
MASTER = ROOT / "data" / "master" / "master.csv"
DIFF_MD = ROOT / "data" / "diff" / "latest.md"
DIFF_CSV = ROOT / "data" / "diff" / "latest.csv"

# OFAC の掲載終了判定。classic CSV では既存マスターと粒度が合わないため無効。
# Advanced XML パーサに移行したら True にする。詳細は run_ofac 内のコメント。
OFAC_DELIST = False


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
    unchanged: list[tuple[str, dict]] = []
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
            # ここでは読まない。全リストが変更なしなら突合そのものを行わないので、
            # 再パースした結果は捨てられる。実測で 7MB / 41,059 レコードを
            # 毎時展開して破棄していた。必要になった場合のみ後段で読み込む。
            unchanged.append((key, prev))
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

    # 片方だけ更新された回。更新されなかったリストを突合対象に含めないと、
    # そのリストの全件が「掲載終了」と判定される。ここで初めて読み戻す。
    cache_ok = True
    for key, prev in unchanged:
        cached = _latest_raw(key, ofac.LISTS[key], prev)
        if cached:
            records += cached
            log("cached", ofac.LISTS[key]["name"], f"保存済みから {len(cached)} 件")
        else:
            cache_ok = False
            log("WARN", ofac.LISTS[key]["name"],
                "保存済み生ファイルを読めなかった。掲載終了の誤判定を避けるため"
                "このリスト分は突合対象から外れる")

    # OFAC は classic CSV (SDN.CSV + ALT.CSV) から取っているが、既存マスターは
    # 別名がより充実した Advanced XML 由来とみられ、約11,000件の粒度差がある。
    # SDN+ALT から取れる名前は39,468件、マスターのOFAC分は50,566件。
    # この差を掲載終了として無効化すると制裁対象の別名が照合から消える。
    # 取りこぼしは誤検知よりはるかに重大なので、削除は報告のみに留める。
    # Advanced XML パーサに移行したら OFAC_DELIST を True にしてよい。
    #
    # cache_ok を条件に入れているのは、更新されなかったリストを読み戻せなかった
    # 回に delist すると、そのリストの全件が一斉に無効化されるため。
    # 将来 delist を有効化したとき、この保険が無いと静かに大量削除が起きる。
    delist = OFAC_DELIST and cache_ok
    if OFAC_DELIST and not cache_ok:
        log("WARN", "OFAC", "生ファイルを読み戻せなかったため今回は delist を抑止する")
    d = M.merge(rows, records, ofac.SOURCE, delist=delist)
    log("changed" if d else "no_effective_change", "OFAC",
        " ".join(f"{k}{v}" for k, v in d.counts.items()) if d else "実質変更なし")
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
    """経産省の更新監視。**失敗してもワークフローは落とさない。**

    経産省サイトは WAF で自動アクセスを拒否しており、弾かれるのが通常状態。
    更新は年1〜3回しかないので監視が止まっても実害はないが、この監視のせいで
    毎回ワークフローが赤くなると財務省や OFAC の本当の障害を見逃す。
    """
    prev = st.get("meti", {})
    try:
        res = meti.check(session=session)
    except Exception as e:  # noqa: BLE001
        log("blocked", meti.NAME, f"取得できず: {e}")
        hb.append(dict(source="meti", status="blocked"))
        return []

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
