"""E2E tests for the full onboarding flow (/onboard).

Covers:
  Step 1 — Create account (email + password)
  Step 2 — Email verification (code from MailHog)
  Step 3 — Workspace creation (name + slug)
  Step 4 — Profile (use_case, team_size)
  Step 5 — LLM mode selection
  Step 6 — Done screen + redirect to dashboard
"""
from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from conftest import BASE_URL, fetch_verification_code


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fill_and_continue(page: Page, email: str, password: str) -> None:
    page.get_by_label("Email").fill(email)
    page.get_by_label("Contraseña").fill(password)
    page.get_by_role("button", name="Continuar →").click()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestOnboardingHappyPath:
    """Full 6-step onboarding with email verification via MailHog."""

    def test_full_flow(self, page: Page, clean_mailbox, new_email, new_slug):
        password = "TestPassword123!"
        ws_name = "E2E Workspace"

        # ── Step 1: create account ────────────────────────────────────────
        page.goto(f"{BASE_URL}/onboard")
        expect(page).to_have_title("Crear cuenta — antcrew")

        _fill_and_continue(page, new_email, password)

        # ── Step 2: email verification ────────────────────────────────────
        expect(page.get_by_text("Verifica tu email")).to_be_visible(timeout=5000)
        expect(page.get_by_text(new_email)).to_be_visible()

        code = fetch_verification_code(new_email)
        page.get_by_placeholder("123456").fill(code)
        page.get_by_role("button", name="Verificar →").click()

        # ── Step 3: workspace ─────────────────────────────────────────────
        expect(page.get_by_text("Tu workspace")).to_be_visible(timeout=5000)

        page.get_by_label("Nombre del workspace").fill(ws_name)
        page.get_by_placeholder("acme-corp").fill(new_slug)
        page.get_by_role("button", name="Continuar →").click()

        # ── Step 4: profile ───────────────────────────────────────────────
        expect(page.get_by_text("Cuéntanos un poco")).to_be_visible(timeout=5000)

        page.get_by_label("Desarrollo de software").click()
        page.get_by_role("button", name="2-5").click()
        page.get_by_role("button", name="Continuar →").click()

        # ── Step 5: LLM mode ──────────────────────────────────────────────
        expect(page.get_by_text("Configuración LLM")).to_be_visible(timeout=5000)

        page.get_by_label("Claves de la plataforma").click()
        page.get_by_role("button", name="Continuar →").click()

        # ── Step 6: done ──────────────────────────────────────────────────
        expect(page.get_by_text("¡Todo listo!")).to_be_visible(timeout=8000)
        expect(page.get_by_text(new_email)).to_be_visible()
        expect(page.get_by_text(ws_name)).to_be_visible()

        # Go to dashboard
        page.get_by_role("link", name="Ir al dashboard").click()
        expect(page).to_have_url(f"{BASE_URL}/dashboard", timeout=8000)


