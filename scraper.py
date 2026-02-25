#!/usr/bin/env python3
"""
scraper.py — Universal structure-based media scraper.
Detects Kemono/Coomer-style sites by analyzing DOM and network traffic.
Downloads full-resolution media with automatic retry sweeps.

Usage:
    python3 scraper.py https://coomer.st/onlyfans/user/laniafawn
    python3 scraper.py https://kemono.su/patreon/user/12345
    python3 scraper.py https://any-site.com/whatever --force --dry-run
"""

import os
import sys
import time
import re
import json
import logging
import argparse
import hashlib
from urllib.parse import urljoin, urlparse, parse_qs

import requests

try:
    from playwright.sync_api import sync_playwright
    from playwright.sync_api import TimeoutError as PWTimeout
except ImportError:
    print("ERROR: playwright not installed. Run:")
    print("  pip install playwright && playwright install chromium")
    sys.exit(1)

# ─── Config ──────────────────────────────────────────────────────────────────

DEFAULT_OUTPUT = os.path.expanduser("~/Downloads/scraper_output")
DEFAULT_DELAY = 1.5
DEFAULT_MAX_PAGES = 0
DEFAULT_RETRIES = 3

MEDIA_EXT = {
    "images": {
        ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp",
        ".tiff", ".avif", ".svg", ".jfif",
    },
    "videos": {
        ".mp4", ".webm", ".mov", ".avi", ".mkv", ".m4v",
        ".flv", ".ts", ".wmv",
    },
}
ALL_EXT = MEDIA_EXT["images"] | MEDIA_EXT["videos"]

# Known thumbnail → full-res path transformations
THUMBNAIL_PATTERNS = [
    # /thumbnail/data/xx/yy/hash.jpg → /data/xx/yy/hash.jpg
    (r'/thumbnail/data/', '/data/'),
    # /thumbnails/ → /data/
    (r'/thumbnails/', '/data/'),
    # Some forks use /thumb/ prefix
    (r'/thumb/', '/data/'),
    # img.domain.com → c1.domain.com or just domain.com
    # (handled separately in resolve_full_res)
]

# Known CDN/thumbnail hostname patterns
THUMB_HOSTS = [
    (r'^img\.', ''),         # img.coomer.st → coomer.st
    (r'^thumb\.', ''),       # thumb.site.com → site.com
    (r'^tn\.', ''),          # tn.site.com → site.com
    (r'^t\.', ''),           # t.site.com → site.com
]

# Data hostnames to try when thumbnail host fails
DATA_HOST_PREFIXES = ['c1', 'c2', 'c3', 'c4', 'c5', 'c6', 'data', '']

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def ext_of(url):
    return os.path.splitext(urlparse(url).path)[1].lower().split("?")[0]

def classify(url):
    e = ext_of(url)
    for typ, exts in MEDIA_EXT.items():
        if e in exts:
            return typ
    return None

def sanitize(name):
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name).strip('. ')[:200]

def slug_from_url(url):
    p = urlparse(url)
    parts = [p.netloc] + [x for x in p.path.strip("/").split("/") if x]
    return sanitize("_".join(parts)) or "unknown"


def resolve_full_res(url, base_url=""):
    """
    Given a URL that might be a thumbnail, produce a list of
    candidate full-resolution URLs to try, ordered by likelihood.
    The first entry is always the original URL as fallback.
    """
    candidates = []
    parsed = urlparse(url)
    path = parsed.path
    host = parsed.netloc
    scheme = parsed.scheme or "https"

    # ── Path-based transforms ──
    for pattern, replacement in THUMBNAIL_PATTERNS:
        if re.search(pattern, path):
            new_path = re.sub(pattern, replacement, path, count=1)
            new_url = f"{scheme}://{host}{new_path}"
            if parsed.query:
                new_url += f"?{parsed.query}"
            if new_url != url:
                candidates.append(new_url)

    # ── Host-based transforms ──
    for thumb_re, replace_with in THUMB_HOSTS:
        if re.match(thumb_re, host):
            new_host = re.sub(thumb_re, replace_with, host, count=1)
            # Also try with /data/ path fix
            for p in [path] + [
                re.sub(pat, rep, path, count=1)
                for pat, rep in THUMBNAIL_PATTERNS
                if re.search(pat, path)
            ]:
                new_url = f"{scheme}://{new_host}{p}"
                if parsed.query:
                    new_url += f"?{parsed.query}"
                if new_url != url and new_url not in candidates:
                    candidates.append(new_url)

    # ── Try CDN host variants (c1.site.com, c2.site.com, etc.) ──
    if base_url:
        base_host = urlparse(base_url).netloc
        base_domain = ".".join(base_host.split(".")[-2:])  # e.g. "coomer.st"

        # Only fix the path part
        data_path = path
        for pat, rep in THUMBNAIL_PATTERNS:
            if re.search(pat, data_path):
                data_path = re.sub(pat, rep, data_path, count=1)

        for prefix in DATA_HOST_PREFIXES:
            cdn_host = f"{prefix}.{base_domain}" if prefix else base_domain
            if cdn_host != host:
                new_url = f"{scheme}://{cdn_host}{data_path}"
                if parsed.query:
                    new_url += f"?{parsed.query}"
                if new_url not in candidates:
                    candidates.append(new_url)

    # Original URL as final fallback
    if url not in candidates:
        candidates.append(url)

    return candidates


def is_thumbnail_url(url):
    """Check if a URL looks like a thumbnail rather than full-res."""
    lower = url.lower()
    indicators = [
        '/thumbnail/', '/thumbnails/', '/thumb/', '/tn/',
        '_thumb.', '_small.', '_preview.', '_low.',
        '/small/', '/preview/', '/low/',
        'thumb=', 'size=small', 'quality=low',
    ]
    for ind in indicators:
        if ind in lower:
            return True

    # Check hostname
    host = urlparse(url).netloc.lower()
    if host.startswith(('img.', 'thumb.', 'tn.', 't.')):
        return True

    return False


# ─── Site Profile ────────────────────────────────────────────────────────────

