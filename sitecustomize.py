"""Small runtime bootstrap for Selenium on hosted Linux services.

The application historically constructs Chrome's Service with the literal
string ``chromedriver``. On Render that file is not on PATH, and Selenium 4
therefore treats the string as an explicit path and rejects it before its
built-in Selenium Manager can run.

This module deliberately does not change the application's browser options
or automation flow. It only converts that invalid relative driver path into
an unspecified Service so Selenium Manager can locate/install the matching
browser driver.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

try:
    from selenium.webdriver.chrome.service import Service

    _original_init = Service.__init__

    def _service_init(self, executable_path=None, *args, **kwargs):
        # The existing project passes the literal string "chromedriver".
        # If it is not an actual executable path, let Selenium Manager handle
        # driver discovery instead of making Selenium validate that string.
        if executable_path == "chromedriver":
            executable_path = None
        return _original_init(self, executable_path, *args, **kwargs)

    Service.__init__ = _service_init
    log.info("Selenium bootstrap loaded: invalid relative ChromeDriver path will use Selenium Manager")
except Exception as exc:
    # Never prevent the Telegram bot from starting just because Selenium is
    # unavailable during Python startup.
    log.debug("Selenium bootstrap skipped: %s", exc)
