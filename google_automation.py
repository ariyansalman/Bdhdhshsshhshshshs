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
    StaleElementReferenceException,
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
    try:
        return driver.get_screenshot_as_png()
    except Exception:
        return None


def _report(cb: ProgressCB, msg: str, driver: Optional[webdriver.Chrome] = None) -> None:
    logger.info(msg)
    if cb:
        screenshot = _shot(driver) if driver else None
        try:
            cb(msg, screenshot)
        except Exception as e:
            logger.warning("progress_callback error: %s", e)


def _build_driver(profile: DeviceProfile) -> webdriver.Chrome:
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
    options.add_experimental_option("mobileEmulation", {
        "deviceMetrics": {"width": w, "height": h, "pixelRatio": profile.pixel_ratio, "touch": True},
        "userAgent": profile.user_agent,
    })

    chromium_binary = shutil.which("chromium") or shutil.which("chromium-browser") or shutil.which("google-chrome")
    chromedriver_binary = shutil.which("chromedriver")
    if chromium_binary:
        options.binary_location = chromium_binary

    if chromedriver_binary:
        logger.info("Using ChromeDriver from PATH: %s", chromedriver_binary)
        driver = webdriver.Chrome(service=Service(executable_path=chromedriver_binary), options=options)
    else:
        logger.info("ChromeDriver not found on PATH; using Selenium Manager")
        driver = webdriver.Chrome(options=options)

    driver.execute_cdp_cmd("Emulation.setDeviceMetricsOverride", {
        "width": w, "height": h, "deviceScaleFactor": profile.pixel_ratio,
        "mobile": True, "screenWidth": w, "screenHeight": h, "positionX": 0, "positionY": 0,
    })
    driver.execute_cdp_cmd("Emulation.setUserAgentOverride", {
        "userAgent": profile.user_agent,
        "acceptLanguage": "en-US,en;q=0.9",
        "platform": "Linux armv8l",
        "userAgentMetadata": profile.client_hints_metadata(),
    })
    driver.execute_cdp_cmd("Emulation.setTouchEmulationEnabled", {
        "enabled": True, "maxTouchPoints": profile.max_touch_points,
    })
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": profile.navigator_js()
    })
    driver.implicitly_wait(config.IMPLICIT_WAIT)
    driver.set_page_load_timeout(config.PAGE_LOAD_TIMEOUT)
    return driver


def _wait_for(driver: webdriver.Chrome, by: str, value: str,
              timeout: int = config.WEBDRIVER_TIMEOUT):
    return WebDriverWait(driver, timeout).until(EC.element_to_be_clickable((by, value)))


def _click_fresh(driver: webdriver.Chrome, by: str, value: str,
                 timeout: int = config.WEBDRIVER_TIMEOUT, retries: int = 4) -> bool:
    """Find and click a fresh element each time. Google sign-in rerenders often."""
    last_error = None
    for attempt in range(retries):
        try:
            WebDriverWait(driver, timeout).until(EC.element_to_be_clickable((by, value)))
            element = driver.find_element(by, value)
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", element)
            element.click()
            return True
        except StaleElementReferenceException as exc:
            last_error = exc
            time.sleep(0.35 + attempt * 0.25)
        except (NoSuchElementException, WebDriverException) as exc:
            last_error = exc
            time.sleep(0.35 + attempt * 0.25)
    raise WebDriverException(f"Could not click fresh element {by}={value}: {last_error}")


def _fill_fresh(driver: webdriver.Chrome, by: str, value: str, text: str,
                timeout: int = config.WEBDRIVER_TIMEOUT, retries: int = 4) -> bool:
    """Fill a fresh input element, retrying if Google's DOM replaces it."""
    last_error = None
    for attempt in range(retries):
        try:
            WebDriverWait(driver, timeout).until(EC.visibility_of_element_located((by, value)))
            element = driver.find_element(by, value)
            element.click()
            element.clear()
            element.send_keys(text)
            return True
        except StaleElementReferenceException as exc:
            last_error = exc
            time.sleep(0.35 + attempt * 0.25)
        except (NoSuchElementException, WebDriverException) as exc:
            last_error = exc
            time.sleep(0.35 + attempt * 0.25)
    raise WebDriverException(f"Could not fill fresh element {by}={value}: {last_error}")


