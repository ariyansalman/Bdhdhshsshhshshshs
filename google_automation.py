"""
Google One automation using Selenium.

Logs into a Gmail account, navigates to Google One, detects the
12-month free Gemini Pro offer, and returns the activation / payment link.

progress_callback(msg, screenshot_bytes=None) is called at every key step
so callers can relay updates to Telegram in real time.
"""

import io
import logging
import re
import shutil
import time
from typing import Callable, Optional
from urllib.parse import urlparse

import pyotp
from selenium import webdriver
from selenium.common.exceptions import (
    NoSuchElementException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

import config
from device_simulator import DeviceProfile

logger = logging.getLogger(__name__)

ProgressCB = Optional[Callable[[str, Optional[bytes]], None]]


# ── Screenshot helper ─────────────────────────────────────────────────────────

def _shot(driver: webdriver.Chrome) -> Optional[bytes]:
    """Return a PNG screenshot as bytes, or None on failure."""
    try:
        return driver.get_screenshot_as_png()
    except Exception:
        return None


def _report(cb: ProgressCB, msg: str, driver: Optional[webdriver.Chrome] = None) -> None:
    """Send a progress message (and optional screenshot) via the callback."""
    logger.info(msg)
    if cb:
        screenshot = _shot(driver) if driver else None
        try:
            cb(msg, screenshot)
        except Exception as e:
            logger.warning("progress_callback error: %s", e)


# ── Driver factory ────────────────────────────────────────────────────────────

def _build_driver(profile: DeviceProfile) -> webdriver.Chrome:
    """
    Return a headless Chrome WebDriver fully configured to impersonate a
    real Google Pixel 10 Pro running Android 16 / Chrome 149.

    Spoofing layers (applied in order):
      1. Chrome launch flags   – mobile emulation, reduced UA, anti-detection
      2. CDP commands          – authoritative screen metrics, UA client hints,
                                 touch emulation
      3. JS injection          – navigator props, WebGL GPU, battery, network,
                                 chrome runtime object, plugins
    """
    options = Options()

    if config.HEADLESS:
        options.add_argument("--headless=new")

    # ── Core stability flags ──────────────────────────────────────────────────
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-infobars")
    options.add_argument("--disable-notifications")
    options.add_argument("--mute-audio")
    options.add_argument("--disable-background-networking")
    options.add_argument("--disable-sync")
    options.add_argument("--metrics-recording-only")
    options.add_argument("--js-flags=--max-old-space-size=512")
    options.add_argument("--renderer-process-limit=1")

    # ── Realism flags (match a real Chrome on Android) ────────────────────────
    options.add_argument("--enable-features=NetworkService,NetworkServiceInProcess")
    options.add_argument("--lang=en-US")

    # ── Window size = Pixel 10 Pro CSS viewport ───────────────────────────────
    w, h = profile.screen_width, profile.screen_height
    options.add_argument(f"--window-size={w},{h}")
    options.add_argument(f"--user-agent={profile.user_agent}")

    # ── Anti-detection ────────────────────────────────────────────────────────
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    # ── Chrome-level mobile emulation ─────────────────────────────────────────
    # Reduced UA: "Android 10; K" — device model intentionally absent (Chrome 110+)
    mobile_emulation = {
        "deviceMetrics": {
            "width":      w,
            "height":     h,
            "pixelRatio": profile.pixel_ratio,
            "touch":      True,
        },
        "userAgent": profile.user_agent,
    }
    options.add_experimental_option("mobileEmulation", mobile_emulation)

    # ── Launch browser ────────────────────────────────────────────────────────
    chromium_binary = shutil.which("chromium") or shutil.which("chromium-browser")
    chromedriver_binary = shutil.which("chromedriver") or "chromedriver"
    if chromium_binary:
        options.binary_location = chromium_binary
    service = Service(executable_path=chromedriver_binary)
    driver = webdriver.Chrome(service=service, options=options)

    # ── CDP layer 1: authoritative screen metrics + touch ─────────────────────
    driver.execute_cdp_cmd("Emulation.setDeviceMetricsOverride", {
        "width":             w,
        "height":            h,
        "deviceScaleFactor": profile.pixel_ratio,
        "mobile":            True,
        "screenWidth":       w,
        "screenHeight":      h,
        "positionX":         0,
        "positionY":         0,
    })

    # ── CDP layer 2: User-Agent + full Sec-CH-UA-* client hints ──────────────
    # This is what Google reads to know the real device behind the reduced UA.
    driver.execute_cdp_cmd("Emulation.setUserAgentOverride", {
        "userAgent":         profile.user_agent,
        "acceptLanguage":    "en-US,en;q=0.9",
        "platform":          "Linux armv8l",
        "userAgentMetadata": profile.client_hints_metadata(),
    })

    # ── CDP layer 3: multitouch emulation ────────────────────────────────────
    driver.execute_cdp_cmd("Emulation.setTouchEmulationEnabled", {
        "enabled":       True,
        "maxTouchPoints": profile.max_touch_points,
    })

    # ── CDP layer 4: JS navigator/WebGL/battery/network spoofing ─────────────
    # Injected before every page's own JS runs.
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": profile.navigator_js()
    })

    driver.implicitly_wait(config.IMPLICIT_WAIT)
    driver.set_page_load_timeout(config.PAGE_LOAD_TIMEOUT)
    return driver


