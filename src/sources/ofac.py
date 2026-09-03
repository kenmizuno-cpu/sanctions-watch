"""OFAC SDN / Consolidated リスト。

Sanctions List Service (SLS) が現行の配信基盤。
旧 treasury.gov/ofac/downloads/ はここへリダイレクトされるが、
新ホストは User-Agent 必須で、付けないと 403 になる (fetch.py で対応)。

SDN.CSV は主名称、ALT.CSV は別名。ent_num で結合する。
"""
from __future__ import annotations

import csv
import io
import unicodedata
import xml.etree.ElementTree as ET

from ..fetch import Fetched
from ..normalize import SRC_OFAC, clean_name, split_aliases

SOURCE = SRC_OFAC
BASE = "https://sanctionslistservice.ofac.treas.gov/api/download/"
ADVANCED_BASE = (
    "https://sanctionslistservice.ofac.treas.gov/"
    "api/PublicationPreview/exports/"
)

ADVANCED_NAMESPACE = (
    "https://sanctionslistservice.ofac.treas.gov/"
    "api/PublicationPreview/exports/ADVANCED_XML"
)
ADVANCED_VERSION = "3"

# Classic CSV は変更検知と独立した Party ID 照合に残す。
# 名称・別名の正式な取込元は Advanced XML とする。
LISTS = {
    "ofac_sdn": dict(
        name="OFAC SDN リスト",
        label="SDN",
        prim=BASE + "SDN.CSV",
        alt=BASE + "ALT.CSV",
        advanced=ADVANCED_BASE + "SDN_ADVANCED.XML",
    ),
    "ofac_cons": dict(
        name="OFAC Consolidated リスト",
        label="Consolidated",
        prim=BASE + "CONS_PRIM.CSV",
        alt=BASE + "CONS_ALT.CSV",
        advanced=ADVANCED_BASE + "CONS_ADVANCED.XML",
    ),
}

# classic CSV はヘッダー行が無く、欠損は "-0-" で表される。
PRIM_COLS = ["ent_num", "name", "sdn_type", "program", "title",
             "call_sign", "vess_type", "tonnage", "grt", "vess_flag",
             "vess_owner", "remarks"]
ALT_COLS = ["ent_num", "alt_num", "alt_type", "alt_name", "alt_remarks"]
NULL = {"-0-", "", "-0- "}


class SchemaError(RuntimeError):
    pass


def _rows(text: str, cols: list[str], label: str) -> list[dict]:
    rdr = csv.reader(io.StringIO(text))
    out = []
    for i, row in enumerate(rdr):
        if not row or all(c.strip() in NULL for c in row):
            continue
        if len(row) < 2:
            continue
        if len(row) > len(cols):
            # 末尾の余剰は remarks の続きとして畳む
            row = row[:len(cols) - 1] + [",".join(row[len(cols) - 1:])]
        d = dict(zip(cols, [c.strip() for c in row]))
        # 先頭行がヘッダーだった場合(SLSが将来付けてきた場合)は捨てる
        if i == 0 and d.get("ent_num", "").lower() in {"ent_num", "entnum", "id"}:
            continue
        out.append(d)
    if not out:
        raise SchemaError(f"OFAC {label} から行を抽出できなかった。書式変更を疑う")
    return out


def parse(prim: Fetched, alt: Fetched | None, label: str) -> list[dict]:
    """SDN.CSV と ALT.CSV を ent_num で結合し、1名前1レコードに展開する。"""
    prim_rows = _rows(prim.text, PRIM_COLS, label)

    programs: dict[str, str] = {}
    out: list[dict] = []
    for r in prim_rows:
        ent = r["ent_num"]
        prog = r.get("program", "")
        prog = "" if prog in NULL else prog
        programs[ent] = prog
        raw = r.get("name", "")
        if raw in NULL:
            continue
        for n in split_aliases(clean_name(raw)):
            out.append(dict(source=SOURCE, category=f"{label}", name=n,
                            source_id=ent, program=prog))

    if alt is not None and alt.body is not None:
        for r in _rows(alt.text, ALT_COLS, f"{label} ALT"):
            raw = r.get("alt_name", "")
            if raw in NULL:
                continue
            ent = r["ent_num"]
            for n in split_aliases(clean_name(raw)):
                out.append(dict(source=SOURCE, category=f"{label}", name=n,
                                source_id=ent, program=programs.get(ent, "")))

    if not out:
        raise SchemaError(f"OFAC {label} から名前を1件も抽出できなかった")
    return out


def _local(tag: str) -> str:
    """XML namespace を除いたタグ名を返す。"""
    return tag.rsplit("}", 1)[-1]


def _element_label(elem) -> str:
    """ReferenceValueSets の表示名を取得する。"""
    for raw in elem.itertext():
        value = clean_name(raw)
        if value:
            return value
    return ""


