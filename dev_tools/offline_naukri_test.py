"""Offline test of apply_naukri against a mock reproducing the real chat-widget
DOM captured live on 2026-09-22 (contenteditable input box, class-based
selectors, dynamic per-session element ids). No Naukri access, throwaway DB."""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from playwright.sync_api import sync_playwright

from jobbot import apply_naukri as N
from jobbot.db import Store

JOB = {
    "title": "Product Owner",
    "company": "Acme Corp",
    "url": "about:blank",  # overwritten per test via page.set_content
    "external_id": "12345",
    "easy_apply": 1,
}


def chat_html(rid="abc123", question="How many years of experience do you have in SQL?"):
    """Mirrors the real captured structure closely enough for selector tests."""
    return f"""
    <button id="apply-button">Apply</button>
    <div class="_chatBotContainer chatbot_DrawerContentWrapper" id="_{rid}Container">
      <div class="chatbot_Nav"><div class="chatBot chatBot-ic-cross"></div></div>
      <div class="chatbot_MessageContainer">
        <ul class="list">
          <li class="botItem chatbot_ListItem"><div class="botMsg msg"><div><span>{question}</span></div></div></li>
        </ul>
      </div>
      <div class="footerInputBoxWrapper">
        <div id="userInput_{rid}InputBox" class="textArea" contenteditable="true"
             data-placeholder="Type message here..."></div>
      </div>
      <div class="sendMsgbtn_container"><div class="sendMsg disabled" tabindex="0">Save</div></div>
    </div>
    """


ok = True


def check(label, cond, detail=""):
    global ok
    ok &= bool(cond)
    print(("PASS" if cond else "FAIL") + f": {label} {detail}")


store = Store(os.path.join(tempfile.mkdtemp(), "t.db"))

