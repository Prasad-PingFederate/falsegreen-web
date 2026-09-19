# falsegreen-web

The hosted front end for [falsegreen](https://github.com/YOURNAME/falsegreen).
Paste a public GitHub repo, get a Trust Score, share the badge.

The free scan is the marketing. It has to be instant, shareable, and
uncomfortable enough to be memorable.

## Run it

```bash
pip install -r requirements.txt
pip install falsegreen
uvicorn app.main:app --reload
```

Open http://localhost:8000.

## Deploy

```bash
fly launch --no-deploy
fly volumes create falsegreen_data --size 1
fly deploy
```

Scales to zero when idle, so an unvisited site costs nothing. The volume is not
optional: SQLite lives on it, and without it every deploy wipes the waitlist.

## Scores observed so far

Useful as a sanity check that the scoring discriminates rather than flagging
everything:

| Repo | Trust Score |
|---|---|
| encode/httpx | 99 |
| tiangolo/fastapi | 98 |
| psf/requests | 94 |
| pallets/flask | 92 |

Well-maintained libraries land in the nineties. A score in the sixties means
something real, which is the whole point — a metric that calls everything broken
gets ignored.

## Security

The service clones repositories that anyone on the internet can name, so:

- **Nothing from the target repo is ever executed.** Scanning is `ast.parse`,
  which builds a tree and runs nothing. Do not add an import of target code, do
  not run its test runner, do not evaluate its `setup.py`.
- Clone arguments are passed as an argument list, never through a shell.
- Only `https://github.com/owner/repo` is accepted — no ssh, `file://`, `git://`,
  arbitrary hosts, or submodules.
- Depth 1, 60s timeout, 200 MB ceiling, 3000-file ceiling. The caller controls
  the input, so every one of those is load-bearing.
- Credential prompts are disabled, so a private repo fails fast instead of
  hanging the worker.
- The container runs unprivileged.

## Business model

| Tier | Price | What it is |
|---|---|---|
| CLI | Free, MIT | Every rule, any repo, no telemetry |
| Web scan | Free | Public repos, rate limited |
| Team | $19/repo/mo | GitHub App, private repos, score history, PR gating |

The CLI stays free and complete. The paid tier sells hosting and history, not
withheld rules — a dev tool that cripples its free version to force upgrades
loses the audience that would have recommended it.

## What to build next, in order

1. **JS/TS detector.** `await`-less `expect` is the same bug class and is rampant
   in Playwright and Jest. It roughly doubles the addressable audience and is the
   single highest-value addition.
2. **GitHub App.** This is the thing people actually pay for — a score on every
   PR, and a gate that blocks a new test which cannot fail.
3. **Score history.** Turns a one-off number into a metric a team watches.
4. **Stripe.** Only after there is a waitlist worth billing.

Do not build 4 before 1.
