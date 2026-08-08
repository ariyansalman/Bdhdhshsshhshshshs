"""
Configuration and constants for the Pixel 10 Pro Google One Gemini Bot.
"""

import os

# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")

# ── Device specs – Google Pixel 10 Pro (Android 16) ──────────────────────────
DEVICE_MODEL        = "Pixel 10 Pro"
DEVICE_BRAND        = "google"
DEVICE_MANUFACTURER = "Google"
ANDROID_VERSION     = "16"
ANDROID_SDK         = "36"
BUILD_ID            = "CP1A.260405.005"       # Pixel 10 Pro build fingerprint

# Hardware profile (used for navigator injection)
DEVICE_RAM_GB           = 16                  # 16 GB RAM (spoofed as 16 via JS)
DEVICE_CPU_CORES        = 8                   # Tensor G5: 8 reported cores
DEVICE_MAX_TOUCH        = 5                   # 5-point multitouch (realistic)
DEVICE_GPU_VENDOR       = "Imagination Technologies"
DEVICE_GPU_RENDERER     = "PowerVR DXT-48-1536"  # Tensor G5 GPU

# Screen – Pixel 10 Pro: CSS viewport 412×915 @3.5× density (~495 PPI)
SCREEN_CSS_WIDTH    = 412
SCREEN_CSS_HEIGHT   = 915
SCREEN_PIXEL_RATIO  = 3.5

# ── Chrome 149 (latest stable on Android) ────────────────────────────────────
CHROME_VERSION       = "149.0.7827.200"
CHROME_MAJOR_VERSION = 149

# ── User-Agent – Chrome UA Reduction (Chrome 110+) ───────────────────────────
# Modern Chrome on Android NEVER reveals device model in the UA string.
# The real device identity is sent via Sec-CH-UA-Model client hint.
# Format: "Mozilla/5.0 (Linux; Android 10; K) ... Chrome/<version> Mobile Safari/537.36"
USER_AGENT_TEMPLATES = [
    (
        "Mozilla/5.0 (Linux; Android 10; K) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/{chrome} Mobile Safari/537.36"
    ),
]

# ── Google URLs ───────────────────────────────────────────────────────────────
GMAIL_LOGIN_URL       = "https://accounts.google.com/signin/v2/identifier"
GOOGLE_ONE_URL        = "https://one.google.com/"
GOOGLE_ONE_OFFERS_URL = "https://one.google.com/about/plans"

# ── Gemini offer detection keywords ──────────────────────────────────────────
GEMINI_OFFER_KEYWORDS = [
    "gemini pro",
    "gemini advanced",
    "12 month",
    "12-month",
    "free trial",
    "activate",
    "get started",
    "claim offer",
    "redeem",
]

# ── Selenium / WebDriver ──────────────────────────────────────────────────────
WEBDRIVER_TIMEOUT  = 30   # seconds – explicit wait
IMPLICIT_WAIT      = 10   # seconds
PAGE_LOAD_TIMEOUT  = 60   # seconds
HEADLESS           = True # always headless on Replit

# ── Session storage ───────────────────────────────────────────────────────────
SESSION_STORE: dict = {}

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_LEVEL  = "INFO"
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
