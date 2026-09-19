"""Base Page Object for Playwright tests."""

from __future__ import annotations
from playwright.sync_api import Page, Locator

class BasePage:
    def __init__(self, page: Page, base_url: str):
        self.page = page
        self.base_url = base_url.rstrip("/")

    def navigate(self, path: str = ""):
        url = f"{self.base_url}/{path.lstrip('/')}"
        self.page.goto(url)
        self.page.wait_for_load_state("networkidle")

    @property
    def title(self) -> str:
        return self.page.title()
