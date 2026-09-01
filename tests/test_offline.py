"""ネット接続不要の自己テスト。

Actions では取得の前に必ず走らせる。正規化ロジックが壊れた状態で
マスターを上書きするのが一番怖いため。

  python -m tests.test_offline
"""
from __future__ import annotations

import csv
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import master as M                      # noqa: E402
from src.normalize import (canonical_category, clean_name, match_key,  # noqa: E402
                           needs_review, parse_remark, parse_remark_multi,
                           render_remark, split_aliases,
                           swap_surname_first, validate)
from src.sources import meti, mof, ofac          # noqa: E402

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    (PASS if got == want else FAIL).append(label)
    if got != want:
        print(f"  NG {label}\n     got : {got!r}\n     want: {want!r}")


# ---------------------------------------------------------------- 正規化
def test_surname_order() -> None:
    """OFAC の `姓, 名` と自然順を同一視すること。

    実データで OFAC の CSV は `HANIYAH, Ismail Abdul Salah`、
    既存マスターは `Ismail Abdul Salah Haniyah` と判明。揃えないと
    同一人物が削除+追加として二重に出る。
    """
    check("語順違いを吸収",
          match_key("HANIYAH, Ismail Abdul Salah")
          == match_key("Ismail Abdul Salah Haniyah"), True)
    check("語順違いを吸収(2)",
          match_key("GUZMAN SALAZAR, Archivaldo Ivan")
          == match_key("Archivaldo Ivan Guzman Salazar"), True)
    # 団体名の読点は語順ではなく法人格の区切り
    check("法人格は入れ替えない",
          swap_surname_first("7 MAKARA PHARY CO., LTD."), "7 MAKARA PHARY CO., LTD.")
    check("法人格は入れ替えない(2)",
          swap_surname_first("A&A ESTUDIO, S. DE R.L. DE C.V."),
          "A&A ESTUDIO, S. DE R.L. DE C.V.")
    check("読点3つ以上は触らない",
          swap_surname_first("A, B, C"), "A, B, C")
    check("別人は一致しない",
          match_key("SMITH, John") == match_key("SMITH, Jane"), False)


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
# 実データ (shisantouketsu20260828.csv) と同じ32列構成の抜粋。
# 別名の区切りは全角 `；`。括弧の内側にも `；` が出るのが罠。
_MOF_HEADER = (
    "区分,番号,告示日付,告示番号,個人・団体,氏名（日本語）,氏名（英語）,"
    "別名・別称（日本語）,別名・別称（英語）,旧称（日本語）,旧称（英語）,"
    "確定に十分でない別名（日本語）,確定に十分でない別名（英語）,"
    "称号（日本語）,称号（英語）,役職（日本語）,役職（英語）,生年月日,"
    "出生地（日本語）,出生地（英語）,国籍（日本語）,国籍（英語）,旅券番号,"
    "身分証番号,住所・所在地（国）（日本語）,住所・所在地（都市その他の情報）（日本語）,"
    "住所・所在地（国）（英語）,住所・所在地（都市その他の情報）（英語）,"
    "国連参照番号,リスト掲載日,その他の情報,外務省告示情報")

MOF_CSV = _MOF_HEADER + "\n" + "\n".join([
    # 別名に説明文が付き、括弧の内側に ； がある実例
    ('2,002-000160,2001.9.22,160,個人,モハンメド・ジダン,Mohammed Zidane,'
     '"サイフ・アル・アドル（生年月日1963/4/11、出生地エジプト、国籍エジプト）；'
     ' ムハマド・マッカウィ(生年月日1960/4/11； 1963/4/11、国籍エジプト)",'
     '"Sayf-Al Adl (DOB: 11 Apr. 1963. POB: Egypt.)",,,'
     'イブラヒム・アル・マダニ,Ibrahim al-Madani,'
     'ムラー; ハッジ,Mullah; Haji,閣僚評議会第一副議長,First Deputy,'
     '1963/4/11,,,エジプト,Egypt,,,,,,,QDi.001,,,'),
    # 引用符付きの団体名
    '28,028-000001,2022.3.1,1,団体,,"150 Aircraft Repair Plant",,,,,,,,,,,,,,,,,,,,,,,,,',
]) + "\n"

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


