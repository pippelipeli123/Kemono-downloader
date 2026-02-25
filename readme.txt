# Universal Structure-Based Media Scraper — Usage Guide

Location: /mnt/btrfs/kuningatar/scriptus/

Downloads FULL-RESOLUTION media with automatic retry sweeps.
Auto-detects compatible sites by page structure, not URL format.

================================================================================
FIRST-TIME SETUP
================================================================================

    cd /mnt/btrfs/kuningatar/scriptus/
    chmod +x run_scraper.sh scraper.py
    ./run_scraper.sh https://coomer.st/onlyfans/user/laniafawn --dry-run


================================================================================
BASIC USAGE
================================================================================

    cd /mnt/btrfs/kuningatar/scriptus/

    # Just paste the URL
    ./run_scraper.sh https://coomer.st/onlyfans/user/laniafawn
    ./run_scraper.sh https://kemono.su/patreon/user/12345
    ./run_scraper.sh https://any-fork.com/service/user/name

    # Preview first
    ./run_scraper.sh URL --dry-run

    # Images only
    ./run_scraper.sh URL --skip-videos

    # Videos only
    ./run_scraper.sh URL --skip-images

    # Custom output
    ./run_scraper.sh URL -o ~/Pictures/stuff

    # More retries for flaky connections
    ./run_scraper.sh URL -r 5

    # Force on unknown sites
    ./run_scraper.sh URL --force


================================================================================
ALL OPTIONS
================================================================================

    ./run_scraper.sh URL [OPTIONS]

    URL                     Any URL to scrape (required)
    -o, --output DIR        Save location (default: auto from URL)
    -d, --delay SECONDS     Delay between downloads (default: 1.5)
    -p, --pages N           Max pages, 0=all (default: 0)
    -r, --retries N         Retry sweeps for failed files (default: 3)
    --skip-images           Don't download images
    --skip-videos           Don't download videos
    --dry-run               List files without downloading
    --show-browser          Show browser window
    --force                 Skip compatibility check
    --min-confidence N      Min detection score 0-100 (default: 20)


================================================================================
NEW FEATURES
================================================================================

--- Full Resolution Downloads ---

    The scraper now automatically:
    - Prefers <a href> (full-res link) over <img src> (thumbnail)
    - Transforms /thumbnail/data/... → /data/... paths
    - Tries alternate CDN hosts (c1.site.com, c2.site.com, etc.)
    - Detects thumbnail URLs by hostname (img.*, thumb.*, etc.)
    - Checks data-full, data-original attributes for hi-res versions
    - Validates downloads aren't HTML error pages disguised as images
    - Marks thumbnails with 🔍THUMB in dry-run output

    In --dry-run mode, thumbnail URLs are flagged so you can verify.

--- Automatic Retry Sweeps ---

    When downloads fail, the scraper:
    1. Finishes downloading everything else first
    2. Collects all failed URLs
    3. For each failed URL, generates alternate candidates:
       - Different CDN hosts (c1, c2, c3, etc.)
       - Thumbnail → full-res path transforms
       - Hosts that are known to work (verified from successful downloads)
    4. Retries each failed file with all candidates
    5. Repeats up to N times (default: 3)
    6. Saves any still-failing URLs to failed_urls.txt

    Configure retries:
      ./run_scraper.sh URL -r 5     # 5 retry sweeps
      ./run_scraper.sh URL -r 0     # no retries (fail fast)


================================================================================
WHERE FILES END UP
================================================================================

    ~/Downloads/scraper_output/
    ├── coomer.st_onlyfans_user_laniafawn/
    │   ├── images/              ← full-res .jpg .png .gif .webp
    │   ├── videos/              ← .mp4 .webm .mov
    │   ├── debug/
    │   │   ├── page1.png        ← screenshot
    │   │   ├── rendered.html    ← rendered HTML
    │   │   ├── network_log.txt  ← all network requests
    │   │   └── profile.json     ← everything discovered about the site
    │   ├── media_urls.txt       ← all media URLs (full-res)
    │   ├── posts.json           ← raw API post data
    │   └── failed_urls.txt      ← URLs that failed all retries (if any)
    └── ...


================================================================================
COMMON WORKFLOWS
================================================================================

--- "Get everything, full quality" ---

    ./run_scraper.sh https://coomer.st/onlyfans/user/laniafawn

--- "Connection is flaky" ---

    ./run_scraper.sh URL -r 5 -d 3

--- "Check resolution before downloading" ---

    ./run_scraper.sh URL --dry-run
    # Look for 🔍THUMB markers — those would be thumbnails
    # If you see many, the full-res resolver should handle them

--- "Some files failed, retry just those" ---

    # Re-run the same command — it skips already-downloaded files
    # and the retry sweep will handle the rest
    ./run_scraper.sh URL

--- "Unknown site, not sure if compatible" ---

    ./run_scraper.sh https://mystery-site.com/page --show-browser --dry-run
    # Check the confidence score
    # If low but looks right: --force


================================================================================
TROUBLESHOOTING
================================================================================

--- Still getting thumbnails? ---

    # Check what the scraper found:
    cat ~/Downloads/scraper_output/FOLDER/debug/profile.json
    # Look at data_hosts and thumb_hosts

    # Check network log for full-res patterns:
    grep '/data/' ~/Downloads/scraper_output/FOLDER/debug/network_log.txt

    # Try with visible browser:
    ./run_scraper.sh URL --show-browser --dry-run

--- Many retries failing? ---

    # Check failed_urls.txt:
    cat ~/Downloads/scraper_output/FOLDER/failed_urls.txt

    # The files might be genuinely gone from the CDN
    # Or you might need more delay:
    ./run_scraper.sh URL -d 5 -r 5

--- "No media found" ---

    xdg-open ~/Downloads/scraper_output/FOLDER/debug/page1.png
    ./run_scraper.sh URL --show-browser --dry-run

--- Browser won't launch ---

    source scraper_env/bin/activate
    playwright install chromium
    playwright install-deps chromium
    deactivate


================================================================================
HOW IT WORKS
================================================================================

    Phase 0: Resolve URL, follow redirects, fingerprint raw HTML
    Phase 1: Browser loads page, intercepts all traffic, discovers:
             - API endpoints (from network interception)
             - Data/CDN hosts (tracked per response)
             - Thumbnail hosts (tracked per response)
             - Full-res links (prefers <a href> over <img src>)
             - Cookies for authenticated requests
    Phase 2: Paginate through all pages (API first, browser fallback)
    Phase 3: Collect + resolve thumbnails to full-res:
             - /thumbnail/data/... → /data/...
             - img.site.com → site.com, c1.site.com, c2.site.com
             - data-full, data-original attributes
    Phase 4: Download with retry sweeps:
             - First pass: download everything
             - Each retry: try alternate hosts/paths for failures
             - Tracks which hosts actually work
             - Validates downloads aren't error pages
             - Saves permanently-failed URLs to failed_urls.txt


================================================================================
FILES
================================================================================

    /mnt/btrfs/kuningatar/scriptus/
    ├── run_scraper.sh       ← run this
    ├── scraper.py           ← scraper code
    ├── scraper_env/         ← Python venv (auto-created)
    └── README.md            ← this guide