class TestOnboardingValidation:
    """Input validation — buttons disabled and errors shown correctly."""

    def test_continue_disabled_without_email(self, page: Page):
        page.goto(f"{BASE_URL}/onboard")
        btn = page.get_by_role("button", name="Continuar →")
        expect(btn).to_be_disabled()

    def test_continue_disabled_short_password(self, page: Page, new_email):
        page.goto(f"{BASE_URL}/onboard")
        page.get_by_label("Email").fill(new_email)
        page.get_by_label("Contraseña").fill("short")
        btn = page.get_by_role("button", name="Continuar →")
        expect(btn).to_be_disabled()

    def test_duplicate_email_shows_error(self, page: Page, clean_mailbox):
        """Registering twice with the same email shows a 409 message."""
        import requests as _req
        email = "duplicate@example.com"
        password = "TestPassword123!"
        # Pre-register via API so we can test the duplicate case without going
        # through the full onboarding again
        _req.post(
            f"{BASE_URL}/auth/register",
            json={"email": email, "password": password},
        )
        page.goto(f"{BASE_URL}/onboard")
        _fill_and_continue(page, email, password)
        expect(page.get_by_text("Ya existe una cuenta")).to_be_visible(timeout=5000)

    def test_verify_disabled_until_6_digits(self, page: Page, clean_mailbox, new_email):
        """Verify button stays disabled until exactly 6 digits are typed."""
        page.goto(f"{BASE_URL}/onboard")
        _fill_and_continue(page, new_email, "TestPassword123!")
        expect(page.get_by_text("Verifica tu email")).to_be_visible(timeout=5000)
        verify_btn = page.get_by_role("button", name="Verificar →")
        expect(verify_btn).to_be_disabled()
        page.get_by_placeholder("123456").fill("12345")
        expect(verify_btn).to_be_disabled()
        page.get_by_placeholder("123456").fill("123456")
        expect(verify_btn).to_be_enabled()

    def test_wrong_code_shows_error(self, page: Page, clean_mailbox, new_email):
        page.goto(f"{BASE_URL}/onboard")
        _fill_and_continue(page, new_email, "TestPassword123!")
        expect(page.get_by_text("Verifica tu email")).to_be_visible(timeout=5000)
        page.get_by_placeholder("123456").fill("000000")
        page.get_by_role("button", name="Verificar →").click()
        expect(page.locator("[x-text='step2Error']")).to_be_visible(timeout=5000)

    def test_invalid_slug_shows_error(self, page: Page, clean_mailbox, new_email):
        """Slug with spaces or uppercase is rejected client-side."""
        page.goto(f"{BASE_URL}/onboard")
        _fill_and_continue(page, new_email, "TestPassword123!")
        expect(page.get_by_text("Verifica tu email")).to_be_visible(timeout=5000)
        code = fetch_verification_code(new_email)
        page.get_by_placeholder("123456").fill(code)
        page.get_by_role("button", name="Verificar →").click()
        expect(page.get_by_text("Tu workspace")).to_be_visible(timeout=5000)
        page.get_by_label("Nombre del workspace").fill("My WS")
        page.get_by_placeholder("acme-corp").fill("Has SPACES!")
        page.get_by_role("button", name="Continuar →").click()
        expect(page.get_by_text("Solo minúsculas")).to_be_visible(timeout=3000)

    def test_skip_profile_step(self, page: Page, clean_mailbox, new_email, new_slug):
        """Step 4 can be skipped — 'Saltar este paso' advances to step 5."""
        page.goto(f"{BASE_URL}/onboard")
        _fill_and_continue(page, new_email, "TestPassword123!")
        expect(page.get_by_text("Verifica tu email")).to_be_visible(timeout=5000)
        code = fetch_verification_code(new_email)
        page.get_by_placeholder("123456").fill(code)
        page.get_by_role("button", name="Verificar →").click()
        expect(page.get_by_text("Tu workspace")).to_be_visible(timeout=5000)
        page.get_by_label("Nombre del workspace").fill("Skip WS")
        page.get_by_placeholder("acme-corp").fill(new_slug)
        page.get_by_role("button", name="Continuar →").click()
        expect(page.get_by_text("Cuéntanos un poco")).to_be_visible(timeout=5000)
        page.get_by_role("button", name="Saltar este paso").click()
        expect(page.get_by_text("Configuración LLM")).to_be_visible(timeout=5000)

    def test_already_authenticated_redirects(self, page: Page, clean_mailbox, new_email, new_slug):
        """If user is fully onboarded, /onboard redirects to /dashboard."""
        # Complete onboarding first
        password = "TestPassword123!"
        page.goto(f"{BASE_URL}/onboard")
        _fill_and_continue(page, new_email, password)
        expect(page.get_by_text("Verifica tu email")).to_be_visible(timeout=5000)
        code = fetch_verification_code(new_email)
        page.get_by_placeholder("123456").fill(code)
        page.get_by_role("button", name="Verificar →").click()
        expect(page.get_by_text("Tu workspace")).to_be_visible(timeout=5000)
        page.get_by_label("Nombre del workspace").fill("Redirect WS")
        page.get_by_placeholder("acme-corp").fill(new_slug)
        page.get_by_role("button", name="Continuar →").click()
        page.get_by_role("button", name="Saltar este paso").click()
        page.get_by_role("button", name="Continuar →").click()
        expect(page.get_by_text("¡Todo listo!")).to_be_visible(timeout=8000)
        # Now visiting /onboard again should redirect
        page.goto(f"{BASE_URL}/onboard")
        expect(page).to_have_url(f"{BASE_URL}/dashboard", timeout=8000)
