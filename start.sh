#!/usr/bin/env bash
#
# One command to run movieBeli locally and expose it to your phone over LAN.
# Usage:  ./start.sh
#
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-8000}"

echo "▶ Starting Postgres (docker compose)…"
docker compose up -d

echo "▶ Waiting for Postgres to accept connections…"
until docker exec moviebeli-db pg_isready -U moviebeli >/dev/null 2>&1; do
  sleep 1
done
echo "  ✓ Postgres ready"

if [ ! -d .venv ]; then
  echo "▶ Creating Python virtualenv (.venv)…"
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
echo "▶ Installing backend dependencies…"
pip install -q -r backend/requirements.txt

if [ ! -f secrets/.env ]; then
  echo ""
  echo "⚠  secrets/.env not found."
  echo "   Run:  cp env.example secrets/.env   and paste your TMDB token."
  echo "   (Search works without it, but adding a movie needs the TMDB key.)"
  echo ""
fi

# Best-effort LAN IP for the phone URL (macOS Wi-Fi first, then Linux).
IP="$(ipconfig getifaddr en0 2>/dev/null \
   || ipconfig getifaddr en1 2>/dev/null \
   || (hostname -I 2>/dev/null | awk '{print $1}') \
   || echo 'your-computer-ip')"

echo ""
echo "────────────────────────────────────────────────────────"
echo "  movieBeli is starting on port ${PORT}"
echo "    • On this computer:  http://localhost:${PORT}"
echo "    • On your iPhone:    http://${IP}:${PORT}   (same Wi-Fi)"
echo "  Open that URL in Safari, then Share ▸ Add to Home Screen."
echo "  Press Ctrl-C to stop.  (Postgres keeps running: docker compose down)"
echo "────────────────────────────────────────────────────────"
echo ""

cd backend
exec python -m app
