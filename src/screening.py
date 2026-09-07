"""
制裁名簿の照合専用ロジック。

重要:
- canonical match_key は master identity / merge / delist / audit 用。
- screening 用の補助キーは canonical identity を変更しない。
- 補助キーで複数 canonical record に一致した場合は自動統合せず REVIEW。
- 無効レコードは通常 screening の候補にしない。
"""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from .normalize import match_key


DECISION_HIT = "HIT"
DECISION_REVIEW = "REVIEW"
DECISION_NO_HIT = "NO_HIT"

MATCH_EXACT = "EXACT"
MATCH_PARENTHESIS_RELAXED = "PARENTHESIS_RELAXED"
MATCH_NONE = "NONE"


def secondary_screening_key(name: str) -> str:
    """
    screening 専用の補助キー。

    canonical match_key() を生成した後、丸括弧だけを無視する。

    NFKC は match_key() 内で実施されるため、
    ASCII () と全角 （） は同じ扱いになる。

    この関数の戻り値を master の主キーとして使用してはいけない。
    """
    return (
        match_key(name)
        .replace("(", "")
        .replace(")", "")
    )


def _active(row: dict) -> bool:
    return str(row.get("status") or "").strip() == "有効"


def build_screening_index(
    rows: Iterable[dict],
) -> dict:
    """
    active master rows だけで screening index を構築する。

    exact:
        canonical match_key -> rows

    secondary:
        parenthesis-relaxed key -> rows
    """
    exact = defaultdict(list)
    secondary = defaultdict(list)

    active_count = 0

    for row in rows:
        if not _active(row):
            continue

        name = str(
            row.get("display_name") or ""
        ).strip()

        canonical = str(
            row.get("match_key") or ""
        ).strip()

        if not name or not canonical:
            continue

        active_count += 1

        exact[canonical].append(row)

        relaxed = secondary_screening_key(
            name
        )

        if relaxed:
            secondary[relaxed].append(row)

    return {
        "exact": dict(exact),
        "secondary": dict(secondary),
        "active_count": active_count,
    }


def _distinct_canonical_keys(
    rows: list[dict],
) -> set[str]:
    return {
        str(r.get("match_key") or "").strip()
        for r in rows
        if str(r.get("match_key") or "").strip()
    }


def screen_name(
    query: str,
    index: dict,
) -> dict:
    """
    1. canonical exact を最優先。
    2. exact が無い場合だけ secondary key を使用。
    3. secondary が1 canonical keyなら HIT。
    4. secondary が複数 canonical keyなら REVIEW。
    5. 候補なしなら NO_HIT。

    exact が存在する場合、同じ secondary key に別recordがいても
    exact を優先する。括弧を明示して入力された名称を曖昧化しないため。
    """
    canonical = match_key(query)
    relaxed = secondary_screening_key(
        query
    )

    exact_rows = list(
        index["exact"].get(
            canonical,
            [],
        )
    )

    if exact_rows:
        return {
            "decision": DECISION_HIT,
            "match_type": MATCH_EXACT,
            "query": query,
            "query_match_key": canonical,
            "query_secondary_key": relaxed,
            "candidate_keys": sorted(
                _distinct_canonical_keys(
                    exact_rows
                )
            ),
            "candidates": exact_rows,
        }

    secondary_rows = list(
        index["secondary"].get(
            relaxed,
            [],
        )
    )

    candidate_keys = sorted(
        _distinct_canonical_keys(
            secondary_rows
        )
    )

    if not secondary_rows:
        return {
            "decision": DECISION_NO_HIT,
            "match_type": MATCH_NONE,
            "query": query,
            "query_match_key": canonical,
            "query_secondary_key": relaxed,
            "candidate_keys": [],
            "candidates": [],
        }

    if len(candidate_keys) == 1:
        return {
            "decision": DECISION_HIT,
            "match_type": MATCH_PARENTHESIS_RELAXED,
            "query": query,
            "query_match_key": canonical,
            "query_secondary_key": relaxed,
            "candidate_keys": candidate_keys,
            "candidates": secondary_rows,
        }

    return {
        "decision": DECISION_REVIEW,
        "match_type": MATCH_PARENTHESIS_RELAXED,
        "query": query,
        "query_match_key": canonical,
        "query_secondary_key": relaxed,
        "candidate_keys": candidate_keys,
        "candidates": secondary_rows,
    }


def secondary_collision_groups(
    index: dict,
) -> dict[str, list[dict]]:
    """
    secondary key が複数 canonical match_key に対応する active group。

    同じ canonical key の複数rowは collision とみなさない。
    """
    out = {}

    for key, rows in index[
        "secondary"
    ].items():

        canonical_keys = (
            _distinct_canonical_keys(
                rows
            )
        )

        if len(canonical_keys) > 1:
            out[key] = rows

    return out