def _is_latin_text(value: str) -> bool:
    """文字列中の英字がすべて Latin script なら True。"""
    letters = [c for c in value if c.isalpha()]
    if not letters:
        return False
    return all(
        "LATIN" in unicodedata.name(c, "")
        for c in letters
    )


def _render_advanced_name(parts: list[tuple[str, str]]) -> str:
    """Advanced XML の name parts を1つの表示名に復元する。

    Latin script の個人名は Classic CSV と同じ
    `LAST, First Middle` 形式へ寄せる。

    非Latin名・団体名・船舶名等は XML の記載順を維持する。
    存在しない語順や別名を生成しないことを優先する。
    """
    parts = [(t, clean_name(v)) for t, v in parts if clean_name(v)]
    if not parts:
        return ""

    values = [v for _, v in parts]

    last = [v for t, v in parts if t == "Last Name"]
    maiden = [v for t, v in parts if t == "Maiden Name"]
    first = [v for t, v in parts if t == "First Name"]
    middle = [v for t, v in parts if t == "Middle Name"]
    patronymic = [v for t, v in parts if t == "Patronymic"]
    matronymic = [v for t, v in parts if t == "Matronymic"]

    personal_types = {
        "Last Name",
        "Maiden Name",
        "First Name",
        "Middle Name",
        "Patronymic",
        "Matronymic",
    }

    only_personal = all(t in personal_types for t, _ in parts)
    latin = _is_latin_text(" ".join(values))

    if only_personal and latin:
        # Maiden Name と Last Name が同時にある場合は、
        # 勝手に1つの姓へ合成せず XML 順へフォールバックする。
        surname = []
        if last and not maiden:
            surname = last
        elif maiden and not last:
            surname = maiden

        tail = first + middle + patronymic + matronymic

        if surname and tail:
            return clean_name(
                f"{' '.join(surname)}, {' '.join(tail)}"
            )

    # Entity / Vessel / Aircraft / Nickname / 非Latin等。
    # OFAC が記載した部品順そのものを維持する。
    return clean_name(" ".join(values))


def classic_party_ids(prim: Fetched, label: str) -> set[str]:
    """Classic primary CSV の実在 Party ID を取得する。

    OFAC classic CSV の末尾には DOS EOF の SUB (\\x1a) が
    1列だけで存在することがあるが、_rows() は2列未満を除外するため
    Party ID として誤認しない。
    """
    rows = _rows(prim.text, PRIM_COLS, label)
    ids = {
        r.get("ent_num", "").strip()
        for r in rows
        if r.get("ent_num", "").strip()
    }

    if not ids:
        raise SchemaError(
            f"OFAC {label} Classic CSV から Party ID を取得できなかった"
        )

    return ids


def validate_party_coverage(
    classic_ids: set[str],
    advanced_ids: set[str],
    label: str,
) -> None:
    """Classic と Advanced XML の Party ID が完全一致することを強制する。

    名称の正式取込元を Advanced XML にしても、
    Classic CSV を独立した完全性チェックとして利用する。

    一致しなければ掲載終了判定以前に workflow を停止する。
    """
    if classic_ids == advanced_ids:
        return

    only_advanced = sorted(advanced_ids - classic_ids)[:20]
    only_classic = sorted(classic_ids - advanced_ids)[:20]

    raise SchemaError(
        f"OFAC {label} Party ID coverage 不一致: "
        f"Classic={len(classic_ids)} "
        f"Advanced={len(advanced_ids)} "
        f"Advancedのみ={len(advanced_ids - classic_ids)} "
        f"Classicのみ={len(classic_ids - advanced_ids)} "
        f"Advanced sample={only_advanced} "
        f"Classic sample={only_classic}"
    )


