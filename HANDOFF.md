# jobbot -- project handoff

Semi-automated job applications on LinkedIn (Python + Playwright). This file lets a fresh
Claude Code session in this folder continue exactly where the last one stopped.
Last updated: end of the 2026-09-21 session.

## What it does

1. **Collect** (`jobbot/collect_linkedin.py`, `collect_naukri.py`) -> `jobs.db` (SQLite), deduped.
2. **Score** (`scorer.py`) -- Claude API fit score 0-100; jobs >= `profile.auto_apply_threshold` become `queued`.
3. **Tailor** (`tailor_resume.py`) -- per-job resume; NOT used by the applier any more (see below).
4. **Apply** (`apply_linkedin.py`) -- opens each queued LinkedIn job, walks the Easy Apply modal,
   fills answers from the `answers` table, submits (`applier.auto_submit`).
   Naukri has no applier; queued Naukri jobs are skipped.

## State at end of session

- 36 jobs applied, ~68 queued (many may be expired / already applied -- the applier detects and
  skips those cheaply), 24 new-but-unscored, 56 scored below threshold, ~9 skipped.
- `config.yaml`: **`applier.auto_submit: true`** (user's explicit choice -- the next `apply` run
  submits for real), `limits.applications_per_day: 50`, `profile.auto_apply_threshold: 60`,
  `profile.linkedin_resume_name: "Rohit Prasad.pdf"` (saved LinkedIn resume the applier always picks).
- The user's answers (CTC, notice period, experience years, etc.) live in the `answers` table of
  `jobs.db` -- deliberately NOT copied into any doc.

## How to run

```bash
cd ~/Documents/jobbot && source .venv/bin/activate
python -m jobbot.cli stats
python -m jobbot.cli apply --limit 5      # --limit counts real attempts, not expired/already-applied skips
```

- A Chrome window opens. If LinkedIn shows logged out, `apply` prompts you to log in by hand.
- **Unknown question** -> the log says `Waiting -- answer it in the browser window`. The user answers
  IN THE LINKEDIN WINDOW (types / clicks); the bot reads the value once it is stable ~2s and saves it
  to `answers`. Typing the answer in the terminal also works.
- A validation error ("Invalid input", "This field is required") -> the bot waits for the user to
  fix the field, saves what changed, then continues (`_await_correction`).
- If a run crashes, an orphaned automation Chrome may hold the profile:
  `pkill -f "ms-playwright.*Google Chrome for Testing"`.
- `debug_*.png` in the project root are failure screenshots; safe to delete.

## Fixes made this session (root causes, so they are not re-derived)

- **Modal kept closing on text-only question pages.** Cause: with no `<label>`s in the form,
  `_modal_root` walked up to a page-level container that included the site footer's
  **"Select language"** `<select>`; the bot "answered" it and LinkedIn reloaded the page, closing
  the Easy Apply window. Fixed: root = nearest ancestor with any form control; `_is_site_chrome()`
  skips header/footer/nav/search/"Select language"; `_looks_like_language_picker()` guard;
  stale `select language` answer deleted.
- New LinkedIn markup: questions are `<p>` text; radios are `fieldset[role=radiogroup]` >
  `div[role=radio][aria-label=QUESTION]`; text inputs carry the question in `aria-label`.
  `_iterate_questions` / `_fill_field` / `_pick_radio` handle it. The resume picker is also a
  radiogroup (file names) -- explicitly ignored.
- Resume: `_select_saved_resume()` clicks the `[role=radio]` named `linkedin_resume_name`,
  expands "Show more resumes", **verifies it is checked**, and stops the job if not.
- Expired postings redirect to a search page -> guarded by a `/jobs/view/` URL check.
  "Application submitted" card -> outcome `already_applied`, status `applied`.
- `cli.py apply`: `--limit` = real attempts; visits at most 40 queued jobs; 20s gap after real
  attempts, 3-6s after skips. Modal-open wait is 20s (slow spinner on some jobs).
- Diagnostics on failure: `_note` (what the bot did), in-page trace, page navigation/reload events
  (`_dump_diagnostics`). This is how the language-picker bug was found.
- `answer_aliases.py`: separate keys for `serving_notice_period` (yes/no), `notice_period` (days),
  `immediate_joiner` (yes/no), `immediate_joiner_notice_days` (number field), `current_salary`
  (incl. "current/last CTC"). "Authorized to work in <country>" is deliberately NOT aliased
  (opposite of sponsorship, differs per country).
- LinkedIn number boxes reject commas: store amounts as plain digits.

## Tomorrow's main goal (user's request): understand the same question in different wording

