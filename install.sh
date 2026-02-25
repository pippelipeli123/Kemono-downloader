#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/scraper_env"

echo "============================================"
echo " Universal Media Scraper — Installer"
echo "============================================"
echo ""
echo " This only needs to be run ONCE."
echo " (Run again anytime to update dependencies)"
echo ""
echo " Location: $SCRIPT_DIR"
echo ""

# ══════════════════════════════════════════════════════════════════════════════
# SYSTEM PACKAGES
# ══════════════════════════════════════════════════════════════════════════════

echo "──────────────────────────────────────────"
echo " 1/6  System packages"
echo "──────────────────────────────────────────"

NEEDED=()
for pkg in python3 python3-pip python3-venv ffmpeg curl; do
    if ! dpkg -s "$pkg" &>/dev/null 2>&1; then
        NEEDED+=("$pkg")
    fi
done

if [ ${#NEEDED[@]} -gt 0 ]; then
    echo "Installing: ${NEEDED[*]}"
    sudo apt update
    sudo apt install -y "${NEEDED[@]}"
else
    echo "  ✅ All system packages present"
fi

# ══════════════════════════════════════════════════════════════════════════════
# BROWSER DEPENDENCIES (for Playwright/Chromium)
# ══════════════════════════════════════════════════════════════════════════════

echo ""
echo "──────────────────────────────────────────"
echo " 2/6  Browser dependencies"
echo "──────────────────────────────────────────"

BROWSER_DEPS=(
    libnss3 libatk-bridge2.0-0 libdrm2 libxkbcommon0
    libxcomposite1 libxdamage1 libxrandr2 libgbm1
    libpango-1.0-0
)

# Ubuntu 24.04+ renamed some packages with t64 suffix
BROWSER_DEPS_ALT=(
    libasound2t64 libatspi2.0-0t64 libcups2t64
)
BROWSER_DEPS_LEGACY=(
    libasound2 libatspi2.0-0 libcups2
)

MISSING=()
for pkg in "${BROWSER_DEPS[@]}"; do
    if ! dpkg -s "$pkg" &>/dev/null 2>&1; then
        MISSING+=("$pkg")
    fi
done

if [ ${#MISSING[@]} -gt 0 ]; then
    echo "Installing browser libs..."
    sudo apt install -y "${MISSING[@]}" 2>/dev/null || true
fi

# Try t64 packages first, fall back to legacy
sudo apt install -y "${BROWSER_DEPS_ALT[@]}" 2>/dev/null || \
sudo apt install -y "${BROWSER_DEPS_LEGACY[@]}" 2>/dev/null || true

echo "  ✅ Browser dependencies ready"

# ══════════════════════════════════════════════════════════════════════════════
# PYTHON VIRTUAL ENVIRONMENT
# ══════════════════════════════════════════════════════════════════════════════

echo ""
echo "──────────────────────────────────────────"
echo " 3/6  Python virtual environment"
echo "──────────────────────────────────────────"

if [ -d "$VENV_DIR" ]; then
    echo "  Existing venv found — upgrading packages..."
else
    echo "  Creating virtual environment..."
    python3 -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"

echo "  Upgrading pip..."
pip install --quiet --upgrade pip

echo "  Installing Python packages..."
pip install --quiet --upgrade \
    requests \
    playwright \
    yt-dlp

echo "  ✅ Python packages installed"
echo ""
echo "  Installed versions:"
echo "    Python:     $(python3 --version 2>&1 | awk '{print $2}')"
echo "    requests:   $(pip show requests 2>/dev/null | grep Version | awk '{print $2}')"
echo "    playwright: $(pip show playwright 2>/dev/null | grep Version | awk '{print $2}')"
echo "    yt-dlp:     $(yt-dlp --version 2>/dev/null || echo 'unknown')"

# ══════════════════════════════════════════════════════════════════════════════
# PLAYWRIGHT CHROMIUM BROWSER
# ══════════════════════════════════════════════════════════════════════════════

echo ""
echo "──────────────────────────────────────────"
echo " 4/6  Chromium browser (for Playwright)"
echo "──────────────────────────────────────────"

if python3 -c "
from playwright.sync_api import sync_playwright
p = sync_playwright().start()
b = p.chromium.launch()
b.close()
p.stop()
" 2>/dev/null; then
    echo "  ✅ Chromium already installed and working"
else
    echo "  Installing Chromium..."
    playwright install chromium
    playwright install-deps chromium 2>/dev/null || true
    echo "  ✅ Chromium installed"
fi

# ══════════════════════════════════════════════════════════════════════════════
# CZKAWKA (deduplication tool)
# ══════════════════════════════════════════════════════════════════════════════

echo ""
echo "──────────────────────────────────────────"
echo " 5/6  czkawka (deduplication tool)"
echo "──────────────────────────────────────────"

CZKAWKA_INSTALLED=false

# Check if already available
if command -v czkawka_cli &>/dev/null; then
    echo "  ✅ czkawka_cli found: $(command -v czkawka_cli)"
    echo "     Version: $(czkawka_cli --version 2>&1 || echo 'unknown')"
    CZKAWKA_INSTALLED=true
elif [ -f "$HOME/.cargo/bin/czkawka_cli" ]; then
    echo "  ✅ Found at ~/.cargo/bin/czkawka_cli"
    CZKAWKA_INSTALLED=true
elif [ -f "$HOME/.local/bin/czkawka_cli" ]; then
    echo "  ✅ Found at ~/.local/bin/czkawka_cli"
    CZKAWKA_INSTALLED=true
fi

if [ "$CZKAWKA_INSTALLED" = false ]; then
    echo "  Not found — attempting install..."

    # Method 1: apt
    if apt-cache show czkawka-cli &>/dev/null 2>&1; then
        echo "  Trying apt..."
        if sudo apt install -y czkawka-cli 2>/dev/null; then
            echo "  ✅ Installed via apt"
            CZKAWKA_INSTALLED=true
        fi
    fi

    # Method 2: Prebuilt binary from GitHub
    if [ "$CZKAWKA_INSTALLED" = false ]; then
        ARCH=$(uname -m)
        if [ "$ARCH" = "x86_64" ]; then
            echo "  Trying prebuilt binary..."

            LATEST=$(curl -s https://api.github.com/repos/qarmin/czkawka/releases/latest \
                     | grep -oP '"tag_name":\s*"\K[^"]+' 2>/dev/null || echo "")

            if [ -n "$LATEST" ]; then
                DL_URL="https://github.com/qarmin/czkawka/releases/download/${LATEST}/linux_czkawka_cli"
                TMP="/tmp/czkawka_cli_$$"

                echo "  Downloading ${LATEST}..."
                if curl -sL --fail -o "$TMP" "$DL_URL" 2>/dev/null && [ -s "$TMP" ]; then
                    chmod +x "$TMP"
                    if "$TMP" --version &>/dev/null; then
                        mkdir -p "$HOME/.local/bin"
                        mv "$TMP" "$HOME/.local/bin/czkawka_cli"
                        echo "  ✅ Installed to ~/.local/bin/czkawka_cli"
                        echo "     Version: $($HOME/.local/bin/czkawka_cli --version 2>&1)"
                        CZKAWKA_INSTALLED=true
                    else
                        rm -f "$TMP"
                        echo "  ⚠  Binary doesn't run on this system"
                    fi
                else
                    rm -f "$TMP" 2>/dev/null
                    echo "  ⚠  Download failed"
                fi
            else
                echo "  ⚠  Could not determine latest version"
            fi
        else
            echo "  ⚠  No prebuilt binary for $ARCH"
        fi
    fi

    # Method 3: Cargo (Rust)
    if [ "$CZKAWKA_INSTALLED" = false ]; then
        if command -v cargo &>/dev/null; then
            echo "  Building from source with cargo (this may take 2-5 minutes)..."
            if cargo install czkawka_cli 2>/dev/null; then
                echo "  ✅ Built and installed via cargo"
                CZKAWKA_INSTALLED=true
            else
                echo "  ⚠  Cargo build failed"
            fi
        fi
    fi

    # Give up
    if [ "$CZKAWKA_INSTALLED" = false ]; then
        echo ""
        echo "  ⚠  czkawka_cli could not be installed automatically."
        echo "     Dedup features (--dedup) will be unavailable."
        echo ""
        echo "     Manual install options:"
        echo "       1. cargo install czkawka_cli"
        echo "       2. Download from https://github.com/qarmin/czkawka/releases"
        echo "       3. Place binary in ~/.local/bin/czkawka_cli"
        echo ""
    fi
fi

deactivate

# ══════════════════════════════════════════════════════════════════════════════
# MAKE SCRIPTS EXECUTABLE
# ══════════════════════════════════════════════════════════════════════════════

echo ""
echo "──────────────────────────────────────────"
echo " 6/6  Finalizing"
echo "──────────────────────────────────────────"

chmod +x "$SCRIPT_DIR/run_scraper.sh" 2>/dev/null || true
chmod +x "$SCRIPT_DIR/scraper.py" 2>/dev/null || true
chmod +x "$SCRIPT_DIR/install.sh" 2>/dev/null || true

# ══════════════════════════════════════════════════════════════════════════════
# VERIFY EVERYTHING
# ══════════════════════════════════════════════════════════════════════════════

echo ""
echo "============================================"
echo " Installation Summary"
echo "============================================"
echo ""

ALL_GOOD=true

# Python
if [ -f "$VENV_DIR/bin/python3" ]; then
    echo "  ✅ Python venv"
else
    echo "  ❌ Python venv"; ALL_GOOD=false
fi

# Playwright
if "$VENV_DIR/bin/python3" -c "import playwright" 2>/dev/null; then
    echo "  ✅ Playwright"
else
    echo "  ❌ Playwright"; ALL_GOOD=false
fi

# yt-dlp
if [ -f "$VENV_DIR/bin/yt-dlp" ]; then
    echo "  ✅ yt-dlp"
else
    echo "  ❌ yt-dlp"; ALL_GOOD=false
fi

# ffmpeg
if command -v ffmpeg &>/dev/null; then
    echo "  ✅ ffmpeg"
else
    echo "  ⚠  ffmpeg (optional, needed for video merging)"
fi

# Chromium
if "$VENV_DIR/bin/python3" -c "
from playwright.sync_api import sync_playwright
p = sync_playwright().start()
b = p.chromium.launch()
b.close()
p.stop()
" 2>/dev/null; then
    echo "  ✅ Chromium browser"
else
    echo "  ❌ Chromium browser"; ALL_GOOD=false
fi

# czkawka
if command -v czkawka_cli &>/dev/null || \
   [ -f "$HOME/.cargo/bin/czkawka_cli" ] || \
   [ -f "$HOME/.local/bin/czkawka_cli" ]; then
    echo "  ✅ czkawka_cli (--dedup)"
else
    echo "  ⚠  czkawka_cli (optional, for --dedup)"
fi

echo ""

if [ "$ALL_GOOD" = true ]; then
    echo "  🎉 All good! Ready to use."
else
    echo "  ⚠  Some components failed. Check errors above."
fi

echo ""
echo "============================================"
echo " Quick Start"
echo "============================================"
echo ""
echo "  cd $SCRIPT_DIR"
echo ""
echo "  # Coomer/Kemono"
echo "  ./run_scraper.sh https://coomer.st/onlyfans/user/laniafawn"
echo ""
echo "  # YouTube"
echo "  ./run_scraper.sh https://www.youtube.com/watch?v=VIDEO_ID"
echo ""
echo "  # Preview first"
echo "  ./run_scraper.sh URL --dry-run"
echo ""
echo "  # With dedup"
echo "  ./run_scraper.sh URL --dedup"
echo ""
echo "  # Full help"
echo "  ./run_scraper.sh"
echo ""
