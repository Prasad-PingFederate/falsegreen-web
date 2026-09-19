"""Report Page Object for falsegreen-web."""

from __future__ import annotations
from playwright.sync_api import Page, Locator
from .base_page import BasePage

class ReportPage(BasePage):
    def __init__(self, page: Page, base_url: str):
        super().__init__(page, base_url)
        self.repo_title = page.locator(".repo-name")
        self.score_value = page.locator(".score-value, .score, .score-number")
        self.findings_section = page.locator(".findings, .finding-card, .finding")
        self.sarif_link = page.locator('a[href*="/sarif"]')
        self.badge_image = page.locator('img[src*="/badge/"]')

    def open_report(self, owner: str, name: str):
        self.navigate(f"/r/{owner}/{name}")

    def get_sarif_href(self) -> str | None:
        if self.sarif_link.is_visible():
            return self.sarif_link.get_attribute("href")
        return None
