"""状態(state.json)とハートビートの管理。

last_checked は state.json に置かない。毎時実行だと内容が変わらなくても
git 差分が出て月720回のコミットが積み上がるため。代わりに
data/heartbeat/YYYY-MM.csv に追記する。副産物として
「毎時チェックしていて、その間に更新が無かった」を証明できる。
"""
from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

STATE = "data/state.json"
HEARTBEAT_DIR = "data/heartbeat"
HB_COLS = ["checked_at", "source", "status", "content_hash",
           "source_updated", "record_count", "raw_path"]


def load_state(root: Path) -> dict:
    p = root / STATE
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def save_state(root: Path, state: dict) -> None:
    p = root / STATE
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                 encoding="utf-8")


def heartbeat(root: Path, entries: list[dict]) -> Path:
    """チェック結果を月別CSVに追記する。304のときも必ず1行残す。"""
    now = datetime.now(timezone.utc)
    d = root / HEARTBEAT_DIR
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{now:%Y-%m}.csv"
    new = not p.exists()
    with p.open("a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HB_COLS)
        if new:
            w.writeheader()
        for e in entries:
            row = {c: "" for c in HB_COLS}
            row.update(e)
            row["checked_at"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
            w.writerow(row)
    return p
