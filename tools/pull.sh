#!/bin/bash
# 制裁リストマスターを GitHub Releases から取得する。
# Public リポジトリのリリース資産は認証不要なのでトークンは要らない。
# 内容が変わったときだけ差し替え、macOS の通知を出す。
set -uo pipefail

REPO="${REPO:-kenmizuno-cpu/sanctions-watch}"
DEST="${DEST:-$HOME/Desktop/files/sanctions-master}"
FILES="${FILES:-black_receiver_name_all.xlsx latest.csv}"
LOG="$DEST/pull.log"

mkdir -p "$DEST"
log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOG"; }
notify() { /usr/bin/osascript -e "display notification \"$2\" with title \"$1\"" 2>/dev/null || true; }

changed=0
failed=0

for name in $FILES; do
  url="https://github.com/$REPO/releases/latest/download/$name"
  tmp="$(mktemp -t sanctions)" || { log "mktemp 失敗"; exit 1; }

  if ! curl -fsSL --retry 3 --retry-delay 5 --max-time 300 -o "$tmp" "$url"; then
    log "取得失敗: $name"; rm -f "$tmp"; failed=1; continue
  fi
  # 空ファイルを掴んで既存を壊さない
  if [ ! -s "$tmp" ]; then
    log "空のファイルを受信したため破棄: $name"; rm -f "$tmp"; failed=1; continue
  fi

  new=$(shasum -a 256 "$tmp" | cut -d' ' -f1)
  old=""
  [ -f "$DEST/$name" ] && old=$(shasum -a 256 "$DEST/$name" | cut -d' ' -f1)

  if [ "$new" = "$old" ]; then
    log "変更なし: $name"; rm -f "$tmp"; continue
  fi

  # 直前の版を1つ残す。取り込み後に問題が出たら戻せる。
  [ -f "$DEST/$name" ] && cp "$DEST/$name" "$DEST/.$name.prev"
  mv "$tmp" "$DEST/$name"
  chmod 644 "$DEST/$name"
  log "更新: $name ($new)"
  changed=1
done

[ "$changed" = 1 ] && notify "制裁リスト更新" "マスターが更新されました。$DEST を確認してください"
[ "$failed" = 1 ] && notify "制裁リスト取得エラー" "取得に失敗しました。$LOG を確認してください"

# ログが際限なく伸びないよう直近1000行に切り詰める
[ -f "$LOG" ] && { tail -n 1000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"; }
exit 0
