"""Tests for the repository scanner.

Every test here exists because of a specific way this service can be wrong
*quietly*. A crash gets noticed; a scan that silently covers less than it
claims does not, and that failure mode is the exact thing the product sells
itself on catching. So the assertions below mostly check that work was
actually done, not merely that nothing raised.

Nothing here touches the network. The clone is the only part that does, and it
is deliberately separated from the logic worth testing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.scanner import (
    MAX_TEST_FILES,
    MAX_TEST_MB,
    ScanError,
    ScanReport,
    _checked_out_mb,
    _sparse_patterns,
    analyze,
    parse_repo,
)
from falsegreen.cli import DEFAULT_INCLUDES
from falsegreen.models import ScanResult
from falsegreen.score import compute

# A Playwright spec with no assertion in it. This is the canonical thing
# falsegreen exists to find, written in the language the web scanner used to
# throw away.
JS_TEST_NO_ASSERTION = """
test('user can log in', async ({ page }) => {
  await page.goto('/login');
});
"""

# The Python equivalent: an assertion that cannot fail because the except
# swallows it.
PY_TEST_SWALLOWED = """
def test_health():
    try:
        assert get_health() == 200
    except Exception:
        pass
"""


# ---------------------------------------------------------------------------
# Language dispatch
# ---------------------------------------------------------------------------


def test_typescript_test_is_analyzed_not_recorded_as_an_error(tmp_path: Path):
    """A .ts test must reach the JavaScript analyzer.

    Regression test. The web scanner used to call scan_python_file on every
    collected path; ast.parse raised SyntaxError on JavaScript, the file landed
    in result.errors, files_scanned stayed at zero, and the test silently left
    the score. The assertion that matters is files_scanned - a version that
    merely "does not crash" is exactly the broken one.
    """
    spec = tmp_path / "login.spec.ts"
    spec.write_text(JS_TEST_NO_ASSERTION, encoding="utf-8")

    result = analyze([spec], tmp_path)

    assert result.files_scanned == 1
    assert result.errors == []
    assert len(result.findings) == 1


def test_python_and_javascript_are_analyzed_in_one_pass(tmp_path: Path):
    """A mixed repo must not lose either language."""
    js = tmp_path / "login.spec.ts"
    js.write_text(JS_TEST_NO_ASSERTION, encoding="utf-8")
    py = tmp_path / "test_api.py"
    py.write_text(PY_TEST_SWALLOWED, encoding="utf-8")

    result = analyze([js, py], tmp_path)

    assert result.files_scanned == 2
    assert result.errors == []
    # Both languages contributed findings, rather than one silently dropping.
    files_with_findings = {Path(str(f.file)).name for f in result.findings}
    assert files_with_findings == {"login.spec.ts", "test_api.py"}


@pytest.mark.parametrize("name", ["a.spec.ts", "a.spec.js", "a.test.tsx", "a.cy.ts"])
def test_every_javascript_extension_reaches_the_js_analyzer(tmp_path: Path, name: str):
    """The dispatch keys off JS_EXTENSIONS, so each spelling must work."""
    spec = tmp_path / name
    spec.write_text(JS_TEST_NO_ASSERTION, encoding="utf-8")

    result = analyze([spec], tmp_path)

    assert result.files_scanned == 1, f"{name} was not analyzed"
    assert result.errors == []


def test_a_genuinely_broken_python_file_is_still_reported(tmp_path: Path):
    """Unparseable source must surface as an error, not disappear.

    The fix for the JavaScript bug must not become a blanket "ignore parse
    failures" - a Python file that really is malformed still needs to be
    visible, because its tests went unjudged.
    """
    bad = tmp_path / "test_broken.py"
    bad.write_text("def test_x(:\n    pass\n", encoding="utf-8")

    result = analyze([bad], tmp_path)

    assert result.files_scanned == 0
    assert len(result.errors) == 1


# ---------------------------------------------------------------------------
# Coverage honesty
# ---------------------------------------------------------------------------


def test_truncation_past_the_file_cap_is_recorded(tmp_path: Path):
    """Scanning only part of a suite must leave a trace.

    Silently capping would produce a confident score over an arbitrary subset,
    which is the product's own cardinal sin.
    """
    files = []
    for i in range(MAX_TEST_FILES + 5):
        f = tmp_path / f"test_{i}.py"
        f.write_text("def test_ok():\n    assert True\n", encoding="utf-8")
        files.append(f)

    result = analyze(files, tmp_path)

    assert result.files_scanned == MAX_TEST_FILES
    assert any(str(MAX_TEST_FILES) in e for e in result.errors)


def test_files_skipped_counts_everything_not_analyzed(tmp_path: Path):
    """files_found minus files_scanned is what the score does not cover."""
    good = tmp_path / "test_ok.py"
    good.write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    bad = tmp_path / "test_broken.py"
    bad.write_text("def test_x(:\n", encoding="utf-8")

    result = analyze([good, bad], tmp_path)
    report = ScanReport(
        owner="o", repo="r", slug="o/r",
        result=result, score=compute(result), files_found=2,
    )

    assert result.files_scanned == 1
    assert report.files_skipped == 1


def test_files_skipped_is_zero_when_everything_was_analyzed(tmp_path: Path):
    good = tmp_path / "test_ok.py"
    good.write_text("def test_ok():\n    assert True\n", encoding="utf-8")

    result = analyze([good], tmp_path)
    report = ScanReport(
        owner="o", repo="r", slug="o/r",
        result=result, score=compute(result), files_found=1,
    )

    assert report.files_skipped == 0


def test_files_skipped_never_goes_negative():
    """Defensive: a mismatched files_found must not render as a negative count."""
    result = ScanResult(root=Path("."))
    result.files_scanned = 5
    report = ScanReport(
        owner="o", repo="r", slug="o/r",
        result=result, score=compute(result), files_found=0,
    )

    assert report.files_skipped == 0


# ---------------------------------------------------------------------------
# Sparse checkout patterns
# ---------------------------------------------------------------------------


def test_sparse_patterns_cover_every_include():
    """What we download must not drift from what collect_files looks for.

    If an include ever stops being fetched, the scan does not fail - it just
    quietly covers less.
    """
    patterns = set(_sparse_patterns())
    for include in DEFAULT_INCLUDES:
        assert include in patterns, f"{include} would never be downloaded"


def test_nested_include_patterns_get_an_unanchored_twin():
    """gitignore anchors any pattern containing a slash to the repo root.

    Without the twin, `tests/**/*.py` would miss `backend/tests/test_api.py` -
    a whole directory of tests missing from the score with nothing to show for
    it.
    """
    patterns = set(_sparse_patterns())
    assert "tests/**/*.py" in patterns
    assert "**/tests/**/*.py" in patterns


def test_vendored_directories_are_excluded():
    """node_modules holds other people's tests; we should not fetch or score them."""
    patterns = _sparse_patterns()
    assert any(p == "!**/node_modules/**" for p in patterns)