# ── Helpers ───────────────────────────────────────────────────────────────────

def _wait_for(driver: webdriver.Chrome, by: str, value: str,
              timeout: int = config.WEBDRIVER_TIMEOUT):
    """Return element after waiting for it to be clickable."""
    return WebDriverWait(driver, timeout).until(
        EC.element_to_be_clickable((by, value))
    )


def _click_element_by_inner_text(driver: webdriver.Chrome,
                                 keyword: str, tag: str = "*") -> bool:
    """Click the first visible element whose full inner text contains keyword."""
    try:
        el = driver.find_element(
            By.XPATH,
            f"//{tag}[contains(translate(., "
            f"'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz')"
            f", '{keyword.lower()}')]"
        )
        if el.is_displayed():
            el.click()
            return True
    except Exception:
        pass
    # JS fallback — walks every element and checks innerText
    try:
        driver.execute_script(
            """
            var kw = arguments[0].toLowerCase();
            var els = document.querySelectorAll(arguments[1]);
            for (var i = 0; i < els.length; i++) {
                if (els[i].innerText && els[i].innerText.toLowerCase().includes(kw)
                        && els[i].offsetParent !== null) {
                    els[i].click();
                    return true;
                }
            }
            return false;
            """,
            keyword.lower(), tag
        )
        return True
    except Exception:
        pass
    return False


def _enter_totp(driver: webdriver.Chrome, totp_secret: str,
                cb: ProgressCB = None) -> bool:
    """Generate a fresh TOTP code and fill it into the visible input. Returns True on submit."""
    code = _generate_totp(totp_secret)
    secs_left = 30 - (int(time.time()) % 30)
    _report(cb, f"🔢 Entering TOTP code: `{code}` ({secs_left}s remaining)", driver)

    # Find the visible TOTP input field
    totp_field = None
    for inp in driver.find_elements(By.CSS_SELECTOR, "input"):
        try:
            if (inp.get_attribute("aria-hidden") or "").lower() == "true":
                continue
            itype = (inp.get_attribute("type") or "").lower()
            iname = (inp.get_attribute("name") or "").lower()
            iac = (inp.get_attribute("autocomplete") or "").lower()
            if (itype in ("tel", "number") or "pin" in iname
                    or "totp" in iname or "one-time" in iac
                    or "security" in iname):
                totp_field = inp
                break
            # fallback: any visible non-hidden text input
            if itype == "text" and inp.is_displayed():
                totp_field = inp
        except Exception:
            continue

    if not totp_field:
        _report(cb, "⚠️ TOTP input field not found on this page", driver)
        return False

    totp_field.clear()
    totp_field.send_keys(code)
    time.sleep(0.5)

    # Submit
    for sid in ["totpNext", "passwordNext"]:
        try:
            driver.find_element(By.ID, sid).click()
            return True
        except NoSuchElementException:
            continue
    try:
        driver.find_element(
            By.CSS_SELECTOR, 'button[type="submit"], [jsname="LgbsSe"]'
        ).click()
        return True
    except NoSuchElementException:
        pass
    return False


