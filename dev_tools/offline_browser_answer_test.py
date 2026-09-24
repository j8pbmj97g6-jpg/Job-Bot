"""Offline test: unknown question answered *in the browser* gets cached.
A timer in the mock page plays the part of the user answering. No LinkedIn."""
import os
import sys
import tempfile
import time

sys.path.insert(0, "/Users/rooney/Documents/jobbot")

from playwright.sync_api import sync_playwright

from jobbot import apply_linkedin as A
from jobbot.db import Store

QT = "How many years of work experience do you have with Tableau?"
QS = "What is your current employment status?"
QR = "Are you willing to work night shifts?"

store = Store(os.path.join(tempfile.mkdtemp(), "t.db"))


def page_with(b, html):
    p = b.new_page()
    p.set_content(html)
    return p


ok = True


def check(label, cond, detail=""):
    global ok
    ok &= bool(cond)
    print(("PASS" if cond else "FAIL") + f": {label} {detail}")


with sync_playwright() as p:
    b = p.chromium.launch(headless=True)

    # 1) text answered in the browser after ~3s
    pg = page_with(b, f'<div><p>{QT}</p><div id="w"><input id="t" type="text" aria-label="{QT}"></div></div>'
                      "<script>setTimeout(()=>{document.getElementById('t').value='6'},3000)</script>")
    t0 = time.time()
    r = A._fill_field(None, pg.locator("#w"), QT, store)
    got = store.get_answer(A.canonicalize(QT) or QT)
    check("text answer captured", r is True and got and got["answer"] == "6", f"(took {time.time()-t0:.1f}s, cached={got['answer'] if got else None})")

    # 2) dropdown changed in the browser
    pg = page_with(b, f'<div id="w"><select id="s"><option>Select</option><option>Employed</option><option>Student</option></select></div>'
                      "<script>setTimeout(()=>{document.getElementById('s').selectedIndex=2},3000)</script>")
    r = A._fill_field(None, pg.locator("#w"), QS, store)
    got = store.get_answer(A.canonicalize(QS) or QS)
    check("dropdown choice captured", r is True and got and got["answer"] == "Student", f"(cached={got['answer'] if got else None})")

    # 2b) single-option dropdown (e.g. one saved email address): the browser
    # pre-selects it by default, so "wait until selectedIndex changes" can
    # never fire -- there's no second option to change to. Must resolve
    # immediately on its own, not hang until the 900s timeout. This is what
    # was silently blocking every field after it (e.g. phone number) in a
    # real run.
    QE = "Email address*"
    pg = page_with(b, f'<div id="w"><select id="e"><option>candidate@example.com</option></select></div>')
    t0 = time.time()
    r = A._fill_field(None, pg.locator("#w"), QE, store)
    elapsed = time.time() - t0
    got = store.get_answer(A.canonicalize(QE) or QE)
    check(
        "single-option dropdown resolves instantly, not left blocking",
        r is True and elapsed < 5 and got and got["answer"] == "candidate@example.com",
        f"(took {elapsed:.1f}s, cached={got['answer'] if got else None})",
    )

    # 3) radio chosen in the browser
    radio = lambda i, t: (f'<div role="radio" aria-label="{QR}"><div><input type="radio" name="g" id="r{i}">'
                          f'<label for="r{i}"></label></div><div><p>{t}</p></div></div>')
    pg = page_with(b, f'<style>input[type=radio]{{position:absolute;opacity:0}}</style><fieldset id="w" role="radiogroup">{radio(1,"Yes")}{radio(2,"No")}</fieldset>'
                      "<script>setTimeout(()=>{document.getElementById('r2').checked=true},3000)</script>")
    r = A._fill_field(None, pg.locator("#w"), QR, store)
    got = store.get_answer(A.canonicalize(QR) or QR)
    check("radio choice captured", r is True and got and got["answer"] == "No", f"(cached={got['answer'] if got else None})")

    # 4) a pre-checked radio is left alone and not cached as a 'user answer'
    QP = "Prefilled question?"
    pg = page_with(b, f'<fieldset id="w" role="radiogroup"><div role="radio" aria-label="{QP}"><input type="radio" name="h" id="p1" checked><label for="p1"></label><p>Yes</p></div>'
                      f'<div role="radio" aria-label="{QP}"><input type="radio" name="h" id="p2"><label for="p2"></label><p>No</p></div></fieldset>')
    r = A._fill_field(None, pg.locator("#w"), QP, store)
    check("pre-checked radio skipped, nothing cached", r is True and store.get_answer(QP) is None)

    # 5) form disappears while waiting -> gives up cleanly (no answer cached)
    QC = "Will this vanish?"
    pg = page_with(b, f'<div id="w"><input id="c" type="text" aria-label="{QC}"></div>'
                      "<script>setTimeout(()=>{document.getElementById('w').remove()},1500)</script>")
    t0 = time.time()
    r = A._fill_field(None, pg.locator("#w"), QC, store)
    check("vanished form handled", r is False and store.get_answer(QC) is None, f"(gave up after {time.time()-t0:.0f}s)")
    # 6) page is reloaded while waiting (wipes in-page state): the Python-side
    #    event log must still show it. Uses a real served page so reload works.
    import http.server, threading
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers()
            first = getattr(self.server, "hits", 0) == 0
            self.server.hits = getattr(self.server, "hits", 0) + 1
            body = ('<div id="w"><input id="c" type="text" aria-label="Reload me?"></div>'
                    "<script>setTimeout(()=>location.reload(),1500)</script>") if first else "<p>fresh page, no form</p>"
            self.wfile.write(body.encode())
        def log_message(self, *a): pass
    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    pg = b.new_page()
    pg.goto(f"http://127.0.0.1:{srv.server_port}/")
    r = A._fill_field(None, pg.locator("#w"), "Reload me?", store)
    check("reload while waiting handled", r is False)
    srv.shutdown()
    b.close()

print("ALL PASS" if ok else "SOME FAILED")
