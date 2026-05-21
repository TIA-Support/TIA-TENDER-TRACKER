#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
#  TIA Solutions | ICT Tender Tracker — Linux / macOS launcher
# ─────────────────────────────────────────────────────────────
set -e
cd "$(dirname "$0")"

echo ""
echo " ========================================================="
echo "  TIA Solutions | ICT Tender Tracker"
echo " ========================================================="
echo ""

# ── Check Python ──────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
    echo " [ERROR] python3 not found. Install Python 3.10+ first."
    exit 1
fi

# ── Virtual environment ───────────────────────────────────────
if [ ! -f ".venv/bin/activate" ]; then
    echo " [Setup] Creating virtual environment..."
    python3 -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate

# ── Install / update dependencies ────────────────────────────
echo " [Setup] Checking Python dependencies..."
pip install -q -r requirements.txt

# ── Install Playwright browser ────────────────────────────────
echo " [Setup] Checking Playwright browser..."
playwright install chromium 2>/dev/null || true

# ── Discover network IP ───────────────────────────────────────
LOCAL_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || ipconfig getifaddr en0 2>/dev/null || echo "")

# ── Launch ────────────────────────────────────────────────────
echo ""
echo " ========================================================="
echo "  Server starting..."
echo ""
echo "  Local:    http://localhost:5000"
if [ -n "$LOCAL_IP" ]; then
    echo "  Network:  http://$LOCAL_IP:5000"
fi
echo ""
echo "  Press Ctrl+C to stop the server."
echo " ========================================================="
echo ""

# Open browser after 2 s (best-effort, skip if no display)
(sleep 2 && (open "http://localhost:5000" 2>/dev/null || xdg-open "http://localhost:5000" 2>/dev/null)) &

python3 app.py
