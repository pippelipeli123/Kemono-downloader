# Universal Structure-Based Media Scraper — Usage Guide

Location: /mnt/btrfs/kuningatar/scriptus/

Detects compatible sites by analyzing page structure (DOM, network calls,
HTML fingerprints) — NOT by URL format. Works on any Kemono/Coomer fork,
mirror, or derivative, even if the URL format is completely different.

================================================================================
FIRST-TIME SETUP
================================================================================

    cd /mnt/btrfs/kuningatar/scriptus/
    chmod +x run_scraper.sh scraper.py
    ./run_scraper.sh link --dry-run


================================================================================
BASIC USAGE — JUST PASTE ANY URL
================================================================================

    cd /mnt/btrfs/kuningatar/scriptus/

    # Known sites
    ./run_scraper.sh https://coomer.st/onlyfans/user/
    ./run_scraper.sh https://kemono.su/patreon/user/12345
    ./run_scraper.sh https://nekohouse.su/fanbox/user/67890

    # Unknown fork — it auto-detects if the site is compatible
    ./run_scraper.sh https://whatever-fork.xyz/service/user/name

    # Not sure if it'll work? Try it — worst case it tells you "not compatible"
    ./run_scraper.sh https://some-random-site.com/page

    # Force it to try anyway even if it doesn't look compatible
    ./run_scraper.sh https://some-site.com/page --force

    # Preview first
    ./run_scraper.sh https://coomer.st/onlyfans/user/ --dry-run


================================================================================
ALL OPTIONS
================================================================================

    ./run_scraper.sh URL [OPTIONS]

    URL                     Any URL to scrape (required)

    -o, --output DIR        Where to save files
                            Default: ~/Downloads/scraper_output/SITE_PATH/

    -d, --delay SECONDS     Wait between downloads (default: 1.5)

    -p, --pages N           Max pages, ~50 posts each (0=all, default: 0)

    --skip-images           Don't download images
    --skip-videos           Don't download videos
    --dry-run               Show what would download, don't do it
    --show-browser          Show the browser window (debugging)
    --force                 Skip compatibility check, try to scrape anyway
    --min-confidence N      Min fingerprint score to proceed (0-100, default: 20)


================================================================================
HOW DETECTION WORKS
================================================================================

    The scraper does NOT rely on the URL format. Instead it:

    1. Loads the page in a real Chrome browser
    2. Fingerprints the HTML for structural markers:
       - SPA shell (<div id="root"></div>)
       - Vite bundler signatures
       - Known CSS classes (post-card, card-list, post__files, etc.)
       - Kemono-style data paths (/data/ab/cd/hash.jpg)
       - Pagination patterns (?o=50)
       - Analytics scripts (probable)
    3. Intercepts all network traffic looking for:
       - JSON API responses that look like post data
       - Media file requests
    4. Validates API responses by checking for expected fields:
       - id, file, attachments, service, user, etc.
    5. Assigns a confidence score (0-100%)
    6. If score >= 20%, proceeds. Otherwise stops (unless --force).

    The detected traits and confidence score are shown in the output.


================================================================================
WHAT IT AUTO-DISCOVERS
================================================================================

    - Site compatibility (via HTML fingerprinting)
    - API endpoint paths (by intercepting browser network calls)
    - DDoS Guard bypass headers (by probing)
    - Pagination format and page size (from DOM and URL patterns)
    - Service type and username (from API data and DOM)
    - Redirect chains (follows 301/302/307)
    - Page size (from offset differences in pagination links)


================================================================================
WHERE FILES END UP
================================================================================

    ~/Downloads/scraper_output/
    ├── coomer.st_onlyfans_user_laniafawn/
    │   ├── images/              ← .jpg .png .gif .webp
    │   ├── videos/              ← .mp4 .webm .mov
    │   ├── debug/
    │   │   ├── page1.png        ← screenshot of rendered page
    │   │   ├── rendered.html    ← full rendered HTML
    │   │   ├── network_log.txt  ← every URL the page requested
    │   │   └── profile.json     ← everything the scraper learned
    │   ├── media_urls.txt       ← all media URLs found
    │   └── posts.json           ← raw post data from API
    └── kemono.su_patreon_user_12345/
        └── ...


