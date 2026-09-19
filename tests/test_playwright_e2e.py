"""Comprehensive E2E Playwright test suite for falsegreen-web application."""

from __future__ import annotations

import socket
import threading
import time
from typing import Generator

import pytest
import uvicorn
from playwright.sync_api import Page, expect

from app.main import app
from app import store
from tests.pages.home_page import HomePage
from tests.pages.report_page import ReportPage


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def server_url() -> Generator[str, None, None]:
    port = find_free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Wait for server readiness
    for _ in range(50):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                break
        except OSError:
            time.sleep(0.05)

    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=1.0)


# ==============================================================================
# Homepage Tests
# ==============================================================================

def test_homepage_structure_and_branding(page: Page, server_url: str):
    home = HomePage(page, server_url)
    home.open()

    assert "falsegreen" in home.title
    expect(home.hero_heading).to_be_visible()
    expect(home.repo_input).to_be_visible()
    expect(home.scan_button).to_be_visible()
    assert "Scan" in home.scan_button.inner_text()


def test_homepage_invalid_url_validation_alert(page: Page, server_url: str):
    home = HomePage(page, server_url)
    home.open()

    home.scan_repository("https://invalid-host.com/not/a/github/repo")
    alert_text = home.get_alert_text()
    assert len(alert_text) > 0
    assert "GitHub" in alert_text or "valid" in alert_text.lower()


# ==============================================================================
# Report Page & SARIF Export Tests
# ==============================================================================

def test_report_page_findings_and_sarif_export(page: Page, server_url: str):
    slug = "testorg/demo-project"
    sample_payload = {
        "score": 82,
        "grade": "B",
        "tests_found": 50,
        "tests_can_fail": 41,
        "tests_cannot_fail": 9,
        "counts": {"critical": 1, "high": 1, "medium": 0, "low": 0},
        "findings": [
            {
                "rule": "mock-assertion-typo",
                "severity": "critical",
                "title": "Mock assertion typo",
                "detail": "mock.assert_called_once without ()",
                "file": "tests/unit/test_service.py",
                "line": 42,
                "test": "test_process_data",
                "snippet": "mock_mailer.assert_called_once",
                "explanation": "Attribute access without calling the assertion function.",
                "suggested_fix": "mock_mailer.assert_called_once()",
            },
            {
                "rule": "constant-condition-trap",
                "severity": "high",
                "title": "Constant condition trap",
                "detail": "assert status == 200 or 201 evaluates to 201 (truthy)",
                "file": "tests/unit/test_api.py",
                "line": 88,
                "test": "test_status_code",
                "snippet": "assert response.status_code == 200 or 201",
                "explanation": "OR operand 201 is always truthy in Python.",
                "suggested_fix": "assert response.status_code in (200, 201)",
            }
        ],
        "headline": "2 in 50 tests cannot fail.",
    }

    store.save_scan(
        slug=slug,
        owner="testorg",
        repo="demo-project",
        score=82,
        grade="B",
        payload=sample_payload,
    )

    report = ReportPage(page, server_url)
    report.open_report("testorg", "demo-project")

    content = page.content()
    assert "testorg/demo-project" in content
    assert "82" in content
    assert "Mock assertion typo" in content or "mock-assertion-typo" in content
    assert "tests/unit/test_service.py" in content
    assert "Constant condition trap" in content or "constant-condition-trap" in content

    sarif_url = report.get_sarif_href()
    assert sarif_url is not None
    assert "/api/scan/testorg/demo-project/sarif" in sarif_url


def test_interactive_severity_filtering(page: Page, server_url: str):
    page.goto(f"{server_url}/r/testorg/demo-project")
    page.wait_for_load_state("networkidle")

    # Click Critical filter button
    page.click('button[data-filter="critical"]')
    critical_items = page.locator('.finding-item[data-severity="critical"]')
    expect(critical_items.first).to_be_visible()

    # Click All button to restore all items
    page.click('button[data-filter="all"]')
    expect(page.locator('.finding-item[data-severity="critical"]').first).to_be_visible()
    expect(page.locator('.finding-item[data-severity="high"]').first).to_be_visible()


def test_suggested_fix_and_badge_copy_ui(page: Page, server_url: str):
    page.goto(f"{server_url}/r/testorg/demo-project")
    page.wait_for_load_state("networkidle")

    copy_fix_btn = page.locator(".btn-copy-fix").first
    expect(copy_fix_btn).to_be_visible()
    assert "Copy Fix" in copy_fix_btn.inner_text()

    copy_badge_btn = page.locator(".btn-copy-badge")
    expect(copy_badge_btn).to_be_visible()
    assert "Copy Markdown" in copy_badge_btn.inner_text()


# ==============================================================================
# Playground, Rules, Badges, Pricing & Docs Page Tests
# ==============================================================================

def test_playground_interactive_analysis(page: Page, server_url: str):
    page.goto(f"{server_url}/playground")
    page.wait_for_load_state("networkidle")

    assert "Playground" in page.title()
    textarea = page.locator("#code-input")
    expect(textarea).to_be_visible()

    # Click analyze button and check results rendered
    page.click('button:has-text("Analyze Code")')
    page.wait_for_timeout(500)
    expect(page.locator("#results-content")).to_be_visible()


def test_rules_directory_filtering(page: Page, server_url: str):
    page.goto(f"{server_url}/rules")
    page.wait_for_load_state("networkidle")

    assert "Rules" in page.title()
    expect(page.locator(".rule-card").first).to_be_visible()

    # Filter by JavaScript
    page.click('button:has-text("JavaScript & Playwright")')
    js_card = page.locator('.rule-card[data-lang="javascript"]')
    expect(js_card.first).to_be_visible()


def test_badges_builder_page(page: Page, server_url: str):
    page.goto(f"{server_url}/badges")
    page.wait_for_load_state("networkidle")

    assert "Badge" in page.title()
    expect(page.locator("#live-badge-preview")).to_be_visible()
    expect(page.locator("#badge-md-code")).to_be_visible()


def test_pricing_page_and_plans(page: Page, server_url: str):
    page.goto(f"{server_url}/pricing")
    page.wait_for_load_state("networkidle")

    assert "Pricing" in page.title()
    expect(page.locator(".pricing-card").first).to_be_visible()
    assert "Community" in page.content()
    assert "Pro Team" in page.content()
    assert "Enterprise" in page.content()


def test_docs_page_quickstart(page: Page, server_url: str):
    page.goto(f"{server_url}/docs")
    page.wait_for_load_state("networkidle")

    assert "Documentation" in page.title()
    assert "CLI Quickstart" in page.content()
    assert "GitHub Actions" in page.content()


# ==============================================================================
# Responsive Layout Tests
# ==============================================================================

def test_responsive_layout_matrix(page: Page, server_url: str):
    viewports = [
        {"name": "desktop", "width": 1440, "height": 900},
        {"name": "tablet", "width": 768, "height": 1024},
        {"name": "mobile", "width": 375, "height": 667},
    ]

    home = HomePage(page, server_url)
    for vp in viewports:
        page.set_viewport_size({"width": vp["width"], "height": vp["height"]})
        home.open()

        expect(home.hero_heading).to_be_visible()
        expect(home.repo_input).to_be_visible()
        expect(home.scan_button).to_be_visible()

        scroll_width = page.evaluate("() => document.documentElement.scrollWidth")
        client_width = page.evaluate("() => document.documentElement.clientWidth")
        assert scroll_width <= client_width + 10, f"Overflow detected on {vp['name']}"
