# jobbot — Quickstart

A semi-automated job-application bot for LinkedIn and Naukri, driven from this
folder with Claude Code. It searches, scores fit against your resume,
optionally tailors a resume per job (for ATS keyword matching), and fills out
applications in a real (visible) Chrome window — either pausing for you to
hit Submit, or submitting on its own once you trust it.

This doc is for a **new person setting this up for the first time**. Read the
checklist, answer the questions inline, then follow the steps in order.

---

## 1. Before you start — answer these questions

Go through this list once. Each answer maps to a specific line in
`config.yaml` (you'll edit it in Step 3).

- [ ] **Where's your resume file?** PDF or DOCX, one file.
      → `profile.resume_path`
- [ ] **What's the exact filename of the resume already saved to your
      LinkedIn profile** (Job Home → Application settings → saved resumes)?
      The applier picks this one by name on the Easy Apply resume step.
      → `profile.linkedin_resume_name`
- [ ] **What roles are you targeting?** (job titles / keywords, LinkedIn
      supports `OR`, e.g. `"Business Analyst" OR "Product Owner"`)
      → `searches.linkedin[].keywords` / `searches.naukri[].keywords`
- [ ] **What location(s)?** One LinkedIn search = one location; add a
      separate entry under `searches.linkedin` per city. Naukri's `location`
      field accepts a comma-separated list in one search.
      → `searches.linkedin[].location` / `searches.naukri[].location`
- [ ] **How recent should postings be?** `day` / `week` / `month` (LinkedIn),
      or a number of days (Naukri).
      → `posted_within` / `posted_within_days`
- [ ] **Remote, hybrid, onsite, or all three?**
      → `searches.linkedin[].workplace`
- [ ] **Your years of experience?** Naukri uses this as a search filter.
      → `searches.naukri[].experience_years`
- [ ] **What's an auto-reject for you?** (night shifts, unpaid, commission
      only, a specific company, etc.)
      → `profile.deal_breakers`
- [ ] **Do you have an Anthropic API key?** Needed for two *optional* stages:
      fit-scoring and per-job resume tailoring. Get one at
      <https://console.anthropic.com/settings/keys>. Skip both stages (see
      Step 5) if you don't want to use one.
      → exported as the `ANTHROPIC_API_KEY` environment variable, **never**
      written into `config.yaml`
- [ ] **What fit score is "good enough" to auto-apply?** (0–100; only used if
      you run the scoring stage) Start high (80+), lower it once you trust it.
      → `profile.auto_apply_threshold`
- [ ] **Review each application before it submits, or let it submit itself?**
      This is the single biggest risk lever in the whole project — see
      **Section 6** before you flip it.
      → `applier.auto_submit`
- [ ] **How many applications per day are you comfortable with?** LinkedIn
      and Naukri both watch for bot-like pacing; higher numbers = higher
      account-restriction risk. Start low (10–20) for the first few runs.
      → `limits.applications_per_day`

---

## 2. Install

```bash
cd ~/Documents/jobbot
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

## 3. Configure

```bash
cp config.example.yaml config.yaml   # config.yaml is gitignored -- never commit it
```

Open `config.yaml` and fill in every value from your Step-1 answers. The
comments in the file explain each field; `searches.linkedin` and
`searches.naukri` each take a list, so add one entry per city/role
combination you want covered.

## 4. First login

```bash
python -m jobbot.cli collect --source linkedin --max 10
```

A real, visible Chrome window opens (not headless — you need to see it the
first time). If you're not logged in, the bot pauses and asks you to log in
by hand in that window, then continue. That session is saved to
`~/.jobbot/chrome-profile` and reused on every future run — **you only log in
once**, and no password is ever stored by this project.

Do the same once for Naukri:

```bash
python -m jobbot.cli collect --source naukri --max 10
```

## 5. The pipeline

Each stage is a separate command, run in order. `jobs.db` (SQLite) tracks
every job through a status lifecycle: `new → scored → queued → applied`.

```bash
# 1. Collect postings matching your configured searches
python -m jobbot.cli collect --source all

