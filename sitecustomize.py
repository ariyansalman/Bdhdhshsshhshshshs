"""Small runtime bootstrap for Selenium on hosted Linux services.

The application historically constructs Chrome's Service with the literal
string ``chromedriver``. On Render that file is not on PATH, and Selenium 4
therefore treats the string as an explicit path and rejects it before its
built-in Selenium Manager can run.

This module converts that invalid relative driver path into an unspecified
Service so Selenium Manager can locate/install the matching browser driver.
It also installs the strict Google One offer extractor before main.py imports
check_gemini_offer, so generic /ai landing pages are never returned as offers.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

try:
    from selenium.webdriver.chrome.service import Service

    _original_init = Service.__init__

    def _service_init(self, executable_path=None, *args, **kwargs):
        if executable_path == "chromedriver":
            executable_path = None
        return _original_init(self, executable_path, *args, **kwargs)

    Service.__init__ = _service_init
    log.info("Selenium bootstrap loaded")
except Exception as exc:
    log.debug("Selenium bootstrap skipped: %s", exc)

# Patch only the final offer-discovery step. The existing Google login,
# credentials, 2FA and device automation remain unchanged.
try:
    import google_automation as _google_automation
    from offer_extractor import _navigate_google_one_strict

    _google_automation._navigate_google_one = _navigate_google_one_strict
    log.info("Strict Google One offer extractor loaded")
except Exception as exc:
    # Do not prevent the bot from starting if the optional extractor cannot
    # load. The original automation remains available as a fallback.
    log.warning("Strict offer extractor not loaded: %s", exc)
