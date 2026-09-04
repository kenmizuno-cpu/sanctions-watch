"""OFAC FixedRef / Alias の永続履歴。

master.csv:
    通常の制裁スクリーニング用。

このindex:
    OFAC Party / Aliasそのものの監査証跡。

重要:
- Party削除とAlias削除を混同しない。
- Weak aliasも削除せず履歴保持する。
- FixedRefを失わない。
- 全行timestamp更新による巨大diffを発生させない。
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from .normalize import canonical_display_name, match_key


FIELDS = [
    "list",
    "party_id",
    "name",
    "match_key",
    "first_seen_ms",
    "last_changed_ms",
    "alias_current",
    "party_current",
    "formats",
    "primary",
    "low_quality",
]


def _bool(value) -> str:
    return "1" if bool(value) else "0"


def _split(value: str) -> set[str]:
    return {
        x
        for x in str(value or "").split(";")
        if x
    }


def _join(values) -> str:
    return ";".join(
        sorted({
            str(v)
            for v in values
            if str(v)
        })
    )


def _key(
    label: str,
    party_id: str,
    name: str,
) -> tuple[str, str, str]:
    return (
        str(label),
        str(party_id),
        canonical_display_name(name),
    )


def load(
    path: Path,
) -> dict[tuple[str, str, str], dict]:
    if not path.exists():
        return {}

    out = {}

    with path.open(
        encoding="utf-8",
        newline="",
    ) as f:
        for row in csv.DictReader(f):
            label = str(
                row.get("list", "")
            ).strip()

            party_id = str(
                row.get("party_id", "")
            ).strip()

            name = canonical_display_name(
                row.get("name", "")
            )

            if (
                not label
                or not party_id
                or not name
            ):
                continue

            row["name"] = name

            out[
                _key(
                    label,
                    party_id,
                    name,
                )
            ] = row

    return out


def save(
    rows: dict[tuple[str, str, str], dict],
    path: Path,
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with path.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as f:
        w = csv.DictWriter(
            f,
            fieldnames=FIELDS,
        )

        w.writeheader()

        for key in sorted(rows):
            row = rows[key]

            w.writerow({
                field: row.get(field, "")
                for field in FIELDS
            })


@dataclass
class IndexDiff:
    baseline: bool = False

    added_parties: set[
        tuple[str, str]
    ] = field(default_factory=set)

    removed_parties: set[
        tuple[str, str]
    ] = field(default_factory=set)

    added_aliases: int = 0

    inactive_aliases_active_party: int = 0


def update(
    history: dict[tuple[str, str, str], dict],
    records: list[dict],
    ts: int,
) -> IndexDiff:
    """現在のOFAC snapshotを履歴へ反映する。

    low_quality は「現在Weakだけで確認できるalias」の意味。

    同一Party/同一名称が
    AdvancedではWeakでもClassicではStrongとして存在する場合は
    Strong扱いとする。
    """
    previous_active_parties = {
        (
            row.get("list", ""),
            row.get("party_id", ""),
        )
        for row in history.values()
        if row.get("party_current") == "1"
    }

    incoming = {}
    current_parties = set()

    for record in records:
        label = str(
            record.get("category", "")
        ).strip()

        party_id = str(
            record.get("source_id", "")
        ).strip()

        name = canonical_display_name(
            record.get("name", "")
        )

        if (
            not label
            or not party_id
            or not name
        ):
            continue

        key = _key(
            label,
            party_id,
            name,
        )

        current_parties.add(
            (label, party_id)
        )

        item = incoming.setdefault(
            key,
            dict(
                list=label,
                party_id=party_id,
                name=name,
                match_key=match_key(name),
                formats=set(),
                primary=False,
                weak_seen=False,
                strong_seen=False,
            ),
        )

        fmt = str(
            record.get("format", "")
        ).strip()

        if fmt:
            item["formats"].add(fmt)

        if record.get("alias_primary"):
            item["primary"] = True

        if record.get("low_quality"):
            item["weak_seen"] = True
        else:
            # Classic CSVにはWeak AKAはALTとして出ず、
            # Advanced LowQuality=falseもStrong。
            item["strong_seen"] = True

    current_alias_keys = set(incoming)
    baseline = not bool(history)

    # --------------------------------------------------------
    # 既存履歴のcurrent状態更新
    # --------------------------------------------------------

    for key, row in history.items():
        party = (
            row.get("list", ""),
            row.get("party_id", ""),
        )

        new_party_current = (
            "1"
            if party in current_parties
            else "0"
        )

        new_alias_current = (
            "1"
            if key in current_alias_keys
            else "0"
        )

        changed = (
            row.get("party_current")
            != new_party_current
            or row.get("alias_current")
            != new_alias_current
        )

        row["party_current"] = (
            new_party_current
        )

        row["alias_current"] = (
            new_alias_current
        )

        if changed:
            row["last_changed_ms"] = ts

    # --------------------------------------------------------
    # 現在aliasの追加/metadata更新
    # --------------------------------------------------------

    added_aliases = 0

    for key, item in incoming.items():
        weak_only = (
            item["weak_seen"]
            and not item["strong_seen"]
        )

        row = history.get(key)

        if row is None:
            history[key] = dict(
                list=item["list"],
                party_id=item["party_id"],
                name=item["name"],
                match_key=item["match_key"],
                first_seen_ms=ts,
                last_changed_ms=ts,
                alias_current="1",
                party_current="1",
                formats=_join(
                    item["formats"]
                ),
                primary=_bool(
                    item["primary"]
                ),
                low_quality=_bool(
                    weak_only
                ),
            )

            added_aliases += 1
            continue

        changed = False

        new_formats = _join(
            _split(
                row.get("formats", "")
            )
            | item["formats"]
        )

        new_primary = _bool(
            item["primary"]
        )

        new_low_quality = _bool(
            weak_only
        )

        for field, value in (
            ("formats", new_formats),
            ("primary", new_primary),
            ("low_quality", new_low_quality),
        ):
            if row.get(field, "") != value:
                row[field] = value
                changed = True

        if changed:
            row["last_changed_ms"] = ts

    added_parties = (
        current_parties
        - previous_active_parties
    )

    removed_parties = (
        previous_active_parties
        - current_parties
    )

    # 初回baselineは全Partyを新規扱いしない。
    if baseline:
        added_parties = set()
        removed_parties = set()

    inactive_aliases_active_party = sum(
        1
        for row in history.values()
        if (
            row.get("party_current") == "1"
            and row.get("alias_current") == "0"
        )
    )

    return IndexDiff(
        baseline=baseline,
        added_parties=added_parties,
        removed_parties=removed_parties,
        added_aliases=added_aliases,
        inactive_aliases_active_party=(
            inactive_aliases_active_party
        ),
    )
