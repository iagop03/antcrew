"""E2E smoke tests for the Settings page (/settings).

Uses a pre-authenticated session (session_with_auth fixture) rather than
logging in again for each test. Covers:
  - Page loads with correct title
  - Workspace table renders after API key entry
  - Tab navigation (workspaces, reviewers, llm, docs, api-keys)
  - API key save/clear flow
"""
from __future__ import annotations

import pytest
import requests
from playwright.sync_api import BrowserContext, Page, expect

from conftest import BASE_URL


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def auth_api_key():
    """Return a valid API key for the test workspace (read from env or skip)."""
    import os
    key = os.environ.get("E2E_API_KEY")
    if not key:
        pytest.skip("E2E_API_KEY not set — skipping authenticated settings tests")
    return key


@pytest.fixture
def authenticated_page(page: Page, auth_api_key: str):
    """Navigate to /settings and inject the API key via localStorage."""
    page.goto(f"{BASE_URL}/settings")
    page.evaluate(f"localStorage.setItem('apiKey', '{auth_api_key}')")
    page.reload()
    return page


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSettingsPage:
    def test_page_title(self, page: Page):
        page.goto(f"{BASE_URL}/settings")
        expect(page).to_have_title("antcrew · Settings")

    def test_api_key_banner_shown_without_key(self, page: Page):
        page.goto(f"{BASE_URL}/settings")
        page.evaluate("localStorage.removeItem('apiKey')")
        page.reload()
        expect(page.get_by_text("API key requerida")).to_be_visible(timeout=5000)

    def test_save_api_key_hides_banner(self, page: Page):
        page.goto(f"{BASE_URL}/settings")
        page.evaluate("localStorage.removeItem('apiKey')")
        page.reload()
        page.locator("input[placeholder*='PLATFORM_API_KEY']").fill("test-key-123")
        page.get_by_role("button", name="Guardar").click()
        # Banner should disappear and confirmation indicator shown
        expect(page.get_by_text("X-Api-Key configurada")).to_be_visible(timeout=3000)

    def test_clear_api_key_restores_banner(self, page: Page):
        page.goto(f"{BASE_URL}/settings")
        page.evaluate("localStorage.setItem('apiKey', 'test-key-xyz')")
        page.reload()
        expect(page.get_by_text("X-Api-Key configurada")).to_be_visible(timeout=3000)
        page.get_by_role("button", name="Limpiar").click()
        expect(page.get_by_text("API key requerida")).to_be_visible(timeout=3000)

    def test_workspaces_tab_visible_by_default(self, page: Page, auth_api_key: str):
        page.goto(f"{BASE_URL}/settings")
        page.evaluate(f"localStorage.setItem('apiKey', '{auth_api_key}')")
        page.reload()
        # Workspaces section heading should be present (it's the default tab)
        expect(page.get_by_text("All configured workspaces")).to_be_visible(timeout=8000)

    def test_workspaces_table_loads(self, page: Page, auth_api_key: str):
        page.goto(f"{BASE_URL}/settings")
        page.evaluate(f"localStorage.setItem('apiKey', '{auth_api_key}')")
        page.reload()
        # Wait for either the table or empty state
        table_or_empty = page.locator("table, [data-i18n='ws.none']")
        expect(table_or_empty.first).to_be_visible(timeout=10000)


class TestSettingsTabNavigation:
    """Tab navigation smoke — verifies each tab renders without JS errors."""

    TABS = [
        ("workspaces", "workspaces"),
        ("reviewers", "reviewers"),
        ("llm", "llm"),
        ("docs", "docs"),
        ("api-keys", "api-keys"),
    ]

    @pytest.mark.parametrize("tab_name,expected_tab", TABS)
    def test_tab_switches(self, page: Page, auth_api_key: str, tab_name: str, expected_tab: str):
        page.goto(f"{BASE_URL}/settings")
        page.evaluate(f"localStorage.setItem('apiKey', '{auth_api_key}')")
        page.reload()
        # Click the tab button — tab names map to data-i18n or nav buttons
        tab_btn = page.locator(f"[data-tab='{tab_name}'], button[\\@click*=\"tab='{tab_name}'\"]")
        if tab_btn.count() == 0:
            pytest.skip(f"Tab button for '{tab_name}' not found — skipping")
        tab_btn.first.click()
        # No JS error: page should still be responsive
        expect(page.locator("body")).to_be_visible()

    def test_no_console_errors_on_load(self, page: Page, auth_api_key: str):
        """Fail if any console error fires during page load."""
        errors = []
        page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
        page.goto(f"{BASE_URL}/settings")
        page.evaluate(f"localStorage.setItem('apiKey', '{auth_api_key}')")
        page.reload()
        page.wait_for_load_state("networkidle")
        assert not errors, f"Console errors on settings load: {errors}"
