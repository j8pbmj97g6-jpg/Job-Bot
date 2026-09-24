"""Walks Naukri's 'Apply' flow for a queued job.

Naukri has no modal-with-Next-button flow like LinkedIn. Clicking Apply opens
a chat-style widget (class names are stable; the numeric ids in each element's
id attribute are per-session and must not be matched on) that asks a handful
of screening questions one at a time -- a free-text/number question answered
in a contenteditable box, or a multiple-choice question answered by clicking
one of a few chip-style buttons. There is no separate review page we've found
so far: once the last question is answered, the application appears to go
through immediately.

Deliberate design, more conservative than the LinkedIn applier: this flow is
new and unproven, so it NEVER auto-submits regardless of applier.auto_submit
in config.yaml. It fills what it can from the cached answer bank, and always
stops for you to click the final Save yourself once no more questions are
detected -- mirroring the very first, most-supervised runs of the LinkedIn
applier before it was trusted.

Success is detected from Naukri's own signal: after a real submission the
page navigates to a URL containing `myapply/saveApply` with a `multiApplyResp`
JSON param mapping the job's external id to HTTP 200.
"""

from __future__ import annotations

import json
import re
import time
from urllib.parse import parse_qs, unquote, urlparse

from playwright.sync_api import Page

from .answer_aliases import canonicalize
from .browser import pause
from .db import Store
from .selectors import NAUKRI as S

MAX_QUESTIONS = 15  # circuit breaker so a stuck chat can't loop forever

CHAT_CONTAINER = "._chatBotContainer, .chatbot_DrawerContentWrapper"
CHAT_INPUT = "[id^='userInput'].textArea[contenteditable='true']"
CHAT_SEND = ".sendMsg:not(.disabled)"
CHAT_SEND_ANY = ".sendMsg"
CHAT_CLOSE = ".chatBot-ic-cross"
CHAT_BOT_MESSAGE = ".botItem .botMsg"
CHAT_CHIP_OPTIONS = ".chatbot_MessageContainer button, .chatbot_MessageContainer [role='option'], .chatbot_MessageContainer li.option"


def _visible(page: Page, selector: str):
    try:
        loc = page.locator(selector).first
        if loc.count() and loc.is_visible():
            return loc
    except Exception:
        pass
    return None


def _wait_visible(page: Page, selector: str, attempts: int = 10, delay: float = 0.5):
    for _ in range(attempts):
        found = _visible(page, selector)
        if found:
            return found
        page.wait_for_timeout(int(delay * 1000))
    return None


def _page_shows_applied(page: Page) -> bool:
    """Fallback success signal for job pages whose Apply flow submits via a
    background request that never changes page.url (confirmed live: a job
    can end up genuinely 'Applied' even when no saveApply navigation and no
    chat widget were ever seen). Naukri replaces the Apply/Save button area
    with the plain word 'Applied' once a submission has gone through."""
    try:
        return page.get_by_text("Applied", exact=True).first.is_visible(timeout=2000)
    except Exception:
        return False


def _apply_result_from_url(url: str, external_id: str) -> str | None:
    """Returns 'submitted', 'error', or None (not a saveApply URL)."""
    if "myapply/saveApply" not in url:
        return None
    try:
        qs = parse_qs(urlparse(url).query)
        raw = qs.get("multiApplyResp", [None])[0]
        if not raw:
            return None
        resp = json.loads(unquote(raw))
        code = resp.get(str(external_id))
        return "submitted" if code == 200 else "error"
    except Exception:
        return None


def _latest_bot_question(page: Page) -> str:
    try:
        msgs = page.locator(CHAT_BOT_MESSAGE)
        n = msgs.count()
        if not n:
            return ""
        return msgs.nth(n - 1).inner_text(timeout=1500).strip()
    except Exception:
        return ""


def _current_chip_options(page: Page) -> list[str]:
    try:
        chips = page.locator(CHAT_CHIP_OPTIONS)
        return [t.strip() for t in chips.all_inner_texts() if t.strip()]
    except Exception:
        return []


def _answer_question(page: Page, store: Store, question: str) -> bool:
    """Answer one chat question, from the cache or by asking. Returns True if
    handled (including 'nothing to do'), False if the caller should stop."""
    if not question:
        return True
    lookup_key = canonicalize(question) or question
    cached = store.get_answer(lookup_key)

    chips = _current_chip_options(page)
    if chips:
        if cached and cached["answer"] in chips:
            page.locator(CHAT_CHIP_OPTIONS).filter(has_text=cached["answer"]).first.click(timeout=3000)
            return True
        print(f"\n  New question: \"{question}\"")
        print(f"  Options: {chips}")
        answer = input("  Type the option to select: ").strip()
        if not answer:
            return False
        for chip_text in chips:
            if answer.lower() in chip_text.lower():
                page.locator(CHAT_CHIP_OPTIONS).filter(has_text=chip_text).first.click(timeout=3000)
                store.put_answer(lookup_key, chip_text, "chip", display_question=question)
                return True
        return False

    box = _visible(page, CHAT_INPUT)
    if not box:
        return True  # nothing to fill -- maybe a pure informational message
    if cached:
        box.click(timeout=2000)
        box.fill("", timeout=2000)  # contenteditable: clear first
        page.keyboard.type(cached["answer"], delay=15)
        return True
    print(f"\n  New question: \"{question}\"")
    answer = input("  Your answer: ").strip()
    if not answer:
        return False
    store.put_answer(lookup_key, answer, "text", display_question=question)
    box.click(timeout=2000)
    page.keyboard.type(answer, delay=15)
    return True


