"""Offline reproduction of the 'Select language' bug: a text-only question page
(no <label>s) with LinkedIn's language picker in the page footer. Changing the
picker would reload the real page; here it sets window.__lang. No LinkedIn."""
import os
import sys
import tempfile

sys.path.insert(0, "/Users/rooney/Documents/jobbot")

from playwright.sync_api import sync_playwright

from jobbot import apply_linkedin as A
from jobbot.db import Store

QS = [
    "How many years of work experience do you have with Visio?",
    "How many years of work experience do you have with Jira?",
    "How many years of work experience do you have with Tableau?",
]
LANGS = "".join(f"<option>Lang{i} (Language {i})</option>" for i in range(12))
HTML = f"""
<body>
<header><input aria-label="Search"></header>
<div id="modal"><div><p>Additional Questions</p>
{''.join(f'<div><p>{q}*</p><div><input type="text" aria-label="{q}" required></div></div>' for q in QS)}
</div><footer><button>Back</button><button>Review</button></footer></div>
<div class="page-footer"><label for="lang">Select language</label>
<select id="lang" onchange="window.__lang=true">{LANGS}<option selected>English (English)</option></select></div>
</body>"""

store = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
for q in QS:
    store.put_answer(A.canonicalize(q) or q, "7", "text", display_question=q)
store.put_answer("select language", "English (English)", "select")  # the stale answer that caused the reload

ok = True


def check(label, cond, detail=""):
    global ok
    ok &= bool(cond)
    print(("PASS" if cond else "FAIL") + f": {label} {detail}")


with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    pg = b.new_page()
    pg.set_content(HTML)

    root = A._modal_root(pg)
    qs = [q for q, _ in A._iterate_questions(root)]
    check("modal root finds exactly the 3 real questions", len(qs) == 3 and "Select language" not in qs, f"{qs}")

    body_qs = [q for q, _ in A._iterate_questions(pg.locator("body"))]
    check("even a page-wide root skips the language picker and search box", len(body_qs) == 3, f"{body_qs}")

    A._handle_step(root, store)
    vals = [pg.locator("input[aria-label]").nth(i + 1).input_value() for i in range(3)]
    check("known answers filled", vals == ["7", "7", "7"], f"{vals}")
    check("language picker untouched (no reload trigger)", pg.evaluate("window.__lang") is None and
          pg.input_value("#lang") == "English (English)")

    # last line of defence: the fill logic itself refuses the picker
    r = A._fill_field(None, pg.locator("#lang").locator("xpath=.."), "Select language", store)
    check("_fill_field refuses the language picker", r is True and pg.evaluate("window.__lang") is None)
    b.close()

print("ALL PASS" if ok else "SOME FAILED")