# ---------------------------------------------------------------------------
# Size accounting
# ---------------------------------------------------------------------------


def test_checked_out_size_ignores_the_git_directory(tmp_path: Path):
    """The ceiling is about test files, not packfiles.

    Counting .git would reintroduce the repository-size gate through the back
    door: a repo with a large history would be refused for having large
    history, which is not a cost this scanner pays.
    """
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "pack").write_bytes(b"x" * 5_000_000)
    (tmp_path / "test_small.py").write_bytes(b"x" * 1_000)

    assert _checked_out_mb(tmp_path) < 0.01


def test_checked_out_size_counts_test_files(tmp_path: Path):
    (tmp_path / "test_big.py").write_bytes(b"x" * 2 * 1024 * 1024)

    assert 1.9 < _checked_out_mb(tmp_path) < 2.1


def test_the_size_ceiling_is_about_tests_not_repositories():
    """Guards the intent of the 2026-09-19 change.

    The old MAX_REPO_MB rejected a 230 MB repo holding 8 KB of tests. If a
    repository-size limit is ever reintroduced, this fails.
    """
    import app.scanner as scanner

    assert not hasattr(scanner, "MAX_REPO_MB")
    assert MAX_TEST_MB <= 50, "a ceiling this high is measuring the wrong thing again"


# ---------------------------------------------------------------------------
# Input validation - this endpoint takes a URL from anyone
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "git@github.com:owner/repo.git",
        "file:///etc/passwd",
        "git://github.com/owner/repo",
        "https://gitlab.com/owner/repo",
        "https://github.com/owner",
        "https://evil.com/github.com/owner/repo",
        "",
        "   ",
    ],
)
def test_parse_repo_rejects_anything_but_a_github_repo(raw: str):
    """Only https://github.com/owner/repo is a valid target.

    parse_repo is the whole allowlist: everything downstream trusts its output,
    so each rejected form here is a fetch that never happens.
    """
    with pytest.raises(ScanError):
        parse_repo(raw)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("https://github.com/owner/repo", ("owner", "repo")),
        ("http://github.com/owner/repo", ("owner", "repo")),
        ("https://www.github.com/owner/repo", ("owner", "repo")),
        ("https://github.com/owner/repo.git", ("owner", "repo")),
        ("https://github.com/owner/repo/", ("owner", "repo")),
        ("owner/repo", ("owner", "repo")),
        ("github.com/owner/repo", ("owner", "repo")),
    ],
)
def test_parse_repo_accepts_the_forms_users_actually_paste(raw: str, expected):
    assert parse_repo(raw) == expected
