# falsegreen-web

The hosted scanner for [falsegreen](https://github.com/Prasad-PingFederate/falsegreen).
Point it at a public GitHub repository and it reports how many of that
repository's tests **could not have failed** — tests that pass because they
assert nothing, because an `except` swallows the assertion, or because the
assertion was never awaited.

A green suite only means something if its tests were capable of going red.

---

## What it detects

Twelve rules. Each one describes a test that passes without being able to fail.

| Rule | What it means | Python | JS/TS | Java | Robot |
|---|---|:---:|:---:|:---:|:---:|
| `no-assertions` | The test body contains no assertion at all | ✅ | ✅ | ✅ | ✅ |
| `swallowed-assertion` | An assertion sits inside a `try` whose handler catches it without re-raising | ✅ | ✅ | ✅ | ✅ |
| `blanket-try-except` | The whole test body is wrapped in a handler that cannot propagate | ✅ | — | — | — |
| `tautological-assertion` | The assertion compares a value to itself, or to a literal that is always true | ✅ | ✅ | ✅ | ✅ |
| `dangling-expect` | An `expect(...)` is built but no matcher is ever called on it | ✅ | ✅ | — | — |
| `unawaited-expect` | An async `expect(...)` is never awaited, so the test finishes before it resolves | — | ✅ | — | — |
| `assertion-free-helper` | The test delegates to a helper that itself asserts nothing | ✅ | — | ✅ | ✅ |
| `sleep-instead-of-wait` | A fixed sleep stands in for a real wait condition | ✅ | ✅ | ✅ | ✅ |
| `disabled-test` | The test is skipped, xfailed or otherwise never runs | ✅ | ✅ | ✅ | ✅ |
| `forced-interaction` | An interaction is forced past the check that would have caught the bug | ✅ | — | — | — |
| `self-guarded-assertion` | The assertion is inside an `if` that its own subject makes false | ✅ | ✅ | — | — |
| `optional-assertion` | The assertion only runs on some branches, so passing proves nothing | ✅ | ✅ | ✅ | ✅ |

Findings are graded `critical` / `high` / `medium` / `low`.

### The Trust Score

`Trust Score = round(100 × tests that can fail ÷ tests found)`

| Grade | Condition |
|---|---|
| A | ≥ 95 and no critical findings |
| B | ≥ 85 |
| C | ≥ 70 |
| D | ≥ 50 |
| F | below 50 |

A score is only ever computed over files that were successfully parsed. When
some could not be, the report says so and shows how many — a score covering
part of a suite describes only that part.

---

## Language support

| Language / framework | Status | Notes |
|---|---|---|
| Python — pytest, unittest | ✅ Supported | Full `ast` analysis |
| Python — Playwright, Selenium | ✅ Supported | Ordinary Python; the E2E rules (`sleep-instead-of-wait`, `forced-interaction`) target them directly |
| JavaScript / TypeScript — Jest, Vitest, Cypress, Playwright | ✅ Supported | See the patterns below |
| Java — JUnit 4/5, TestNG, Selenium | ✅ Supported | `@Test`, `@ParameterizedTest`, `@RepeatedTest`; understands that `AssertionError` is an `Error`, so `catch (Exception e)` is not treated as swallowing |
| Robot Framework | ✅ Supported | `.robot` and `.resource`; user keywords are resolved transitively, so a test whose only step is `Verify Dashboard` is judged by what that keyword does |
| C# — NUnit, xUnit, SpecFlow | ❌ Not yet | |
| Go — `testing` | ❌ Not yet | |
| Kotlin — JUnit, Kotest | ❌ Not yet | |

TypeScript needs no separate support — it is handled by the JavaScript
detector, including `.spec.ts` and `.test.tsx`.

Selenium is a library, not a language, so it is covered wherever its host
language is: Python, JavaScript/TypeScript and Java suites are all analyzed
today. A Selenium suite written in C# is not yet.

### Which files are picked up

```
test_*.py   *_test.py   *Test*.py   *_Playwright.py   *.spec.py   tests/**/*.py
*.spec.{js,jsx,ts,tsx}   *.test.{js,jsx,ts,tsx}   *.cy.{js,ts}
tests/**/*.{js,ts}   e2e/**/*.{js,ts}   __tests__/**/*.{js,ts}
*.robot   *.resource
*Test.java   *Tests.java   Test*.java   *IT.java   *TestCase.java   src/test/java/**/*.java
```

Always skipped: `node_modules`, `.venv`, `venv`, `__pycache__`, `build`,
`dist`, `site-packages`.

The JavaScript detector can also read `.mjs`, `.cjs`, `.mts` and `.cts`, but no
default pattern matches them — pass `--include '*.spec.mjs'` (or similar) if
your suite uses those extensions. The same applies to any layout that does not
match the list above; the hosted scanner uses the defaults, the CLI does not
have to.

---

## How it works

Three pieces: a browser front end, a FastAPI back end, and the analysis engine
the back end calls into.

```
Browser ──▶ FastAPI (app/main.py)
               │
               ├─▶ app/scanner.py ──▶ git clone (blobless)
               │                  └─▶ git sparse-checkout  (test files only)
               │
               ├─▶ falsegreen engine ─▶ detectors/python_ast.py  (ast.parse)
               │                     ├─▶ detectors/javascript.py (source scan)
               │                     ├─▶ detectors/java.py       (source scan)
               │                     └─▶ detectors/robot.py      (keyword scan)
               │
               └─▶ app/store.py ──▶ SQLite (cache, rate limit, waitlist)
```

### Front end

Server-rendered Jinja2 templates styled by one stylesheet. There is no
JavaScript framework and no build step — the pages are HTML produced by the
back end.

| File | Purpose |
|---|---|
| `app/templates/index.html` | Landing page, scan form, error banners |
| `app/templates/report.html` | Score, per-rule breakdown, findings, coverage warning |
| `static/style.css` | All styling; colours are tokens on `:root` |

### Back end

| File | Purpose |
|---|---|
| `app/main.py` | FastAPI routes, rate limiting, payload construction |
| `app/scanner.py` | Repository fetch, sparse checkout, language dispatch, ceilings |
| `app/store.py` | SQLite: cached scans, rate-limit hits, waitlist |
| `app/badge.py` | SVG Trust Score badge |

### The scan pipeline

1. **Validate.** `parse_repo()` accepts only `https://github.com/owner/repo`.
   It is the entire allowlist; everything downstream trusts its output.
2. **Clone the shape, not the contents.** `git clone --filter=blob:none
   --no-checkout --depth 1`. Commits and trees are fetched; file contents are
   not.
3. **Check out only test files.** A sparse checkout whose patterns are derived
   from the include list above, so only matching blobs are ever downloaded. A
   230 MB repository typically costs about 1 MB and two seconds.
4. **Collect and measure.** Matching files are listed and their combined size
   checked against the ceiling.
5. **Analyze.** Each file is dispatched by extension to the Python,
   JavaScript, Java or Robot Framework detector. Nothing is executed.
6. **Score and store.** The Trust Score is computed, the report is cached for
   six hours, and the page is rendered.

### Limits

| Limit | Value | Why |
|---|---|---|
| Test-file size | 20 MB | Bounds parse time and bandwidth. **Not** a limit on repository size — repository size is irrelevant, because only test files are downloaded |
| Test-file count | 3000 | Scans past this are truncated, and the report says so |
| Clone timeout | 60 s | |
| Scans per IP | 10 / hour | `SCAN_RATE_LIMIT`, `SCAN_RATE_WINDOW` |

Anything beyond these is a reason to run the CLI locally, where there are none.

---

## Running it locally

Requires Python 3.12+ and `git` on the PATH.

```bash
pip install -r requirements-dev.txt
uvicorn --app-dir . app.main:app --reload --reload-dir .
```

Open <http://localhost:8000>.

> **Install the engine from its repository, never by bare name.** The name
> `falsegreen` on PyPI belongs to an unrelated project by a different author.
> `requirements-dev.txt` pins this project's engine to a commit of
> `Prasad-PingFederate/falsegreen`; `pip install falsegreen` fetches something
> else entirely.

`--reload-dir` matters: `--app-dir` only affects `sys.path`, so without it
uvicorn watches the shell's working directory and your edits are never picked
up.

### Tests

```bash
python -m pytest tests/ -q
```

---

## Command line

The CLI scans a local directory, with no limits and no network access.

```bash
falsegreen                      # scan the current directory
falsegreen tests/               # scan a path
falsegreen --include 'check_*.py'
```

| Flag | Effect |
|---|---|
| `--format {terminal,markdown,json,sarif}` | Output format |
| `--output PATH` | Write to a file instead of stdout |
| `--fail-under N` | Exit non-zero if the Trust Score is below N |
| `--fail-on {critical,high,medium,low,never}` | Exit non-zero on findings at or above a severity |
| `--include GLOB` / `--exclude GLOB` | Override which files are collected |
| `--limit N` | Findings shown in terminal output (default 25) |
| `--quiet` | Print only the score line |
| `--no-color` | Disable ANSI colour |

### In CI

```yaml
- run: pip install "falsegreen @ git+https://github.com/Prasad-PingFederate/falsegreen@<commit>"
- run: falsegreen tests/ --fail-under 90
```

`--format sarif` produces output for GitHub code scanning.

---

## Security

The service clones repositories that anyone on the internet can name.

- **Nothing from the target repository is ever executed.** Analysis is
  `ast.parse` and source inspection. Do not add an import of target code, do
  not run its test runner, do not evaluate its `setup.py`.
- Clone arguments are passed as an argument list, never through a shell, so a
  URL cannot smuggle a command.
- Only `https://github.com/owner/repo` is accepted — no ssh, `file://`,
  `git://`, arbitrary hosts, or submodules.
- Depth 1, a 60 s timeout, and the ceilings above. The caller controls the
  input, so each one is load-bearing.
- Credential prompts are disabled, so a private repository fails fast instead
  of hanging the worker.
- The container runs unprivileged.

---

## Deploying

```bash
fly launch --no-deploy
fly volumes create falsegreen_data --size 1
fly deploy
```

The volume is required: SQLite lives on it, and without it every deploy discards
the cache and the waitlist. The app scales to zero when idle.
