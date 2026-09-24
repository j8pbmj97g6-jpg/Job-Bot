"""Offline test of _select_saved_resume against local mock pages. No LinkedIn."""
import json
import sys

sys.path.insert(0, "/Users/rooney/Documents/jobbot")

from playwright.sync_api import sync_playwright

from jobbot import apply_linkedin as A

NAME = "Rohit Prasad.pdf"
CSS = "<style>input[type=radio]{position:absolute;opacity:0;width:0;height:0}</style>"


def card(fname, cid, checked=False, works=True):
    click = "this.querySelector('input').checked=true" if works else ""
    return (f'<div role="radio" aria-label="{fname}" onclick="{click}"><div>'
            f'<input type="radio" name="cv" id="{cid}" {"checked" if checked else ""}>'
            f'<label for="{cid}"></label></div><div><p>{fname}</p></div></div>')


PAGES = {
    "resume step, other preselected": (
        CSS + '<fieldset role="radiogroup">' + card("Other Resume.pdf", "c2", True) + card(NAME, "c1") + "</fieldset>"
        + "<button>Upload resume</button>", "selected"),
    "review page (plain text only)": (
        f"<p>Resume</p><p>{NAME}</p><button>Submit application</button>", "not_a_resume_step"),
    "click does not register": (
        CSS + '<fieldset role="radiogroup">' + card(NAME, "c1", works=False) + "</fieldset><button>Upload resume</button>",
        "unverified"),
    "resume step without the file": (
        CSS + '<fieldset role="radiogroup">' + card("Other Resume.pdf", "c2", True) + "</fieldset>"
        + "<button>Upload resume</button>", "not_found"),
    "hidden behind Show more resumes": (
        CSS + '<fieldset role="radiogroup" id="fs">' + card("Other Resume.pdf", "c2", True) + "</fieldset>"
        + "<script>function more(btn){document.getElementById('fs').insertAdjacentHTML('beforeend', "
        + json.dumps(card(NAME, "c1")) + "); btn.remove();}</script>"
        + '<button onclick="more(this)">Show more resumes</button>', "selected"),
}

ok = True
with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    for label, (html, expected) in PAGES.items():
        page = b.new_page()
        page.set_content(html)
        got = A._select_saved_resume(page, NAME)
        extra = ""
        if expected == "selected":
            extra = f" | c1 checked={page.is_checked('#c1')}"
        status = "PASS" if got == expected else "FAIL"
        ok &= got == expected
        print(f"{status}: {label}: got {got!r}, expected {expected!r}{extra}")
        page.close()
    b.close()
print("ALL PASS" if ok else "SOME FAILED")