def parse_advanced(
    advanced: Fetched,
    label: str,
) -> tuple[list[dict], set[str]]:
    """OFAC Advanced XML Version 3 を1名称1レコードへ展開する。

    DistinctParty.FixedRef を Party ID とし、
    Identity -> Alias -> DocumentedName -> NamePartValue を読む。

    Alias の Primary/LowQuality に関係なく OFAC が掲載している名称は
    screening 用に保持する。ただし False=true の Identity は除外する。

    非Latin script 同士を混ぜて架空名称を作らないよう、
    NamePartValue は ScriptID 単位で復元する。
    """
    if not advanced.body:
        raise SchemaError(
            f"OFAC {label} Advanced XML が空"
        )

    try:
        parser = ET.iterparse(
            io.BytesIO(advanced.body),
            events=("start", "end"),
        )
    except ET.ParseError as exc:
        raise SchemaError(
            f"OFAC {label} Advanced XML を解析できない: {exc}"
        ) from exc

    root_checked = False
    type_labels: dict[str, str] = {}
    party_ids: set[str] = set()
    out: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    no_name_parties: list[str] = []

    try:
        for event, elem in parser:
            tag = _local(elem.tag)

            if event == "start" and not root_checked:
                root_checked = True

                if tag != "Sanctions":
                    raise SchemaError(
                        f"OFAC {label} Advanced XML root が "
                        f"Sanctions ではない: {elem.tag}"
                    )

                if "}" not in elem.tag:
                    raise SchemaError(
                        f"OFAC {label} Advanced XML namespace が無い"
                    )

                namespace = elem.tag.split("}", 1)[0].lstrip("{")
                if namespace != ADVANCED_NAMESPACE:
                    raise SchemaError(
                        f"OFAC {label} Advanced XML namespace変更を検出: "
                        f"{namespace}"
                    )

                version = elem.attrib.get("Version", "")
                if version != ADVANCED_VERSION:
                    raise SchemaError(
                        f"OFAC {label} Advanced XML Version変更を検出: "
                        f"{version}"
                    )

                continue

            if event != "end":
                continue

            # ReferenceValueSets 内の辞書。
            if tag == "NamePartType":
                ident = elem.attrib.get("ID", "").strip()
                value = _element_label(elem)
                if ident and value:
                    type_labels[ident] = value
                elem.clear()
                continue

            if tag != "DistinctParty":
                continue

            if not type_labels:
                raise SchemaError(
                    f"OFAC {label} NamePartType辞書を取得できなかった"
                )

            party_ref = elem.attrib.get("FixedRef", "").strip()

            if not party_ref:
                raise SchemaError(
                    f"OFAC {label} FixedRef無し DistinctParty を検出"
                )

            if party_ref in party_ids:
                raise SchemaError(
                    f"OFAC {label} DistinctParty FixedRef重複: "
                    f"{party_ref}"
                )

            party_ids.add(party_ref)
            before = len(out)

            identities = [
                x for x in elem.iter()
                if _local(x.tag) == "Identity"
            ]

            for identity in identities:
                if (
                    identity.attrib.get("False", "false")
                    .strip()
                    .lower()
                    == "true"
                ):
                    continue

                group_types: dict[str, str] = {}

                for group in identity.iter():
                    if _local(group.tag) != "NamePartGroup":
                        continue

                    gid = group.attrib.get("ID", "").strip()
                    tid = group.attrib.get(
                        "NamePartTypeID", ""
                    ).strip()

                    if gid:
                        group_types[gid] = type_labels.get(
                            tid,
                            f"UNKNOWN:{tid}",
                        )

                for alias in identity.iter():
                    if _local(alias.tag) != "Alias":
                        continue

                    low_quality = (
                        alias.attrib.get("LowQuality", "false")
                        .strip()
                        .lower()
                        == "true"
                    )
                    primary = (
                        alias.attrib.get("Primary", "false")
                        .strip()
                        .lower()
                        == "true"
                    )

                    for documented in alias.iter():
                        if _local(documented.tag) != "DocumentedName":
                            continue

                        by_script: dict[
                            str,
                            list[tuple[str, str]],
                        ] = {}

                        for value in documented.iter():
                            if _local(value.tag) != "NamePartValue":
                                continue

                            raw = clean_name(value.text or "")
                            if not raw:
                                continue

                            gid = value.attrib.get(
                                "NamePartGroupID", ""
                            ).strip()
                            script = value.attrib.get(
                                "ScriptID", ""
                            ).strip()

                            part_type = group_types.get(
                                gid,
                                "UNKNOWN",
                            )

                            by_script.setdefault(
                                script,
                                [],
                            ).append(
                                (part_type, raw)
                            )

                        for script_id, parts in by_script.items():
                            name = _render_advanced_name(parts)

                            if not name:
                                continue

                            key = (
                                party_ref,
                                label,
                                name,
                            )

                            if key in seen:
                                continue

                            seen.add(key)

                            out.append(
                                dict(
                                    source=SOURCE,
                                    category=label,
                                    name=name,
                                    source_id=party_ref,
                                    program="",
                                    script_id=script_id,
                                    alias_primary=primary,
                                    low_quality=low_quality,
                                    format="advanced_xml_v3",
                                )
                            )

            if len(out) == before:
                no_name_parties.append(party_ref)

            # 121MB超のSDN XMLを全ツリー保持しない。
            elem.clear()

    except ET.ParseError as exc:
        raise SchemaError(
            f"OFAC {label} Advanced XML 構文エラー: {exc}"
        ) from exc

    if not root_checked:
        raise SchemaError(
            f"OFAC {label} Advanced XML root を取得できなかった"
        )

    if not party_ids:
        raise SchemaError(
            f"OFAC {label} Advanced XML に DistinctParty が無い"
        )

    if no_name_parties:
        raise SchemaError(
            f"OFAC {label} 名称を復元できない DistinctParty: "
            f"{len(no_name_parties)}件 "
            f"sample={no_name_parties[:20]}"
        )

    if not out:
        raise SchemaError(
            f"OFAC {label} Advanced XML から名称を取得できなかった"
        )

    return out, party_ids