def _click_authenticator_option(driver: webdriver.Chrome,
                                cb: ProgressCB = None) -> bool:
    """
    Click the Authenticator app option on Google's method-selection page.

    The <li> elements have no jsaction. The real click handler is on a child
    <div> or <a> INSIDE the authenticator <li>. Strategy:
      1. Find the <li> whose innerText contains "authenticator"
      2. Look INSIDE it for any jsaction child, <a>, or <button>
      3. If none, click the first <div> child (Google wraps options in divs)
      4. Last resort: plain .click() + dispatchEvent on the <li> itself
    """
    result = driver.execute_script(
        """
        // Strategy A: data-challengetype="6" = TOTP (Google Authenticator).
        // Do NOT use data-challengeid — it is not unique and matches other options.
        var directEl = document.querySelector('[data-action="selectchallenge"][data-challengetype="6"]');
        if (directEl && directEl.offsetParent !== null) {
            directEl.click();
            return 'direct_challengetype6';
        }

        // Strategy B: find <li> whose innerText contains "authenticator",
        // then click its first child with role=link/button or any jsaction,
        // or fall back to the first <div> child (which is Google's click wrapper).
        var kw = 'authenticator';
        var targetLi = null;
        var lis = document.querySelectorAll('li');
        for (var i = 0; i < lis.length; i++) {
            var t = (lis[i].innerText || '').toLowerCase();
            if (t.includes(kw) && lis[i].offsetParent !== null) {
                targetLi = lis[i];
                break;
            }
        }
        if (!targetLi) return 'li_not_found';

        // Prefer role=link/button or any jsaction child inside the li
        var jaEl = targetLi.querySelector('[role="link"], [role="button"], [jsaction], a, button');
        if (jaEl) {
            jaEl.click();
            return 'inner_role:' + jaEl.tagName + '/' + (jaEl.getAttribute('role') || '');
        }

        // First <div> child is Google's click container
        var divEl = targetLi.querySelector('div');
        if (divEl) {
            divEl.click();
            return 'inner_div';
        }

        // Last resort: click the LI itself
        targetLi.click();
        targetLi.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true, view: window}));
        return 'li_click_dispatch';
        """
    )
    _report(cb, f"🔍 Authenticator click: {result}", driver)
    if result and result not in ("li_not_found",):
        return True

    # DOM fallback via Selenium ActionChains
    try:
        from selenium.webdriver.common.action_chains import ActionChains
        el = driver.find_element(
            By.XPATH,
            "//li[.//*[contains(translate(.,"
            "'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'authenticator')]]"
        )
        ActionChains(driver).move_to_element(el).click(el).perform()
        _report(cb, "✅ Clicked Authenticator via ActionChains", driver)
        return True
    except Exception as e:
        _report(cb, f"⚠️ ActionChains fallback failed: {e}", driver)

    return False


def _click_try_another_way(driver: webdriver.Chrome) -> bool:
    """Click the 'Try another way' link/button. Returns True on success."""
    # JS innerText scan — most reliable across Google UI variants
    try:
        driver.execute_script(
            """
            var els = document.querySelectorAll('a, button, [role="button"]');
            for (var i = 0; i < els.length; i++) {
                if ((els[i].innerText || '').toLowerCase().includes('try another way')
                        && els[i].offsetParent !== null) {
                    els[i].click(); return;
                }
            }
            """
        )
        return True
    except Exception:
        pass
    # DOM fallback
    for el in driver.find_elements(By.CSS_SELECTOR, "a, button, [role='button']"):
        try:
            if "try another way" in (el.get_attribute("innerText") or el.text or "").lower() \
                    and el.is_displayed():
                el.click()
                return True
        except Exception:
            continue
    return False


