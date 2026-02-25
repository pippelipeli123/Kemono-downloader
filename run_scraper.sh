#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/scraper_env"
SCRAPER="$SCRIPT_DIR/scraper.py"

if [ $# -eq 0 ]; then
    echo "============================================"
    echo " Universal Structure-Based Media Scraper"
    echo "============================================"
    echo ""
    echo "Usage:  ./run_scraper.sh URL [OPTIONS]"
    echo ""
    echo "Examples:"
    echo "  ./run_scraper.sh https://coomer.st/onlyfans/user/laniafawn"
    echo "  ./run_scraper.sh https://kemono.su/patreon/user/12345"
    echo "  ./run_scraper.sh https://any-fork.com/page --force"
    echo ""
    echo "Options:"
    echo "  -o, --output DIR        Save location (default: auto)"
    echo "  -d, --delay SECONDS     Delay between downloads (default: 1.5)"
    echo "  -p, --pages N           Max pages, 0=all (default: 0)"
    echo "  -r, --retries N         Retry sweeps for failures (default: 3)"
    echo "  --skip-images           Don't download images"
    echo "  --skip-videos           Don't download videos"
    echo "  --dry-run               List files without downloading"
    echo "  --show-browser          Show browser window for debugging"
    echo "  --force                 Skip compatibility check"
    echo "  --min-confidence N      Min detection score 0-100 (default: 20)"
    echo ""
    exit 0
fi

echo "============================================"
echo " Universal Structure-Based Media Scraper"
echo "============================================"

for pkg in python3 python3-pip python3-venv; do
    if ! dpkg -s "$pkg" &>/dev/null 2>&1; then
        sudo apt update && sudo apt install -y "$pkg"
    fi
done

if ! dpkg -s libnss3 &>/dev/null 2>&1; then
    echo "Installing browser dependencies..."
    sudo apt install -y libnss3 libatk-bridge2.0-0 libdrm2 libxkbcommon0 \
        libxcomposite1 libxdamage1 libxrandr2 libgbm1 libpango-1.0-0 \
        libasound2t64 libatspi2.0-0t64 libcups2t64 2>/dev/null || \
    sudo apt install -y libnss3 libatk-bridge2.0-0 libdrm2 libxkbcommon0 \
        libxcomposite1 libxdamage1 libxrandr2 libgbm1 libpango-1.0-0 \
        libasound2 libatspi2.0-0 libcups2 2>/dev/null || true
fi

if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtual environment..."
    python3 -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"
pip install --quiet --upgrade pip
pip install --quiet requests playwright

if ! python3 -c "
from playwright.sync_api import sync_playwright
p = sync_playwright().start()
b = p.chromium.launch()
b.close()
p.stop()
" 2>/dev/null; then
    echo "Installing Chromium browser..."
    playwright install chromium
    playwright install-deps chromium 2>/dev/null || true
fi

echo ""
python3 "$SCRAPER" "$@"
deactivate
