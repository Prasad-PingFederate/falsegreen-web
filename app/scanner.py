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
- Depth 1, a hard timeout, a size ceiling and a file-count ceiling, because the
  caller controls the input and an unbounded clone is a free denial of service.
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

from falsegreen.cli import collect_files, DEFAULT_EXCLUDES, DEFAULT_INCLUDES
from falsegreen.detectors.python_ast import scan_python_file
from falsegreen.models import ScanResult
from falsegreen.score import Score, compute

GITHUB_REPO_RE = re.compile(
    r"^(?:https?://)?(?:www\.)?github\.com/"
    r"(?P<owner>[A-Za-z0-9](?:[A-Za-z0-9._-]{0,38}))/"
    r"(?P<repo>[A-Za-z0-9._-]{1,100}?)(?:\.git)?/?$"
)

CLONE_TIMEOUT_SECONDS = 60
MAX_REPO_MB = 200
MAX_TEST_FILES = 3000


class ScanError(Exception):
    """A failure worth showing the user verbatim."""


@dataclass
class ScanReport:
    owner: str
    repo: str
    slug: str
    result: ScanResult
    score: Score

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


def _clone(owner: str, repo: str, dest: Path) -> None:
    url = f"https://github.com/{owner}/{repo}.git"
    try:
        proc = subprocess.run(
            [
                "git", "clone",
                "--depth", "1",
                "--single-branch",
                "--no-tags",
                "--recurse-submodules=no",
                "--config", "core.askPass=true",   # never prompt for credentials
                url, str(dest),
            ],
            capture_output=True,
            text=True,
            timeout=CLONE_TIMEOUT_SECONDS,
            shell=False,
            env=_clone_env(),
        )
    except subprocess.TimeoutExpired:
        raise ScanError(
            f"Cloning timed out after {CLONE_TIMEOUT_SECONDS}s. The repository is "
            f"too large for the free scanner."
        )
    except FileNotFoundError:
        raise ScanError("git is not available on the server.")

    if proc.returncode != 0:
        stderr = (proc.stderr or "").lower()
        if "not found" in stderr or "repository not found" in stderr:
            raise ScanError("Repository not found, or it is private.")
        if "authentication" in stderr or "could not read username" in stderr:
            raise ScanError("That repository is private. The free scanner reads public repos only.")
        raise ScanError("Could not clone that repository.")


def _directory_mb(path: Path) -> float:
    total = 0
    for item in path.rglob("*"):
        try:
            if item.is_file() and not item.is_symlink():
                total += item.stat().st_size
        except OSError:
            continue
        if total > MAX_REPO_MB * 1024 * 1024:
            break
    return total / (1024 * 1024)


def scan_repository(raw_url: str) -> ScanReport:
    owner, repo = parse_repo(raw_url)
    workdir = Path(tempfile.mkdtemp(prefix="fg-"))
    checkout = workdir / "repo"

    try:
        _clone(owner, repo, checkout)

        size = _directory_mb(checkout)
        if size > MAX_REPO_MB:
            raise ScanError(
                f"That repository is {size:.0f} MB, over the {MAX_REPO_MB} MB limit "
                f"for the free scanner. Run the CLI locally instead: pip install falsegreen"
            )

        files = collect_files(checkout, DEFAULT_INCLUDES, DEFAULT_EXCLUDES)
        if not files:
            raise ScanError(
                "No Python test files found. falsegreen looks for test_*.py, *_test.py "
                "and files under tests/. JavaScript support is not live yet."
            )

        if len(files) > MAX_TEST_FILES:
            files = files[:MAX_TEST_FILES]

        result = ScanResult(root=checkout)
        for path in files:
            scan_python_file(path, result)

        return ScanReport(
            owner=owner,
            repo=repo,
            slug=f"{owner}/{repo}",
            result=result,
            score=compute(result),
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
