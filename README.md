# jobbot — milestone 1: collector + store

Collects LinkedIn and Naukri job listings into a local SQLite database,
deduplicated, ready for the scoring and applying stages.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium

cp config.example.yaml config.yaml   # then edit your searches
```

## First run

```bash
python -m jobbot.cli collect --source linkedin --max 25
```

A real Chrome window opens. The first time, you'll be prompted to log in by
hand — do it, press Enter, and the session persists in `~/.jobbot/chrome-profile`
for every future run. **No credentials are stored in this project.**

```bash
python -m jobbot.cli stats
python -m jobbot.cli list --status new --limit 30
python -m jobbot.cli export --out jobs.csv
```

## How it's put together

| File | Role |
|---|---|
| `db.py` | SQLite schema + upsert. Re-runs never clobber fit scores or status. |
| `browser.py` | Persistent Playwright context, randomised pacing, selector fallback helpers. |
| `selectors.py` | **Every CSS selector.** When collection breaks, patch here first. |
| `collect_linkedin.py` | Walks search pages, clicks cards, expands and stores full JDs. |
| `collect_naukri.py` | Two passes: cheap list harvest, then optional detail-page fetch. |
| `cli.py` | `collect` / `list` / `stats` / `export`. |

The `jobs` table already carries `fit_score`, `fit_reason` and a `status`
lifecycle (`new → scored → queued → applied`), plus an `answers` table for the
application-form answer bank. Milestones 2 and 3 fill those in without a
schema migration.

## Things that will bite you

- **Selectors drift.** Both sites rewrite their DOM every few weeks. Zero cards
  collected almost always means `selectors.py` is stale, not that the search
  is empty. Every extraction uses a fallback chain to soften this.
- **Rate limits are real.** Delays between cards and pages are randomised on
  purpose. LinkedIn in particular flags rapid, perfectly-timed traversal.
  Leave `applications_per_day` in the low teens.
- **Terms of service.** LinkedIn's User Agreement prohibits automated access;
  Naukri's terms are similar. The realistic risk is account restriction or a
  permanent ban, and it lands on your own account. Running this at a human
  pace, on your own profile, is the difference between "power user" and
  "obvious bot" — but it isn't zero risk, and the risk is yours to accept.
- **External ATS.** Jobs that redirect to Workday, Greenhouse, Lever or Taleo
  have completely different form flows. The collector flags them (`easy_apply = 0`
  on LinkedIn, `EX` in Naukri output) so the applier can skip them.

## Roadmap

2. **Scorer** — feed JD + your resume to the Claude API, get a 0–100 fit score
   and a one-line reason. This is what makes "semi-auto" safe: only jobs above
   your threshold get auto-applied, everything else lands in a review queue.
3. **Applier** — LinkedIn Easy Apply modal walker with the answer bank.
   Unknown question → pause and ask you once, cache the answer forever.
4. **Review dashboard** — small local Flask/HTMX page over the same SQLite file.