with sync_playwright() as p:
    b = p.chromium.launch(headless=True)

    # 1) Cached answer is typed into the contenteditable box
    store.put_answer(N.canonicalize("How many years of experience do you have in SQL?") or "x",
                      "12", "text", display_question="How many years of experience do you have in SQL?")
    page = b.new_page()
    page.set_content(chat_html())
    q = N._latest_bot_question(page)
    check("reads the bot's question text", "SQL" in q, f"(got {q!r})")
    ok_ans = N._answer_question(page, store, q)
    typed = page.inner_text("[contenteditable]")
    check("cached answer typed into contenteditable box", ok_ans and typed == "12", f"(box text={typed!r})")

    # 2) Success detection from the saveApply URL shape actually observed live
    url_ok = ("https://www.naukri.com/myapply/saveApply?strJobsarr=[12345]&applytype=single"
              "&multiApplyResp=%7B%2212345%22%3A200%7D&jobTitle=Product%20Owner")
    check("recognizes a real success URL", N._apply_result_from_url(url_ok, "12345") == "submitted")

    url_fail = url_ok.replace("200", "500")
    check("recognizes a non-200 result as error", N._apply_result_from_url(url_fail, "12345") == "error")

    check("a normal job URL is not mistaken for a result", N._apply_result_from_url(
        "https://www.naukri.com/job-listings-product-owner-98765", "12345") is None)

    # 3) Chip-style multiple-choice question, cached answer clicked
    chip_html = """
    <div class="chatbot_MessageContainer">
      <ul class="list"><li class="botItem chatbot_ListItem"><div class="botMsg msg"><div><span>Are you willing to relocate?</span></div></div></li></ul>
      <button class="chip">Yes</button>
      <button class="chip">No</button>
    </div>
    """
    store.put_answer(N.canonicalize("Are you willing to relocate?") or "y", "Yes", "chip",
                      display_question="Are you willing to relocate?")
    page2 = b.new_page()
    page2.set_content(chip_html)
    page2.evaluate("document.querySelectorAll('.chip').forEach(c => c.onclick = () => c.setAttribute('data-clicked','1'))")
    handled = N._answer_question(page2, store, "Are you willing to relocate?")
    clicked = page2.evaluate("[...document.querySelectorAll('.chip')].map(c => c.getAttribute('data-clicked'))")
    check("cached chip answer clicked", handled and clicked == ["1", None], f"(clicked={clicked})")

    # 4) End-to-end via apply_to_job() itself, which does its own page.goto()
    # first thing -- serve the mock over a real local HTTP server so that
    # navigation actually loads it (set_content would be wiped by the goto).
    # auto_submit=True must click Save itself, with no input() call blocking
    # the run, and must return 'submitted' from the real saveApply URL shape.
    import http.server
    import threading

    rid = "e2e1"
    html4 = f"""<html><body>
    <button id="apply-button">Apply</button>
    {chat_html(rid=rid, question="How many years of experience do you have in SQL?")}
    <script>
      const box = document.getElementById('userInput_{rid}InputBox');
      new MutationObserver(() => {{
        if (box.innerText.trim()) document.querySelector('.sendMsg').classList.remove('disabled');
      }}).observe(box, {{childList: true, characterData: true, subtree: true}});
      document.querySelector('.sendMsg').addEventListener('click', () => {{
        history.pushState({{}}, '', '/myapply/saveApply?strJobsarr=[12345]&applytype=single'
          + '&multiApplyResp=%7B%2212345%22%3A200%7D&jobTitle=Product%20Owner');
      }});
    </script>
    </body></html>"""

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(html4.encode())

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    store.put_answer(N.canonicalize("How many years of experience do you have in SQL?") or "x",
                      "12", "text", display_question="How many years of experience do you have in SQL?")
    page4 = b.new_page()
    job4 = dict(JOB)
    job4["url"] = f"http://127.0.0.1:{srv.server_port}/"
    outcome = N.apply_to_job(page4, store, job4, auto_submit=True)
    check("auto_submit clicks Save itself and detects success, no input() block",
          outcome == "submitted", f"(got {outcome!r})")
    srv.shutdown()

    # 5) Silent success: clicking Apply submits via a background request (no
    # URL change, no recognised widget), but the page replaces its Apply
    # button with the word "Applied" -- confirmed live behavior. The fallback
    # check in apply_to_job() must catch this rather than report failure.
    silent_html = (
        "<html><body><button id='apply-button'>Apply</button>"
        "<script>"
        "document.getElementById('apply-button').addEventListener('click', function() {"
        "  this.replaceWith(document.createTextNode('Applied'));"
        "});"
        "</script></body></html>"
    )

    class H2(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(silent_html.encode())

        def log_message(self, *a):
            pass

    srv2 = http.server.HTTPServer(("127.0.0.1", 0), H2)
    threading.Thread(target=srv2.serve_forever, daemon=True).start()
    page5 = b.new_page()
    job5 = dict(JOB)
    job5["url"] = f"http://127.0.0.1:{srv2.server_port}/"
    outcome5 = N.apply_to_job(page5, store, job5, auto_submit=True)
    check("silent background-submit caught via the 'Applied' page-state fallback",
          outcome5 == "submitted", f"(got {outcome5!r})")
    srv2.shutdown()

    # 6) Already-applied pre-check: a job page that already shows "Applied"
    # must be recognized before any Apply click is attempted, so a re-run
    # never wastes an attempt (or worse, double-applies) on it.
    applied_html = "<html><body><span>Applied</span></body></html>"

    class H3(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(applied_html.encode())

        def log_message(self, *a):
            pass

    srv3 = http.server.HTTPServer(("127.0.0.1", 0), H3)
    threading.Thread(target=srv3.serve_forever, daemon=True).start()
    page6 = b.new_page()
    job6 = dict(JOB)
    job6["url"] = f"http://127.0.0.1:{srv3.server_port}/"
    outcome6 = N.apply_to_job(page6, store, job6, auto_submit=True)
    check("already-applied job recognized before any click", outcome6 == "already_applied", f"(got {outcome6!r})")
    srv3.shutdown()

    b.close()

store.close()
print("ALL PASS" if ok else "SOME FAILED")
