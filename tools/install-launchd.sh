#!/bin/bash
# pull.sh を launchd に登録して定期実行させる。
#   ./install-launchd.sh                 # 6時間ごと
#   INTERVAL=3600 ./install-launchd.sh   # 1時間ごと
set -euo pipefail

LABEL="com.sanctions-watch.pull"
INTERVAL="${INTERVAL:-21600}"
SCRIPT="$(cd "$(dirname "$0")" && pwd)/pull.sh"
DEST="${DEST:-$HOME/Desktop/files/sanctions-master}"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

chmod +x "$SCRIPT"
mkdir -p "$DEST" "$HOME/Library/LaunchAgents"

cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$SCRIPT</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict><key>DEST</key><string>$DEST</string></dict>
  <key>StartInterval</key><integer>$INTERVAL</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$DEST/launchd.out.log</string>
  <key>StandardErrorPath</key><string>$DEST/launchd.err.log</string>
</dict>
</plist>
PLISTEOF

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl kickstart -k "gui/$(id -u)/$LABEL"

echo "登録しました: $LABEL"
echo "  実行間隔 : ${INTERVAL}秒"
echo "  保存先   : $DEST"
echo "  ログ     : $DEST/pull.log"