Today the bot only recognises a question if its text exactly matches a saved one or hits a hand-written
regex in `answer_aliases.py`. "What is Your CTC?", "Current CTC", "What's your current / last drawn
salary package in LPA?" were three separate questions and each interrupted the user. The user wants the
bot to spot that they mean the same thing and answer on its own.

Suggested design (agree it with the user first):
- **Match by meaning, not wording.** Turn each question into a normalised "slot" (topic + subject +
  qualifier + unit + widget type/options), e.g. `salary / current / rupees-per-year / number`. Learn
  slots from questions already answered; match new questions to existing slots.
- **Keep answer VALUES local.** If an LLM (Claude API) is used, send only the question text + the list of
  known slot names and get back "slot X" or "none". The local code then looks up the value, so CTC,
  phone, etc. never leave the machine. Needs `ANTHROPIC_API_KEY` at run time -- ask the user.
- **Cache every mapping** in the DB (question text -> slot) so each wording is resolved once.
- **Only auto-answer when confident.** Low confidence / ambiguous -> ask in the browser as today, then
  learn the new wording -> slot mapping from the user's answer.
- **Pitfalls seen today (must be handled):**
  - Same topic, different answer *format*: current CTC as full rupees (`2400000`) vs in LPA (`24`);
    notice period as days vs Yes/No "serving notice?"; "immediate joiner" Yes/No vs number-of-days field.
    A slot needs a unit/format, and the value must be converted or asked separately.
  - **Ambiguous wording:** "What is Your CTC?" could be current or expected -- ask, don't guess.
  - **Opposite polarity:** "authorized to work in X?" vs "require sponsorship?"; and it is country-specific.
  - **Different skill = different slot:** years with SQL vs Jira vs Banking must not merge; but
    "experience with Business Analysis" and "experience as a Business Analyst / Technical BA" are the
    same slot.
  - Never auto-fill personal/legal answers on a fuzzy match below the confidence threshold.
- Test offline with a table of reworded question pairs (should match / must NOT match) before any live run.
- Also finish the placeholder-answer cleanup first (street / state / zip / skill set) so bad values
  are not matched into new forms.

## Open items / TODO for next session

1. **User takes over a page by hand.** When the user fills a page themselves and clicks Next (or even
   Submit), the bot loses track: it logs "form went away", saves nothing, and may record the job as
   `skipped`/`awaiting_review`. Wanted: detect "modal still open but our field is gone" -> re-scan the
   new step; detect a hand submit ("application was sent" / "Application submitted") -> record
   `submitted`. Until then fix statuses manually (`applications.outcome`, `jobs.status`).
2. **Unsupported widgets** (user fills them by hand; the bot says "no changed answer was found to save"):
   checkbox-group questions (e.g. SAFe certification, years-of-experience checkbox lists) and the
   **"Location (city)"** search-as-you-type combobox on contact-info pages. Inspect the DOM first
   (read-only outline script -- see below), then add support + offline test.
3. **Suspect saved answers** in `answers` (created from placeholder input): `street`, `state/province`,
   `zip/postal code`, `skill set`. They would be auto-filled into future forms. Ask the user: delete, or
   replace with real values.
4. Score the 24 `new` jobs: needs `ANTHROPIC_API_KEY` exported by the user
   (`python -m jobbot.cli score`).
5. No Naukri applier exists.
6. Volume: 36 applications in one day -- suggest smaller/slower batches to protect the account.
7. `README.md` still describes only milestone 1.

## Testing without touching LinkedIn

`dev_tools/` has offline tests against local mock pages (headless Chromium, throwaway DB, dummy values):
`offline_test.py`, `offline_resume_test.py`, `offline_browser_answer_test.py`,
`offline_language_test.py`, `offline_correction_test.py`, plus `login_only.py` (manual login helper).
Run e.g. `python dev_tools/offline_language_test.py` from the project root with the venv active.
Rule that paid off repeatedly: reproduce the DOM in a mock page + offline test BEFORE another live run.
To see a live form's structure without submitting, write a read-only Playwright script that opens the
job, clicks Easy Apply, fills the contact page, clicks Next up to the target page, and prints a compact
outline of the DOM (tags + id/for/type/name/role/aria-* + own text) -- never click Review/Submit.

## Working notes

- The user is at the machine during runs and answers questions in the browser window; post each new
  question in chat as one short line, and confirm each saved answer.
- Auto-submit and broad applying are intentional. No undo on a submitted application, so test
  fixes offline first and keep runs to `--limit 5`.
- Sometimes the user finishes a page by hand (see TODO 1) -- check the trace in the log before
  assuming the bot did something wrong.
- Never guess the user's personal answers (salary, notice period, years, legal status); ask.