class SiteProfile:
    def __init__(self):
        self.base_url = ""
        self.target_url = ""
        self.site_name = ""
        self.is_compatible = False
        self.confidence = 0
        self.traits = []
        self.service = None
        self.user_id = None
        self.target_path = None
        self.api_url = None
        self.api_base_path = None
        self.api_offset_param = "o"
        self.api_page_size = 50
        self.ddg_bypass = None
        self.has_pagination = False
        self.pagination_offsets = []
        self.data_hosts = set()       # discovered CDN/data hostnames
        self.thumb_hosts = set()      # discovered thumbnail hostnames


# ─── Fingerprinting ─────────────────────────────────────────────────────────

def fingerprint_html(html):
    traits = []
    score = 0
    h = html.lower()

    checks = [
        (r'<div\s+id\s*=\s*["\']root["\']\s*>\s*</div>', "SPA empty #root div", 15),
        (r'__vite_is_modern_browser', "Vite modern browser check", 10),
        (r'vite-legacy-polyfill', "Vite legacy polyfill", 10),
        (r'vite-legacy-entry', "Vite legacy entry", 5),
        (r'data-api.*probable|probable.*data-api', "Probable analytics", 12),
        (r'/assets/index-[a-zA-Z0-9_-]+\.js', "Hashed JS bundle", 8),
        (r'/assets/style-[a-zA-Z0-9_-]+\.css', "Hashed CSS bundle", 8),
        (r'/static/js/lazy-styles\.js', "lazy-styles.js", 10),
        (r'kemono', "Kemono keyword", 5),
        (r'coomer', "Coomer keyword", 5),
        (r'nekohouse', "Nekohouse keyword", 5),
        (r'post-card', "post-card class", 8),
        (r'card-list', "card-list class", 6),
        (r'post__files', "post__files class", 8),
        (r'post__attachments', "post__attachments class", 8),
        (r'post__thumbnail', "post__thumbnail class", 6),
        (r'fancy-link', "fancy-link class", 5),
        (r'user-header', "user-header class", 5),
        (r'/api/v1/', "API v1 path", 6),
        (r'flagged[-_]?post', "flagged post reference", 5),
        (r'[?&]o=\d+', "offset pagination", 8),
        (r'class="paginator"', "paginator class", 6),
        (r'kemono-logo', "Kemono logo", 8),
        (r'/data/[a-f0-9]{2}/[a-f0-9]{2}/', "Kemono data path", 12),
        (r'/thumbnail/data/', "Kemono thumbnail path", 10),
    ]

    for pattern, name, points in checks:
        if re.search(pattern, html if name.startswith("SPA") else h):
            traits.append(name)
            score += points

    return min(score, 100), traits


def fingerprint_api_response(data):
    traits = []
    if not isinstance(data, list) or len(data) == 0:
        return False, traits

    samples = data[:5]
    post_score = 0
    expected = {
        "id": 3, "user": 3, "service": 3, "title": 2, "content": 2,
        "file": 5, "attachments": 5, "added": 2, "published": 2,
        "edited": 1, "embed": 1, "shared_file": 1,
    }

    for item in samples:
        if not isinstance(item, dict):
            continue
        keys = set(item.keys())
        for field, points in expected.items():
            if field in keys:
                post_score += points
        fobj = item.get("file")
        if isinstance(fobj, dict) and ("path" in fobj or "name" in fobj):
            post_score += 5
            traits.append("file.path structure")
        atts = item.get("attachments")
        if isinstance(atts, list):
            for a in atts[:3]:
                if isinstance(a, dict) and ("path" in a or "name" in a):
                    post_score += 3
                    traits.append("attachments[].path structure")
                    break

    avg = post_score / max(len(samples), 1)
    is_compat = avg >= 8
    if is_compat:
        traits.insert(0, f"Post structure match (avg: {avg:.0f})")
    return is_compat, traits


# ─── Scraper ─────────────────────────────────────────────────────────────────