def _click_element_by_inner_text(driver: webdriver.Chrome, keyword: str, tag: str = "*") -> bool:
    try:
        result = driver.execute_script(
            """
            var kw = arguments[0].toLowerCase();
            var els = document.querySelectorAll(arguments[1]);
            for (var i = 0; i < els.length; i++) {
                if ((els[i].innerText || '').toLowerCase().includes(kw) && els[i].offsetParent !== null) {
                    els[i].click(); return true;
                }
            }
            return false;
            """, keyword.lower(), tag)
        return bool(result)
    except Exception:
        return False


def _generate_totp(secret: str) -> str:
    return pyotp.TOTP(secret).now()


def _enter_totp(driver: webdriver.Chrome, totp_secret: str, cb: ProgressCB = None) -> bool:
    code = _generate_totp(totp_secret)
    secs_left = 30 - (int(time.time()) % 30)
    _report(cb, f"🔢 Entering TOTP code: `{code}` ({secs_left}s remaining)", driver)

    for attempt in range(4):
        try:
            totp_field = None
            for inp in driver.find_elements(By.CSS_SELECTOR, "input"):
                try:
                    if (inp.get_attribute("aria-hidden") or "").lower() == "true":
                        continue
                    itype = (inp.get_attribute("type") or "").lower()
                    iname = (inp.get_attribute("name") or "").lower()
                    iac = (inp.get_attribute("autocomplete") or "").lower()
                    if (itype in ("tel", "number") or "pin" in iname or "totp" in iname
                            or "one-time" in iac or "security" in iname):
                        totp_field = inp
                        break
                except StaleElementReferenceException:
                    continue
            if not totp_field:
                raise NoSuchElementException("TOTP input not found")
            totp_field.clear()
            totp_field.send_keys(code)
            time.sleep(0.5)
            for sid in ["totpNext", "passwordNext"]:
                if _click_fresh(driver, By.ID, sid, timeout=5, retries=2):
                    return True
            return _click_fresh(driver, By.CSS_SELECTOR, 'button[type="submit"], [jsname="LgbsSe"]', timeout=5, retries=2)
        except (StaleElementReferenceException, NoSuchElementException, WebDriverException):
            time.sleep(0.5 + attempt * 0.25)
    _report(cb, "⚠️ Could not enter TOTP because Google replaced the form.", driver)
    return False


def _click_authenticator_option(driver: webdriver.Chrome, cb: ProgressCB = None) -> bool:
    try:
        result = driver.execute_script("""
            var directEl = document.querySelector('[data-action="selectchallenge"][data-challengetype="6"]');
            if (directEl && directEl.offsetParent !== null) { directEl.click(); return 'direct_challengetype6'; }
            var lis = document.querySelectorAll('li');
            for (var i = 0; i < lis.length; i++) {
                if ((lis[i].innerText || '').toLowerCase().includes('authenticator') && lis[i].offsetParent !== null) {
                    var x = lis[i].querySelector('[role="link"], [role="button"], a, button, div');
                    (x || lis[i]).click(); return 'authenticator';
                }
            }
            return 'not_found';
        """)
        _report(cb, f"🔍 Authenticator click: {result}", driver)
        return result != "not_found"
    except Exception:
        return False


def _click_try_another_way(driver: webdriver.Chrome) -> bool:
    return _click_element_by_inner_text(driver, "try another way", "a,button,[role='button']")


