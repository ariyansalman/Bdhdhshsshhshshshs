"""Runtime browser-driver bootstrap.

Loaded automatically by Python's site module before the application starts.
It makes an installed/system ChromeDriver discoverable to the existing
Selenium code, and falls back to webdriver-manager when the host does not
provide one on PATH.
"""

import logging
import os
import shutil

log = logging.getLogger("sitecustomize")
_original_which = shutil.which


def _find_driver():
    # Normal PATH lookup first.
    path = _original_which("chromedriver")
    if path and os.path.isfile(path):
        return path

    # Common locations used by Debian/Ubuntu/Render-style environments.
    for path in (
        "/usr/bin/chromedriver",
        "/usr/local/bin/chromedriver",
        "/opt/chromedriver/chromedriver",
    ):
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path

    # requirements.txt already contains webdriver-manager.
    try:
        from webdriver_manager.chrome import ChromeDriverManager

        path = ChromeDriverManager().install()
        if path and os.path.isfile(path):
            return path
    except Exception as exc:
        log.warning("Could not bootstrap ChromeDriver: %s", exc)

    return None


_driver_path = _find_driver()

if _driver_path:
    log.info("ChromeDriver ready: %s", _driver_path)

    def _which(command, mode=os.F_OK, path=None):
        if command in ("chromedriver", "chromedriver.exe"):
            return _driver_path
        return _original_which(command, mode=mode, path=path)

    shutil.which = _which
else:
    log.warning("ChromeDriver was not found or downloaded; Selenium may fail to start Chrome.")
