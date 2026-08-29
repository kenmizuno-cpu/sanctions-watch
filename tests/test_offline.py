"""ネット接続不要の自己テスト。

Actions では取得の前に必ず走らせる。正規化ロジックが壊れた状態で
マスターを上書きするのが一番怖いため。

  python -m tests.test_offline
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import master as M                      # noqa: E402
from src.normalize import (canonical_category, clean_name, match_key,  # noqa: E402
                           needs_review, parse_remark, parse_remark_multi,
                           render_remark, split_aliases, validate)
from src.sources import meti, mof, ofac          # noqa: E402

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    (PASS if got == want else FAIL).append(label)
    if got != want:
        print(f"  NG {label}\n     got : {got!r}\n     want: {want!r}")


# ---------------------------------------------------------------- 正規化
def test_match_key() -> None:
    check("大文字小文字を吸収",
          match_key("'ABD AL-MALIK") == match_key("'Abd al-Malik"), True)
    check("クォート記号の揺れを吸収",
          match_key("’Abd Al-Haq") == match_key("'Abd AL-HAQ"), True)
    check("全角数字を吸収", match_key("５SRZ") == match_key("5 SRZ"), True)
    check("全角スペースを吸収",
          match_key("A\u3000B") == match_key("A B"), True)
    check("別人は統合しない", match_key("Ali Hassan") == match_key("Ali Hussein"), False)


def test_clean_name() -> None:
    check("前後空白", clean_name("  ABDULMALIK  "), "ABDULMALIK")
    check("全角スペース", clean_name("A\u3000B"), "A B")
    check("連続空白", clean_name("A    B"), "A B")
    check("ローマ数字を壊さない", clean_name("リビア (Ⅱ)"), "リビア (Ⅱ)")


def test_split_aliases() -> None:
    # マーカーが無い行は絶対に割らない。
    # a.k.a. をドット無しで書くと Abu[baka]r に誤爆して3分割される事故があった。
    check("aka誤爆しない", split_aliases("Abdifatah Abubakar Abdi"),
          ["Abdifatah Abubakar Abdi"])
    check("Makara誤爆しない", split_aliases("7 MAKARA PHARY CO., LTD."),
          ["7 MAKARA PHARY CO., LTD."])
    check("読点だけでは割らない", split_aliases("“ズベイル、アブ”"), ["“ズベイル、アブ”"])
    check("括弧内の英名を割らない", split_aliases("「チーフ・カワ（Chief Kahwa）」"),
          ["「チーフ・カワ（Chief Kahwa）」"])
    check("別名N接頭辞", split_aliases("（別名1）A・B、C・D"), ["A・B", "C・D"])
    check("別称インライン", split_aliases("（別称、ヨッフェ研究所） Ioffe Institute"),
          ["ヨッフェ研究所", "Ioffe Institute"])
    check("aka括弧", split_aliases("Igor Chayka (Chaika; a.k.a. IFYAU9)"),
          ["Igor Chayka", "Chaika", "IFYAU9"])
    check("船舶メタデータ除去",
          split_aliases("MS ANGIA (a.k.a. GATHER VIEW) (T7AX8) Crude Oil Tanker San Marino flag"),
          ["MS ANGIA", "GATHER VIEW"])
    check("囲み括弧を外す", split_aliases("(Gaffar Mohmed Elhassan)"),
          ["Gaffar Mohmed Elhassan"])
    check("閉じ忘れ括弧", split_aliases("（別称、「株式会社UZGA」"), ["「株式会社UZGA」"])
    check("通常名はそのまま", split_aliases("ABDALLAH AZZAM BRIGADES (AAB)"),
          ["ABDALLAH AZZAM BRIGADES (AAB)"])


def test_validate() -> None:
    check("数字のみは無効", bool(validate("27")), True)
    check("Excelシリアルは無効", bool(validate("45866")), True)
    check("空は無効", bool(validate("   ")), True)
    check("通常名は有効", validate("ABDULMALIK AL-HOUTHI"), None)
    # 別名や短い名前は「無効化」ではなく「要確認」に回す。
    # 取りこぼしは誤検知より重大なため。
    check("短い名は無効化しない", validate("ADF"), None)
    check("短い名は要確認", bool(needs_review("ADF")), True)
    check("CJK2文字は要確認に留める", validate("张伟"), None)
    check("通常名は要確認にしない", needs_review("ABDULMALIK AL-HOUTHI"), None)


def test_remark() -> None:
    check("制裁リスト書式", parse_remark("制裁リスト（2.タリバーン関係者等)"),
          ("財務省", "タリバーン関係者等"))
    check("番号書式", parse_remark("29.ロシア連邦(個人)"), ("財務省", "ロシア連邦(個人)"))
    check("OFAC", parse_remark("OFAC"), ("OFAC", ""))
    check("OFACタイポ", parse_remark("OFCA"), ("OFAC", ""))
    check("METI", parse_remark("METI"), ("経産省", ""))
    check("財務省タイポ", parse_remark("財務諸表　タリバーン関係者")[0], "財務省")
    check("外務省告示", parse_remark("ロシア連邦の特定団体への輸出等に係る禁止措置(外務省告示第61号）")[0],
          "外務省")
    # 財務省は新カテゴリ挿入のたびに以降を繰り下げる。番号を残すと
    # 再採番のたびに数千行が偽の「変更」判定になる。
    check("番号ドリフトを吸収",
          parse_remark("39.ハイチ共和国") == parse_remark("40.ハイチ共和国"), True)
    check("閉じ括弧不足を補正",
          canonical_category("ロシア連邦(団体(特定銀行を除く)"), "ロシア連邦(団体(特定銀行を除く))")
    check("単一出所の描画", render_remark([("財務省", "タリバーン関係者等")]),
          "制裁リスト（財務省：タリバーン関係者等）")
    check("複数出所の描画",
          render_remark([("財務省", "タリバーン関係者等"), ("OFAC", "")]),
          "制裁リスト（財務省：タリバーン関係者等／OFAC）")


def test_remark_roundtrip() -> None:
    """生成した G列 を読み戻せること。

    配布した Excel からマスターを作り直せないと、元データを失ったときに
    詰む。実際に生成物を再取り込みして複数出所が全て消える事故が起きた。
    """
    for pairs in ([("財務省", "タリバーン関係者等")],
                  [("財務省", "タリバーン関係者等"), ("OFAC", "")],
                  [("経産省", ""), ("OFAC", "SDN")],
                  [("OFAC", "")],
                  [("出所不明", "令和5年12月15日外為法")],
                  [("UK FCDO", "")]):
        rendered = render_remark(pairs)
        check(f"往復: {rendered}", sorted(parse_remark_multi(rendered)), sorted(pairs))
    # 旧書式は従来どおり
    check("旧書式は従来経路", parse_remark_multi("制裁リスト（2.タリバーン関係者等)"),
          [("財務省", "タリバーン関係者等")])
    check("素のOFAC表記", parse_remark_multi("OFAC"), [("OFAC", "")])
    check("未知の出所名は旧書式扱い",
          parse_remark_multi("制裁リスト（29.ロシア連邦(個人)）"),
          [("財務省", "ロシア連邦(個人)")])


# ---------------------------------------------------------------- パーサ
MOF_CSV = """区分,番号,告示日付,告示番号,個人・団体,氏名（日本語）,氏名（英語）,別名
2.タリバーン関係者等,001-000001,2022-03-31,1,個人,モハンマド・ハッサン,MOHAMMAD HASSAN,Mohammad Hasan;Hassan M
29.ロシア連邦(個人),002-000002,2024-01-05,7,個人,イワン・イワノフ,IVAN IVANOV,
"""

OFAC_SDN = """1,"AEROCARIBBEAN AIRLINES","-0- ","CUBA","-0- ","-0- ","-0- ","-0- ","-0- ","-0- ","-0- ","-0- "
2,"ANGLO-CARIBBEAN CO., LTD.","-0- ","CUBA","-0- ","-0- ","-0- ","-0- ","-0- ","-0- ","-0- ","-0- "
"""
OFAC_ALT = """1,101,"aka","AERO-CARIBBEAN","-0- "
2,102,"aka","ANGLO CARIBBEAN","-0- "
"""


class _F:
    def __init__(self, text: str):
        self._t = text
        self.body = text.encode()

    @property
    def text(self) -> str:
        return self._t


def test_parsers() -> None:
    recs = mof.parse(_F(MOF_CSV))
    names = {r["name"] for r in recs}
    check("財務省: 日英別名を全て展開",
          {"モハンマド・ハッサン", "MOHAMMAD HASSAN", "Mohammad Hasan",
           "Hassan M", "イワン・イワノフ", "IVAN IVANOV"} <= names, True)
    check("財務省: 区分番号を落とす",
          {r["category"] for r in recs},
          {"タリバーン関係者等", "ロシア連邦(個人)"})

    try:
        mof.parse(_F("foo,bar\n1,2\n"))
        check("財務省: 列構成変更で例外", False, True)
    except mof.SchemaError:
        check("財務省: 列構成変更で例外", True, True)

    o = ofac.parse(_F(OFAC_SDN), _F(OFAC_ALT), "SDN")
    on = {r["name"] for r in o}
    check("OFAC: 主名称と別名を結合",
          {"AEROCARIBBEAN AIRLINES", "AERO-CARIBBEAN",
           "ANGLO-CARIBBEAN CO., LTD.", "ANGLO CARIBBEAN"} <= on, True)
    check("OFAC: -0- を名前にしない", any("-0-" in n for n in on), False)

    sig = meti.signature('<a href="/x/user_list.pdf">令和7年10月9日</a>')
    check("経産省: 署名にPDFと日付が入る",
          "user_list.pdf" in sig and "令和7年10月9日" in sig, True)
    check("経産省: 無関係な差分では署名が変わらない",
          meti.signature('<a href="/x/user_list.pdf">令和7年10月9日</a><p>閲覧数 12</p>'),
          sig)


# ---------------------------------------------------------------- マージ
def _rec(name, cat="SDN", src="OFAC"):
    return dict(source=src, category=cat, name=name, source_id="1")


def test_merge() -> None:
    rows: dict[str, dict] = {}

    d = M.merge(rows, [_rec("ALPHA CORP"), _rec("BETA LTD")], "OFAC", ts=1000)
    check("新規追加", (len(d.added), len(d.removed), len(d.changed)), (2, 0, 0))
    check("初回登録時間", rows[match_key("ALPHA CORP")]["first_seen_ms"], 1000)

    d = M.merge(rows, [_rec("ALPHA CORP"), _rec("BETA LTD")], "OFAC", ts=2000)
    check("同じ内容なら差分なし", bool(d), False)
    check("無変更なら更新時間を触らない",
          rows[match_key("ALPHA CORP")]["last_updated_ms"], 1000)

    # 他ソースの行を巻き込まないこと。経産省(PDF)や外務省の行は
    # 自動再取得できないため、全件入れ替え実装だと毎回消える。
    rows[match_key("METI ONLY CO")] = dict(
        match_key=match_key("METI ONLY CO"), display_name="METI ONLY CO",
        status="有効", risk_type="制裁リスト", risk_level="高",
        first_seen_ms=500, last_updated_ms=500, sources="経産省",
        categories="", remark="制裁リスト（経産省）", invalid_reason="",
        review_flag="", variants="[]")
    d = M.merge(rows, [_rec("ALPHA CORP")], "OFAC", ts=3000)
    check("他ソースの行を消さない",
          rows[match_key("METI ONLY CO")]["status"], "有効")
    check("掲載終了を検出", len(d.removed), 1)
    check("掲載終了は削除でなく無効化",
          rows[match_key("BETA LTD")]["status"], "無効")
    check("無効化理由を残す",
          rows[match_key("BETA LTD")]["invalid_reason"], M.DELISTED)

    # 複数ソースに載る対象は、片方から消えても有効のまま
    M.merge(rows, [_rec("ALPHA CORP", cat="タリバーン関係者等", src="財務省")],
            "財務省", ts=4000)
    # SDN と Consolidated は制裁の性質が違う(前者は資産凍結、後者は
    # セクター制裁等を含む)ので、ラベルは残す価値がある。
    check("複数出所を併記",
          rows[match_key("ALPHA CORP")]["remark"],
          "制裁リスト（財務省：タリバーン関係者等／OFAC：SDN）")
    M.merge(rows, [], "財務省", ts=5000)
    check("片方から消えても有効",
          rows[match_key("ALPHA CORP")]["status"], "有効")
    check("残った出所だけになる",
          rows[match_key("ALPHA CORP")]["remark"], "制裁リスト（OFAC：SDN）")

    # 再掲載されたら有効に戻る
    M.merge(rows, [_rec("ALPHA CORP"), _rec("BETA LTD")], "OFAC", ts=6000)
    check("再掲載で有効に戻る", rows[match_key("BETA LTD")]["status"], "有効")


def test_roundtrip() -> None:
    rows: dict[str, dict] = {}
    M.merge(rows, [_rec("ALPHA CORP"), _rec("ベータ商事")], "OFAC", ts=1000)
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "master.csv"
        M.save(rows, p)
        back = M.load(p)
    check("保存して読み直しても同じ", set(back), set(rows))
    check("列が揃っている", set(back[match_key("ALPHA CORP")]), set(M.FIELDS))


def main() -> int:
    for fn in (test_match_key, test_clean_name, test_split_aliases, test_validate,
               test_remark, test_remark_roundtrip, test_parsers, test_merge,
               test_roundtrip):
        fn()
    total = len(PASS) + len(FAIL)
    print(f"\n{len(PASS)}/{total} 項目通過")
    if FAIL:
        print("失敗: " + ", ".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
