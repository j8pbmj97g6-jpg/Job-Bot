"""Offline test: LinkedIn's "Location (city)" type-ahead combobox.

A plain .fill() sets the visible text but not the site's required underlying
selection -- validation keeps rejecting it ("This field is required"). The
fix types the answer, then confirms with Down-arrow + Enter (not a click on
the suggestion div, which was unreliable in live testing). The mock page
below plays the part of LinkedIn's widget: it only "commits" a value on
ArrowDown+Enter, so this test would fail against the old plain-.fill() code.
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, "/Users/rooney/Documents/jobbot")

from playwright.sync_api import sync_playwright

from jobbot import apply_linkedin as A
from jobbot.db import Store

Q = "Location (city)"

# Simulates the real widget: typing opens a suggestion list; only
# ArrowDown+Enter (not a plain value set, not a click) marks it "committed".
# A plain .fill() would leave __committed false, same as the real bug.
# Uses data-testid="typeahead-input" -- confirmed via live DevTools
# inspection to be the real, stable marker (there is NO role="combobox" on
# the actual element; a first version of this test used that and passed
# while the real selector silently never matched anything live).
COMBOBOX_HTML = f"""
<div id="w">
  <input id="loc" type="text" data-testid="typeahead-input" aria-label="{Q}"
         aria-autocomplete="list" aria-expanded="false" value="">
  <div id="listbox" role="listbox" style="display:none">
    <div role="option" id="opt1">Bengaluru, Karnataka, India</div>
  </div>
</div>
<script>
  window.__committed = false;
  window.__highlighted = false;
  const el = document.getElementById('loc');
  const opt = document.getElementById('opt1');
  el.addEventListener('input', () => {{
    document.getElementById('listbox').style.display = 'block';
    el.setAttribute('aria-expanded', 'true');
    window.__highlighted = false;
  }});
  el.addEventListener('keydown', (e) => {{
    if (e.key === 'ArrowDown') {{
      window.__highlighted = true;
      opt.setAttribute('aria-selected', 'true');
    }} else if (e.key === 'Enter') {{
      if (window.__highlighted) {{
        el.value = opt.innerText;
        window.__committed = true;
        document.getElementById('listbox').style.display = 'none';
      }}
    }}
  }});
</script>
"""

store = Store(os.path.join(tempfile.mkdtemp(), "t.db"))

ok = True


def check(label, cond, detail=""):
    global ok
    ok &= bool(cond)
    print(("PASS" if cond else "FAIL") + f": {label} {detail}")


def page_with(b, html):
    p = b.new_page()
    p.set_content(html)
    return p


with sync_playwright() as p:
    b = p.chromium.launch(headless=True)

    # 1) No cached answer, "typed in the terminal" path (simulated directly
    #    by pre-seeding the answer as if it came back from _await_answer) --
    #    exercise the actual type + ArrowDown + Enter sequence the fix adds.
    pg = page_with(b, COMBOBOX_HTML)
    import jobbot.apply_linkedin as mod
    orig_await = mod._await_answer
    mod._await_answer = lambda *a, **k: ("Bengaluru", "terminal")
    try:
        r = A._fill_field(None, pg.locator("#w"), Q, store)
    finally:
        mod._await_answer = orig_await
    committed = pg.evaluate("window.__committed")
    val = pg.locator("#loc").input_value()
    got = store.get_answer(A.canonicalize(Q) or Q)
    check(
        "typed answer confirmed via ArrowDown+Enter (not left as plain text)",
        r is True and committed is True and val == "Bengaluru, Karnataka, India",
        f"(committed={committed}, value={val!r})",
    )
    check("answer cached for next time", got is not None and got["answer"] == "Bengaluru")

    # 2) Cached answer on a fresh instance of the same field -- must re-type
    #    and re-confirm via keyboard every time (the input is per-page, so a
    #    cached string alone isn't enough; a plain .fill() would fail this).
    pg2 = page_with(b, COMBOBOX_HTML)
    r2 = A._fill_field(None, pg2.locator("#w"), Q, store)
    committed2 = pg2.evaluate("window.__committed")
    check("cached answer also confirmed via keyboard, not plain-filled", r2 is True and committed2 is True)

    # 3) User does it themselves in the browser (types AND picks a
    #    suggestion) -- bot must detect the settled value and just save it,
    #    not re-type over what the user did.
    store2 = Store(os.path.join(tempfile.mkdtemp(), "t2.db"))
    pg3 = page_with(b, COMBOBOX_HTML)
    pg3.evaluate(
        "() => setTimeout(() => { document.getElementById('loc').value = 'Chennai, Tamil Nadu, India'; "
        "window.__committed = true; }, 1500)"
    )
    r3 = A._fill_field(None, pg3.locator("#w"), Q, store2)
    got3 = store2.get_answer(A.canonicalize(Q) or Q)
    check(
        "user's own browser selection is captured, not overwritten",
        r3 is True and got3 is not None and got3["answer"] == "Chennai, Tamil Nadu, India",
    )

    # 4) Field already shows SOME text (e.g. browser/OS address autofill, or
    #    a leftover partial attempt) -- this is the actual bug that was seen
    #    live: stray text looks "filled" but was never run through
    #    LinkedIn's own selection flow, so it still fails validation. Must
    #    NOT be trusted/left alone -- must be cleared and properly
    #    re-selected (here via a cached answer, so no wait is needed).
    store3 = Store(os.path.join(tempfile.mkdtemp(), "t3.db"))
    store3.put_answer(A.canonicalize(Q) or Q, "Bengaluru", "combobox", display_question=Q)
    pg4 = page_with(
        b,
        COMBOBOX_HTML.replace('value=""', 'value="Mumbai, Maharashtra, India"'),
    )
    r4 = A._fill_field(None, pg4.locator("#w"), Q, store3)
    committed4 = pg4.evaluate("window.__committed")
    val4 = pg4.locator("#loc").input_value()
    check(
        "stray pre-existing text is not trusted -- cleared and properly re-selected",
        r4 is True and committed4 is True and val4 == "Bengaluru, Karnataka, India",
        f"(committed={committed4}, value={val4!r})",
    )

    b.close()

print("ALL PASS" if ok else "SOME FAILED")
