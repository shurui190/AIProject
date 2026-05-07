#!/bin/bash
# wait-reminder.sh — After timeout, send a Feishu notification reminding the user to respond.
# Usage: wait-reminder.sh <config_path> "<question text>"
# The script writes its PID to a file so it can be cancelled if the user responds in time.

if [ $# -lt 2 ]; then
  echo "Usage: wait-reminder.sh <config_path> \"<question text>\"" >&2
  exit 1
fi

CONFIG_PATH="$1"
QUESTION="$2"
PID_FILE="/tmp/lark-notify-reminder.pid"

# Write PID so caller can kill this timer
echo $$ > "$PID_FILE"

# Read timeout from config, default 180 seconds
DELAY=180
if [ -f "$CONFIG_PATH" ]; then
  TIMEOUT=$(grep -o '"wait_reminder_timeout_seconds"[[:space:]]*:[[:space:]]*[0-9]*' "$CONFIG_PATH" | grep -o '[0-9]*$')
  if [ -n "$TIMEOUT" ] && [ "$TIMEOUT" -gt 0 ] 2>/dev/null; then
    DELAY=$TIMEOUT
  fi
fi

sleep "$DELAY"

# If we reach here, the timer wasn't cancelled — send reminder
# Find config: specified path → skill dir → project dir → home dir
CONFIG=""
for path in "$CONFIG_PATH" "$(cd "$(dirname "$0")/.." && pwd)/.lark-notify.json" "$(pwd)/.lark-notify.json" "$HOME/.lark-notify.json"; do
  if [ -f "$path" ]; then
    CONFIG="$path"
    break
  fi
done

if [ -z "$CONFIG" ]; then
  echo "No .lark-notify.json found" >&2
  exit 1
fi

# Parse config (prefer chat_id over user_id)
CHAT_ID=$(grep -o '"chat_id"[[:space:]]*:[[:space:]]*"[^"]*"' "$CONFIG" | head -1 | grep -o '"[^"]*"$' | tr -d '"')
USER_ID=$(grep -o '"user_id"[[:space:]]*:[[:space:]]*"[^"]*"' "$CONFIG" | head -1 | grep -o '"[^"]*"$' | tr -d '"')
PREFER=$(grep -o '"prefer"[[:space:]]*:[[:space:]]*"[^"]*"' "$CONFIG" | head -1 | grep -o '"[^"]*"$' | tr -d '"')

SEND_FLAG=""
TARGET_ID=""
if [ "$PREFER" = "user_id" ] && [ -n "$USER_ID" ] && [ "$USER_ID" != "ou_xxxxx" ]; then
  SEND_FLAG="--user-id"
  TARGET_ID="$USER_ID"
elif [ -n "$CHAT_ID" ] && [ "$CHAT_ID" != "oc_xxxxx" ]; then
  SEND_FLAG="--chat-id"
  TARGET_ID="$CHAT_ID"
else
  echo "No valid target ID in config" >&2
  exit 1
fi

# Escape markdown special chars in question
ESCAPED_QUESTION=$(echo "$QUESTION" | sed 's/"/\\"/g' | sed 's/\n/\\n/g')

lark-cli im +messages-send \
  "$SEND_FLAG" "$TARGET_ID" \
  --markdown "🔔 **Claude 等待确认**
---
**待确认问题**：
${ESCAPED_QUESTION}
---
请回到 Claude Code 终端进行操作" \
  --as bot 2>/dev/null

# Clean up PID file
rm -f "$PID_FILE"