def _handle_2fa(driver: webdriver.Chrome, totp_secret: str,
                cb: ProgressCB = None) -> None:
    """
    Handle any Google 2FA challenge after password submission.

    Detection is CONTENT-based, not URL-based, because Google reuses the
    /skotp URL for both the g.co/sc device code page AND the TOTP page.

    Flow:
      1. If page shows "g.co/sc"  →  click "Try another way"
      2. If page shows method list →  click "Authenticator app" option
      3. Enter the TOTP code from the authenticator key
    """
    hostname = urlparse(driver.current_url).hostname or ""
    if "accounts.google.com" not in hostname:
        _report(cb, "ℹ️ Step 4/6 — No 2FA challenge, already past login", driver)
        return

    src = driver.page_source.lower()
    path = urlparse(driver.current_url).path
    _report(cb, f"🔐 Step 4/6 — Challenge page\n📍 {path}", driver)

    # ── Stage 1: g.co/sc device-code page → click "Try another way" ──────────
    if "g.co/sc" in src:
        _report(cb,
                "🔄 Step 4a — g.co/sc page detected (device browser code).\n"
                "Clicking 'Try another way' to switch to Authenticator app…",
                driver)
        clicked = _click_try_another_way(driver)
        time.sleep(2)
        src = driver.page_source.lower()
        path = urlparse(driver.current_url).path
        _report(cb,
                f"{'✅' if clicked else '⚠️'} Step 4a — "
                f"{'Navigated away from g.co/sc' if clicked else 'Try another way not found'}\n"
                f"📍 Now: {path}",
                driver)

    # ── Stage 2: Method selection page → pick Authenticator app ──────────────
    src = driver.page_source.lower()
    # Selection page = has "authenticator" in content but no TOTP code input yet
    has_authenticator_option = "authenticator" in src
    has_totp_input = any(
        kw in src for kw in ['name="pin"', 'type="tel"', 'type="number"']
    )

    if has_authenticator_option and not has_totp_input:
        _report(cb,
                "🔍 Step 4b — Method selection page.\n"
                "Selecting 'Authenticator app'…",
                driver)

        # Log the clickable items on the page to aid debugging
        try:
            items_info = driver.execute_script(
                """
                var out = [];
                var items = document.querySelectorAll('li, [role="option"], [role="listitem"]');
                for (var i = 0; i < items.length; i++) {
                    var t = (items[i].innerText || '').trim().substring(0, 80);
                    var ja = items[i].getAttribute('jsaction') || '';
                    if (t) out.push(items[i].tagName + ' ja=' + ja.substring(0,30) + ' | ' + t);
                }
                return out.join('\\n');
                """
            )
            _report(cb, f"📋 Options found:\n{items_info or '(none)'}", driver)
        except Exception:
            pass

        picked = _click_authenticator_option(driver, cb)

        if picked:
            # Wait up to 8s for the TOTP input to appear (actual page navigation)
            try:
                WebDriverWait(driver, 8).until(
                    lambda d: any(
                        (inp.get_attribute("type") or "") in ("tel", "number", "text")
                        and inp.is_displayed()
                        for inp in d.find_elements(By.CSS_SELECTOR, "input")
                        if (inp.get_attribute("aria-hidden") or "").lower() != "true"
                    )
                )
                _report(cb,
                        f"✅ Step 4b — TOTP input appeared\n"
                        f"📍 Now: {urlparse(driver.current_url).path}",
                        driver)
            except Exception:
                _report(cb,
                        f"⚠️ Step 4b — TOTP input not found after waiting\n"
                        f"📍 Now: {urlparse(driver.current_url).path}",
                        driver)
        else:
            _report(cb, "⚠️ Step 4b — Could not click Authenticator option", driver)
            time.sleep(2)

    # ── Stage 3: TOTP input page → enter the code ────────────────────────────
    time.sleep(0.5)
    _report(cb, "🔑 Step 4c — Entering authenticator TOTP code…", driver)
    submitted = _enter_totp(driver, totp_secret, cb)
    if submitted:
        time.sleep(3)
        _report(cb,
                f"✅ Step 4 — TOTP submitted\n📍 URL: {driver.current_url[:80]}",
                driver)
    else:
        _report(cb,
                "⚠️ Step 4 — Could not find TOTP input field.\n"
                f"📍 Current page: {urlparse(driver.current_url).path}",
                driver)


