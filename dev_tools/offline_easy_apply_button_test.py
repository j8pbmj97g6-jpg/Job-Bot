"""Offline test: the Easy Apply button selector must match both the usual
<button> markup AND the <a>-tag variant LinkedIn sometimes uses (confirmed
live: 'Easy Apply to this job' rendered as an anchor, not a button -- jobs
with that variant were being wrongly treated as requiring an external site,
both at collection time and at apply time)."""
import sys

sys.path.insert(0, "/Users/rooney/Documents/jobbot")

from playwright.sync_api import sync_playwright

from jobbot.apply_linkedin import _wait_visible
from jobbot.selectors import LINKEDIN as S

ok = True


def check(label, cond, detail=""):
    global ok
    ok &= bool(cond)
    print(("PASS" if cond else "FAIL") + f": {label} {detail}")


with sync_playwright() as p:
    b = p.chromium.launch(headless=True)

    # Usual case: a <button>
    pg = b.new_page()
    pg.set_content("<button class='jobs-apply-button' aria-label='Easy Apply to this job'>Easy Apply</button>")
    found = _wait_visible(pg, S["easy_apply_button"], attempts=2, delay=0.1)
    check("button variant matched", found is not None)

    # The variant that was silently falling through: an <a>, not a <button>
    pg2 = b.new_page()
    pg2.set_content("<a href='#' aria-label='Easy Apply to this job'>Easy Apply</a>")
    found2 = _wait_visible(pg2, S["easy_apply_button"], attempts=2, delay=0.1)
    check("anchor (<a>) variant matched -- this was the live bug", found2 is not None)

    # A merely apply-adjacent link ("Easy Apply through Jobgether"-style
    # third-party text) must NOT false-positive if it doesn't carry the
    # real aria-label.
    pg3 = b.new_page()
    pg3.set_content("<a href='#' aria-label='Open external site'>Why Apply Through Jobgether?</a>")
    found3 = _wait_visible(pg3, S["easy_apply_button"], attempts=2, delay=0.1)
    check("unrelated apply-adjacent link is NOT matched", found3 is None)

    b.close()

print("ALL PASS" if ok else "SOME FAILED")