def test_mof_parser() -> None:
    kmap = {"2": "タリバーン関係者等", "28": "ロシア連邦(団体)"}
    recs = mof.parse(_F(MOF_CSV), kubun_map=kmap)
    names = {r["name"] for r in recs}

    # 括弧の内側の ； で名前が割れないこと
    check("財務省: 説明文を落として名前だけ取る",
          {"サイフ・アル・アドル", "ムハマド・マッカウィ", "Sayf-Al Adl"} <= names, True)
    check("財務省: 生年月日の断片を作らない",
          any(re.search(r"^\d{4}/", n) or "国籍" in n or "DOB" in n for n in names),
          False)
    check("財務省: 確定に十分でない別名も取り込む",
          {"イブラヒム・アル・マダニ", "Ibrahim al-Madani"} <= names, True)
    # 称号と役職は敬称・肩書きであって名前ではない
    check("財務省: 称号を名前にしない", "ムラー" in names or "Mullah" in names, False)
    check("財務省: 役職を名前にしない", "閣僚評議会第一副議長" in names, False)
    check("財務省: 区分番号をカテゴリ名に変換",
          {r["category"] for r in recs}, {"タリバーン関係者等", "ロシア連邦(団体)"})

    # 引用符の有無で同一実体が別物にならないこと
    check("財務省: 引用符付き団体名と素の名前が同一キー",
          match_key("“150 Aircraft Repair Plant”") == match_key("150 Aircraft Repair Plant"),
          True)

    try:
        mof.parse(_F("foo,bar\n1,2\n"), kubun_map={})
        check("財務省: 列構成変更で例外", False, True)
    except mof.SchemaError:
        check("財務省: 列構成変更で例外", True, True)

    check("財務省: 深さ0の；でだけ分割",
          mof.split_top_level("A（x；y）；B"), ["A（x；y）", "B"])
    check("財務省: 説明括弧を除去",
          mof.strip_descriptor("名前（生年月日1963/4/11、国籍エジプト）"), "名前")
    check("財務省: 通常の括弧は残す",
          mof.strip_descriptor("ABDALLAH AZZAM BRIGADES (AAB)"),
          "ABDALLAH AZZAM BRIGADES (AAB)")


def test_parsers() -> None:

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


# ---------------------------------------------------------------- 生ファイル
def test_archive_roundtrip() -> None:
    """gzip 保存した生ファイルが元のバイト列に戻ること。

    生データは git 履歴に永久に残るので圧縮して保存する。展開できなければ
    片方だけ更新された回に相手側を読み戻せず、全件が掲載終了と誤判定される。
    """
    from src.fetch import Fetched, archive, read_raw

    body = "ent_num,name\n1,ALPHA CORP\n".encode()
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        f = Fetched(url="http://x/sdn.csv", body=body, filename="sdn.csv")
        rel = archive(f, "ofac_sdn", root)
        check("gzip で保存される", rel.endswith(".csv.gz"), True)
        check("gzip 往復で一致", read_raw(root / rel), body)
        check("raw_path が記録される", f.raw_path, rel)

        # 圧縮導入前に保存された非圧縮ファイルも読めること
        f2 = Fetched(url="http://x/old.csv", body=body, filename="old.csv")
        rel2 = archive(f2, "ofac_sdn", root, compress=False)
        check("非圧縮も読める", read_raw(root / rel2), body)


def test_resolve_raw() -> None:
    """保存済み生ファイルの特定。

    SLS は Content-Disposition で小文字のファイル名を返すため実体は
    `..__sdn.csv` になる。以前は `*SDN.CSV` で glob していて Linux では
    常に何もマッチせず、この読み戻しが丸ごと機能していなかった。
    """
    import src.watch as W

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        d = root / "data" / "raw" / "ofac_sdn"
        d.mkdir(parents=True)
        for n in ("20260101T000000Z__sdn.csv.gz", "20260101T000000Z__alt.csv.gz",
                  "20250101T000000Z__sdn.csv.gz", "20250101T000000Z__alt.csv.gz"):
            (d / n).write_bytes(b"x")

        orig, W.ROOT = W.ROOT, root
        try:
            prim, alt = W.resolve_raw("ofac_sdn", {})
            check("小文字 sdn.csv.gz を拾う", prim.name if prim else None,
                  "20260101T000000Z__sdn.csv.gz")
            check("小文字 alt.csv.gz を拾う", alt.name if alt else None,
                  "20260101T000000Z__alt.csv.gz")

            # state.json の記録が優先されること
            prim2, _ = W.resolve_raw("ofac_sdn", {
                "raw_prim": "data/raw/ofac_sdn/20250101T000000Z__sdn.csv.gz",
                "raw_alt": "data/raw/ofac_sdn/20250101T000000Z__alt.csv.gz"})
            check("state の raw_prim が優先される", prim2.name,
                  "20250101T000000Z__sdn.csv.gz")

            # state が壊れた参照を持っていても glob に落ちること
            prim3, _ = W.resolve_raw("ofac_sdn", {"raw_prim": "data/raw/ofac_sdn/nope.gz"})
            check("消えた参照は glob に落ちる", prim3.name if prim3 else None,
                  "20260101T000000Z__sdn.csv.gz")

            cons = root / "data" / "raw" / "ofac_cons"
            cons.mkdir(parents=True)
            (cons / "20260101T000000Z__cons_prim.csv.gz").write_bytes(b"x")
            (cons / "20260101T000000Z__cons_alt.csv.gz").write_bytes(b"x")
            p4, a4 = W.resolve_raw("ofac_cons", {})
            check("cons_prim を拾う", p4.name if p4 else None,
                  "20260101T000000Z__cons_prim.csv.gz")
            check("cons_alt を拾う", a4.name if a4 else None,
                  "20260101T000000Z__cons_alt.csv.gz")
        finally:
            W.ROOT = orig