# 2. (Optional) Score fit against your resume via Claude -- needs ANTHROPIC_API_KEY
python -m jobbot.cli score

# 3. (Optional) Generate a tailored, ATS-keyword-matched resume per queued job
python -m jobbot.cli tailor

# 4. Apply
python -m jobbot.cli apply --source linkedin --limit 5
python -m jobbot.cli apply --source naukri --limit 5
```

**Skipping scoring:** if you'd rather not spend API credits or don't have a
key yet, you can send every freshly collected job straight to the apply
queue instead of running `score`:

```bash
python -m jobbot.cli list --status new --limit 5   # sanity check first
python3 -c "
from jobbot.db import Store
s = Store('jobs.db')
s.conn.execute(\"UPDATE jobs SET status='queued' WHERE status='new'\")
s.conn.commit()
"
```

Without a tailored resume (Step 3 skipped, or no `resume_path` on the job),
the applier always falls back to your fixed saved resume
(`profile.linkedin_resume_name`).

Always run `apply` with a small `--limit` the first few times and watch it
work. `--limit` counts real application attempts only — expired or
already-applied postings are skipped for free and don't count against it.

## 6. `applier.auto_submit` — read this before you flip it to `true`

- **`false` (default, recommended to start):** the bot fills the entire form
  and stops one click before Submit. You review, then click Submit yourself.
  Nothing is ever sent without your eyes on it.
- **`true`:** the bot clicks Submit itself, unattended. There is no undo on a
  submitted application. Only enable this once you've watched it fill out
  enough real forms correctly that you trust it — and even then, keep
  `limits.applications_per_day` low and check in on long runs.

## 7. When the bot doesn't know an answer

The first time it hits a question it hasn't seen (salary, notice period,
sponsorship, etc.), it **pauses and asks** — either you answer it directly in
the browser window (the bot watches for the change) or you type the answer
in the terminal. That answer is saved and reused automatically for every
future job with the same question. The bot never guesses a personal answer
on its own.

## 8. External-site applications (advanced, mostly manual)

Some LinkedIn/Naukri postings redirect to an external ATS (Workday, Oracle
Cloud Recruiting, iCIMS, etc.) instead of an in-platform Easy Apply. These
are **not** handled by `jobbot apply` — that command only fills LinkedIn's
own modal or Naukri's own flow, and cleanly skips anything that requires an
external site.

Applying to those is a slower, mostly hand-driven process done ad hoc
(not a single CLI command): the bot pre-fills what it safely can (email,
password, resume upload, known-safe fields) using one fixed set of sign-up
credentials for every external site, and a real person clicks the actual
"Create Account" / "Submit" buttons — some of these platforms actively
detect and reject fully-scripted account creation. If you want to go down
this path, ask your Claude Code session to walk you through it live; it
isn't something to run unattended.

## 9. Troubleshooting

- **`Opening in existing browser session` / a run won't start:** a previous
  run crashed and left the profile locked.
  ```bash
  pkill -f "ms-playwright.*Google Chrome for Testing"
  rm -f ~/.jobbot/chrome-profile/SingletonLock
  ```
- **Zero jobs collected:** almost always a stale CSS selector, not an empty
  search. Check `jobbot/selectors.py` first.
- **`debug_*.png` files in the project root:** failure screenshots from the
  applier. Safe to delete; open them if a run reports something odd.
- **Terms of service:** LinkedIn's and Naukri's user agreements prohibit
  automated access. The realistic risk is account restriction, and it's on
  your own account — running at a human pace, with the daily caps left in
  place, is what keeps this from looking like an obvious bot. The risk is
  yours to accept.

## 10. Everyday commands, once set up

```bash
python -m jobbot.cli stats                        # counts by status
python -m jobbot.cli list --status queued          # what's about to be applied to
python -m jobbot.cli export --out jobs.csv         # dump everything to CSV
```
