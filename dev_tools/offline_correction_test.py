"""Offline test: a cached answer ("Yes") is rejected by a number-only field; the
bot waits, the 'user' (a timer) fixes it in the page, and the corrected value
replaces the cached answer. No LinkedIn, throwaway DB, dummy values."""
import os
import sys
import tempfile
import time

sys.path.insert(0, "/Users/rooney/Documents/jobbot")

from playwright.sync_api import sync_playwright

from jobbot import apply_linkedin as A
from jobbot.db import Store

Q = "Are you an immediate joiner ? If not, please specify your official notice period and remaining days, if serving."
HTML = f"""
<div id="modal"><div><p>Additional Questions</p>
 <div><p>{Q}*</p><div><input id="n" type="text" aria-label="{Q}" required oninput="v(this)"></div>
 <p id="err" style="display:none;color:red">Invalid input</p></div>
</div><footer><button>Back</button><button>Review</button></footer></div>
<script>function v(el){{document.getElementById('err').style.display = /^[0-9]+$/.test(el.value) ? 'none' : 'block';}}</script>
"""

store = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
key = A.canonicalize(Q)
store.put_answer(key, "Yes", "text", display_question=Q)  # the wrong cached answer
print("key:", key)

ok = True


def check(label, cond, detail=""):
    global ok
    ok &= bool(cond)
    print(("PASS" if cond else "FAIL") + f": {label} {detail}")


with sync_playwright() as p:
    b = p.chromium.launch(headless=True)

    # 1) user corrects the field -> corrected value saved over the cached one
    pg = b.new_page()
    pg.set_content(HTML)
    root = A._modal_root(pg)
    A._handle_step(root, store)
    check("bot filled the cached 'Yes'", pg.input_value("#n") == "Yes")
    check("LinkedIn-style error is showing", pg.is_visible("#err"))
    pg.evaluate("setTimeout(()=>{const i=document.getElementById('n'); i.value='0'; v(i);}, 3000)")
    t0 = time.time()
    r = A._await_correction(pg, root, store)
    got = store.get_answer(key)
    check("waited for the user, then continued", r is True, f"(after {time.time()-t0:.1f}s)")
    check("corrected answer replaced the cached one", got and got["answer"] == "0", f"(cached={got['answer'] if got else None})")

    # 2) form vanishes while waiting -> gives up cleanly, cached answer untouched
    store.put_answer(key, "Yes", "text", display_question=Q)
    pg2 = b.new_page()
    pg2.set_content(HTML)
    root2 = A._modal_root(pg2)
    A._handle_step(root2, store)
    pg2.evaluate("setTimeout(()=>document.getElementById('modal').remove(), 1500)")
    r2 = A._await_correction(pg2, root2, store)
    check("vanished form -> gives up", r2 is False and store.get_answer(key)["answer"] == "Yes")
    b.close()

print("ALL PASS" if ok else "SOME FAILED")
