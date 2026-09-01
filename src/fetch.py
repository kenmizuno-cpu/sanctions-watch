"""取得層: 条件付きGET / ハッシュ / 生ファイルの日時付き保存。

毎時ポーリングする以上、更新が無いのに数十MBを毎回落とすのは
提供元にも自分にも無駄なので ETag / Last-Modified を必ず使う。
"""
from __future__ import annotations

import gzip
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
          allow_conditional: bool = True, user_agent: str | None = None) -> Fetched:
    """URL を取得する。prev に前回の etag/last_modified があれば条件付きGETする。

    user_agent: 提供元ごとに要求が食い違うため切り替えられるようにしてある。
    OFAC は User-Agent 必須（無いと403）、経産省は逆に自動アクセスを拒否する。
    """
    s = session or requests.Session()
    headers = {"User-Agent": user_agent or UA, "Accept": "*/*"}
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


GZIP_LEVEL = 6


def archive(f: Fetched, source: str, root: Path, *, compress: bool = True) -> str:
    """生ファイルを日時付きで保存する。

    外為法の検査で「いつ時点のリストで照合したか」を聞かれたときに
    そのまま出せる形にしておく。

    既定で gzip 圧縮する。生データはコミットされ git 履歴に永久に残るため、
    非圧縮だと OFAC 更新1回ごとに約 6.7MB がリポジトリに積み上がる。
    実測で CSV は 17% まで縮み、圧縮 0.10 秒・展開 0.02 秒。
    mtime=0 にしているのは、同一内容なら同一バイト列になるようにするため。
    """
    if f.body is None:
        return ""
    d = root / "data" / "raw" / source
    d.mkdir(parents=True, exist_ok=True)
    name = f"{utc_stamp()}__{f.filename}"
    if compress:
        p = d / f"{name}.gz"
        p.write_bytes(gzip.compress(f.body, GZIP_LEVEL, mtime=0))
    else:
        p = d / name
        p.write_bytes(f.body)
    f.raw_path = str(p.relative_to(root))
    return f.raw_path


def read_raw(path: Path | str) -> bytes:
    """保存済みの生ファイルを読む。gzip なら透過的に展開する。

    拡張子ではなくマジックナンバーでも判定するので、圧縮を入れる前に
    保存された非圧縮ファイルもそのまま読める。
    """
    p = Path(path)
    body = p.read_bytes()
    if body[:2] == b"\x1f\x8b":
        return gzip.decompress(body)
    return body


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