def test_prune_raw() -> None:
    """保管数の制限は世代単位で数えること。

    世代 = 同じ取得時刻に書かれたファイルの組。ファイル数で切ると
    OFAC (1回2本) の保持期間が財務省 (1回1本) の半分になり、さらに
    世代の途中で切れて alt を失った prim だけが残ることがある。
    その状態で _latest_raw() が走ると別名なしでパースされ、39,468件が
    静かに1万件程度まで減る。
    """
    from src.fetch import prune_raw, raw_generations

    def put(d: Path, gens: int, names) -> None:
        for i in range(gens):
            for n in names:
                (d / f"202601{i + 1:02d}T000000Z__{n}").write_bytes(b"x")

    def gens_of(d: Path) -> int:
        return len({p.name.split("__")[0] for p in d.iterdir()
                    if not p.name.startswith(".")})

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        mof = root / "data" / "raw" / "mof"
        sdn = root / "data" / "raw" / "ofac_sdn"
        mof.mkdir(parents=True)
        sdn.mkdir(parents=True)
        put(mof, 20, ["shisantouketsu.csv.gz"])
        put(sdn, 20, ["sdn.csv.gz", "alt.csv.gz"])

        prune_raw("mof", root, keep=5)
        prune_raw("ofac_sdn", root, keep=5)
        check("財務省が5世代残る", gens_of(mof), 5)
        check("OFACも5世代残る", gens_of(sdn), 5)
        check("OFACは1世代2本なので10ファイル", len(list(sdn.iterdir())), 10)

        # どの世代も prim と alt が揃っていること
        pairs: dict[str, set] = {}
        for p in sdn.iterdir():
            pairs.setdefault(p.name.split("__")[0], set()).add(p.name.split("__")[1])
        check("全世代でaltが揃っている",
              all(v == {"sdn.csv.gz", "alt.csv.gz"} for v in pairs.values()), True)

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        d = root / "data" / "raw" / "meti"
        d.mkdir(parents=True)
        put(d, 3, ["page.html.gz"])
        (d / ".gitkeep").write_bytes(b"")
        (d / "README.txt").write_bytes(b"x")

        removed = prune_raw("meti", root, keep=3)
        check("keep以内なら何も消えない", removed, [])
        check(".gitkeep が残る", (d / ".gitkeep").exists(), True)
        check("命名規則外のファイルは触らない", (d / "README.txt").exists(), True)
        check("世代の抽出に混ざらない", len(raw_generations(d)), 3)

        removed = prune_raw("meti", root, keep=1)
        check("古い2世代だけ消える", len(removed), 2)
        check(".gitkeep はまだ残る", (d / ".gitkeep").exists(), True)

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        check("ディレクトリが無ければ空", prune_raw("nope", root), [])

    # 容量上限。世代数に余裕があっても合計容量で切る。
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        d = root / "data" / "raw" / "ofac_sdn"
        d.mkdir(parents=True)
        for i in range(20):
            (d / f"202601{i + 1:02d}T000000Z__sdn.csv.gz").write_bytes(b"x" * 900_000)
            (d / f"202601{i + 1:02d}T000000Z__alt.csv.gz").write_bytes(b"x" * 300_000)
        prune_raw("ofac_sdn", root, keep=30, max_bytes=6_000_000)
        kept = len({p.name.split("__")[0] for p in d.iterdir()})
        size = sum(p.stat().st_size for p in d.iterdir())
        check("容量上限で世代数が決まる", kept, 5)
        check("上限を超えない", size <= 6_000_000, True)

    # 1世代が上限を超えていても最新だけは残す
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        d = root / "data" / "raw" / "mof"
        d.mkdir(parents=True)
        for i in range(3):
            (d / f"202601{i + 1:02d}T000000Z__x.csv.gz").write_bytes(b"x" * 5_000_000)
        prune_raw("mof", root, keep=30, max_bytes=1_000_000)
        check("最新1世代は必ず残る", len(list(d.iterdir())), 1)
        check("残るのは最新", next(d.iterdir()).name, "20260103T000000Z__x.csv.gz")


