"""E2E tests for the login flow (/login).

Covers:
  - Happy path: email + password → redirect to dashboard
  - MFA challenge flow (TOTP)
  - Error states: wrong password, disabled button
  - "Back to login" from MFA screen
"""
from __future__ import annotations

import pytest
import requests
from playwright.sync_api import Page, expect

from conftest import BASE_URL, fetch_verification_code

# ---------------------------------------------------------------------------
# Fixtures: pre-create a verified user for login tests
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def verified_user(request):
    """Create and verify a test account once per module, yield (email, password)."""
    import random, string, time, re
    tag = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    email = f"login-test-{tag}@example.com"
    password = "TestPassword123!"

    # Clear MailHog before registering
    try:
        requests.delete(f"http://localhost:8025/api/v2/messages", timeout=5)
    except Exception:
        pass

    # Register
    r = requests.post(f"{BASE_URL}/auth/register", json={"email": email, "password": password})
    assert r.status_code in (200, 201), f"Register failed: {r.text}"

    # Get verification code
    deadline = time.time() + 20
    code = None
    while time.time() < deadline:
        msgs = requests.get("http://localhost:8025/api/v2/messages", timeout=5).json()
        for msg in msgs.get("items", []):
            recipients = [r2["Mailbox"] + "@" + r2["Domain"] for r2 in msg["To"]]
            if email in recipients:
                match = re.search(r"\b(\d{6})\b", msg["Content"]["Body"])
                if match:
                    code = match.group(1)
                    break
        if code:
            break
        time.sleep(0.8)
    assert code, "Verification email not received"

    # Verify
    r = requests.post(f"{BASE_URL}/auth/verify-email", json={"code": code})
    assert r.status_code in (200, 204), f"Verify failed: {r.text}"

    return email, password


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestLoginHappyPath:
    def test_login_redirects_to_dashboard(self, page: Page, verified_user):
        email, password = verified_user
        page.goto(f"{BASE_URL}/login")
        expect(page).to_have_title("Iniciar sesión — antcrew")

        page.get_by_label("Email").fill(email)
        page.get_by_label("Contraseña").fill(password)
        page.get_by_role("button", name="Iniciar sesión →").click()

        expect(page).to_have_url(f"{BASE_URL}/dashboard", timeout=10000)

    def test_page_title_and_brand(self, page: Page):
        page.goto(f"{BASE_URL}/login")
        expect(page.get_by_text("antcrew")).to_be_visible()
        expect(page.get_by_text("AI pipeline platform")).to_be_visible()
        expect(page.get_by_role("link", name="Crear cuenta")).to_be_visible()

    def test_enter_key_submits_login(self, page: Page, verified_user):
        email, password = verified_user
        page.goto(f"{BASE_URL}/login")
        page.get_by_label("Email").fill(email)
        page.get_by_label("Contraseña").fill(password)
        page.get_by_label("Contraseña").press("Enter")
        expect(page).to_have_url(f"{BASE_URL}/dashboard", timeout=10000)


class TestLoginValidation:
    def test_button_disabled_without_email(self, page: Page):
        page.goto(f"{BASE_URL}/login")
        page.get_by_label("Contraseña").fill("somepassword")
        btn = page.get_by_role("button", name="Iniciar sesión →")
        expect(btn).to_be_disabled()

    def test_button_disabled_without_password(self, page: Page):
        page.goto(f"{BASE_URL}/login")
        page.get_by_label("Email").fill("test@example.com")
        btn = page.get_by_role("button", name="Iniciar sesión →")
        expect(btn).to_be_disabled()

    def test_wrong_password_shows_error(self, page: Page, verified_user):
        email, _ = verified_user
        page.goto(f"{BASE_URL}/login")
        page.get_by_label("Email").fill(email)
        page.get_by_label("Contraseña").fill("WrongPassword999!")
        page.get_by_role("button", name="Iniciar sesión →").click()
        # Either an Alpine x-text error or any visible error text
        error = page.locator("[x-text='loginError']")
        expect(error).to_be_visible(timeout=5000)

    def test_unknown_email_shows_error(self, page: Page):
        page.goto(f"{BASE_URL}/login")
        page.get_by_label("Email").fill("nobody@notexist.io")
        page.get_by_label("Contraseña").fill("TestPassword123!")
        page.get_by_role("button", name="Iniciar sesión →").click()
        error = page.locator("[x-text='loginError']")
        expect(error).to_be_visible(timeout=5000)


class TestMFAFlow:
    """MFA screen smoke tests (no real TOTP — only UI-level checks)."""

    def test_mfa_screen_hidden_on_load(self, page: Page):
        page.goto(f"{BASE_URL}/login")
        mfa_heading = page.get_by_text("Autenticación de dos factores")
        expect(mfa_heading).not_to_be_visible()

    def test_mfa_verify_disabled_until_6_digits(self, page: Page, monkeypatch):
        """Manually set state to 'mfa' via evaluate and check button behavior."""
        page.goto(f"{BASE_URL}/login")
        page.evaluate("document.querySelector('[x-data]').__x.$data.state = 'mfa'")
        expect(page.get_by_text("Autenticación de dos factores")).to_be_visible(timeout=3000)
        verify_btn = page.get_by_role("button", name="Verificar →")
        expect(verify_btn).to_be_disabled()
        page.get_by_placeholder("000000").fill("12345")
        expect(verify_btn).to_be_disabled()
        page.get_by_placeholder("000000").fill("123456")
        expect(verify_btn).to_be_enabled()

    def test_mfa_back_button_returns_to_login(self, page: Page):
        page.goto(f"{BASE_URL}/login")
        page.evaluate("document.querySelector('[x-data]').__x.$data.state = 'mfa'")
        expect(page.get_by_text("Autenticación de dos factores")).to_be_visible(timeout=3000)
        page.get_by_role("button", name="← Volver al login").click()
        expect(page.get_by_text("Inicia sesión")).to_be_visible(timeout=3000)
        expect(page.get_by_text("Autenticación de dos factores")).not_to_be_visible()

    def test_create_account_link_goes_to_onboard(self, page: Page):
        page.goto(f"{BASE_URL}/login")
        page.get_by_role("link", name="Crear cuenta").click()
        expect(page).to_have_url(f"{BASE_URL}/onboard", timeout=5000)
