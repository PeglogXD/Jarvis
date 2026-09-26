#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
PORT="${OPENCODE_WEB_PORT:-7791}"

if command -v python3 >/dev/null 2>&1; then
  PY=python3
elif command -v python >/dev/null 2>&1; then
  PY=python
else
  echo "python3 is required (on ChromeOS: enable Linux support, then: sudo apt install python3)"
  exit 1
fi

echo ""
echo "  opencode-web 2.0"
echo "  ---------------------------------------------"
echo "  URL: http://127.0.0.1:$PORT"
echo ""

if command -v xdg-open >/dev/null 2>&1; then
  (sleep 1 && xdg-open "http://127.0.0.1:$PORT") &
fi

exec "$PY" server.py
