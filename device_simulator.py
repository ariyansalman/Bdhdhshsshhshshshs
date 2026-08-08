"""
Android Pixel 10 Pro device simulator.

Each session gets unique identifiers (IMEI, Android ID, device fingerprint,
Chrome version patch) while the hardware identity remains "Pixel 10 Pro".

Key implementation detail:
  Chrome 110+ uses UA Reduction — the User-Agent string always shows
  "Android 10; K" regardless of real device. The actual device model
  is communicated via Sec-CH-UA-Model client hint (set via CDP).
"""

import random
import string
import uuid
from dataclasses import dataclass, field

import config


# ── Real Google Pixel TAC prefixes (Type Allocation Code) ────────────────────
# These are genuine GSMA-registered TAC codes for Google Pixel devices.
PIXEL_TAC_PREFIXES = [
    "35272012",  # Pixel 9 Pro
    "35383711",  # Pixel 8 Pro
    "35174912",  # Pixel 7 Pro
    "35632208",  # Pixel 6a
    "35383714",  # Pixel 8
    "35272014",  # Pixel 9
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _luhn_checksum(number: str) -> int:
    digits = [int(d) for d in number]
    odd_digits = digits[-1::-2]
    even_digits = digits[-2::-2]
    total = sum(odd_digits)
    for d in even_digits:
        total += sum(divmod(d * 2, 10))
    return total % 10


def _generate_imei() -> str:
    """Generate a Luhn-valid IMEI using a real Google Pixel TAC prefix."""
    tac = random.choice(PIXEL_TAC_PREFIXES)   # 8-digit real TAC
    serial = "".join(random.choices(string.digits, k=6))
    partial = tac + serial                      # 14 digits
    check_digit = (10 - _luhn_checksum(partial + "0")) % 10
    return partial + str(check_digit)


def _generate_android_id() -> str:
    return "".join(random.choices("0123456789abcdef", k=16))


def _generate_device_fingerprint(model: str, build_id: str, android: str) -> str:
    slug = model.lower().replace(" ", "_")
    return f"google/{slug}/{slug}:{android}/{build_id}/eng.user.release-keys"


def _random_chrome_patch() -> str:
    """Return a realistic Chrome 149 version string with slight random patch."""
    major = config.CHROME_MAJOR_VERSION       # 149
    build = random.randint(7820, 7840)
    patch = random.randint(180, 220)
    return f"{major}.0.{build}.{patch}"


# ── Device profile dataclass ──────────────────────────────────────────────────

@dataclass
class DeviceProfile:
    imei:               str
    android_id:         str
    device_fingerprint: str
    user_agent:         str
    chrome_version:     str
    session_id:         str = field(default_factory=lambda: str(uuid.uuid4()))

    # Fixed Pixel 10 Pro hardware identity
    model:           str   = config.DEVICE_MODEL
    brand:           str   = config.DEVICE_BRAND
    manufacturer:    str   = config.DEVICE_MANUFACTURER
    android_version: str   = config.ANDROID_VERSION
    android_sdk:     str   = config.ANDROID_SDK
    build_id:        str   = config.BUILD_ID

    # Hardware capabilities (spoofed via CDP + JS injection)
    device_memory:          int   = 16        # 16 GB RAM (spoofed, browser API has no cap in JS)
    hardware_concurrency:   int   = 8         # Tensor G5 reported cores
    max_touch_points:       int   = 5         # realistic multitouch
    screen_width:           int   = config.SCREEN_CSS_WIDTH   # 412
    screen_height:          int   = config.SCREEN_CSS_HEIGHT  # 915
    pixel_ratio:            float = config.SCREEN_PIXEL_RATIO # 3.5
    gpu_vendor:             str   = config.DEVICE_GPU_VENDOR
    gpu_renderer:           str   = config.DEVICE_GPU_RENDERER

    def client_hints_metadata(self) -> dict:
        """
        Full userAgentMetadata dict for CDP Emulation.setUserAgentOverride.
        This is what Google reads via the Sec-CH-UA-* headers to identify
        the real device behind the reduced User-Agent string.
        """
        major = str(config.CHROME_MAJOR_VERSION)
        return {
            "brands": [
                {"brand": "Google Chrome",  "version": major},
                {"brand": "Chromium",       "version": major},
                {"brand": "Not:A-Brand",    "version": "24"},
            ],
            "fullVersionList": [
                {"brand": "Google Chrome",  "version": self.chrome_version},
                {"brand": "Chromium",       "version": self.chrome_version},
                {"brand": "Not:A-Brand",    "version": "24.0.0.0"},
            ],
            "platform":        "Android",
            "platformVersion": f"{config.ANDROID_VERSION}.0.0",
            "architecture":    "arm",
            "model":           self.model,      # "Pixel 10 Pro" — the real device hint
            "mobile":          True,
            "bitness":         "64",
            "wow64":           False,
        }

    def navigator_js(self) -> str:
        """
        JavaScript injected on every new document via CDP
        Page.addScriptToEvaluateOnNewDocument.

        Overrides all detectable browser fingerprint signals to match a real
        physical Pixel 10 Pro running Chrome 149.
        """
        gpu_vendor   = self.gpu_vendor.replace("'", "\\'")
        gpu_renderer = self.gpu_renderer.replace("'", "\\'")
        return f"""
(function () {{
  'use strict';

  // ── 1. Remove automation trace ──────────────────────────────────────────
  try {{
    const desc = Object.getOwnPropertyDescriptor(navigator, 'webdriver');
    if (desc) {{
      Object.defineProperty(navigator, 'webdriver', {{
        get: () => undefined,
        configurable: true
      }});
    }}
  }} catch (e) {{}}

  // ── 2. Platform ─────────────────────────────────────────────────────────
  try {{
    Object.defineProperty(navigator, 'platform', {{
      get: () => 'Linux armv8l',
      configurable: true
    }});
  }} catch (e) {{}}

  // ── 3. Hardware ─────────────────────────────────────────────────────────
  try {{
    Object.defineProperty(navigator, 'deviceMemory', {{
      get: () => {self.device_memory},
      configurable: true
    }});
  }} catch (e) {{}}

  try {{
    Object.defineProperty(navigator, 'hardwareConcurrency', {{
      get: () => {self.hardware_concurrency},
      configurable: true
    }});
  }} catch (e) {{}}

  try {{
    Object.defineProperty(navigator, 'maxTouchPoints', {{
      get: () => {self.max_touch_points},
      configurable: true
    }});
  }} catch (e) {{}}

  // ── 4. Touch support flags ──────────────────────────────────────────────
  try {{
    window.ontouchstart = null;
    window.ontouchmove  = null;
    window.ontouchend   = null;
    window.TouchEvent   = window.TouchEvent || function () {{}};
  }} catch (e) {{}}

  // ── 5. Vendor ───────────────────────────────────────────────────────────
  try {{
    Object.defineProperty(navigator, 'vendor', {{
      get: () => 'Google Inc.',
      configurable: true
    }});
  }} catch (e) {{}}

  // ── 6. Language ─────────────────────────────────────────────────────────
  try {{
    Object.defineProperty(navigator, 'language', {{
      get: () => 'en-US',
      configurable: true
    }});
    Object.defineProperty(navigator, 'languages', {{
      get: () => Object.freeze(['en-US', 'en']),
      configurable: true
    }});
  }} catch (e) {{}}

  // ── 7. Screen geometry ──────────────────────────────────────────────────
  try {{
    Object.defineProperty(screen, 'width',       {{ get: () => {self.screen_width},  configurable: true }});
    Object.defineProperty(screen, 'height',      {{ get: () => {self.screen_height}, configurable: true }});
    Object.defineProperty(screen, 'availWidth',  {{ get: () => {self.screen_width},  configurable: true }});
    Object.defineProperty(screen, 'availHeight', {{ get: () => {self.screen_height}, configurable: true }});
    Object.defineProperty(screen, 'colorDepth',  {{ get: () => 24, configurable: true }});
    Object.defineProperty(screen, 'pixelDepth',  {{ get: () => 24, configurable: true }});
    Object.defineProperty(window, 'devicePixelRatio', {{
      get: () => {self.pixel_ratio},
      configurable: true
    }});
    Object.defineProperty(window, 'innerWidth',  {{ get: () => {self.screen_width},  configurable: true }});
    Object.defineProperty(window, 'innerHeight', {{ get: () => {self.screen_height}, configurable: true }});
  }} catch (e) {{}}

  // ── 8. WebGL GPU fingerprint ────────────────────────────────────────────
  const patchWebGL = (Ctx) => {{
    if (!Ctx) return;
    const orig = Ctx.prototype.getParameter;
    Ctx.prototype.getParameter = function (param) {{
      if (param === 0x9245) return '{gpu_vendor}';   // UNMASKED_VENDOR_WEBGL
      if (param === 0x9246) return '{gpu_renderer}'; // UNMASKED_RENDERER_WEBGL
      if (param === 0x1F00) return '{gpu_vendor}';   // VENDOR
      if (param === 0x1F01) return '{gpu_renderer}'; // RENDERER
      return orig.call(this, param);
    }};
  }};
  try {{ patchWebGL(WebGLRenderingContext);  }} catch (e) {{}}
  try {{ patchWebGL(WebGL2RenderingContext); }} catch (e) {{}}

  // ── 9. Battery API ──────────────────────────────────────────────────────
  try {{
    const level = 0.82 + Math.random() * 0.16;
    const fakeBattery = {{
      charging: true,
      chargingTime: 0,
      dischargingTime: Infinity,
      level: level,
      addEventListener:    () => {{}},
      removeEventListener: () => {{}},
      dispatchEvent:       () => false,
    }};
    navigator.getBattery = () => Promise.resolve(fakeBattery);
  }} catch (e) {{}}

  // ── 10. Network info ────────────────────────────────────────────────────
  try {{
    const conn = {{
      effectiveType: '4g',
      downlink:      12.5,
      rtt:           45,
      saveData:      false,
      type:          'wifi',
      addEventListener:    () => {{}},
      removeEventListener: () => {{}},
    }};
    Object.defineProperty(navigator, 'connection', {{
      get: () => conn,
      configurable: true
    }});
  }} catch (e) {{}}

  // ── 11. Plugins / MimeTypes (Android Chrome has none) ──────────────────
  try {{
    Object.defineProperty(navigator, 'plugins', {{
      get: () => Object.freeze([]),
      configurable: true
    }});
    Object.defineProperty(navigator, 'mimeTypes', {{
      get: () => Object.freeze([]),
      configurable: true
    }});
  }} catch (e) {{}}

  // ── 12. Permissions (notifications always denied on Android) ───────────
  try {{
    const origQuery = navigator.permissions.query.bind(navigator.permissions);
    navigator.permissions.query = (params) => {{
      if (params && params.name === 'notifications') {{
        return Promise.resolve({{ state: 'denied', onchange: null }});
      }}
      return origQuery(params);
    }};
  }} catch (e) {{}}

  // ── 13. Chrome runtime object (present on real Chrome) ─────────────────
  try {{
    if (!window.chrome) {{
      window.chrome = {{
        runtime: {{
          connect:   () => {{}},
          sendMessage: () => {{}},
        }},
        loadTimes:  () => {{}},
        csi:        () => {{}},
      }};
    }}
  }} catch (e) {{}}

}})();
"""

    def as_headers(self) -> dict:
        return {
            "User-Agent":      self.user_agent,
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
        }

    def summary(self) -> str:
        return (
            f"📱 *Device Profile*\n"
            f"Model: {self.model}  |  Build: {self.build_id}\n"
            f"Android: {self.android_version}  |  Chrome: {self.chrome_version}\n"
            f"RAM: {self.device_memory}GB  |  CPU: {self.hardware_concurrency} cores\n"
            f"Screen: {self.screen_width}×{self.screen_height} @{self.pixel_ratio}×\n"
            f"GPU: {self.gpu_renderer}\n"
            f"IMEI: `{self.imei}`\n"
            f"Android ID: `{self.android_id}`\n"
            f"Session: `{self.session_id[:8]}…`"
        )


# ── Public factory ────────────────────────────────────────────────────────────

def create_device_profile() -> DeviceProfile:
    """
    Create a fresh Pixel 10 Pro device profile with unique per-session
    identifiers and a fully spoofed hardware fingerprint.
    """
    chrome_version = _random_chrome_patch()

    # Chrome UA Reduction: device model never appears in UA string
    template   = config.USER_AGENT_TEMPLATES[0]
    user_agent = template.format(chrome=chrome_version)

    fingerprint = _generate_device_fingerprint(
        config.DEVICE_MODEL,
        config.BUILD_ID,
        config.ANDROID_VERSION,
    )
    return DeviceProfile(
        imei=_generate_imei(),
        android_id=_generate_android_id(),
        device_fingerprint=fingerprint,
        user_agent=user_agent,
        chrome_version=chrome_version,
    )
