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


def _build_driver(profile: DeviceProfile) -> webdriver.Chrome:
    """Build a Chrome WebDriver using an available Chrome/Chromium binary.

    The previous implementation always fell back to the literal string
    ``chromedriver``. On Render that file is not necessarily on PATH, which
    caused Selenium's ``NoSuchDriverException``.

    Selenium Manager is used when no explicit chromedriver executable is
    available. This lets Selenium resolve a compatible driver automatically.
    """
    options = Options()

    if config.HEADLESS:
        options.add_argument("--headless=new")

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
    options.add_argument("--enable-features=NetworkService,NetworkServiceInProcess")
    options.add_argument("--lang=en-US")

    w, h = profile.screen_width, profile.screen_height
    options.add_argument(f"--window-size={w},{h}")
    options.add_argument(f"--user-agent={profile.user_agent}")

    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    mobile_emulation = {
        "deviceMetrics": {
            "width": w,
            "height": h,
            "pixelRatio": profile.pixel_ratio,
            "touch": True,
        },
        "userAgent": profile.user_agent,
    }
    options.add_experimental_option("mobileEmulation", mobile_emulation)

    # Prefer explicitly installed executables. Otherwise let Selenium Manager
    # locate/download a compatible driver instead of passing a nonexistent
    # literal path such as ``chromedriver`` to Service().
    chromium_binary = shutil.which("chromium") or shutil.which("chromium-browser") or shutil.which("google-chrome")
    chromedriver_binary = shutil.which("chromedriver")

    if chromium_binary:
        options.binary_location = chromium_binary

    if chromedriver_binary:
        logger.info("Using ChromeDriver from PATH: %s", chromedriver_binary)
        service = Service(executable_path=chromedriver_binary)
        driver = webdriver.Chrome(service=service, options=options)
    else:
        logger.info("ChromeDriver not found on PATH; using Selenium Manager")
        driver = webdriver.Chrome(options=options)

    driver.execute_cdp_cmd("Emulation.setDeviceMetricsOverride", {
        "width": w,
        "height": h,
        "deviceScaleFactor": profile.pixel_ratio,
        "mobile": True,
        "screenWidth": w,
        "screenHeight": h,
        "positionX": 0,
        "positionY": 0,
    })

    driver.execute_cdp_cmd("Emulation.setUserAgentOverride", {
        "userAgent": profile.user_agent,
        "acceptLanguage": "en-US,en;q=0.9",
        "platform": "Linux armv8l",
        "userAgentMetadata": profile.client_hints_metadata(),
    })

    driver.execute_cdp_cmd("Emulation.setTouchEmulationEnabled", {
        "enabled": True,
        "maxTouchPoints": profile.max_touch_points,
    })

    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": profile.navigator_js()
    })

    driver.implicitly_wait(config.IMPLICIT_WAIT)
    driver.set_page_load_timeout(config.PAGE_LOAD_TIMEOUT)
    return driver


def _wait_for(driver: webdriver.Chrome, by: str, value: str,
              timeout: int = config.WEBDRIVER_TIMEOUT):
    return WebDriverWait(driver, timeout).until(
        EC.element_to_be_clickable((by, value))
    )


