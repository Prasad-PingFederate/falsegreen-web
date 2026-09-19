"""Tests for web API endpoints including SARIF export."""

from __future__ import annotations

import json
from unittest.mock import patch

from app.main import api_scan, api_scan_sarif


def test_api_scan_sarif_404():
    with patch("app.main.store.get_scan", return_value=None):
        resp = api_scan_sarif("nonexistent", "repo")
        assert resp.status_code == 404


def test_api_scan_sarif_success():
    sample_record = {
        "slug": "owner/repo",
        "score": 60,
        "grade": "D",
        "scanned_at": 1700000000,
        "payload": {
            "tests_found": 10,
            "tests_can_fail": 6,
            "tests_cannot_fail": 4,
            "counts": {"critical": 2, "high": 1, "medium": 0, "low": 1},
            "findings": [
                {
                    "rule": "mock-assertion-typo",
                    "severity": "critical",
                    "title": "Mock assertion typo",
                    "detail": "assert_called_once without ()",
                    "file": "tests/test_foo.py",
                    "line": 42,
                    "test": "test_mock",
                    "snippet": "mock.assert_called_once",
                    "explanation": "Missing parentheses",
                }
            ],
        },
    }

    with patch("app.main.store.get_scan", return_value=sample_record):
        resp = api_scan_sarif("owner", "repo")
        assert resp.status_code == 200
        assert resp.media_type == "application/sarif+json"

        body = json.loads(resp.body.decode("utf-8"))
        assert body["version"] == "2.1.0"
        assert len(body["runs"]) == 1

        driver = body["runs"][0]["tool"]["driver"]
        assert driver["name"] == "falsegreen"
        assert len(driver["rules"]) == 1
        assert driver["rules"][0]["id"] == "mock-assertion-typo"

        results = body["runs"][0]["results"]
        assert len(results) == 1
        assert results[0]["ruleId"] == "mock-assertion-typo"
        assert results[0]["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == "tests/test_foo.py"