def _generate_totp(secret: str) -> str:
    """Generate the current TOTP code from a base32 secret."""
    totp = pyotp.TOTP(secret)
    code = totp.now()
    secs_left = 30 - (int(time.time()) % 30)
    logger.info("Generated TOTP: %s (%ds left)", code, secs_left)
    return code


# ── Login ─────────────────────────────────────────────────────────────────────

def _gmail_login(driver: webdriver.Chrome, email: str, password: str,
                 totp_secret: Optional[str] = None,
                 cb: ProgressCB = None) -> bool:
    """
    Perform Gmail login with optional TOTP 2FA.
    Calls cb() with a message + screenshot at every key step.
    Returns True on success, False on detectable failure.
    """
    try:
        # ── Step 1: Load login page ───────────────────────────────────────────
        _report(cb, "🌐 Step 1/6 — Loading Google sign-in page…", driver)
        driver.get(config.GMAIL_LOGIN_URL)
        time.sleep(2)

        # ── Step 2: Enter email ───────────────────────────────────────────────
        _report(cb, f"📧 Step 2/6 — Entering email: {email}", driver)
        email_field = _wait_for(driver, By.CSS_SELECTOR,
                                'input[name="identifier"], input[type="email"]')
        email_field.clear()
        email_field.send_keys(email)
        _wait_for(driver, By.ID, "identifierNext").click()
        time.sleep(2)

        # ── Step 3: Enter password ────────────────────────────────────────────
        _report(cb, "🔒 Step 3/6 — Entering password…", driver)
        password_field = _wait_for(driver, By.CSS_SELECTOR,
                                   'input[type="password"]')
        password_field.clear()
        password_field.send_keys(password)
        _wait_for(driver, By.ID, "passwordNext").click()
        time.sleep(3)

        # ── Step 4: 2FA / TOTP ────────────────────────────────────────────────
        if totp_secret:
            _report(cb, "🔐 Step 4/6 — Checking for 2FA challenge…", driver)
            _handle_2fa(driver, totp_secret, cb)

        # ── Step 5: Verify login ──────────────────────────────────────────────
        current_url = driver.current_url
        parsed = urlparse(current_url)
        hostname = parsed.hostname or ""
        path = parsed.path or ""

        # Check for visible error text
        try:
            error_el = driver.find_element(
                By.CSS_SELECTOR, '[jsname="B34EJ"], [aria-live="assertive"]'
            )
            if error_el.text.strip():
                _report(cb,
                        f"❌ Step 5/6 — Login error: {error_el.text.strip()}",
                        driver)
                return False
        except NoSuchElementException:
            pass

        if (hostname == "myaccount.google.com"
                or (hostname.endswith(".google.com") and "/u/" in path)):
            _report(cb, "✅ Step 5/6 — Logged in successfully!", driver)
            return True

        if not (hostname == "accounts.google.com"
                and path.startswith("/signin")):
            _report(cb,
                    f"✅ Step 5/6 — Login appears successful\n"
                    f"📍 URL: {current_url[:80]}",
                    driver)
            return True

        _report(cb,
                f"❌ Step 5/6 — Still on sign-in page after login\n"
                f"📍 URL: {current_url[:80]}",
                driver)
        return False

    except TimeoutException as exc:
        _report(cb, f"⏱️ Timeout during login: {exc}", driver)
        return False
    except WebDriverException as exc:
        _report(cb, f"❌ WebDriver error during login: {exc}", driver)
        return False


