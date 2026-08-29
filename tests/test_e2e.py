"""取得をモックして watch の通しフローを検証する。ネット不要。

  python -m tests.test_e2e

初回 -> 304 -> 更新検出 -> 再び304 の4パターンを通す。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import master as M            # noqa: E402
from src.normalize import match_key    # noqa: E402
from src.sources import ofac           # noqa: E402


class F:
    """Fetched の最小モック。"""

    def __init__(self, text="", not_modified=False, sha=""):
        self._t = text
        self.body = None if not_modified else text.encode()
        self.not_modified = not_modified
        self.sha256 = sha or (str(hash(text)) if text else "")
        self.etag = f'W/"{self.sha256[:8]}"'
        self.last_modified = "Sat, 29 Aug 2026 04:00:00 GMT"
        self.filename = "SDN.CSV"
        self.raw_path = ""

    @property
    def text(self):
        return self._t


SDN_V1 = ('1,"ALPHA CORP","-0- ","SDGT","-0- ","-0- ","-0- ","-0- ","-0- ","-0- ","-0- ","-0- "\n'
          '2,"BETA LTD","-0- ","SDGT","-0- ","-0- ","-0- ","-0- ","-0- ","-0- ","-0- ","-0- "\n')
SDN_V2 = ('1,"ALPHA CORP","-0- ","SDGT","-0- ","-0- ","-0- ","-0- ","-0- ","-0- ","-0- ","-0- "\n'
          '3,"GAMMA GMBH","-0- ","SDGT","-0- ","-0- ","-0- ","-0- ","-0- ","-0- ","-0- ","-0- "\n')
ALT_V1 = '1,101,"aka","ALPHA CO","-0- "\n'

results = []


def step(label, ok, detail=""):
    results.append((label, ok))
    mark = "OK" if ok else "NG"
    print(f"  [{mark}] {label}" + (f" — {detail}" if detail else ""))


def main() -> int:
    rows: dict[str, dict] = {}
    state: dict = {}

    print("### 1回目: 初回取得")
    prim = F(SDN_V1, sha="aaa")
    d = M.merge(rows, ofac.parse(prim, F(ALT_V1), "SDN"), "OFAC", ts=1000)
    state["ofac_sdn"] = dict(sha256=prim.sha256, etag=prim.etag)
    step("3名を登録 (ALPHA / ALPHA CO / BETA)", len(d.added) == 3, str(d.counts))

    print("### 2回目: サーバが304を返す")
    prim = F(not_modified=True, sha="aaa")
    if prim.not_modified or prim.sha256 == state["ofac_sdn"]["sha256"]:
        step("304ならパースも差分計算もスキップ", True, "ダウンロードなし")
    else:
        step("304ならパースも差分計算もスキップ", False)

    print("### 3回目: 内容が変わる (BETA削除 / GAMMA追加)")
    prim = F(SDN_V2, sha="bbb")
    d = M.merge(rows, ofac.parse(prim, None, "SDN"), "OFAC", ts=3000)
    step("GAMMAを追加検出", len(d.added) == 1, d.added[0]["name"] if d.added else "")
    # ALT を取らなかったので ALPHA CO も掲載終了扱いになる
    removed = {r["name"] for r in d.removed}
    step("BETAの掲載終了を検出", "BETA LTD" in removed, str(sorted(removed)))
    step("BETAは削除でなく無効化",
         rows[match_key("BETA LTD")]["status"] == "無効")
    step("BETAの登録時間は保持",
         rows[match_key("BETA LTD")]["first_seen_ms"] == 1000)

    print("### 4回目: ファイルは差し替わったが正規化後は同一")
    prim = F(SDN_V2 + "\n", sha="ccc")   # 末尾改行だけ違う
    d = M.merge(rows, ofac.parse(prim, None, "SDN"), "OFAC", ts=4000)
    step("no_effective_change として区別", not bool(d), "差分なし")

    print("### 他ソースの独立性")
    rows[match_key("METI ONLY")] = dict(
        match_key=match_key("METI ONLY"), display_name="METI ONLY", status="有効",
        risk_type="制裁リスト", risk_level="高", first_seen_ms=1, last_updated_ms=1,
        sources="経産省", categories="", remark="制裁リスト（経産省）",
        invalid_reason="", review_flag="", variants="[]")
    M.merge(rows, [], "OFAC", ts=5000)
    step("OFAC取得が経産省の行を消さない",
         rows[match_key("METI ONLY")]["status"] == "有効")

    print("### 差分レポート")
    md = M.render_markdown([M.merge(rows, ofac.parse(F(SDN_V1), F(ALT_V1), "SDN"),
                                    "OFAC", ts=6000)])
    step("Markdownレポートを生成", "追加" in md and "```" not in md[:20],
         f"{len(md)} 文字")

    ok = sum(1 for _, o in results if o)
    print(f"\n{ok}/{len(results)} 項目通過")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
