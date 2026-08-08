"""Reliable Google One offer-link extraction layer.

This module keeps the existing login/device automation intact and replaces only
its final offer-discovery step. It accepts a link only when it is an actual
Google One offer URL, not a generic Google AI landing page.
"""

import time
from typing import Optional
from urllib.parse import urlparse

import google_automation as _ga
from selenium.common.exceptions import NoSuchElementException, TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

import config

VALID_PATH_MARKERS = ("/offer/", "/partner-eft-onboard/")


def _is_real_offer_url(url: str) -> bool:
    if not url:
        return False
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if parsed.scheme not in ("https", "http") or parsed.netloc.lower() != "one.google.com":
        return False
    path = parsed.path.lower()
    return any(marker in path for marker in VALID_PATH_MARKERS)


def _find_real_offer_link(driver) -> Optional[str]:
    """Find an actual offer URL already present in the DOM."""
    for el in driver.find_elements(By.CSS_SELECTOR, "a[href], area[href]"):
        try:
            href = el.get_attribute("href") or ""
            if _is_real_offer_url(href):
                return href
        except Exception:
            continue
    return None


def _click_start_trial(driver) -> bool:
    """Click the visible Start trial CTA used by the Google AI offer page."""
    try:
        result = driver.execute_script(
            """
            const nodes = document.querySelectorAll('a, button, [role="button"]');
            for (const el of nodes) {
                const text = (el.innerText || el.textContent || '').trim().toLowerCase();
                if (text === 'start trial' && el.offsetParent !== null) {
                    el.click();
                    return true;
                }
            }
            return false;
            """
        )
        if result:
            return True
    except Exception:
        pass

    try:
        for el in driver.find_elements(By.XPATH, "//a|//button|//*[@role='button']"):
            try:
                text = (el.text or el.get_attribute("aria-label") or "").strip().lower()
                if text == "start trial" and el.is_displayed() and el.is_enabled():
                    el.click()
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False


def _wait_for_offer_result(driver, cb=None) -> Optional[str]:
    """Wait briefly after CTA activation and capture the real offer URL."""
    for _ in range(12):
        link = _find_real_offer_link(driver)
        if link:
            return link
        if _is_real_offer_url(driver.current_url):
            return driver.current_url
        time.sleep(1)
    return None


def _navigate_google_one_strict(driver, cb=None) -> Optional[str]:
    urls = (config.GOOGLE_ONE_URL, config.GOOGLE_ONE_OFFERS_URL)

    for url in urls:
        try:
            _ga._report(cb, f"🔍 Checking Google One: {url}", driver)
            driver.get(url)
            time.sleep(3)

            # Consent dialogs, when present.
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

            link = _find_real_offer_link(driver)
            if link:
                _ga._report(cb, f"🎯 Offer link found!\n🔗 {link}", driver)
                return link

            _ga._report(cb, "🔎 Looking for the Start trial button…", driver)
            clicked = _click_start_trial(driver)
            if clicked:
                _ga._report(cb, "🖱️ Start trial clicked. Waiting for the offer URL…", driver)
                link = _wait_for_offer_result(driver, cb)
                if link:
                    _ga._report(cb, f"🎯 Real offer link found!\n🔗 {link}", driver)
                    return link

            # Never return /ai or other generic landing pages as an offer.
            _ga._report(cb, "ℹ️ No real /offer/ or /partner-eft-onboard/ link here.", driver)
        except (TimeoutException, WebDriverException) as exc:
            _ga._report(cb, f"⚠️ Google One page error: {exc}", driver)

    return None


def check_gemini_offer(email, password, device, totp_secret=None, progress_callback=None):
    """Run the existing login automation with strict offer extraction."""
    original = _ga._navigate_google_one
    _ga._navigate_google_one = _navigate_google_one_strict
    try:
        return _ga.check_gemini_offer(
            email,
            password,
            device,
            totp_secret=totp_secret,
            progress_callback=progress_callback,
        )
    finally:
        _ga._navigate_google_one = original


GoogleAutomationError = _ga.GoogleAutomationError