def _click_element_by_inner_text(driver: webdriver.Chrome,
                                 keyword: str, tag: str = "*") -> bool:
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
    try:
        result = driver.execute_script(
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
        return bool(result)
    except Exception:
        return False


def _generate_totp(secret: str) -> str:
    totp = pyotp.TOTP(secret)
    code = totp.now()
    secs_left = 30 - (int(time.time()) % 30)
    logger.info("Generated TOTP: %s (%ds left)", code, secs_left)
    return code


def _enter_totp(driver: webdriver.Chrome, totp_secret: str,
                cb: ProgressCB = None) -> bool:
    code = _generate_totp(totp_secret)
    secs_left = 30 - (int(time.time()) % 30)
    _report(cb, f"🔢 Entering TOTP code: `{code}` ({secs_left}s remaining)", driver)

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
        return False


def _click_authenticator_option(driver: webdriver.Chrome,
                                cb: ProgressCB = None) -> bool:
    result = driver.execute_script(
        """
        var directEl = document.querySelector('[data-action="selectchallenge"][data-challengetype="6"]');
        if (directEl && directEl.offsetParent !== null) {
            directEl.click();
            return 'direct_challengetype6';
        }
        var targetLi = null;
        var lis = document.querySelectorAll('li');
        for (var i = 0; i < lis.length; i++) {
            var t = (lis[i].innerText || '').toLowerCase();
            if (t.includes('authenticator') && lis[i].offsetParent !== null) {
                targetLi = lis[i];
                break;
            }
        }
        if (!targetLi) return 'li_not_found';
        var jaEl = targetLi.querySelector('[role="link"], [role="button"], [jsaction], a, button');
        if (jaEl) { jaEl.click(); return 'inner_role:' + jaEl.tagName; }
        var divEl = targetLi.querySelector('div');
        if (divEl) { divEl.click(); return 'inner_div'; }
        targetLi.click();
        targetLi.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true, view: window}));
        return 'li_click_dispatch';
        """
    )
    _report(cb, f"🔍 Authenticator click: {result}", driver)
    if result and result != "li_not_found":
        return True
    try:
        from selenium.webdriver.common.action_chains import ActionChains
        el = driver.find_element(
            By.XPATH,
            "//li[.//*[contains(translate(.,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'authenticator')]]"
        )
        ActionChains(driver).move_to_element(el).click(el).perform()
        _report(cb, "✅ Clicked Authenticator via ActionChains", driver)
        return True
    except Exception as e:
        _report(cb, f"⚠️ ActionChains fallback failed: {e}", driver)
        return False


def _click_try_another_way(driver: webdriver.Chrome) -> bool:
    try:
        driver.execute_script(
            """
            var els = document.querySelectorAll('a, button, [role="button"]');
            for (var i = 0; i < els.length; i++) {
                if ((els[i].innerText || '').toLowerCase().includes('try another way')
                        && els[i].offsetParent !== null) {
                    els[i].click(); return true;
                }
            }
            return false;
            """
        )
        return True
    except Exception:
        pass
    for el in driver.find_elements(By.CSS_SELECTOR, "a, button, [role='button']"):
        try:
            if "try another way" in (el.get_attribute("innerText") or el.text or "").lower() and el.is_displayed():
                el.click()
                return True
        except Exception:
            continue
    return False


def _handle_2fa(driver: webdriver.Chrome, totp_secret: str,
                cb: ProgressCB = None) -> None:
    hostname = urlparse(driver.current_url).hostname or ""
    if "accounts.google.com" not in hostname:
        _report(cb, "ℹ️ Step 4/6 — No 2FA challenge, already past login", driver)
        return
    src = driver.page_source.lower()
    path = urlparse(driver.current_url).path
    _report(cb, f"🔐 Step 4/6 — Challenge page\n📍 {path}", driver)
    if "g.co/sc" in src:
        _report(cb, "🔄 Step 4a — g.co/sc page detected. Clicking 'Try another way'…", driver)
        clicked = _click_try_another_way(driver)
        time.sleep(2)
        src = driver.page_source.lower()
        path = urlparse(driver.current_url).path
        _report(cb, f"{'✅' if clicked else '⚠️'} Step 4a — Now: {path}", driver)
    src = driver.page_source.lower()
    has_authenticator_option = "authenticator" in src
    has_totp_input = any(kw in src for kw in ['name="pin"', 'type="tel"', 'type="number"'])
    if has_authenticator_option and not has_totp_input:
        _report(cb, "🔍 Step 4b — Selecting 'Authenticator app'…", driver)
        _click_authenticator_option(driver, cb)
        time.sleep(2)
    time.sleep(0.5)
    _report(cb, "🔑 Step 4c — Entering authenticator TOTP code…", driver)
    submitted = _enter_totp(driver, totp_secret, cb)
    if submitted:
        time.sleep(3)
        _report(cb, f"✅ Step 4 — TOTP submitted\n📍 URL: {driver.current_url[:80]}", driver)
    else:
        _report(cb, "⚠️ Step 4 — Could not find TOTP input field.", driver)


def _gmail_login(driver: webdriver.Chrome, email: str, password: str,
                 totp_secret: Optional[str] = None,
                 cb: ProgressCB = None) -> bool:
    try:
        _report(cb, "🌐 Step 1/6 — Loading Google sign-in page…", driver)
        driver.get(config.GMAIL_LOGIN_URL)
        time.sleep(2)
        _report(cb, f"📧 Step 2/6 — Entering email: {email}", driver)
        email_field = _wait_for(driver, By.CSS_SELECTOR, 'input[name="identifier"], input[type="email"]')
        email_field.clear(); email_field.send_keys(email)
        _wait_for(driver, By.ID, "identifierNext").click()
        time.sleep(2)
        _report(cb, "🔒 Step 3/6 — Entering password…", driver)
        password_field = _wait_for(driver, By.CSS_SELECTOR, 'input[type="password"]')
        password_field.clear(); password_field.send_keys(password)
        _wait_for(driver, By.ID, "passwordNext").click()
        time.sleep(3)
        if totp_secret:
            _handle_2fa(driver, totp_secret, cb)
        current_url = driver.current_url
        parsed = urlparse(current_url)
        hostname = parsed.hostname or ""
        path = parsed.path or ""
        try:
            error_el = driver.find_element(By.CSS_SELECTOR, '[jsname="B34EJ"], [aria-live="assertive"]')
            if error_el.text.strip():
                _report(cb, f"❌ Step 5/6 — Login error: {error_el.text.strip()}", driver)
                return False
        except NoSuchElementException:
            pass
        if hostname == "myaccount.google.com" or (hostname.endswith(".google.com") and "/u/" in path):
            _report(cb, "✅ Step 5/6 — Logged in successfully!", driver)
            return True
        if not (hostname == "accounts.google.com" and path.startswith("/signin")):
            _report(cb, f"✅ Step 5/6 — Login appears successful\n📍 URL: {current_url[:80]}", driver)
            return True
        _report(cb, f"❌ Step 5/6 — Still on sign-in page after login\n📍 URL: {current_url[:80]}", driver)
        return False
    except TimeoutException as exc:
        _report(cb, f"⏱️ Timeout during login: {exc}", driver)
        return False
    except WebDriverException as exc:
        _report(cb, f"❌ WebDriver error during login: {exc}", driver)
        return False


def _extract_payment_link(driver: webdriver.Chrome) -> Optional[str]:
    keywords = config.GEMINI_OFFER_KEYWORDS
    url_pat = re.compile(r"(gemini|upgrade|activate|offer|redeem|trial|checkout)", re.IGNORECASE)
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
            if url_pat.search(href): return href
        except Exception:
            continue
    for btn in driver.find_elements(By.CSS_SELECTOR, "button, [role='button']"):
        try:
            if any(kw in btn.text.lower() for kw in keywords):
                try:
                    parent = btn.find_element(By.XPATH, "ancestor::a")
                    href = parent.get_attribute("href") or ""
                    if href: return href
                except NoSuchElementException:
                    pass
                return driver.current_url
        except Exception:
            continue
    return None


def _navigate_google_one(driver: webdriver.Chrome, cb: ProgressCB = None) -> Optional[str]:
    for url in (config.GOOGLE_ONE_URL, config.GOOGLE_ONE_OFFERS_URL):
        try:
            _report(cb, f"🔍 Step 6/6 — Scanning: {url}", driver)
            driver.get(url); time.sleep(3)
            for selector in ('[aria-label="Accept all"]', 'button[jsname="higCR"]', '[data-action="accept"]'):
                try:
                    driver.find_element(By.CSS_SELECTOR, selector).click(); time.sleep(1); break
                except NoSuchElementException:
                    pass
            _report(cb, "🔎 Searching page for Gemini offer links…", driver)
            link = _extract_payment_link(driver)
            if link:
                _report(cb, f"🎯 Offer link found!\n🔗 {link}", driver)
                return link
            _report(cb, f"😔 No offer link on {url}, trying next…", driver)
        except (TimeoutException, WebDriverException) as exc:
            _report(cb, f"⚠️ Error loading {url}: {exc}", driver)
    return None


class GoogleAutomationError(Exception):
    """Raised when automation encounters an unrecoverable error."""


def check_gemini_offer(email: str, password: str,
                       device: DeviceProfile,
                       totp_secret: Optional[str] = None,
                       progress_callback: ProgressCB = None) -> Optional[str]:
    driver: Optional[webdriver.Chrome] = None
    try:
        _report(progress_callback, f"🤖 Starting Pixel 10 Pro simulator\n📱 Session: {device.session_id[:8]}…\n🌐 User-Agent: {device.user_agent[:60]}…")
        driver = _build_driver(device)
        _report(progress_callback, "✅ Browser launched successfully", driver)
        logged_in = _gmail_login(driver, email, password, totp_secret=totp_secret, cb=progress_callback)
        if not logged_in:
            raise GoogleAutomationError("Login failed — please check your credentials.")
        return _navigate_google_one(driver, cb=progress_callback)
    finally:
        if driver:
            try:
                _report(progress_callback, "🧹 Closing browser session…")
                driver.quit()
            except Exception:
                pass
