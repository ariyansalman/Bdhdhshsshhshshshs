"""
Configuration and constants for the Pixel 10 Pro Google One Gemini Bot.
"""

import os

# ── Telegram ──────────────────────────────────────────────────────────────────
# Support both names so existing deployments using BOT_TOKEN continue to work.
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("BOT_TOKEN", "")
BOT_TOKEN = TELEGRAM_BOT_TOKEN

# ── Device specs – Google Pixel 10 Pro (Android 16) ──────────────────────────
DEVICE_MODEL        = "Pixel 10 Pro"
DEVICE_BRAND        = "google"
DEVICE_MANUFACTURER = "Google"
ANDROID_VERSION     = "16"
ANDROID_SDK         = "36"
BUILD_ID            = "CP1A.260405.005"

# Hardware profile (used for navigator injection)
DEVICE_RAM_GB           = 16
DEVICE_CPU_CORES        = 8
DEVICE_MAX_TOUCH        = 5
DEVICE_GPU_VENDOR       = "Imagination Technologies"
DEVICE_GPU_RENDERER     = "PowerVR DXT-48-1536"

# Screen – Pixel 10 Pro: CSS viewport 412×915 @3.5× density
SCREEN_CSS_WIDTH    = 412
SCREEN_CSS_HEIGHT   = 915
SCREEN_PIXEL_RATIO  = 3.5

# ── Chrome 149 ────────────────────────────────────────────────────────────────
CHROME_VERSION       = "149.0.7827.200"
CHROME_MAJOR_VERSION = 149

# ── User-Agent ────────────────────────────────────────────────────────────────
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
# IMPORTANT: Do not use broad words such as "gemini pro", "activate" or
# "get started" here. The Google AI landing page contains those words but is
# NOT the Pixel offer URL. The offer page shown for eligible Pixel devices has
# a real "Start trial" CTA whose href is the activation/offer URL.
GEMINI_OFFER_KEYWORDS = [
    "start trial",
]

# ── Selenium / WebDriver ──────────────────────────────────────────────────────
WEBDRIVER_TIMEOUT  = 30
IMPLICIT_WAIT      = 10
PAGE_LOAD_TIMEOUT  = 60
HEADLESS           = True

# ── Session storage ───────────────────────────────────────────────────────────
SESSION_STORE: dict = {}

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_LEVEL  = "INFO"
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
