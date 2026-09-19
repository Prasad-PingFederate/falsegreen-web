"""Home Page Object for falsegreen-web."""

from __future__ import annotations
from playwright.sync_api import Page, Locator
from .base_page import BasePage

class HomePage(BasePage):
    def __init__(self, page: Page, base_url: str):
        super().__init__(page, base_url)
        self.hero_heading = page.locator("h1")
        self.hero_lede = page.locator("p.lede")
        self.repo_input = page.locator('.scan-form input[name="repo"]')
        self.scan_button = page.locator('.scan-form button[type="submit"]')
        self.alert_box = page.locator(".alert")
        self.waitlist_email_input = page.locator('input[type="email"], input[name="email"]')
        self.waitlist_submit_button = page.locator('form[action*="waitlist"] button[type="submit"]')

    def open(self):
        self.navigate("/")

    def scan_repository(self, repo_url: str):
        self.repo_input.fill(repo_url)
        self.scan_button.click()
        self.page.wait_for_load_state("networkidle")

    def get_alert_text(self) -> str:
        return self.alert_box.inner_text() if self.alert_box.is_visible() else ""