# ── Offer detection ───────────────────────────────────────────────────────────

def _extract_payment_link(driver: webdriver.Chrome) -> Optional[str]:
    """Scan current page for a Gemini Pro offer / activation link."""
    keywords = config.GEMINI_OFFER_KEYWORDS
    url_pat = re.compile(
        r"(gemini|upgrade|activate|offer|redeem|trial|checkout)",
        re.IGNORECASE,
    )

    all_links = driver.find_elements(By.TAG_NAME, "a")

    for link in all_links:
        try:
            text = (link.text + " " + (link.get_attribute("aria-label") or "")).lower()
            href = link.get_attribute("href") or ""
            if any(kw in text for kw in keywords) and href:
                return href
        except Exception:
            continue

    for link in all_links:
        try:
            href = link.get_attribute("href") or ""
            if url_pat.search(href):
                return href
        except Exception:
            continue

    for btn in driver.find_elements(By.CSS_SELECTOR, "button, [role='button']"):
        try:
            if any(kw in btn.text.lower() for kw in keywords):
                try:
                    parent = btn.find_element(By.XPATH, "ancestor::a")
                    href = parent.get_attribute("href") or ""
                    if href:
                        return href
                except NoSuchElementException:
                    pass
                return driver.current_url
        except Exception:
            continue

    return None


def _navigate_google_one(driver: webdriver.Chrome,
                         cb: ProgressCB = None) -> Optional[str]:
    """Navigate Google One pages and return the Gemini offer link."""
    for url in (config.GOOGLE_ONE_URL, config.GOOGLE_ONE_OFFERS_URL):
        try:
            _report(cb, f"🔍 Step 6/6 — Scanning: {url}", driver)
            driver.get(url)
            time.sleep(3)

            for selector in (
                '[aria-label="Accept all"]',
                'button[jsname="higCR"]',
                '[data-action="accept"]',
            ):
                try:
                    driver.find_element(By.CSS_SELECTOR, selector).click()
                    time.sleep(1)
                    break
                except NoSuchElementException:
                    pass

            _report(cb, f"🔎 Searching page for Gemini offer links…", driver)
            link = _extract_payment_link(driver)
            if link:
                _report(cb,
                        f"🎯 Offer link found!\n🔗 {link}",
                        driver)
                return link
            else:
                _report(cb, f"😔 No offer link on {url}, trying next…", driver)

        except (TimeoutException, WebDriverException) as exc:
            _report(cb, f"⚠️ Error loading {url}: {exc}", driver)

    return None


# ── Public API ────────────────────────────────────────────────────────────────

class GoogleAutomationError(Exception):
    """Raised when automation encounters an unrecoverable error."""


def check_gemini_offer(email: str, password: str,
                       device: DeviceProfile,
                       totp_secret: Optional[str] = None,
                       progress_callback: ProgressCB = None) -> Optional[str]:
    """
    Main entry point.

    Logs into email/password with optional TOTP, navigates to Google One,
    and returns the Gemini Pro offer link (or None).

    progress_callback(msg: str, screenshot_bytes: Optional[bytes]) is called
    at every key step so the caller can relay live updates to Telegram.
    """
    driver: Optional[webdriver.Chrome] = None
    try:
        _report(progress_callback,
                f"🤖 Starting Pixel 10 Pro simulator\n"
                f"📱 Session: {device.session_id[:8]}…\n"
                f"🌐 User-Agent: {device.user_agent[:60]}…")

        driver = _build_driver(device)
        _report(progress_callback, "✅ Browser launched successfully", driver)

        logged_in = _gmail_login(
            driver, email, password,
            totp_secret=totp_secret,
            cb=progress_callback,
        )
        if not logged_in:
            raise GoogleAutomationError(
                "Login failed — please check your credentials."
            )

        offer_link = _navigate_google_one(driver, cb=progress_callback)
        return offer_link

    finally:
        if driver:
            try:
                _report(progress_callback, "🧹 Closing browser session…")
                driver.quit()
            except Exception:
                pass