================================================================================
COMMON WORKFLOWS
================================================================================

--- "Just get everything" ---

    ./run_scraper.sh https://coomer.st/onlyfans/user/laniafawn

--- "Preview first" ---

    ./run_scraper.sh https://coomer.st/onlyfans/user/laniafawn --dry-run
    ./run_scraper.sh https://coomer.st/onlyfans/user/laniafawn

--- "Images only" ---

    ./run_scraper.sh URL --skip-videos

--- "Videos only" ---

    ./run_scraper.sh URL --skip-images

--- "Multiple users" ---

    ./run_scraper.sh https://coomer.st/onlyfans/user/user1
    ./run_scraper.sh https://coomer.st/onlyfans/user/user2
    ./run_scraper.sh https://kemono.su/patreon/user/12345

--- "New content since last run" ---

    ./run_scraper.sh URL    # skips already-downloaded files

--- "Site I'm not sure about" ---

    ./run_scraper.sh https://mystery-site.com/page --show-browser --dry-run
    # Look at confidence score and traits
    # If it says "NOT COMPATIBLE" but you think it should work:
    ./run_scraper.sh https://mystery-site.com/page --force

--- "Something's wrong" ---

    ./run_scraper.sh URL --show-browser --dry-run
    # Check debug files:
    xdg-open ~/Downloads/scraper_output/FOLDER/debug/page1.png
    cat ~/Downloads/scraper_output/FOLDER/debug/profile.json
    cat ~/Downloads/scraper_output/FOLDER/debug/network_log.txt


================================================================================
TROUBLESHOOTING
================================================================================

--- "NOT COMPATIBLE" but I think it should work ---

    # Lower the threshold:
    ./run_scraper.sh URL --min-confidence 5

    # Or force it:
    ./run_scraper.sh URL --force

    # Check what it detected:
    cat ~/Downloads/scraper_output/FOLDER/debug/profile.json

--- "No media found" ---

    xdg-open ~/Downloads/scraper_output/FOLDER/debug/page1.png
    less ~/Downloads/scraper_output/FOLDER/debug/rendered.html
    ./run_scraper.sh URL --show-browser --dry-run

--- Browser won't launch ---

    source scraper_env/bin/activate
    playwright install chromium
    playwright install-deps chromium
    deactivate

--- Domain doesn't resolve ---

    for d in coomer.st kemono.su nekohouse.su; do
        echo -n "$d: "
        curl -s -o /dev/null -w "%{http_code}" --connect-timeout 5 "https://$d"
        echo
    done

--- Permission denied ---

    chmod +x run_scraper.sh scraper.py


================================================================================
HOW IT WORKS (PHASES)
================================================================================

    Phase 0: Resolve
    ├── Follow redirects to find real domain
    ├── Fetch raw HTML
    └── Fingerprint: SPA shell? Vite? Known classes? Data paths?

    Phase 1: Browser Discovery
    ├── Load page in real Chrome (passes DDoS Guard)
    ├── Intercept ALL network traffic
    ├── Identify API calls by fingerprinting JSON responses
    ├── Learn API path, offset param, page size
    ├── Detect DDoS Guard bypass headers
    ├── Extract media from rendered DOM
    ├── Discover service/user from network + DOM
    └── Capture session cookies

    Phase 2: Pagination
    ├── Use discovered API endpoint + cookies (fast)
    └── Fallback: browser pagination if API blocked

    Phase 3: Collect
    ├── Extract media from all post data (flexible key matching)
    ├── Extract inline media from post content HTML
    └── Deduplicate everything

    Phase 4: Download
    ├── Download with progress bars
    ├── Skip existing files (resume-safe)
    └── Organize into images/ and videos/


================================================================================
FILES
================================================================================

    /mnt/btrfs/kuningatar/scriptus/
    ├── run_scraper.sh       ← run this
    ├── scraper.py           ← scraper code
    ├── scraper_env/         ← Python venv (auto-created)
    └── README.md            ← this guide
