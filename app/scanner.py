"""Clone a public repo and scan it.

SECURITY NOTES - read before changing anything here.

- We never execute repository code. Scanning is `ast.parse`, which builds a
  tree and runs nothing. That is the single most important property of this
  service and it must stay true: do not add an import of the target's code,
  do not run its test runner, do not evaluate its setup.py.
- Clone arguments are passed as a list, never through a shell, so a URL cannot
  smuggle a command.
- Only https://github.com/owner/repo is accepted. No ssh, no file://, no
  git://, no arbitrary hosts, no submodules (they would fetch unvetted URLs).
- Depth 1, a blobless partial clone, a sparse checkout restricted to test-file
  patterns, a hard timeout, a byte ceiling on what gets materialized and a
  file-count ceiling, because the caller controls the input and an unbounded
  clone is a free denial of service.
- That byte ceiling is applied to the bytes we actually parse, never to the
  repository's total size. The two differ by orders of magnitude on an ordinary
  project - the repo this was rewritten for is 230 MB of TypeScript and assets
  wrapped around 8 KB of Python tests. Gating on repository size refused a scan
  costing milliseconds while doing nothing to bound the real work, so the bound
  now sits on the only thing that scales the cost: bytes handed to ast.parse.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from falsegreen.cli import (
    collect_files,
    DEFAULT_EXCLUDES,
    DEFAULT_INCLUDES,
    JAVA_EXTENSIONS,
    JS_EXTENSIONS,
    ROBOT_EXTENSIONS,
    CSHARP_EXTENSIONS,
    GO_EXTENSIONS,
    KOTLIN_EXTENSIONS,
)
from falsegreen.detectors.csharp import scan_csharp_file
from falsegreen.detectors.golang import scan_golang_file
from falsegreen.detectors.java import scan_java_file
from falsegreen.detectors.javascript import scan_js_file
from falsegreen.detectors.kotlin import scan_kotlin_file
from falsegreen.detectors.python_ast import scan_python_file
from falsegreen.detectors.robot import scan_robot_file
from falsegreen.models import ScanResult
from falsegreen.score import Score, compute

GITHUB_REPO_RE = re.compile(
    r"^(?:https?://)?(?:www\.)?github\.com/"
    r"(?P<owner>[A-Za-z0-9](?:[A-Za-z0-9._-]{0,38}))/"
    r"(?P<repo>[A-Za-z0-9._-]{1,100}?)(?:\.git)?/?$"
)

CLONE_TIMEOUT_SECONDS = 60
MAX_TEST_FILES = 3000

# Ceiling on the test files themselves - deliberately NOT on repository size.
# Because the checkout is sparse, test files are the only thing we ever
# download, so this one number bounds bandwidth, disk and parse time together.
# It is generous for its purpose: 20 MB is on the order of half a million lines
# of test code, and sits in the same range as MAX_TEST_FILES.
MAX_TEST_MB = 20


class ScanError(Exception):
    """A failure worth showing the user verbatim."""


@dataclass
class ScanReport:
    owner: str
    repo: str
    slug: str
    result: ScanResult
    score: Score

    #: Test files collect_files matched, before any were scanned. Kept next to
    #: result.files_scanned so the two can be compared: a Trust Score computed
    #: over fewer files than were found is not wrong so much as unqualified,
    #: and the gap has to reach the user rather than being averaged away.
    files_found: int = 0

    #: Files matched but never analyzed - truncated past MAX_TEST_FILES, or
    #: failed to parse. Each one is a test whose trustworthiness is unknown.
    @property
    def files_skipped(self) -> int:
        return max(0, self.files_found - self.result.files_scanned)

    @property
    def url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}"


def parse_repo(raw: str) -> Tuple[str, str]:
    """Validate and split a GitHub repo reference. Rejects everything else."""
    candidate = (raw or "").strip()
    if not candidate:
        raise ScanError("Enter a GitHub repository URL.")

    # Bare owner/repo is a convenience, not a second code path.
    if "/" in candidate and "github.com" not in candidate and candidate.count("/") == 1:
        candidate = f"https://github.com/{candidate}"

    match = GITHUB_REPO_RE.match(candidate)
    if not match:
        raise ScanError(
            "That does not look like a public GitHub repository. "
            "Use a URL like https://github.com/owner/repo"
        )
    return match.group("owner"), match.group("repo")


def _clone_env() -> dict:
    """Inherit the real environment, then disable every interactive prompt.

    Hardcoding PATH here breaks git on any host whose layout differs from the
    author's, so instead we copy the environment and override only the variables
    that could make a clone hang waiting for credentials.
    """
    env = dict(os.environ)
    env.update(
        {
            "GIT_TERMINAL_PROMPT": "0",   # never ask for a username on the tty
            "GIT_ASKPASS": "echo",        # and never pop a GUI credential helper
            "SSH_ASKPASS": "echo",
            "GCM_INTERACTIVE": "never",   # git-credential-manager on Windows
            "GIT_CONFIG_NOSYSTEM": "1",   # ignore host-level git config
        }
    )
    return env


def _run_git(args: list[str], doing: str) -> subprocess.CompletedProcess:
    """Run one git command: no shell, no prompts, hard timeout.

    Every git invocation in this module goes through here so that the timeout
    and the prompt-proof environment cannot be forgotten at a new call site.
    """
    try:
        return subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            timeout=CLONE_TIMEOUT_SECONDS,
            shell=False,
            env=_clone_env(),
        )
    except subprocess.TimeoutExpired:
        raise ScanError(
            f"{doing} timed out after {CLONE_TIMEOUT_SECONDS}s. "
            f"Run the CLI locally instead: pip install falsegreen"
        )
    except FileNotFoundError:
        raise ScanError("git is not available on the server.")


def _sparse_patterns() -> list[str]:
    """Translate falsegreen's include globs into gitignore-style sparse rules.

    Derived from DEFAULT_INCLUDES rather than spelled out a second time, so the
    set of files we download can never drift from the set collect_files will
    later pick up. A pattern that stopped matching here would not fail loudly -
    it would silently scan less - which is exactly the failure this tool exists
    to catch, so the single source of truth matters.

    The two dialects differ in one way that bites: a gitignore pattern
    containing a slash is anchored to the repository root, so `tests/**/*.py`
    alone would miss `backend/tests/test_api.py`. Emit an unanchored twin for
    those. Patterns with no slash already match at any depth.
    """
    patterns: list[str] = []
    for include in DEFAULT_INCLUDES:
        patterns.append(include)
        if "/" in include:
            patterns.append(f"**/{include}")

    # Never fetch vendored or generated trees, even when they contain matching
    # files. collect_files discards them anyway; excluding them here means we
    # do not pay to download a dependency's test suite first.
    for excluded in DEFAULT_EXCLUDES:
        name = excluded.strip("*/")
        if name:
            patterns.append(f"!**/{name}/**")

    return patterns


def _clone(owner: str, repo: str, dest: Path) -> None:
    """Fetch the repository's shape without its contents.

    `--filter=blob:none --no-checkout` downloads commits and trees only. File
    contents are fetched later, on demand, for just the paths the sparse
    checkout asks for - so a 230 MB repository costs about 1 MB and a second
    here, and repository size stops being a quantity this service cares about.
    """
    url = f"https://github.com/{owner}/{repo}.git"
    proc = _run_git(
        [
            "clone",
            "--filter=blob:none",              # contents on demand, not up front
            "--no-checkout",                   # choose what to materialize later
            "--depth", "1",
            "--single-branch",
            "--no-tags",
            "--recurse-submodules=no",
            "--config", "core.askPass=true",   # never prompt for credentials
            url, str(dest),
        ],
        doing="Cloning",
    )

    if proc.returncode != 0:
        stderr = (proc.stderr or "").lower()
        if "not found" in stderr or "repository not found" in stderr:
            raise ScanError("Repository not found, or it is private.")
        if "authentication" in stderr or "could not read username" in stderr:
            raise ScanError("That repository is private. The free scanner reads public repos only.")
        raise ScanError("Could not clone that repository.")


def _checkout_tests(dest: Path) -> None:
    """Materialize the test files, and only the test files.

    This is where blobs are actually fetched, so it is bounded by the same
    timeout as the clone.
    """
    proc = _run_git(
        ["-C", str(dest), "sparse-checkout", "set", "--no-cone", *_sparse_patterns()],
        doing="Selecting test files",
    )
    if proc.returncode != 0:
        raise ScanError("Could not select the test files in that repository.")

    proc = _run_git(["-C", str(dest), "checkout"], doing="Fetching test files")
    if proc.returncode != 0:
        raise ScanError("Could not read the test files from that repository.")


def _checked_out_mb(path: Path) -> float:
    """Megabytes materialized by the sparse checkout.

    After a sparse checkout this is the test files and nothing else, which makes
    it both what we downloaded and an upper bound on what ast.parse will read.
    Skips .git, which holds the packfile rather than working-tree content.

    Stops counting once past the ceiling: the caller only asks whether the limit
    was exceeded, and the exact total beyond it is not worth the syscalls.
    """
    ceiling = MAX_TEST_MB * 1024 * 1024
    total = 0
    for item in path.rglob("*"):
        if ".git" in item.parts:
            continue
        try:
            if item.is_file() and not item.is_symlink():
                total += item.stat().st_size
        except OSError:
            continue
        if total > ceiling:
            break
    return total / (1024 * 1024)


def analyze(files: list[Path], root: Path) -> ScanResult:
    """Parse each collected test file with the analyzer for its language.

    Separate from scan_repository so the dispatch can be exercised without a
    network round trip. That separation is the point: the bug this replaced -
    JavaScript tests falling into the Python parser, raising SyntaxError and
    vanishing into result.errors - survived precisely because nothing tested
    this logic on its own.
    """
    result = ScanResult(root=root)

    if len(files) > MAX_TEST_FILES:
        # Truncating is a legitimate judgement about cost, but it narrows what
        # the score describes, so it is recorded like any other skipped file
        # rather than applied invisibly.
        result.errors.append(
            f"Only the first {MAX_TEST_FILES} of {len(files)} test files were "
            f"scanned. Run the CLI locally for the whole suite: pip install falsegreen"
        )
        files = files[:MAX_TEST_FILES]

    for path in files:
        # Dispatch on extension, exactly as cli.py does.
        if path.suffix in ROBOT_EXTENSIONS:
            scan_robot_file(path, result)
        elif path.suffix in JAVA_EXTENSIONS:
            scan_java_file(path, result)
        elif path.suffix in JS_EXTENSIONS:
            scan_js_file(path, result)
        elif path.suffix in CSHARP_EXTENSIONS:
            scan_csharp_file(path, result)
        elif path.suffix in GO_EXTENSIONS:
            scan_golang_file(path, result)
        elif path.suffix in KOTLIN_EXTENSIONS:
            scan_kotlin_file(path, result)
        else:
            scan_python_file(path, result)

    return result


def scan_repository(raw_url: str) -> ScanReport:
    owner, repo = parse_repo(raw_url)
    workdir = Path(tempfile.mkdtemp(prefix="fg-"))
    checkout = workdir / "repo"

    try:
        _clone(owner, repo, checkout)
        _checkout_tests(checkout)

        size = _checked_out_mb(checkout)
        if size > MAX_TEST_MB:
            raise ScanError(
                f"That repository's test files come to more than {MAX_TEST_MB} MB, "
                f"over the limit for the free scanner. Run the CLI locally "
                f"instead: pip install falsegreen"
            )

        files = collect_files(checkout, DEFAULT_INCLUDES, DEFAULT_EXCLUDES)
        if not files:
            raise ScanError(
                "No test files found. falsegreen looks for test_*.py, *_test.py and "
                "files under tests/, plus *.spec.ts, *.test.js, *.cy.ts and files "
                "under e2e/ and __tests__/."
            )

        files_found = len(files)
        result = analyze(files, checkout)

        return ScanReport(
            owner=owner,
            repo=repo,
            slug=f"{owner}/{repo}",
            result=result,
            score=compute(result),
            files_found=files_found,
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
