"""取得層: 条件付きGET / ハッシュ / 生ファイルの日時付き保存。

毎時ポーリングする以上、更新が無いのに数十MBを毎回落とすのは
提供元にも自分にも無駄なので ETag / Last-Modified を必ず使う。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import requests

# OFAC の新ホスト(sanctionslistservice)は User-Agent が無いと 403 を返す。
# 旧 treasury.gov/ofac/downloads/ からのリダイレクト先がここ。自動化が
# ここで黙って死ぬ事例が多いので、必ず付ける。
UA = "sanctions-watch/1.0 (compliance list monitor; +https://github.com)"
TIMEOUT = 120


@dataclass
class Fetched:
    """取得結果。not_modified が True のとき body は None。"""
    url: str
    body: bytes | None = None
    sha256: str = ""
    not_modified: bool = False
    etag: str = ""
    last_modified: str = ""
    filename: str = ""
    raw_path: str = ""
    headers: dict = field(default_factory=dict)

    @property
    def text(self) -> str:
        if self.body is None:
            return ""
        for enc in ("utf-8-sig", "utf-8", "cp932", "shift_jis"):
            try:
                return self.body.decode(enc)
            except UnicodeDecodeError:
                continue
        return self.body.decode("utf-8", errors="replace")


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch(url: str, *, prev: dict | None = None, session: requests.Session | None = None,
          allow_conditional: bool = True) -> Fetched:
    """URL を取得する。prev に前回の etag/last_modified があれば条件付きGETする。"""
    s = session or requests.Session()
    headers = {"User-Agent": UA, "Accept": "*/*"}
    prev = prev or {}
    if allow_conditional:
        if prev.get("etag"):
            headers["If-None-Match"] = prev["etag"]
        if prev.get("last_modified"):
            headers["If-Modified-Since"] = prev["last_modified"]

    r = s.get(url, headers=headers, timeout=TIMEOUT)

    if r.status_code == 304:
        return Fetched(url=url, not_modified=True,
                       etag=prev.get("etag", ""), sha256=prev.get("sha256", ""),
                       last_modified=prev.get("last_modified", ""),
                       filename=prev.get("filename", ""))

    r.raise_for_status()
    return Fetched(
        url=url, body=r.content, sha256=sha256(r.content),
        etag=r.headers.get("ETag", ""),
        last_modified=r.headers.get("Last-Modified", ""),
        filename=_filename_from(url, r.headers),
        headers=dict(r.headers),
    )


def _filename_from(url: str, headers) -> str:
    cd = headers.get("Content-Disposition", "")
    m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', cd)
    if m:
        return m.group(1)
    return url.rstrip("/").split("/")[-1].split("?")[0] or "download"


def archive(f: Fetched, source: str, root: Path) -> str:
    """生ファイルを日時付きで保存する。

    外為法の検査で「いつ時点のリストで照合したか」を聞かれたときに
    そのまま出せる形にしておく。
    """
    if f.body is None:
        return ""
    d = root / "data" / "raw" / source
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{utc_stamp()}__{f.filename}"
    p.write_bytes(f.body)
    f.raw_path = str(p.relative_to(root))
    return f.raw_path


def prune_raw(source: str, root: Path, keep: int = 30) -> list[str]:
    """生ファイルの保管数を制限する。最新 keep 件を残す。"""
    d = root / "data" / "raw" / source
    if not d.exists():
        return []
    files = sorted(d.iterdir(), key=lambda p: p.name, reverse=True)
    removed = []
    for p in files[keep:]:
        removed.append(p.name)
        p.unlink()
    return removed