class UniversalScraper:
    def __init__(self, url, output, delay, max_pages, headless,
                 force=False, min_confidence=20, max_retries=3):
        self.input_url = url
        self.output = output
        self.delay = delay
        self.max_pages = max_pages
        self.headless = headless
        self.force = force
        self.min_confidence = min_confidence
        self.max_retries = max_retries

        self.profile = SiteProfile()
        self.http = requests.Session()
        self.http.headers["User-Agent"] = UA
        self.cookies = {}
        self.api_posts = []
        self.media_urls = []         # final full-res URLs
        self.raw_media_urls = []     # everything found (thumbs + full)

        # Resolution mapping: thumbnail_url → [full_res_candidates]
        self.resolution_map = {}

        # Track which data/CDN hosts actually work
        self.verified_hosts = set()

    def make_absolute(self, url):
        if not url:
            return ""
        if url.startswith("//"):
            return "https:" + url
        if url.startswith("/"):
            return self.profile.base_url + url
        if not url.startswith("http"):
            return self.profile.base_url + "/" + url
        return url

    # ── Phase 0 ──────────────────────────────────────────────────────────

    def phase0_resolve(self):
        log.info("🔎 Phase 0: Resolving URL...")

        url = self.input_url.strip()
        if not url.startswith("http"):
            if url.startswith("//"):
                url = "https:" + url
            elif not url.startswith("/"):
                url = "https://" + url
            else:
                log.error(f"Cannot resolve bare path: {url}")
                sys.exit(1)

        try:
            r = self.http.head(url, allow_redirects=True, timeout=15)
            final = r.url
            parsed = urlparse(final)
            self.profile.base_url = f"{parsed.scheme}://{parsed.netloc}"
            self.profile.target_url = final
            if final != url:
                log.info(f"  ↳ {url} → {final}")
            else:
                log.info(f"  URL: {final}")
        except requests.exceptions.RequestException as e:
            log.warning(f"  ⚠ HEAD failed: {e}")
            parsed = urlparse(url)
            self.profile.base_url = f"{parsed.scheme}://{parsed.netloc}"
            self.profile.target_url = url

        parsed = urlparse(self.profile.target_url)
        self.profile.target_path = parsed.path.rstrip("/") or "/"
        self._hint_from_url(self.profile.target_url)

        try:
            r = self.http.get(self.profile.target_url, timeout=15)
            score, traits = fingerprint_html(r.text)
            self.profile.confidence = score
            self.profile.traits = traits
            m = re.search(r'<title>([^<]+)</title>', r.text)
            if m:
                self.profile.site_name = m.group(1).strip()
            log.info(f"  Fingerprint: {score}/100")
            for t in traits:
                log.info(f"    ✓ {t}")
        except requests.exceptions.RequestException as e:
            log.warning(f"  ⚠ Fetch failed: {e}")

    def _hint_from_url(self, url):
        parsed = urlparse(url)
        path = parsed.path.rstrip("/")
        m = re.search(r'/([^/]+)/user/([^/]+)', path)
        if m:
            self.profile.service = m.group(1).lower()
            self.profile.user_id = m.group(2)
            log.info(f"  URL hint: service={self.profile.service}, "
                     f"user={self.profile.user_id}")
            return
        m = re.search(r'/user/([^/]+)', path)
        if m:
            self.profile.user_id = m.group(1)

    # ── Phase 1 ──────────────────────────────────────────────────────────

    def phase1_discover(self):
        log.info("\n🌐 Phase 1: Browser discovery...")

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=self.headless)
            ctx = browser.new_context(user_agent=UA)
            page = ctx.new_page()

            intercepted_apis = []
            intercepted_media = []
            all_request_urls = []

            def on_response(resp):
                url = resp.url
                status = resp.status
                ct = resp.headers.get("content-type", "")
                all_request_urls.append(url)

                # Track data/CDN hosts
                host = urlparse(url).netloc
                if "/data/" in url and status == 200:
                    self.profile.data_hosts.add(host)
                if "/thumbnail/" in url and status == 200:
                    self.profile.thumb_hosts.add(host)

                if status == 200 and ext_of(url) in ALL_EXT:
                    intercepted_media.append(url)

                if status == 200:
                    is_api = (
                        "json" in ct or "/api/" in url or
                        url.endswith("?o=0") or
                        re.search(r'\?.*o=\d+', url)
                    )
                    if is_api:
                        try:
                            data = resp.json()
                            ok, api_traits = fingerprint_api_response(data)
                            if ok:
                                intercepted_apis.append((url, data))
                                self.profile.traits.extend(api_traits)
                                log.info(
                                    f"  📦 Intercepted {len(data)} posts "
                                    f"from {urlparse(url).path}"
                                )
                        except Exception:
                            pass

            page.on("response", on_response)

            log.info(f"  Loading: {self.profile.target_url}")
            try:
                resp = page.goto(
                    self.profile.target_url,
                    wait_until="networkidle", timeout=60000,
                )
                if resp and resp.url:
                    fp = urlparse(resp.url)
                    new_base = f"{fp.scheme}://{fp.netloc}"
                    if new_base != self.profile.base_url:
                        log.info(f"  ↳ Redirected to: {new_base}")
                        self.profile.base_url = new_base
            except PWTimeout:
                log.warning("  ⚠ Timeout — continuing")

            time.sleep(5)

            try:
                for _ in range(3):
                    page.evaluate(
                        "window.scrollTo(0, document.body.scrollHeight)"
                    )
                    time.sleep(1)
                page.evaluate("window.scrollTo(0, 0)")
                time.sleep(1)
            except Exception:
                pass

            rendered_html = page.content()
            rs, rt = fingerprint_html(rendered_html)
            if rs > self.profile.confidence:
                self.profile.confidence = rs
            for t in rt:
                if t not in self.profile.traits:
                    self.profile.traits.append(t)

            dom_info = self._analyze_dom(page)
            self.profile.traits.extend(dom_info.get("extra_traits", []))

            if intercepted_apis:
                self.profile.confidence = min(
                    self.profile.confidence + 25, 100
                )
                self.profile.traits.append(
                    f"Intercepted {len(intercepted_apis)} API call(s)"
                )

            if dom_info.get("post_count", 0) > 0:
                self.profile.confidence = min(
                    self.profile.confidence + 10, 100
                )

            self.profile.is_compatible = (
                self.profile.confidence >= self.min_confidence
            )

            log.info(f"\n  📊 Confidence: {self.profile.confidence}/100")
            log.info(
                f"  {'✅ COMPATIBLE' if self.profile.is_compatible else '❌ NOT COMPATIBLE'}"
            )

            if self.profile.data_hosts:
                log.info(f"  📡 Data hosts: {self.profile.data_hosts}")
            if self.profile.thumb_hosts:
                log.info(f"  📡 Thumb hosts: {self.profile.thumb_hosts}")

            if not self.profile.is_compatible and not self.force:
                log.error(
                    "\n  Site doesn't look like a Kemono/Coomer fork."
                )
                log.error(
                    f"  Score {self.profile.confidence}% "
                    f"< threshold {self.min_confidence}%"
                )
                log.error("  Use --force to try anyway.")
                for t in self.profile.traits:
                    log.info(f"    • {t}")
                self._save_debug(page, rendered_html, all_request_urls)
                browser.close()
                sys.exit(1)

            if intercepted_apis:
                self._learn_from_apis(intercepted_apis)

            if not self.profile.user_id:
                self._discover_identity(page, all_request_urls)

            for url_data in intercepted_apis:
                _, data = url_data
                if isinstance(data, list):
                    self.api_posts.extend(data)

            # Scrape DOM for media — get BOTH thumbnail and full-res
            dom_media = self._scrape_dom_full_res(page)
            self.raw_media_urls.extend(dom_media)
            self.raw_media_urls.extend(intercepted_media)

            self._discover_pagination(page)

            for c in ctx.cookies():
                self.cookies[c["name"]] = c["value"]

            self._save_debug(page, rendered_html, all_request_urls)

            log.info(f"\n  🍪 Cookies:    {len(self.cookies)}")
            log.info(f"  📦 API posts:  {len(self.api_posts)}")
            log.info(f"  🖼  Raw media:  {len(dom_media)}")
            log.info(f"  🔗 API path:   "
                     f"{self.profile.api_base_path or 'not found'}")

            browser.close()

    def _analyze_dom(self, page):
        info = {"post_count": 0, "extra_traits": []}
        for sel in [
            "article", ".post-card", '[class*="post-card"]',
            ".card-list__items > *",
        ]:
            try:
                count = page.eval_on_selector_all(sel, "els => els.length")
                if count > 0:
                    info["post_count"] = max(info["post_count"], count)
                    info["extra_traits"].append(
                        f"DOM: {count}x '{sel}'"
                    )
            except Exception:
                pass
        for sel in [
            ".user-header", '[class*="user-header"]',
            ".user-card", ".creator-header",
        ]:
            try:
                if page.query_selector(sel):
                    info["extra_traits"].append(f"DOM: user header ({sel})")
                    break
            except Exception:
                pass
        for sel in [
            ".paginator", '[class*="paginator"]',
            "menu.paginator",
        ]:
            try:
                if page.query_selector(sel):
                    info["extra_traits"].append(f"DOM: pagination ({sel})")
                    break
            except Exception:
                pass
        return info

    def _scrape_dom_full_res(self, page):
        """
        Extract media from DOM, preferring full-resolution versions.
        For each thumbnail found, also generate full-res candidates.
        """
        urls = []

        # ── Strategy 1: <a> wrapping <img> — the link is usually full-res ──
        try:
            pairs = page.evaluate("""
                () => {
                    const results = [];
                    // Links containing images (thumbnail links to full-res)
                    document.querySelectorAll('a[href] img[src]').forEach(img => {
                        const a = img.closest('a[href]');
                        if (a) {
                            results.push({
                                link: a.href,
                                img: img.src,
                                type: 'linked_image'
                            });
                        }
                    });
                    // Standalone images (no wrapping link)
                    document.querySelectorAll('img[src]').forEach(img => {
                        const a = img.closest('a[href]');
                        if (!a) {
                            results.push({
                                link: null,
                                img: img.src,
                                type: 'standalone_image'
                            });
                        }
                    });
                    return results;
                }
            """)

            for pair in pairs:
                link_url = pair.get("link")
                img_url = pair.get("img")

                # Prefer the link (usually full-res) over the img (usually thumb)
                if link_url and ext_of(link_url) in ALL_EXT:
                    urls.append(link_url)
                    # If the img looks like a thumbnail, map it
                    if img_url and is_thumbnail_url(img_url):
                        self.resolution_map[img_url] = link_url
                elif link_url and "/data/" in link_url:
                    urls.append(link_url)
                elif img_url and ext_of(img_url) in ALL_EXT:
                    urls.append(img_url)

        except Exception as e:
            log.warning(f"  ⚠ Link+img extraction failed: {e}")
            # Fallback: just get all img srcs
            try:
                srcs = page.eval_on_selector_all(
                    "img[src]", "els => els.map(e => e.src)"
                )
                urls.extend(s for s in srcs if ext_of(s) in ALL_EXT)
            except Exception:
                pass

        # ── Strategy 2: Direct download links ──
        try:
            dl_links = page.eval_on_selector_all(
                'a[href*="/data/"], a[download][href]',
                "els => els.map(e => e.href)"
            )
            urls.extend(dl_links)
        except Exception:
            pass

        # ── Strategy 3: Video sources ──
        try:
            vsrcs = page.eval_on_selector_all(
                "video source[src], video[src]",
                "els => els.map(e => e.src)"
            )
            urls.extend(v for v in vsrcs if v)
        except Exception:
            pass

        # ── Strategy 4: All <a> with media extensions ──
        try:
            hrefs = page.eval_on_selector_all(
                "a[href]", "els => els.map(e => e.href)"
            )
            for h in hrefs:
                if ext_of(h) in ALL_EXT and h not in urls:
                    urls.append(h)
        except Exception:
            pass

        # ── Strategy 5: data-src (lazy loading) ──
        try:
            ds = page.eval_on_selector_all(
                "[data-src]",
                "els => els.map(e => e.getAttribute('data-src'))"
            )
            for d in ds:
                if d and ext_of(d) in ALL_EXT:
                    urls.append(self.make_absolute(d))
        except Exception:
            pass

        # ── Strategy 6: data-full, data-original attributes ──
        try:
            for attr in ["data-full", "data-original", "data-large",
                         "data-src-full", "data-highres"]:
                vals = page.eval_on_selector_all(
                    f"[{attr}]",
                    f"els => els.map(e => e.getAttribute('{attr}'))"
                )
                for v in vals:
                    if v and (ext_of(v) in ALL_EXT or "/data/" in v):
                        urls.append(self.make_absolute(v))
        except Exception:
            pass

        # ── Strategy 7: Background images ──
        try:
            bgs = page.evaluate("""
                () => {
                    const u = [];
                    document.querySelectorAll('[style*="background"]')
                        .forEach(e => {
                            const m = e.style.backgroundImage.match(
                                /url\\(["']?([^"')]+)["']?\\)/
                            );
                            if (m) u.push(m[1]);
                        });
                    return u;
                }
            """)
            for b in bgs:
                if ext_of(b) in ALL_EXT:
                    urls.append(self.make_absolute(b))
        except Exception:
            pass

        return urls

    def _learn_from_apis(self, intercepted_apis):
        for url, data in intercepted_apis:
            parsed = urlparse(url)
            path = parsed.path
            query = parse_qs(parsed.query)

            for param in ("o", "offset", "skip", "page"):
                if param in query:
                    self.profile.api_offset_param = param
                    break

            if isinstance(data, list):
                self.profile.api_page_size = max(len(data), 25)

            self.profile.api_url = url.split("?")[0]
            self.profile.api_base_path = path.split("?")[0]

            if not self.profile.service or not self.profile.user_id:
                m = re.search(r'/(\w+)/user/([^/?]+)', path)
                if m:
                    self.profile.service = m.group(1)
                    self.profile.user_id = m.group(2)

            self._detect_ddg()
            break

    def _detect_ddg(self):
        if not self.profile.api_url:
            return
        try:
            r = self.http.get(
                self.profile.api_url,
                params={self.profile.api_offset_param: 0},
                timeout=10,
            )
            if r.status_code == 200:
                try:
                    r.json()
                    return
                except Exception:
                    pass
            if r.status_code == 403:
                for hdr in [
                    {"Accept": "text/css"},
                    {"Accept": "*/*"},
                    {"Accept": "text/html"},
                ]:
                    try:
                        r2 = self.http.get(
                            self.profile.api_url,
                            params={self.profile.api_offset_param: 0},
                            headers=hdr, cookies=self.cookies,
                            timeout=10,
                        )
                        if r2.status_code == 200:
                            r2.json()
                            self.profile.ddg_bypass = hdr
                            log.info(f"  🛡 DDG bypass: {hdr}")
                            return
                    except Exception:
                        continue
        except Exception:
            pass

    def _discover_identity(self, page, urls):
        for url in urls:
            m = re.search(r'/(\w+)/user/([^/?&#]+)', url)
            if m:
                svc = m.group(1).lower()
                if svc not in ("api", "v1", "static", "assets"):
                    self.profile.service = svc
                    self.profile.user_id = m.group(2)
                    return
        try:
            for sel in [
                ".user-header__username", ".user-header__name",
                '[class*="user"] [class*="name"]', "h1", "h2",
            ]:
                el = page.query_selector(sel)
                if el:
                    text = el.inner_text().strip()
                    if text and len(text) < 100:
                        if not self.profile.user_id:
                            self.profile.user_id = sanitize(text)
                        break
        except Exception:
            pass
        if not self.profile.user_id:
            parts = urlparse(page.url).path.strip("/").split("/")
            if parts:
                self.profile.user_id = parts[-1]

    def _discover_pagination(self, page):
        try:
            hrefs = page.eval_on_selector_all(
                'a[href*="?o="], a[href*="&o="]',
                'els => els.map(e => e.href)'
            )
            offsets = set()
            for h in hrefs:
                m = re.search(r'[?&]o=(\d+)', h)
                if m:
                    offsets.add(int(m.group(1)))
            if offsets:
                self.profile.has_pagination = True
                self.profile.pagination_offsets = sorted(offsets)
                log.info(f"  📄 Pagination: offsets {self.profile.pagination_offsets}")
                offs = self.profile.pagination_offsets
                if len(offs) >= 2:
                    diffs = [offs[i+1]-offs[i] for i in range(len(offs)-1)]
                    common = max(set(diffs), key=diffs.count)
                    if common > 0:
                        self.profile.api_page_size = common
        except Exception:
            pass

        if not self.profile.has_pagination:
            try:
                hrefs = page.eval_on_selector_all(
                    'a[href]', 'els => els.map(e => e.href)'
                )
                for h in hrefs:
                    for param in ("offset", "page", "skip", "p"):
                        if re.search(rf'[?&]{param}=\d+', h):
                            self.profile.has_pagination = True
                            self.profile.api_offset_param = param
                            return
            except Exception:
                pass

    def _save_debug(self, page, html, urls):
        d = os.path.join(self.output, "debug")
        os.makedirs(d, exist_ok=True)
        try:
            page.screenshot(path=os.path.join(d, "page1.png"), full_page=True)
        except Exception:
            pass
        with open(os.path.join(d, "rendered.html"), "w", encoding="utf-8") as f:
            f.write(html)
        with open(os.path.join(d, "network_log.txt"), "w") as f:
            f.write("\n".join(urls))
        with open(os.path.join(d, "profile.json"), "w") as f:
            json.dump({
                "base_url": self.profile.base_url,
                "target_url": self.profile.target_url,
                "site_name": self.profile.site_name,
                "is_compatible": self.profile.is_compatible,
                "confidence": self.profile.confidence,
                "traits": self.profile.traits,
                "service": self.profile.service,
                "user_id": self.profile.user_id,
                "api_base_path": self.profile.api_base_path,
                "api_offset_param": self.profile.api_offset_param,
                "api_page_size": self.profile.api_page_size,
                "has_pagination": self.profile.has_pagination,
                "data_hosts": list(self.profile.data_hosts),
                "thumb_hosts": list(self.profile.thumb_hosts),
            }, f, indent=2)
        log.info(f"  📸 Debug saved to {d}/")

    # ── Phase 2 ──────────────────────────────────────────────────────────

    def phase2_paginate(self):
        if self.profile.api_base_path:
            self._paginate_api()
        elif self.profile.has_pagination:
            self._paginate_browser()
        else:
            log.info("\n📄 No pagination — single page")

    def _paginate_api(self):
        log.info(f"\n📡 Phase 2: API pagination ({self.profile.api_base_path})")
        already = len(self.api_posts)
        ps = self.profile.api_page_size
        offset = ps if already > 0 else 0
        page_num = 1 if already > 0 else 0
        empty = 0
        headers = {"Referer": self.profile.target_url}
        if self.profile.ddg_bypass:
            headers.update(self.profile.ddg_bypass)

        while True:
            page_num += 1
            if self.max_pages > 0 and page_num > self.max_pages:
                log.info(f"  Max pages ({self.max_pages})")
                break
            log.info(f"  Page {page_num} (offset={offset})...")
            try:
                r = self.http.get(
                    self.profile.base_url + self.profile.api_base_path,
                    params={self.profile.api_offset_param: offset},
                    cookies=self.cookies, headers=headers, timeout=30,
                )
                if r.status_code == 403:
                    log.warning("  403 — switching to browser")
                    self._paginate_browser(start_offset=offset)
                    return
                if r.status_code != 200:
                    empty += 1
                    if empty >= 3: break
                    offset += ps; time.sleep(self.delay); continue
                try:
                    posts = r.json()
                except Exception:
                    empty += 1
                    if empty >= 3: break
                    offset += ps; time.sleep(self.delay); continue

                if not isinstance(posts, list) or len(posts) == 0:
                    empty += 1
                    if empty >= 2:
                        log.info("  No more posts"); break
                    offset += ps; time.sleep(self.delay); continue

                empty = 0
                self.api_posts.extend(posts)
                log.info(f"    +{len(posts)} (total: {len(self.api_posts)})")
                if len(posts) < ps:
                    log.info("  Last page"); break
                offset += ps; time.sleep(self.delay)
            except Exception as e:
                log.error(f"  Error: {e}")
                empty += 1
                if empty >= 3: break
                offset += ps; time.sleep(self.delay)

    def _paginate_browser(self, start_offset=0):
        log.info(f"\n🌐 Phase 2b: Browser pagination (offset={start_offset})")
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=self.headless)
            ctx = browser.new_context(user_agent=UA)
            if self.cookies:
                domain = urlparse(self.profile.base_url).netloc
                ctx.add_cookies([
                    {"name": k, "value": v, "domain": domain, "path": "/"}
                    for k, v in self.cookies.items()
                ])
            page = ctx.new_page()

            def on_response(resp):
                if resp.status == 200:
                    try:
                        data = resp.json()
                        ok, _ = fingerprint_api_response(data)
                        if ok:
                            self.api_posts.extend(data)
                            log.info(f"    📦 +{len(data)} posts")
                    except Exception:
                        pass
                # Track data hosts
                if resp.status == 200 and "/data/" in resp.url:
                    self.profile.data_hosts.add(urlparse(resp.url).netloc)

            page.on("response", on_response)

            ps = self.profile.api_page_size
            offset = start_offset
            pn = 0; empty = 0
            param = self.profile.api_offset_param

            while True:
                pn += 1
                if self.max_pages > 0 and pn > self.max_pages: break
                url = f"{self.profile.base_url}{self.profile.target_path}?{param}={offset}"
                log.info(f"  📄 Loading {url}")
                before = len(self.api_posts)
                try:
                    page.goto(url, wait_until="networkidle", timeout=45000)
                    time.sleep(4)
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    time.sleep(2)
                except PWTimeout:
                    log.warning("  ⚠ Timeout")
                dom = self._scrape_dom_full_res(page)
                self.raw_media_urls.extend(dom)
                gained = len(self.api_posts) - before
                if gained == 0 and len(dom) == 0:
                    empty += 1
                    if empty >= 2:
                        log.info("  No more content"); break
                else:
                    empty = 0
                    log.info(f"    +{gained} API, +{len(dom)} DOM")
                offset += ps; time.sleep(self.delay)
            browser.close()

    # ── Phase 3 ──────────────────────────────────────────────────────────

    def phase3_collect(self):
        """Build final full-resolution media URL list."""
        log.info(f"\n🔍 Phase 3: Collecting full-resolution media...")

        # Dedup posts
        seen_ids = set()
        unique = []
        for post in self.api_posts:
            pid = post.get("id")
            if pid is not None:
                if pid in seen_ids: continue
                seen_ids.add(pid)
            unique.append(post)
        self.api_posts = unique
        log.info(f"  Unique posts: {len(self.api_posts)}")

        # ── Extract from API post data (always full-res) ──
        file_keys = ("file", "post_file", "content_file")
        path_keys = ("path", "url", "src", "name", "file_url")
        list_keys = ("attachments", "files", "media", "post_attachments")

        for post in self.api_posts:
            for fk in file_keys:
                fobj = post.get(fk)
                if isinstance(fobj, dict):
                    for pk in path_keys:
                        val = fobj.get(pk, "")
                        if val and (ext_of(val) in ALL_EXT or "/data/" in val):
                            self.raw_media_urls.append(self.make_absolute(val))
                elif isinstance(fobj, str) and fobj:
                    if ext_of(fobj) in ALL_EXT or "/data/" in fobj:
                        self.raw_media_urls.append(self.make_absolute(fobj))

            for lk in list_keys:
                items = post.get(lk, [])
                if not isinstance(items, list): continue
                for item in items:
                    if isinstance(item, dict):
                        for pk in path_keys:
                            val = item.get(pk, "")
                            if val and (ext_of(val) in ALL_EXT or "/data/" in val):
                                self.raw_media_urls.append(self.make_absolute(val))
                    elif isinstance(item, str) and item:
                        if ext_of(item) in ALL_EXT or "/data/" in item:
                            self.raw_media_urls.append(self.make_absolute(item))

            for ck in ("content", "body", "text", "description"):
                content = post.get(ck, "")
                if isinstance(content, str) and content:
                    for iu in re.findall(
                        r'(?:src|href)\s*=\s*["\']([^"\']+)', content
                    ):
                        if ext_of(iu) in ALL_EXT:
                            self.raw_media_urls.append(self.make_absolute(iu))

        # ── Resolve thumbnails to full-res ──
        log.info("  🔄 Resolving thumbnails to full resolution...")

        resolved = []
        thumb_count = 0
        fullres_count = 0

        seen = set()
        for url in self.raw_media_urls:
            url = self.make_absolute(url).split("#")[0].rstrip("/")
            if url in seen or ext_of(url) not in ALL_EXT:
                continue
            seen.add(url)

            # Check if we already have a known resolution mapping
            if url in self.resolution_map:
                full = self.resolution_map[url]
                if full not in seen:
                    seen.add(full)
                    resolved.append(full)
                    thumb_count += 1
                continue

            if is_thumbnail_url(url):
                # Generate full-res candidates
                candidates = resolve_full_res(url, self.profile.base_url)
                # Put best candidates first (non-thumbnail ones)
                best = [c for c in candidates if not is_thumbnail_url(c)]
                rest = [c for c in candidates if is_thumbnail_url(c)]

                if best:
                    chosen = best[0]
                    thumb_count += 1
                else:
                    chosen = url  # Couldn't find a better version

                if chosen not in seen:
                    seen.add(chosen)
                    resolved.append(chosen)

                    # Store all candidates for retry if primary fails
                    if len(candidates) > 1:
                        self.resolution_map[chosen] = candidates
            else:
                fullres_count += 1
                resolved.append(url)

        self.media_urls = resolved

        log.info(f"  Thumbnails resolved: {thumb_count}")
        log.info(f"  Already full-res:    {fullres_count}")

        imgs = [u for u in self.media_urls if classify(u) == "images"]
        vids = [u for u in self.media_urls if classify(u) == "videos"]
        log.info(f"  Total unique: {len(self.media_urls)}")
        log.info(f"    🖼  Images: {len(imgs)}")
        log.info(f"    🎬 Videos: {len(vids)}")

    # ── Phase 4 ──────────────────────────────────────────────────────────

    def phase4_download(self, skip_images=False, skip_videos=False,
                        dry_run=False):
        imgs_dir = os.path.join(self.output, "images")
        vids_dir = os.path.join(self.output, "videos")
        os.makedirs(imgs_dir, exist_ok=True)
        os.makedirs(vids_dir, exist_ok=True)

        url_file = os.path.join(self.output, "media_urls.txt")
        with open(url_file, "w") as f:
            f.write("\n".join(self.media_urls))
        posts_file = os.path.join(self.output, "posts.json")
        with open(posts_file, "w") as f:
            json.dump(self.api_posts, f, indent=2, default=str)

        log.info(f"  📝 URL list:  {url_file}")
        log.info(f"  📝 Posts:     {posts_file}")

        if dry_run:
            log.info("\n🔍 DRY RUN:")
            for u in self.media_urls:
                t = classify(u) or "?"
                thumb = " 🔍THUMB" if is_thumbnail_url(u) else ""
                skip_f = ""
                if (t == "images" and skip_images) or \
                   (t == "videos" and skip_videos):
                    skip_f = " ⏭SKIP"
                print(f"  [{t:6s}] {u}{thumb}{skip_f}")
            return

        if not self.media_urls:
            log.warning("⚠ No media found!")
            log.info("  Check debug/page1.png and debug/rendered.html")
            return

        # ── First pass ──
        ok, skipped, failed_urls = self._download_pass(
            self.media_urls, imgs_dir, vids_dir,
            skip_images, skip_videos, pass_label="Download"
        )

        # ── Retry sweeps ──
        retry_num = 0
        while failed_urls and retry_num < self.max_retries:
            retry_num += 1
            log.info(f"\n🔄 RETRY SWEEP {retry_num}/{self.max_retries} "
                     f"— {len(failed_urls)} failed URLs")
            time.sleep(self.delay * 2)  # Extra pause before retries

            # For each failed URL, try alternate candidates
            retry_list = []
            for furl in failed_urls:
                alternates = self._get_alternates(furl)
                if alternates:
                    retry_list.append((furl, alternates))
                else:
                    retry_list.append((furl, [furl]))

            retry_ok = 0
            still_failed = []

            for i, (original_url, candidates) in enumerate(retry_list, 1):
                mtype = classify(original_url)
                if mtype == "images" and skip_images: continue
                if mtype == "videos" and skip_videos: continue
                dest = imgs_dir if mtype == "images" else vids_dir

                log.info(f"\n  [Retry {i}/{len(retry_list)}] ({mtype}) "
                         f"{len(candidates)} candidate(s)")

                success = False
                for ci, candidate in enumerate(candidates):
                    log.info(f"    Try {ci+1}/{len(candidates)}: "
                             f"{os.path.basename(urlparse(candidate).path)}")
                    if self._download_single(candidate, dest):
                        retry_ok += 1
                        success = True
                        # Remember which host worked
                        self.verified_hosts.add(urlparse(candidate).netloc)
                        break

                if not success:
                    still_failed.append(original_url)

            ok += retry_ok
            failed_urls = still_failed

            log.info(f"\n  Retry sweep {retry_num}: "
                     f"✅ {retry_ok} recovered, "
                     f"❌ {len(still_failed)} still failing")

            if not still_failed:
                log.info("  🎉 All files recovered!")
                break

        # ── Final summary ──
        total_failed = len(failed_urls)
        label = self.profile.user_id or slug_from_url(self.input_url)
        svc = self.profile.service or "unknown"
        site = self.profile.site_name or self.profile.base_url

        log.info(f"\n{'='*55}")
        log.info(f"📊 DONE — {label} ({svc}) @ {site}")
        log.info(f"  ✅ Downloaded: {ok}")
        log.info(f"  ❌ Failed:     {total_failed}")
        log.info(f"  ⏭  Skipped:    {skipped}")
        log.info(f"  📂 Output:     {self.output}")

        if total_failed > 0:
            failed_file = os.path.join(self.output, "failed_urls.txt")
            with open(failed_file, "w") as f:
                f.write("\n".join(failed_urls))
            log.info(f"  📝 Failed:     {failed_file}")

        log.info(f"{'='*55}")

    def _download_pass(self, urls, imgs_dir, vids_dir,
                       skip_images, skip_videos, pass_label="Download"):
        """Run a download pass. Returns (ok_count, skip_count, failed_list)."""
        ok = 0
        skipped = 0
        failed = []
        total = len(urls)

        for i, url in enumerate(urls, 1):
            mtype = classify(url)
            if mtype == "images" and skip_images:
                skipped += 1; continue
            if mtype == "videos" and skip_videos:
                skipped += 1; continue

            dest = imgs_dir if mtype == "images" else vids_dir
            log.info(f"\n[{pass_label} {i}/{total}] ({mtype})")

            if self._download_single(url, dest):
                ok += 1
            else:
                failed.append(url)

        log.info(f"\n  {pass_label} pass: ✅ {ok}, ❌ {len(failed)}, ⏭ {skipped}")
        return ok, skipped, failed

    def _get_alternates(self, url):
        """
        Get alternate URLs to try for a failed download.
        Uses resolution map, path transforms, and host variants.
        """
        candidates = []
        seen = {url}

        # Check resolution map
        if url in self.resolution_map:
            mapped = self.resolution_map[url]
            if isinstance(mapped, list):
                for m in mapped:
                    if m not in seen:
                        seen.add(m)
                        candidates.append(m)
            elif isinstance(mapped, str) and mapped not in seen:
                seen.add(mapped)
                candidates.append(mapped)

        # Generate full-res candidates from URL transforms
        generated = resolve_full_res(url, self.profile.base_url)
        for g in generated:
            if g not in seen:
                seen.add(g)
                candidates.append(g)

        # Try with verified hosts (hosts we know work)
        parsed = urlparse(url)
        data_path = parsed.path
        for pat, rep in THUMBNAIL_PATTERNS:
            if re.search(pat, data_path):
                data_path = re.sub(pat, rep, data_path, count=1)

        for host in self.verified_hosts:
            if host != parsed.netloc:
                new_url = f"{parsed.scheme or 'https'}://{host}{data_path}"
                if parsed.query:
                    new_url += f"?{parsed.query}"
                if new_url not in seen:
                    seen.add(new_url)
                    candidates.append(new_url)

        # Try discovered data hosts
        for host in self.profile.data_hosts:
            if host != parsed.netloc:
                new_url = f"{parsed.scheme or 'https'}://{host}{data_path}"
                if parsed.query:
                    new_url += f"?{parsed.query}"
                if new_url not in seen:
                    seen.add(new_url)
                    candidates.append(new_url)

        # Sort: prefer verified hosts first, then non-thumbnail URLs
        def sort_key(u):
            h = urlparse(u).netloc
            score = 0
            if h in self.verified_hosts:
                score -= 100
            if not is_thumbnail_url(u):
                score -= 50
            if "/data/" in u:
                score -= 25
            return score

        candidates.sort(key=sort_key)

        # Always include the original as last resort
        if url not in seen:
            candidates.append(url)

        return candidates

    def _download_single(self, url, dest_dir):
        """Download a single file. Returns True on success."""
        fpath = None
        try:
            fname = sanitize(os.path.basename(urlparse(url).path))
            if not fname:
                h = hashlib.md5(url.encode()).hexdigest()[:12]
                fname = f"media_{h}{ext_of(url) or '.bin'}"

            fpath = os.path.join(dest_dir, fname)

            if os.path.exists(fpath) and os.path.getsize(fpath) > 0:
                log.info(f"  ⏭ Exists: {fname}")
                return True

            # Handle collision
            if os.path.exists(fpath):
                base, ext = os.path.splitext(fname)
                c = 1
                while os.path.exists(fpath):
                    fpath = os.path.join(dest_dir, f"{base}_{c}{ext}")
                    c += 1

            log.info(f"  ⬇ {fname}")
            if is_thumbnail_url(url):
                log.info(f"    ⚠ (thumbnail URL — may be low-res)")

            r = self.http.get(
                url, stream=True, timeout=180,
                cookies=self.cookies,
                headers={"Referer": self.profile.base_url},
            )
            r.raise_for_status()

            # Check if response is actually an image/video
            ct = r.headers.get("content-type", "").lower()
            if "text/html" in ct or "application/json" in ct:
                log.warning(f"  ⚠ Got {ct} instead of media — skipping")
                return False

            total = int(r.headers.get("content-length", 0))
            got = 0

            with open(fpath, "wb") as f:
                for chunk in r.iter_content(65536):
                    f.write(chunk)
                    got += len(chunk)
                    if total > 5_000_000:
                        pct = got / total * 100 if total else 0
                        print(
                            f"\r    {got/1048576:.1f}/{total/1048576:.1f} MB "
                            f"({pct:.0f}%)",
                            end="", flush=True,
                        )
                if total > 5_000_000:
                    print()

            size = os.path.getsize(fpath)

            # Validate: file shouldn't be suspiciously tiny for an image
            if size < 1000 and classify(url) == "images":
                log.warning(
                    f"  ⚠ Suspiciously small ({size} bytes) — may be error page"
                )
                # Read and check if it's actually HTML
                with open(fpath, "rb") as f:
                    head = f.read(200)
                if b"<html" in head.lower() or b"<!doctype" in head.lower():
                    log.warning(f"  ❌ File is HTML, not media — removing")
                    os.remove(fpath)
                    return False

            log.info(f"  ✅ {fname} ({size/1048576:.2f} MB)")

            # Track working host
            self.verified_hosts.add(urlparse(url).netloc)

            time.sleep(self.delay)
            return True

        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else "?"
            log.error(f"  ❌ HTTP {status}: {os.path.basename(urlparse(url).path)}")
            if fpath and os.path.exists(fpath):
                os.remove(fpath)
            return False

        except Exception as e:
            log.error(f"  ❌ {e}")
            if fpath and os.path.exists(fpath):
                os.remove(fpath)
            return False


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(
        description="Universal structure-based media scraper",
        usage="%(prog)s URL [OPTIONS]",
        epilog="""
Examples:
  %(prog)s https://coomer.st/onlyfans/user/laniafawn
  %(prog)s https://kemono.su/patreon/user/12345
  %(prog)s https://any-fork.com/page --force --dry-run

Downloads full-resolution media with automatic retry sweeps.
Auto-detects Kemono/Coomer-style sites by page structure.
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("url", help="URL to scrape")
    p.add_argument("-o", "--output", default=None,
                   help="Output directory (default: auto)")
    p.add_argument("-d", "--delay", type=float, default=DEFAULT_DELAY,
                   help="Delay between downloads (default: 1.5s)")
    p.add_argument("-p", "--pages", type=int, default=DEFAULT_MAX_PAGES,
                   help="Max pages, 0=all (default: 0)")
    p.add_argument("-r", "--retries", type=int, default=DEFAULT_RETRIES,
                   help="Retry sweeps for failed downloads (default: 3)")
    p.add_argument("--skip-images", action="store_true")
    p.add_argument("--skip-videos", action="store_true")
    p.add_argument("--dry-run", action="store_true",
                   help="List files without downloading")
    p.add_argument("--show-browser", action="store_true",
                   help="Show browser window")
    p.add_argument("--force", action="store_true",
                   help="Scrape even if site looks incompatible")
    p.add_argument("--min-confidence", type=int, default=20,
                   help="Min fingerprint score 0-100 (default: 20)")
    args = p.parse_args()

    if args.output is None:
        args.output = os.path.join(DEFAULT_OUTPUT, slug_from_url(args.url))
    os.makedirs(args.output, exist_ok=True)

    log.info("=" * 55)
    log.info(" Universal Structure-Based Media Scraper")
    log.info(f"  Input:   {args.url}")
    log.info(f"  Output:  {args.output}")
    log.info(f"  Delay:   {args.delay}s")
    log.info(f"  Pages:   {'all' if args.pages == 0 else args.pages}")
    log.info(f"  Retries: {args.retries}")
    if args.force:
        log.info(f"  Mode:    FORCE")
    log.info("=" * 55)

    s = UniversalScraper(
        url=args.url,
        output=args.output,
        delay=args.delay,
        max_pages=args.pages,
        headless=not args.show_browser,
        force=args.force,
        min_confidence=args.min_confidence,
        max_retries=args.retries,
    )

    s.phase0_resolve()
    s.phase1_discover()
    s.phase2_paginate()
    s.phase3_collect()
    s.phase4_download(
        skip_images=args.skip_images,
        skip_videos=args.skip_videos,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
