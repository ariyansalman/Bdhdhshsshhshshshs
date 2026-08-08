"""
Test script: verifies that the 6-digit TOTP code is entered AND that the
account is confirmed as logged in afterwards.

Run with:  python test_totp.py
"""

import logging
import sys
from device_simulator import create_device_profile
from google_automation import check_gemini_offer, GoogleAutomationError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("test_totp")

# ── Test account credentials ──────────────────────────────────────────────────
EMAIL       = "tajishag1@gmail.com"
PASSWORD    = "0112469760"
TOTP_SECRET = "oakrsdmqu5gq6lk4fkho3n326fw2hwdd"

# ── Tracked checks ────────────────────────────────────────────────────────────
totp_code_entered  = False   # 6-digit code was typed into the field
totp_submitted     = False   # form was submitted after entering the code
account_logged_in  = False   # Google confirmed login after TOTP


def progress_callback(msg: str, screenshot_bytes=None):
    global totp_code_entered, totp_submitted, account_logged_in

    logger.info("STEP: %s", msg)
    msg_lower = msg.lower()

    # Check 1 – 6-digit code was entered into the TOTP field
    if "entering totp code" in msg_lower or "totp code" in msg_lower:
        totp_code_entered = True

    # Check 2 – TOTP form was submitted (Google received the code)
    if "totp submitted" in msg_lower or "step 4 — totp submitted" in msg_lower:
        totp_submitted = True

    # Check 3 – Account is confirmed logged in AFTER the TOTP step
    if (
        "logged in successfully" in msg_lower
        or ("step 5" in msg_lower and "success" in msg_lower)
        or "myaccount.google.com" in msg_lower
    ):
        account_logged_in = True


def main():
    logger.info("=" * 60)
    logger.info("TEST: 6-digit TOTP entry + login confirmation")
    logger.info("Account : %s", EMAIL)
    logger.info("=" * 60)

    device = create_device_profile()
    logger.info("Device  : %s", device.summary().replace("\n", " | "))

    try:
        offer_link = check_gemini_offer(
            email=EMAIL,
            password=PASSWORD,
            device=device,
            totp_secret=TOTP_SECRET,
            progress_callback=progress_callback,
        )
    except GoogleAutomationError as exc:
        logger.error("Automation error: %s", exc)
        offer_link = None
    except Exception as exc:
        logger.exception("Unexpected error: %s", exc)
        offer_link = None

    # ── Print results ─────────────────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("RESULTS")
    logger.info(
        "  1. 6-digit code entered into field : %s",
        "PASS ✅" if totp_code_entered else "FAIL ❌",
    )
    logger.info(
        "  2. TOTP form submitted to Google   : %s",
        "PASS ✅" if totp_submitted else "FAIL ❌",
    )
    logger.info(
        "  3. Account logged in after TOTP    : %s",
        "PASS ✅" if account_logged_in else "FAIL ❌",
    )
    logger.info(
        "  4. Offer link found                : %s",
        offer_link or "None (offer may not be available on this account)",
    )
    logger.info("=" * 60)

    all_passed = totp_code_entered and totp_submitted and account_logged_in

    if all_passed:
        logger.info(
            "✅ ALL CHECKS PASSED — "
            "code entered, submitted, and account confirmed logged in."
        )
        sys.exit(0)
    else:
        failed = []
        if not totp_code_entered:
            failed.append("6-digit code was never entered")
        if not totp_submitted:
            failed.append("TOTP form was not submitted")
        if not account_logged_in:
            failed.append("account login was NOT confirmed after TOTP")
        logger.error("❌ TEST FAILED — %s", "; ".join(failed))
        sys.exit(1)


if __name__ == "__main__":
    main()