def apply_to_job(page: Page, store: Store, job, auto_submit: bool = False) -> str:
    """Returns one of: 'submitted', 'already_applied', 'awaiting_review', 'skipped', 'error'."""
    print(f"\n--- {job['title']} | {job['company']} ---")
    page.goto(job["url"], wait_until="domcontentloaded", timeout=60_000)
    pause(2, 4)

    # A job we (or the user, by hand) already applied to shows the plain word
    # "Applied" in place of the Apply button. Confirmed live: re-clicking
    # Apply on an already-applied job is at best a wasted click.
    if _page_shows_applied(page):
        print("  Already applied -- this job shows 'Applied'.")
        return "already_applied"

    if not job["easy_apply"]:
        print("  Marked as 'Apply on company site' at collection time -- skipping.")
        return "skipped"

    if _visible(page, ", ".join(S["company_site_button"])):
        print("  This job redirects to the company's own site -- skipping.")
        return "skipped"

    apply_btn = _wait_visible(page, ", ".join(S["apply_button"]), attempts=8, delay=0.5)
    if not apply_btn:
        print("  No Apply button found -- may have expired.")
        return "skipped"
    apply_btn.click(timeout=5000)
    pause(1.5, 2.5)

    result = _after_apply_click(page, store, job, auto_submit)
    # Confirmed live: some jobs submit via a background request that never
    # changes page.url and never shows a widget our selectors recognise, so a
    # URL/widget-based "it failed" verdict can be wrong. Before reporting
    # anything other than a confirmed submission, check the page's own
    # Applied/Apply state as a last-resort second opinion.
    if result != "submitted" and _page_shows_applied(page):
        print("  Correcting: the page now shows 'Applied' -- it went through after all.")
        return "submitted"
    return result


def _after_apply_click(page: Page, store: Store, job, auto_submit: bool) -> str:

    # Some jobs apply instantly with no chat at all (profile already has
    # everything Naukri needs) -- check for the success URL before assuming
    # a chat widget is coming.
    result = _apply_result_from_url(page.url, job["external_id"])
    if result:
        print(f"  Applied instantly, no questions asked. ({result})")
        return result

    chat = _wait_visible(page, CHAT_CONTAINER, attempts=8, delay=0.5)
    if not chat:
        result = _apply_result_from_url(page.url, job["external_id"])
        if result:
            print(f"  Applied instantly, no questions asked. ({result})")
            return result
        debug_path = "debug_naukri_no_chat.png"
        try:
            page.screenshot(path=debug_path, full_page=True)
            print(f"  Clicked Apply but no chat widget and no success URL appeared -- "
                  f"saved {debug_path}, please share it.")
        except Exception:
            print("  Clicked Apply but no chat widget and no success URL appeared -- "
                  "leaving this one for you to finish by hand.")
        return "awaiting_review"

    for _ in range(MAX_QUESTIONS):
        question = _latest_bot_question(page)
        if question:
            print(f"  Question: {question}")
        if not _answer_question(page, store, question):
            print("  Leaving this application open for you to finish by hand.")
            return "awaiting_review"
        pause(0.5, 1.0)

        send = _visible(page, CHAT_SEND)
        if not send:
            # Button is present but disabled, or not found at all -- if a
            # question is still showing, something didn't take; stop rather
            # than guess. If the widget itself is gone, the flow finished.
            if _visible(page, CHAT_CONTAINER):
                print("  Send button never became active -- leaving this one for you to finish.")
                return "awaiting_review"
        else:
            # This may be the LAST question -- there's no distinct review step
            # in this flow, so the Save click itself can be the final submit.
            if auto_submit:
                send.click(timeout=5000)
                pause(1.5, 2.5)
            else:
                print("  Answer entered. Review it in the browser and click Save/Send")
                print("  yourself to submit, then come back here and press Enter.")
                input("  Press Enter once you've submitted (or closed the chat to skip)... ")
            pause(0.5, 1.0)
            result = _apply_result_from_url(page.url, job["external_id"])
            if result == "submitted":
                print("  Submitted.")
                return result
            # Not the last question after all -- the chat is still open with
            # more to answer. Fall through and keep looping either way.
            if _visible(page, CHAT_CONTAINER):
                new_question = _latest_bot_question(page)
                if new_question and new_question != question:
                    continue
            return result or "awaiting_review"

        pause(1.0, 1.5)
        new_question = _latest_bot_question(page)
        if new_question and new_question != question:
            continue  # another question appeared -- keep going

        result = _apply_result_from_url(page.url, job["external_id"])
        if result:
            print(f"  Application completed. ({result})")
            return result
        if not _visible(page, CHAT_CONTAINER):
            print("  Chat widget closed with no clear success signal -- "
                  "please check this one by hand.")
            return "awaiting_review"

    print(f"  Hit the {MAX_QUESTIONS}-question safety limit -- stopping.")
    return "awaiting_review"
