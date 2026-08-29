"""Google Drive へのアップロード。

同じファイルIDを更新し続ける（毎回新規作成しない）。そうすると
Mac の Drive デスクトップアプリが同名ファイルを上書き同期するので、
体感は「勝手に最新版が手元にある」になる。

注意: サービスアカウントのマイドライブ直下は容量エラーになることがある。
共有ドライブにフォルダを作り、サービスアカウントを編集者で招待して、
そのフォルダIDを DRIVE_FOLDER_ID に設定するのが確実。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

MIME_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _service():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT", "").strip()
    if not raw:
        return None
    info = json.loads(raw)
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/drive"])
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def upload(path: Path, folder_id: str | None = None,
           name: str | None = None) -> str | None:
    """フォルダ内の同名ファイルを探して更新。無ければ作成。ファイルIDを返す。"""
    from googleapiclient.http import MediaFileUpload

    svc = _service()
    if svc is None:
        print("GOOGLE_SERVICE_ACCOUNT 未設定のため Drive アップロードをスキップ")
        return None

    folder_id = folder_id or os.environ.get("DRIVE_FOLDER_ID", "").strip()
    if not folder_id:
        print("DRIVE_FOLDER_ID 未設定のため Drive アップロードをスキップ")
        return None

    name = name or path.name
    q = (f"name = '{name}' and '{folder_id}' in parents and trashed = false")
    res = svc.files().list(q=q, fields="files(id,name)", pageSize=1,
                           supportsAllDrives=True,
                           includeItemsFromAllDrives=True).execute()
    files = res.get("files", [])
    media = MediaFileUpload(str(path), mimetype=MIME_XLSX, resumable=True)

    if files:
        fid = files[0]["id"]
        svc.files().update(fileId=fid, media_body=media,
                           supportsAllDrives=True).execute()
        print(f"Drive 上書き更新: {name} ({fid})")
    else:
        meta = {"name": name, "parents": [folder_id]}
        fid = svc.files().create(body=meta, media_body=media, fields="id",
                                 supportsAllDrives=True).execute()["id"]
        print(f"Drive 新規作成: {name} ({fid})")
    return fid


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: python -m src.drive <file> [more files...]", file=sys.stderr)
        return 2
    for arg in sys.argv[1:]:
        p = Path(arg)
        if not p.exists():
            print(f"見つからない: {p}", file=sys.stderr)
            return 1
        upload(p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
