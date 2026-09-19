"""End-to-end browser automation tests for falsegreen-web using Playwright."""

from __future__ import annotations

import socket
import threading
import time
from typing import Generator

import pytest
import uvicorn
from playwright.sync_api import Page, sync_playwright

from app.main import app
from app import store


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

    # Wait for server to boot
    for _ in range(50):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                break
        except OSError:
            time.sleep(0.05)

    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=1.0)


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        yield browser
        browser.close()


@pytest.fixture
def page(browser) -> Generator[Page, None, None]:
    context = browser.new_context()
    page = context.new_page()
    yield page
    context.close()


def test_home_page_rendering(page: Page, server_url: str):
    page.goto(server_url)
    assert "falsegreen" in page.title()

    h1_text = page.locator("h1").inner_text()
    assert "Your test suite is green" in h1_text

    input_elem = page.locator('.scan-form input[name="repo"]')
    assert input_elem.is_visible()

    submit_btn = page.locator('.scan-form button[type="submit"]')
    assert submit_btn.is_visible()
    assert "Scan" in submit_btn.inner_text()


def test_home_page_invalid_repo_error(page: Page, server_url: str):
    page.goto(server_url)
    page.fill('.scan-form input[name="repo"]', "invalid-format-repo-name")
    page.click('.scan-form button[type="submit"]')

    page.wait_for_load_state("networkidle")
    alert = page.locator(".alert")
    assert alert.is_visible()
    assert "GitHub" in alert.inner_text() or "valid" in alert.inner_text() or "format" in alert.inner_text().lower()


def test_report_page_ui_and_sarif_export(page: Page, server_url: str):
    # Seed mock scan data into store
    sample_record = {
        "slug": "sampleorg/awesome-repo",
        "owner": "sampleorg",
        "repo": "awesome-repo",
        "score": 75,
        "grade": "C",
        "scanned_at": int(time.time()),
        "payload": {
            "score": 75,
            "grade": "C",
            "tests_found": 20,
            "tests_can_fail": 15,
            "tests_cannot_fail": 5,
            "counts": {"critical": 1, "high": 2, "medium": 1, "low": 1},
            "findings": [
                {
                    "rule": "mock-assertion-typo",
                    "severity": "critical",
                    "title": "Mock assertion typo",
                    "detail": "mock.assert_called_once without ()",
                    "file": "tests/test_auth.py",
                    "line": 15,
                    "test": "test_login",
                    "snippet": "mock.assert_called_once",
                    "explanation": "Attribute access without calling the assertion function.",
                    "suggested_fix": "mock.assert_called_once()",
                }
            ],
            "headline": "1 in 4 tests cannot fail.",
        },
    }

    store.save_scan(
        slug=sample_record["slug"],
        owner=sample_record["owner"],
        repo=sample_record["repo"],
        score=sample_record["score"],
        grade=sample_record["grade"],
        payload=sample_record["payload"],
    )

    # Navigate to report page
    page.goto(f"{server_url}/r/sampleorg/awesome-repo")
    page.wait_for_load_state("networkidle")

    # Verify report header and score
    content = page.content()
    assert "sampleorg/awesome-repo" in content
    assert "75" in content

    # Verify SARIF export link exists and points to API endpoint
    sarif_link = page.locator('a[href="/api/scan/sampleorg/awesome-repo/sarif"]')
    assert sarif_link.is_visible()
    assert "SARIF" in sarif_link.inner_text()

    # Verify finding detail and code snippet
    assert "mock-assertion-typo" in content or "Mock assertion typo" in content
    assert "tests/test_auth.py" in content


def test_mobile_responsive_viewport(page: Page, server_url: str):
    # Set mobile viewport
    page.set_viewport_size({"width": 375, "height": 667})
    page.goto(server_url)

    input_elem = page.locator('.scan-form input[name="repo"]')
    assert input_elem.is_visible()

    box = input_elem.bounding_box()
    assert box is not None
    assert box["x"] >= 0
    assert box["x"] + box["width"] <= 375 + 15  # no horizontal viewport overflow