def test_dashboard() -> None:
    """スプレッドシート取込用CSV。

    IMPORTDATA で直接読ませるため、小さいことと列見出しが日本語であることが要件。
    status は変更が無い回も必ず書く。ここが古いままなら Actions が止まったと
    判断できるようにするため。
    """
    from src import dashboard as D

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        hb = [dict(source="ofac_sdn", status="unchanged", content_hash="a" * 64,
                   source_updated="Fri, 29 Aug 2026 12:00:00 GMT", record_count=39468),
              dict(source="mof", status="fetched", content_hash="b" * 64,
                   source_updated="", record_count=1200)]
        st = {"ofac_sdn": dict(sha256="a" * 64, record_count=39468)}

        p = D.write_status(root, hb, st)
        rows = list(csv.reader(p.open(encoding="utf-8")))
        check("status の見出し", rows[0], D.STATUS_COLS)
        check("出所が日本語ラベル", rows[1][0], "OFAC SDN")
        check("状態が日本語ラベル", rows[1][1], "変更なし")
        check("最終更新がJST", rows[1][3], "2026-08-29 21:00:00")
        check("ハッシュは短縮", len(rows[1][5]), 12)
        check("財務省の行もある", rows[2][0], "財務省")

        # 変更履歴は新しいものが上、見出しは1回だけ
        D.append_changes(root, [["OFAC", "追加", "ALPHA", "", "x"]], when="2026-01-01 00:00:00")
        pc = D.append_changes(root, [["OFAC", "掲載終了", "BETA", "y", "z"]],
                              when="2026-01-02 00:00:00")
        rows = list(csv.reader(pc.open(encoding="utf-8")))
        check("changes の見出し", rows[0], D.CHANGE_COLS)
        check("見出しは1行だけ", sum(1 for r in rows if r == D.CHANGE_COLS), 1)
        check("新しいものが上", rows[1][3], "BETA")
        check("古い行も残る", rows[2][3], "ALPHA")
        check("検知日時が入る", rows[1][0], "2026-01-02 00:00:00")

        # 上限を超えたら古いものから落ちる
        D.MAX_CHANGES, keep = 3, D.MAX_CHANGES
        try:
            D.append_changes(root, [["X", "追加", f"N{i}", "", ""] for i in range(5)],
                             when="2026-01-03 00:00:00")
            rows = list(csv.reader((root / D.DASH / "changes.csv").open(encoding="utf-8")))
            check("上限で打ち切る", len(rows) - 1, 3)
            check("残るのは新しい方", rows[1][3], "N0")
        finally:
            D.MAX_CHANGES = keep

        entry = dict(display_name="ALPHA", risk_type="t", status="有効",
                     risk_level="高", remark="消えるべき列")
        pl = D.write_list(root, [entry])
        rows = list(csv.reader(pl.open(encoding="utf-8")))
        check("list は4列だけ", rows[0], D.LIST_COLS)
        check("余分な列を含まない", len(rows[1]), 4)

        # M.load() は dict を返すのでそちらでも動くこと
        pl = D.write_list(root, {"k": entry})
        rows = list(csv.reader(pl.open(encoding="utf-8")))
        check("dict を渡しても動く", rows[1][0], "ALPHA")


def main() -> int:
    for fn in (test_surname_order, test_match_key, test_clean_name, test_split_aliases, test_validate,
               test_remark, test_remark_roundtrip, test_mof_parser, test_parsers, test_merge,
               test_roundtrip, test_archive_roundtrip, test_resolve_raw, test_prune_raw,
               test_dashboard):
        fn()
    total = len(PASS) + len(FAIL)
    print(f"\n{len(PASS)}/{total} 項目通過")
    if FAIL:
        print("失敗: " + ", ".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