def _handle_2fa(driver: webdriver.Chrome, totp_secret: str, cb: ProgressCB = None) -> None:
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
        _report(cb, f"{'✅' if clicked else '⚠️'} Step 4a — Now: {urlparse(driver.current_url).path}", driver)
    src = driver.page_source.lower()
    if "authenticator" in src and not any(kw in src for kw in ['name="pin"', 'type="tel"', 'type="number"']):
        _report(cb, "🔍 Step 4b — Selecting 'Authenticator app'…", driver)
        _click_authenticator_option(driver, cb)
        time.sleep(2)
    _report(cb, "🔑 Step 4c — Entering authenticator TOTP code…", driver)
    if _enter_totp(driver, totp_secret, cb):
        time.sleep(3)
        _report(cb, f"✅ Step 4 — TOTP submitted\n📍 URL: {driver.current_url[:80]}", driver)


def _gmail_login(driver: webdriver.Chrome, email: str, password: str,
                 totp_secret: Optional[str] = None, cb: ProgressCB = None) -> bool:
    try:
        _report(cb, "🌐 Step 1/6 — Loading Google sign-in page…", driver)
        driver.get(config.GMAIL_LOGIN_URL)
        time.sleep(2)
        _report(cb, f"📧 Step 2/6 — Entering email: {email}", driver)
        _fill_fresh(driver, By.CSS_SELECTOR, 'input[name="identifier"], input[type="email"]', email)
        _click_fresh(driver, By.ID, "identifierNext")
        time.sleep(2)
        _report(cb, "🔒 Step 3/6 — Entering password…", driver)
        _fill_fresh(driver, By.CSS_SELECTOR, 'input[type="password"]', password)
        _click_fresh(driver, By.ID, "passwordNext")
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
        except (NoSuchElementException, StaleElementReferenceException):
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
    for link in driver.find_elements(By.TAG_NAME, "a"):
        try:
            text = (link.text + " " + (link.get_attribute("aria-label") or "")).lower()
            href = link.get_attribute("href") or ""
            if any(kw in text for kw in keywords) and href:
                return href
        except (Exception, StaleElementReferenceException):
            continue
    for link in driver.find_elements(By.TAG_NAME, "a"):
        try:
            href = link.get_attribute("href") or ""
            if url_pat.search(href):
                return href
        except (Exception, StaleElementReferenceException):
            continue
    return None


def _navigate_google_one(driver: webdriver.Chrome, cb: ProgressCB = None) -> Optional[str]:
    for url in (config.GOOGLE_ONE_URL, config.GOOGLE_ONE_OFFERS_URL):
        try:
            _report(cb, f"🔍 Step 6/6 — Scanning: {url}", driver)
            driver.get(url)
            time.sleep(3)
            _report(cb, "🔎 Searching page for Gemini offer links…", driver)
            link = _extract_payment_link(driver)
            if link:
                _report(cb, f"🎯 Offer link found!\n🔗 {link}", driver)
                return link
        except (TimeoutException, WebDriverException) as exc:
            _report(cb, f"⚠️ Error loading {url}: {exc}", driver)
    return None


class GoogleAutomationError(Exception):
    pass


def check_gemini_offer(email: str, password: str, device: DeviceProfile,
                       totp_secret: Optional[str] = None,
                       progress_callback: ProgressCB = None) -> Optional[str]:
    driver: Optional[webdriver.Chrome] = None
    try:
        _report(progress_callback, f"🤖 Starting Pixel 10 Pro simulator\n📱 Session: {device.session_id[:8]}…\n🌐 User-Agent: {device.user_agent[:60]}…")
        driver = _build_driver(device)
        _report(progress_callback, "✅ Browser launched successfully", driver)
        if not _gmail_login(driver, email, password, totp_secret=totp_secret, cb=progress_callback):
            raise GoogleAutomationError("Login failed — please check your credentials.")
        return _navigate_google_one(driver, cb=progress_callback)
    finally:
        if driver:
            try:
                _report(progress_callback, "🧹 Closing browser session…")
                driver.quit()
            except Exception:
                pass
