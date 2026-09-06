"""METI公式HTMLによる外国ユーザーリスト更新候補センサー。

GitHub Hosted Runner からMETI RSSが403になるため、
一次ソースであるMETI公式HTMLを二重監視する。

A. 対外経済ニュースリリース一覧
B. 安全保障貿易管理「改正情報」

候補検知は正本確定ではない。
候補検知後、既存のMETI公式リスト確認へ進む。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

import requests

from . import meti_rss as rules
from . import source_audit
from .fetch import Fetched, archive, fetch as fetch_url


ROOT = Path(__file__).resolve().parent.parent

STATE_PATH = (
    ROOT / "data" / "meti_html" / "state.json"
)

UA = (
    "sanctions-watch/1.0 "
    "(METI HTML compliance monitor; "
    "contact via repository issues)"
)


@dataclass(frozen=True)
class SourceSpec:
    key: str
    role: str
    url: str
    kind: str


SOURCES = (
    SourceSpec(
        key="press_external_economy",
        role="METI_EXTERNAL_ECONOMY_PRESS_HTML",
        url=(
            "https://www.meti.go.jp/"
            "press/category_02.html"
        ),
        kind="press",
    ),
    SourceSpec(
        key="anpo_revision",
        role="METI_ANPO_REVISION_HTML",
        url=(
            "https://www.meti.go.jp/"
            "policy/anpo/law09-2.html"
        ),
        kind="law",
    ),
)


PRESS_RELEASE_RE = re.compile(
    r"^https://www\.meti\.go\.jp/"
    r"press/(\d{4})/(\d{2})/"
    r"(\d{11})/(\d{11})\.html$"
)

SPACE_RE = re.compile(r"\s+")


class SensorSchemaError(RuntimeError):
    """HTML構造が期待値を満たさない。"""


class _HTMLCollector(HTMLParser):
    def __init__(self):
        super().__init__(
            convert_charrefs=True
        )
        self.anchors: list[
            tuple[str, str]
        ] = []

        self._href: str | None = None
        self._parts: list[str] = []
        self._a_depth = 0

        self._text_parts: list[str] = []
        self._skip = 0

    def handle_starttag(
        self,
        tag: str,
        attrs,
    ):
        tag = tag.lower()

        if tag in {"script", "style"}:
            self._skip += 1

        if tag == "a":
            href = dict(attrs).get(
                "href",
                "",
            )
            self._href = str(
                href or ""
            ).strip()
            self._parts = []
            self._a_depth = 1

        elif self._a_depth:
            self._a_depth += 1

    def handle_endtag(
        self,
        tag: str,
    ):
        tag = tag.lower()

        if tag in {"script", "style"}:
            self._skip = max(
                0,
                self._skip - 1,
            )

        if self._a_depth:
            self._a_depth -= 1

            if (
                tag == "a"
                and self._a_depth == 0
            ):
                text = _clean(
                    " ".join(
                        self._parts
                    )
                )

                if self._href:
                    self.anchors.append(
                        (
                            self._href,
                            text,
                        )
                    )

                self._href = None
                self._parts = []

    def handle_data(
        self,
        data: str,
    ):
        if not self._skip:
            self._text_parts.append(
                data
            )

        if self._a_depth:
            self._parts.append(
                data
            )

    @property
    def visible_text(self) -> str:
        return _clean(
            " ".join(
                self._text_parts
            )
        )


def _clean(value: str) -> str:
    return SPACE_RE.sub(
        " ",
        str(value or ""),
    ).strip()


def _parse_html(
    body: bytes | str,
) -> _HTMLCollector:
    if isinstance(body, bytes):
        text = body.decode(
            "utf-8",
            errors="replace",
        )
    else:
        text = body

    parser = _HTMLCollector()

    try:
        parser.feed(text)
        parser.close()
    except Exception as exc:
        raise SensorSchemaError(
            "METI HTMLを解析できない: "
            f"{exc}"
        ) from exc

    if not parser.visible_text:
        raise SensorSchemaError(
            "METI HTML本文が空"
        )

    return parser


def parse_press_entries(
    body: bytes | str,
    *,
    base_url: str = SOURCES[0].url,
) -> list[rules.Entry]:
    parser = _parse_html(body)

    if (
        "ニュースリリース"
        not in parser.visible_text
    ):
        raise SensorSchemaError(
            "対外経済ニュースリリース"
            "ページ識別文字列がない"
        )

    entries: list[rules.Entry] = []
    seen: set[str] = set()

    for href, title in parser.anchors:
        url = urljoin(
            base_url,
            href,
        )

        m = PRESS_RELEASE_RE.match(
            url
        )

        if not m:
            continue

        if m.group(3) != m.group(4):
            continue

        if not title:
            continue

        if url in seen:
            continue

        seen.add(url)

        code = m.group(3)
        date = code[:8]

        published = (
            f"{date[:4]}-"
            f"{date[4:6]}-"
            f"{date[6:8]}"
        )

        entries.append(
            rules.Entry(
                entry_id=url,
                title=title,
                url=url,
                published=published,
                text=_clean(
                    f"{title} {url}"
                ),
            )
        )

    if not entries:
        raise SensorSchemaError(
            "ニュースリリース記事リンク"
            "を1件も抽出できない"
        )

    return entries


def law_semantic_text(
    body: bytes | str,
) -> str:
    parser = _parse_html(body)
    text = parser.visible_text

    if (
        "外国ユーザーリスト"
        not in text
    ):
        raise SensorSchemaError(
            "安全保障貿易管理改正ページから"
            "「外国ユーザーリスト」が消失"
        )

    if "改正" not in text:
        raise SensorSchemaError(
            "安全保障貿易管理改正ページから"
            "「改正」が消失"
        )

    return text


def _semantic_hash(
    value: str,
) -> str:
    return hashlib.sha256(
        value.encode(
            "utf-8",
            "replace",
        )
    ).hexdigest()


def load_state(
    path: Path = STATE_PATH,
) -> dict:
    if not path.exists():
        return {
            "version": 1,
            "sources": {},
            "issues": {},
        }

    try:
        value = json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )
    except Exception as exc:
        raise SensorSchemaError(
            f"METI HTML state読込失敗: {exc}"
        ) from exc

    if not isinstance(
        value,
        dict,
    ):
        raise SensorSchemaError(
            "METI HTML stateの"
            "ルートがobjectではない"
        )

    value.setdefault(
        "sources",
        {},
    )
    value.setdefault(
        "issues",
        {},
    )

    return value


def save_state(
    state: dict,
    path: Path = STATE_PATH,
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    tmp = path.with_suffix(
        ".json.tmp"
    )

    tmp.write_text(
        json.dumps(
            state,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    tmp.replace(path)


def _dashboard_row(
    *,
    event_type: str,
    title: str,
    detail: str,
    url: str,
    now: datetime,
) -> list[str]:
    stamp = (
        now.astimezone(
            rules.JST
        )
        .strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    )

    return [
        stamp,
        "経済産業省",
        event_type,
        title,
        detail[:1000],
        url,
    ]


def _candidate_row(
    candidate: rules.Candidate,
    now: datetime,
) -> list[str]:
    reason = (
        f"{candidate.confidence}: "
        f"{candidate.reason}"
    )

    if candidate.published:
        reason += (
            " / 公開日: "
            + candidate.published
        )

    return _dashboard_row(
        event_type=(
            "HTML更新候補（未確定）"
        ),
        title=candidate.title,
        detail=reason,
        url=candidate.url,
        now=now,
    )


def _fingerprint(
    key: str,
    exc: Exception,
) -> str:
    return hashlib.sha256(
        (
            f"{key}|"
            f"{type(exc).__name__}|"
            f"{exc}"
        ).encode(
            "utf-8",
            "replace",
        )
    ).hexdigest()


def _audit(
    *,
    spec: SourceSpec,
    status: str,
    fetched: Fetched | None,
    record_count: int | str = "",
    diff_counts: dict | None = None,
    fetch_failed: bool = False,
    schema_changed: bool = False,
    error: Exception | None = None,
) -> None:
    if fetched is not None:
        row = source_audit.entry(
            "meti_html",
            spec.role,
            status,
            fetched=fetched,
            record_count=record_count,
            diff_counts=diff_counts,
            fetch_failed=fetch_failed,
            schema_changed=schema_changed,
            error=error,
        )
    elif error is not None:
        row = source_audit.error_entry(
            "meti_html",
            spec.role,
            error,
            url=spec.url,
            status=status,
            fetch_failed=fetch_failed,
            schema_changed=schema_changed,
        )
    else:
        row = source_audit.entry(
            "meti_html",
            spec.role,
            status,
            url=spec.url,
            record_count=record_count,
            diff_counts=diff_counts,
        )

    source_audit.write(
        ROOT,
        [row],
    )


def _record_issue(
    *,
    spec: SourceSpec,
    state: dict,
    exc: Exception,
    now: datetime,
    fetched: Fetched | None,
    schema_changed: bool,
) -> None:
    fp = _fingerprint(
        spec.key,
        exc,
    )

    old = (
        state["issues"]
        .get(spec.key)
        or {}
    )

    if (
        old.get("fingerprint")
        == fp
    ):
        return

    if (
        fetched is not None
        and fetched.body
        and not fetched.raw_path
    ):
        archive(
            fetched,
            "meti_html",
            ROOT,
            compress=True,
        )

    event = (
        "HTMLスキーマ異常"
        if schema_changed
        else "HTML監視エラー"
    )

    rules.append_dashboard_rows(
        [
            _dashboard_row(
                event_type=event,
                title=(
                    "METI公式HTML監視"
                ),
                detail=(
                    f"{spec.key}: "
                    f"{type(exc).__name__}: "
                    f"{exc}"
                ),
                url=spec.url,
                now=now,
            )
        ]
    )

    _audit(
        spec=spec,
        status=(
            "schema_error"
            if schema_changed
            else "fetch_error"
        ),
        fetched=fetched,
        fetch_failed=(
            not schema_changed
        ),
        schema_changed=(
            schema_changed
        ),
        error=exc,
    )

    state["issues"][
        spec.key
    ] = {
        "fingerprint": fp,
        "type": type(exc).__name__,
        "message": str(exc)[:1000],
        "detected_at": (
            now.isoformat(
                timespec="seconds"
            )
        ),
    }


def _recover_issue(
    *,
    spec: SourceSpec,
    state: dict,
    fetched: Fetched,
    now: datetime,
    record_count: int,
) -> None:
    previous = (
        state["issues"]
        .get(spec.key)
    )

    if not previous:
        return

    rules.append_dashboard_rows(
        [
            _dashboard_row(
                event_type=(
                    "HTML監視復旧"
                ),
                title=(
                    "METI公式HTML監視"
                ),
                detail=(
                    f"{spec.key}: "
                    "前回異常から正常取得へ復旧"
                ),
                url=spec.url,
                now=now,
            )
        ]
    )

    _audit(
        spec=spec,
        status="recovered",
        fetched=fetched,
        record_count=record_count,
    )

    state["issues"].pop(
        spec.key,
        None,
    )


def _fetch(
    spec: SourceSpec,
    prev: dict,
    session: requests.Session,
) -> Fetched:
    fetch_prev = {
        "etag": prev.get(
            "etag",
            "",
        ),
        "last_modified": prev.get(
            "last_modified",
            "",
        ),
        "sha256": prev.get(
            "content_hash",
            "",
        ),
        "filename": prev.get(
            "filename",
            "",
        ),
    }

    return fetch_url(
        spec.url,
        prev=fetch_prev,
        session=session,
        allow_conditional=True,
        user_agent=UA,
    )


def _process_press(
    *,
    spec: SourceSpec,
    fetched: Fetched,
    previous: dict,
    first: bool,
) -> tuple[
    int,
    str,
    list[rules.Candidate],
    dict,
]:
    if fetched.not_modified:
        if not previous.get(
            "baseline_synced"
        ):
            raise SensorSchemaError(
                "304だがHTML baseline未確立"
            )

        return (
            int(
                previous.get(
                    "record_count",
                    0,
                )
            ),
            str(
                previous.get(
                    "semantic_hash",
                    "",
                )
            ),
            [],
            {
                "added": 0,
                "removed": 0,
                "changed": 0,
            },
        )

    entries = parse_press_entries(
        fetched.body or b"",
        base_url=spec.url,
    )

    current_ids = [
        e.entry_id
        for e in entries
    ]

    semantic = "\n".join(
        sorted(
            f"{e.entry_id}|{e.title}"
            for e in entries
        )
    )

    semantic_hash = (
        _semantic_hash(
            semantic
        )
    )

    old_current = set(
        previous.get(
            "current_ids",
            [],
        )
    )
    current = set(
        current_ids
    )

    diff = {
        "added": len(
            current - old_current
        ),
        "removed": len(
            old_current - current
        ),
        "changed": (
            1
            if (
                previous.get(
                    "semantic_hash"
                )
                and previous.get(
                    "semantic_hash"
                )
                != semantic_hash
            )
            else 0
        ),
    }

    candidates: list[
        rules.Candidate
    ] = []

    if not first:
        seen = set(
            previous.get(
                "seen_ids",
                [],
            )
        )

        for entry in entries:
            if (
                entry.entry_id
                in seen
            ):
                continue

            candidate = (
                rules.classify(
                    entry
                )
            )

            if candidate:
                candidates.append(
                    candidate
                )

    return (
        len(entries),
        semantic_hash,
        candidates,
        {
            **diff,
            "current_ids": (
                current_ids
            ),
        },
    )


def _process_law(
    *,
    fetched: Fetched,
    previous: dict,
    first: bool,
    spec: SourceSpec,
) -> tuple[
    int,
    str,
    list[rules.Candidate],
    dict,
]:
    if fetched.not_modified:
        if not previous.get(
            "baseline_synced"
        ):
            raise SensorSchemaError(
                "304だがHTML baseline未確立"
            )

        return (
            int(
                previous.get(
                    "record_count",
                    0,
                )
            ),
            str(
                previous.get(
                    "semantic_hash",
                    "",
                )
            ),
            [],
            {
                "added": 0,
                "removed": 0,
                "changed": 0,
            },
        )

    text = law_semantic_text(
        fetched.body or b""
    )

    semantic_hash = (
        _semantic_hash(
            text
        )
    )

    occurrences = text.count(
        "外国ユーザーリスト"
    )

    candidates: list[
        rules.Candidate
    ] = []

    changed = bool(
        previous.get(
            "semantic_hash"
        )
        and previous.get(
            "semantic_hash"
        )
        != semantic_hash
    )

    if (
        not first
        and changed
    ):
        candidates.append(
            rules.Candidate(
                entry_id=(
                    "anpo_revision:"
                    + semantic_hash
                ),
                title=(
                    "安全保障貿易管理"
                    "「改正情報」ページが"
                    "更新されました"
                ),
                url=spec.url,
                published="",
                reason=(
                    "安全保障貿易管理の"
                    "改正情報HTML本文に"
                    "意味的変更を検知"
                ),
                confidence="REVIEW",
            )
        )

    return (
        occurrences,
        semantic_hash,
        candidates,
        {
            "added": 0,
            "removed": 0,
            "changed": (
                1 if changed else 0
            ),
        },
    )


def run(
    *,
    state_path: Path = STATE_PATH,
) -> int:
    now = datetime.now(
        timezone.utc
    )

    state = load_state(
        state_path
    )

    state["version"] = 1
    state.setdefault(
        "sources",
        {},
    )
    state.setdefault(
        "issues",
        {},
    )

    all_candidates: list[
        rules.Candidate
    ] = []

    failures = 0

    with requests.Session() as session:
        for spec in SOURCES:
            previous = dict(
                state["sources"]
                .get(spec.key)
                or {}
            )

            fetched: (
                Fetched | None
            ) = None

            try:
                fetched = _fetch(
                    spec,
                    previous,
                    session,
                )

                first = not bool(
                    previous.get(
                        "baseline_synced"
                    )
                )

                if spec.kind == "press":
                    (
                        record_count,
                        semantic_hash,
                        candidates,
                        details,
                    ) = _process_press(
                        spec=spec,
                        fetched=fetched,
                        previous=previous,
                        first=first,
                    )
                else:
                    (
                        record_count,
                        semantic_hash,
                        candidates,
                        details,
                    ) = _process_law(
                        spec=spec,
                        fetched=fetched,
                        previous=previous,
                        first=first,
                    )

                raw_changed = (
                    not fetched.not_modified
                    and (
                        first
                        or (
                            fetched.sha256
                            != previous.get(
                                "content_hash",
                                "",
                            )
                        )
                    )
                )

                if raw_changed:
                    archive(
                        fetched,
                        "meti_html",
                        ROOT,
                        compress=True,
                    )

                    _audit(
                        spec=spec,
                        status=(
                            "baseline"
                            if first
                            else "source_updated"
                        ),
                        fetched=fetched,
                        record_count=(
                            record_count
                        ),
                        diff_counts=details,
                    )

                current = dict(
                    previous
                )

                current.update(
                    {
                        "url": spec.url,
                        "role": spec.role,
                        "kind": spec.kind,
                        "baseline_synced": True,
                        "etag": (
                            fetched.etag
                            or previous.get(
                                "etag",
                                "",
                            )
                        ),
                        "last_modified": (
                            fetched.last_modified
                            or previous.get(
                                "last_modified",
                                "",
                            )
                        ),
                        "content_hash": (
                            fetched.sha256
                            or previous.get(
                                "content_hash",
                                "",
                            )
                        ),
                        "filename": (
                            fetched.filename
                            or previous.get(
                                "filename",
                                "",
                            )
                        ),
                        "semantic_hash": (
                            semantic_hash
                        ),
                        "record_count": (
                            record_count
                        ),
                    }
                )

                if (
                    spec.kind
                    == "press"
                    and not fetched.not_modified
                ):
                    current_ids = details[
                        "current_ids"
                    ]

                    current[
                        "current_ids"
                    ] = current_ids

                    old_seen = list(
                        previous.get(
                            "seen_ids",
                            [],
                        )
                    )

                    current[
                        "seen_ids"
                    ] = list(
                        dict.fromkeys(
                            old_seen
                            + current_ids
                        )
                    )[-3000:]

                state["sources"][
                    spec.key
                ] = current

                _recover_issue(
                    spec=spec,
                    state=state,
                    fetched=fetched,
                    now=now,
                    record_count=(
                        record_count
                    ),
                )

                all_candidates.extend(
                    candidates
                )

                print(
                    "[checked] "
                    f"{spec.key}: "
                    f"HTTP "
                    f"{fetched.http_status} "
                    f"/ records "
                    f"{record_count} "
                    f"/ candidates "
                    f"{len(candidates)}"
                )

            except SensorSchemaError as exc:
                failures += 1

                _record_issue(
                    spec=spec,
                    state=state,
                    exc=exc,
                    now=now,
                    fetched=fetched,
                    schema_changed=True,
                )

                print(
                    "[ERROR] "
                    f"{spec.key}: "
                    f"{exc}",
                    file=sys.stderr,
                )

            except Exception as exc:
                failures += 1

                _record_issue(
                    spec=spec,
                    state=state,
                    exc=exc,
                    now=now,
                    fetched=fetched,
                    schema_changed=False,
                )

                print(
                    "[ERROR] "
                    f"{spec.key}: "
                    f"{type(exc).__name__}: "
                    f"{exc}",
                    file=sys.stderr,
                )

    if all_candidates:
        rules.append_dashboard_rows(
            [
                _candidate_row(
                    candidate,
                    now,
                )
                for candidate
                in all_candidates
            ]
        )

        newest = (
            all_candidates[-1]
        )

        state[
            "last_candidate"
        ] = {
            "entry_id": newest.entry_id,
            "title": newest.title,
            "url": newest.url,
            "published": newest.published,
            "confidence": (
                newest.confidence
            ),
            "detected_at": (
                now.isoformat(
                    timespec="seconds"
                )
            ),
        }

    save_state(
        state,
        state_path,
    )

    rules._write_output(
        "candidate",
        bool(all_candidates),
    )
    rules._write_output(
        "candidate_count",
        len(all_candidates),
    )
    rules._write_output(
        "sensor_ok",
        failures == 0,
    )

    if all_candidates:
        newest = (
            all_candidates[-1]
        )

        rules._write_output(
            "candidate_title",
            newest.title,
        )
        rules._write_output(
            "candidate_url",
            newest.url,
        )
        rules._write_output(
            "candidate_confidence",
            newest.confidence,
        )

    if failures:
        return 1

    return 0


def main() -> int:
    argparse.ArgumentParser(
        description=(
            "METI公式HTML更新候補センサー"
        )
    ).parse_args()

    return run()


if __name__ == "__main__":
    sys.exit(main())
