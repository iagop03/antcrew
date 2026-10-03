"""Shared fixtures for E2E tests.

Prerequisites (local):
  1. App running: uvicorn app.main:app --port 8000
     with SMTP_HOST=localhost SMTP_PORT=1025 SMTP_TLS=none
  2. MailHog running: docker run -p 1025:1025 -p 8025:8025 mailhog/mailhog

In CI both services are started automatically by the GitHub Actions workflow.
"""
from __future__ import annotations

import random
import string
import time
from typing import Optional

import pytest
import requests
from playwright.sync_api import Page, expect

BASE_URL = "http://localhost:8000"
MAILHOG_API = "http://localhost:8025/api/v2"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _random_email() -> str:
    tag = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    return f"e2e-{tag}@example.com"


def _random_slug() -> str:
    tag = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    return f"ws-{tag}"


def fetch_verification_code(email: str, timeout: float = 15.0) -> str:
    """Poll MailHog until the verification email for *email* arrives, then return the 6-digit code."""
    import re
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = requests.get(f"{MAILHOG_API}/messages", timeout=5)
        resp.raise_for_status()
        for msg in resp.json().get("items", []):
            # Check recipient
            recipients = [r["Mailbox"] + "@" + r["Domain"]
                          for r in msg["To"]]
            if email not in recipients:
                continue
            # Extract 6-digit code from body
            body = msg["Content"]["Body"]
            match = re.search(r"\b(\d{6})\b", body)
            if match:
                return match.group(1)
        time.sleep(0.8)
    raise TimeoutError(f"Verification email for {email} not received within {timeout}s")


def delete_mailhog_messages() -> None:
    """Clear all MailHog messages before a test to avoid stale emails."""
    try:
        requests.delete(f"{MAILHOG_API}/messages", timeout=5)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    return {**browser_context_args, "base_url": BASE_URL}


@pytest.fixture
def clean_mailbox():
    """Wipe MailHog before each test that uses email."""
    delete_mailhog_messages()
    yield


@pytest.fixture
def new_email() -> str:
    return _random_email()


@pytest.fixture
def new_slug() -> str:
    return _random_slug()
