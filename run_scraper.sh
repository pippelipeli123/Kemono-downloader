#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/scraper_env"
SCRAPER="$SCRIPT_DIR/scraper.py"

# ── Check if installed ──
if [ ! -d "$VENV_DIR" ]; then
    echo "============================================"
    echo " Not installed yet!"
    echo "============================================"
    echo ""
    echo " Run the installer first (one-time only):"
    echo ""
    echo "   cd $SCRIPT_DIR"
    echo "   chmod +x install.sh"
    echo "   ./install.sh"
    echo ""
    exit 1
fi

# ── Help ──
if [ $# -eq 0 ]; then
    cat << 'EOF'
============================================
 Universal Media Scraper
============================================

Usage:  ./run_scraper.sh URL [OPTIONS]

 ── Kemono / Coomer ──────────────────────
  ./run_scraper.sh https://coomer.st/onlyfans/user/laniafawn
  ./run_scraper.sh https://kemono.su/patreon/user/12345

 ── YouTube ──────────────────────────────
  ./run_scraper.sh https://www.youtube.com/watch?v=VIDEO_ID
  ./run_scraper.sh https://www.youtube.com/@ChannelName
  ./run_scraper.sh "https://youtube.com/watch?v=ID" -f 1080p
  ./run_scraper.sh "https://youtube.com/watch?v=ID" -f audio

 ── Other video sites ────────────────────
  ./run_scraper.sh https://vimeo.com/123456
  ./run_scraper.sh https://www.tiktok.com/@user/video/123

 ── Options ──────────────────────────────
  General:
    -o, --output DIR          Save location (default: auto)
    -d, --delay SECONDS       Delay between downloads (default: 1.5)
    -r, --retries N           Retry attempts (default: 3)
    --skip-images             Don't download images
    --skip-videos             Don't download videos
    --dry-run                 List files without downloading

  Kemono/Coomer:
    -p, --pages N             Max pages, 0=all (default: 0)
    --show-browser            Show browser window
    --force                   Skip compatibility check
    --min-confidence N        Min detection score (default: 20)

  YouTube / yt-dlp:
    -f, --format FMT          best, 1080p, 720p, 480p, 4k, audio, mp4
    --subs                    Download subtitles
    --no-thumbs               Don't save thumbnails
    --max-items N             Max playlist items, 0=all
    --cookies-from BROWSER    Import cookies (chrome, firefox)
    --use-ytdlp               Force yt-dlp mode
    --use-kemono              Force Kemono mode

  Deduplication:
    --dedup                   Remove duplicate files after download
    --similar N               Also find similar images (1-10 strictness)
    --dedup-only DIR          Just dedup a folder, no scraping
    --no-dedup                Disable dedup

 ── Maintenance ──────────────────────────
  Update dependencies:    ./install.sh
  Update yt-dlp only:     source scraper_env/bin/activate && pip install -U yt-dlp && deactivate

EOF
    exit 0
fi

# ── Ensure PATH includes local bins (for czkawka) ──
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

# ── Run ──
source "$VENV_DIR/bin/activate"
python3 "$SCRAPER" "$@"
deactivate
