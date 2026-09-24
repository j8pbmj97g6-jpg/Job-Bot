"""Walks LinkedIn's Easy Apply modal for a queued job.

Deliberate design: by default this fills every question it can and stops
one click before Submit, printing a summary for you to check in the open
browser window. A wrong auto-submitted answer (notice period, CTC, a
misread yes/no) goes straight to a real recruiter with no undo -- that's
worth a few extra seconds of your attention until you trust it. Flip
applier.auto_submit: true in config.yaml once you've watched it get several
right in a row.

Unknown questions pause here and ask you in the terminal once; the answer
is cached in the `answers` table and reused automatically after that.
"""

from __future__ import annotations

import re
import select
import sys
import time
from pathlib import Path

from playwright.sync_api import Page

from .answer_aliases import canonicalize
from .browser import pause
from .db import Store
from .selectors import LINKEDIN as S

MAX_STEPS = 12  # circuit breaker so a stuck modal can't loop forever

# --- Diagnostics for "the Easy Apply window vanished" -----------------------
# A timestamped log of what the bot itself did, an in-page log of what the page
# saw (clicks, keys, focus, the modal's buttons disappearing), and a Python-side
# log of navigations/reloads (which survive a reload, unlike in-page state).
_NOTES: list[str] = []
_NOTE_T0 = [time.time()]


def _note(msg: str) -> None:
    _NOTES.append(f"{time.time() - _NOTE_T0[0]:.1f}s {msg}")


_SESSION_TRACE_JS = """
() => {
  if (window.__jbTrace) return;
  const t0 = Date.now();
  const log = window.__jbTrace = [];
  const push = (k, d) => log.push(((Date.now() - t0) / 1000).toFixed(1) + 's ' + k + (d ? ' ' + d : ''));
  const desc = e => !e || !e.tagName ? '' : e.tagName.toLowerCase() +
    (e.getAttribute('aria-label') ? '[' + e.getAttribute('aria-label').slice(0, 30) + ']' : '') +
    ' "' + (e.innerText || '').trim().slice(0, 30) + '"';
  document.addEventListener('keydown', e => push('keydown', e.key + ' on ' + desc(e.target)), true);
  document.addEventListener('click', e => push('click', desc(e.target)), true);
  document.addEventListener('visibilitychange', () => push('visibility', document.visibilityState), true);
  window.addEventListener('blur', () => push('window blur'));
  window.addEventListener('focus', () => push('window focus'));
  window.addEventListener('popstate', () => push('popstate', location.pathname));
  window.addEventListener('beforeunload', () => push('beforeunload'));
  const modalUp = () => [...document.querySelectorAll('button')]
    .some(b => /^(Next|Review|Submit application|Back)$/.test((b.innerText || '').trim()));
  let was = modalUp();
  new MutationObserver(() => {
    const now = modalUp();
    if (was && !now) push('MODAL BUTTONS GONE');
    if (!was && now) push('modal buttons present');
    was = now;
  }).observe(document.body, {childList: true, subtree: true});
  push('trace started; modal ' + (was ? 'up' : 'not up'));
}
"""


def _install_trace(page) -> None:
    try:
        page.evaluate(_SESSION_TRACE_JS)
    except Exception:
        pass


def _dump_diagnostics(page, events: list[str]) -> None:
    print("  --- what the bot did (last 20) ---")
    for n in _NOTES[-20:] or ["nothing recorded"]:
        print(f"    {n}")
    try:
        trace = page.evaluate("window.__jbTrace || []") or []
    except Exception:
        trace = []
    print("  --- what the page saw (last 20; empty if the page was reloaded) ---")
    for t in trace[-20:] or ["nothing recorded"]:
        print(f"    {t}")
    print("  --- page-level events: navigation / reload / crash ---")
    for e in events or ["none"]:
        print(f"    {e}")


def _visible(page: Page, selectors: list[str]):
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible():
                return loc
        except Exception:
            continue
    return None


def _wait_visible(page: Page, selectors: list[str], attempts: int = 8, delay: float = 0.5):
    """Retry _visible for a few seconds -- modals and their inner content
    often render a beat after the click that opens them."""
    for _ in range(attempts):
        found = _visible(page, selectors)
        if found:
            return found
        page.wait_for_timeout(int(delay * 1000))
    return None


_CHROME_JS = (
    "e => !!e.closest(\"[role='contentinfo'], [role='banner'], [role='navigation'], "
    "nav, header, #global-footer, .global-footer, .global-footer-compact\")"
)


def _is_site_chrome(text: str, loc) -> bool:
    """True for LinkedIn's own page furniture (the footer's language picker, the
    nav search box, anything in the page header/footer) -- never an application
    question. Acting on the language picker reloads the whole page and takes the
    open Easy Apply window with it."""
    if text.strip().lower() in ("select language", "search"):
        return True
    try:
        return bool(loc.evaluate(_CHROME_JS, timeout=1000))
    except Exception:
        return False


def _looks_like_language_picker(options: list[str]) -> bool:
    return len(options) > 10 and any("(English)" in o for o in options)


def _field_label(container) -> str:
    for sel in S["form_label"]:
        try:
            loc = container.locator(sel).first
            if loc.count():
                txt = loc.inner_text(timeout=1000).strip()
                if txt:
                    return txt
        except Exception:
            continue
    return ""


# Injected while we wait on the user: records the clicks/keys/focus changes the
# page sees, plus the moment the question's field is removed from the DOM, so a
# vanished Easy Apply window can be traced to what (if anything) preceded it.
_TRACE_JS = """
(q) => {
  if (window.__jbTrace) return;
  const t0 = Date.now();
  const log = window.__jbTrace = [];
  const push = (k, d) => log.push(((Date.now() - t0) / 1000).toFixed(1) + 's ' + k + (d ? ' ' + d : ''));
  const desc = e => !e || !e.tagName ? '' : e.tagName.toLowerCase() +
    (e.getAttribute('aria-label') ? '[' + e.getAttribute('aria-label').slice(0, 30) + ']' : '') +
    ' "' + (e.innerText || '').trim().slice(0, 30) + '"';
  document.addEventListener('keydown', e => push('keydown', e.key + ' on ' + desc(e.target)), true);
  document.addEventListener('click', e => push('click', desc(e.target)), true);
  document.addEventListener('visibilitychange', () => push('visibility', document.visibilityState), true);
  window.addEventListener('blur', () => push('window blur'));
  window.addEventListener('focus', () => push('window focus'));
  window.addEventListener('popstate', () => push('popstate', location.pathname));
  window.addEventListener('beforeunload', () => push('beforeunload'));
  const marker = [...document.querySelectorAll('input,textarea,select,[role=radio]')]
    .find(e => (e.getAttribute('aria-label') || '').startsWith(q.slice(0, 40)));
  if (marker) {
    new MutationObserver(() => {
      if (!document.contains(marker) && !window.__jbGone) { window.__jbGone = true; push('QUESTION FIELD REMOVED from page'); }
    }).observe(document.body, {childList: true, subtree: true});
  }
  push('trace started; marker ' + (marker ? 'found' : 'not found'));
}
"""


def _await_answer_inner(question: str, read_page, options, timeout: int, page, events: list[str]):
    """Wait for the user to answer an unknown question, either by filling the
    field in the browser window (read back via `read_page()`, which returns the
    current answer text or '') or by typing a line in the terminal.

    Returns (answer, source) where source is 'browser', 'terminal', 'closed'
    (the form went away while waiting) or 'timeout'. A browser answer must hold
    steady for ~2s so a half-typed number is never saved.
    """
    print(f"\n  New question: \"{question}\"")
    if options:
        print(f"  Options: {options}")
    print("  Waiting -- answer it in the browser window, or type the answer here.", flush=True)
    started = time.time()
    if page is not None:
        try:
            page.evaluate(_TRACE_JS, question)
        except Exception:
            pass
    deadline = started + timeout
    last, stable, failures = "", 0, 0
    while time.time() < deadline:
        try:
            if select.select([sys.stdin], [], [], 0)[0]:
                line = sys.stdin.readline().strip()
                if line:
                    return line, "terminal"
        except Exception:
            pass
        try:
            val = (read_page() or "").strip()
            failures = 0
        except Exception:
            val, failures = "", failures + 1
            if failures >= 4:
                print(f"  The form went away {time.time() - started:.0f}s after this question appeared.")
                if page is not None:
                    try:
                        page.screenshot(path="debug_modal_closed.png", full_page=True)
                        print("  Saved debug_modal_closed.png. Page events leading up to it:")
                        for line in (page.evaluate("window.__jbTrace || []") or [])[-15:]:
                            print(f"    {line}")
                        print("  Page-level events seen by the bot (navigation / reload / crash):")
                        for line in events or ["none"]:
                            print(f"    {line}")
                    except Exception as e:
                        print(f"  (couldn't read the page trace: {e})")
                return "", "closed"
        if val and val == last:
            stable += 1
            if stable >= 2:
                return val, "browser"
        else:
            last, stable = val, 0
        time.sleep(1)
    return "", "timeout"


def _await_answer(question: str, read_page, options: list[str] | None = None, timeout: int = 900, page=None):
    """See _await_answer_inner. Also records page-level events (navigations,
    reloads, crashes) from the Python side while waiting -- unlike the in-page
    trace, these survive a full page reload, which would wipe window state."""
    events: list[str] = []
    started = time.time()
    attached = []
    if page is not None:
        def make(name):
            def handler(arg=None):
                if name == "framenavigated" and arg is not page.main_frame:
                    return  # ignore sub-frames (ads, tracking iframes)
                url = getattr(arg, "url", "") or ""
                events.append(f"{time.time() - started:.1f}s {name} {url[:90]}".rstrip())
            return handler

        for name in ("framenavigated", "load", "domcontentloaded", "crash", "close"):
            h = make(name)
            page.on(name, h)
            attached.append((name, h))
    try:
        return _await_answer_inner(question, read_page, options, timeout, page, events)
    finally:
        for name, h in attached:
            try:
                page.remove_listener(name, h)
            except Exception:
                pass


def _fill_field(_unused_page, container, question: str, store: Store) -> bool:
    """Fill one form element. Returns True if it was handled."""
    # Common questions (phone, email, expected salary, years of experience,
    # etc.) get looked up under a canonical key so an answer given once is
    # reused regardless of how a particular company phrases the question.
    lookup_key = canonicalize(question) or question
    cached = store.get_answer(lookup_key)

    # Text / number / phone input
    text_in = container.locator(", ".join(S["text_input"])).first
    if text_in.count():
        current = text_in.input_value() if text_in.get_attribute("value") else ""
        if current:
            return True  # already prefilled by LinkedIn (e.g. from profile)
        if cached:
            text_in.fill(cached["answer"])
            store.conn.execute(
                "UPDATE answers SET times_used = times_used + 1 WHERE id = ?", (cached["id"],)
            )
            store.conn.commit()
            return True
        answer, source = _await_answer(
            question, lambda: text_in.input_value(timeout=2000), page=container.page
        )
        if not answer:
            return False
        # Save first: an answer must survive even if the fill below fails
        # (e.g. the modal closed while we waited).
        store.put_answer(lookup_key, answer, "text", display_question=question)
        if source == "terminal":
            text_in.fill(answer, timeout=8000)
        else:
            print(f"    Saved the answer you entered in the browser: {answer}")
        return True

    # Dropdown
    select_in = container.locator(", ".join(S["select_input"])).first
    if select_in.count():
        options = select_in.locator("option").all_inner_texts()
        if _looks_like_language_picker(options):
            return True  # site chrome, never a question -- changing it reloads the page
        if cached and cached["answer"] in options:
            select_in.select_option(label=cached["answer"])
            return True
        start_idx = select_in.evaluate("e => e.selectedIndex", timeout=2000)

        def read_select() -> str:
            idx = select_in.evaluate("e => e.selectedIndex", timeout=2000)
            return options[idx].strip() if idx != start_idx and 0 <= idx < len(options) else ""

        answer, source = _await_answer(question, read_select, options=options, page=container.page)
        if not answer:
            return False
        if source == "terminal":
            if answer not in options:
                return False
            select_in.select_option(label=answer)
        else:
            print(f"    Saved the option you chose in the browser: {answer}")
        store.put_answer(lookup_key, answer, "select", display_question=question)
        return True

    # Radio group
    radios = container.locator(", ".join(S["radio_input"]))
    if radios.count():
        # Only look at <label> tags for the option text (Yes/No, etc.) --
        # NOT <legend>, which is the question itself and would otherwise
        # get counted as a phantom first "option", shifting every real
        # option's index by one and corrupting the whole mapping.
        labels = container.locator("label").all_inner_texts()
        if not any(t.strip() for t in labels):
            # Newer markup: the <label> is empty and the option text lives in
            # the [role="radio"] wrapper around each input.
            labels = container.locator("[role='radio']").all_inner_texts()
        if cached:
            for i in range(radios.count()):
                lbl = labels[i] if i < len(labels) else ""
                if cached["answer"].lower() in lbl.lower():
                    _pick_radio(container, radios, i)
                    return True
        if any(radios.nth(i).is_checked() for i in range(radios.count())):
            return True  # already chosen (e.g. prefilled by LinkedIn) -- leave it

        def read_radio() -> str:
            for i in range(radios.count()):
                if radios.nth(i).is_checked(timeout=2000):
                    return (labels[i] if i < len(labels) else "").strip()
            return ""

        answer, source = _await_answer(question, read_radio, options=labels, page=container.page)
        if not answer:
            return False  # "" is a substring of every label -- never pick the first option by accident
        if source == "browser":
            print(f"    Saved the option you chose in the browser: {answer}")
            store.put_answer(lookup_key, answer, "radio", display_question=question)
            return True
        for i in range(radios.count()):
            lbl = labels[i] if i < len(labels) else ""
            if answer.lower() in lbl.lower():
                _pick_radio(container, radios, i)
                store.put_answer(lookup_key, lbl.strip(), "radio", display_question=question)
                return True
        return False

    return True  # unknown widget type -- don't block the whole form on it


def _pick_radio(container, radios, i: int) -> None:
    """Select the i-th radio option. Newer markup hides the <input> behind a
    clickable [role="radio"] wrapper, so click that when it's there."""
    wrappers = container.locator("[role='radio']")
    if wrappers.count() > i:
        wrappers.nth(i).click(timeout=3000)
    else:
        radios.nth(i).check(force=True)


def _modal_root(page: Page):
    """Locate the modal's content root.

    Find the visible Next/Review/Submit button (matched by its actual
    text) and walk up to the nearest ancestor that also contains form
    labels -- that ancestor is the modal's real content area, however
    LinkedIn currently wraps it.

    Deliberately does NOT fall back to generic role="dialog" / .artdeco-modal
    class selectors: those produced false positives (matching some
    unrelated hidden element already in the page) on jobs where the Easy
    Apply modal genuinely never opened, causing the code to barrel ahead as
    if a form existed when the page was just slow to load. No button found
    reliably means no modal is open -- report that honestly instead.
    """
    btn = (
        _visible(page, S["next_button"])
        or _visible(page, S["review_button"])
        or _visible(page, S["submit_button"])
    )
    if not btn:
        return None

    # "Contains a form control" -- not just a <label>/<legend>: newer question
    # pages (all text inputs) have no labels at all, and requiring one made
    # this walk up past the modal to a page-level container that also holds
    # the site footer's language picker.
    root = btn.locator(
        "xpath=ancestor::*[self::div or self::section]"
        "[.//label or .//legend or .//input or .//select or .//textarea][1]"
    )
    try:
        if root.count():
            return root.first
    except Exception:
        pass
    root = btn.locator("xpath=ancestor::div[2]")
    try:
        if root.count():
            return root.first
    except Exception:
        pass
    return None


def _visible_in(root, selectors: list[str]):
    for sel in selectors:
        try:
            loc = root.locator(sel).first
            if loc.count() and loc.is_visible():
                return loc
        except Exception:
            continue
    return None


def _iterate_questions(root):
    """Yield (question_text, container_locator) pairs, scoped to `root`
    (the modal dialog).

    Finds fields by their visible <label>/<legend> text and walks up to the
    nearest ancestor <div> or <fieldset> that actually contains the input --
    this is far more stable than guessing LinkedIn's current wrapper class
    names, which change every few months and silently break class-based
    selectors.
    """
    results = []
    seen_containers = set()
    for tag in ("label", "legend"):
        locs = root.locator(tag)
        n = locs.count()
        for i in range(n):
            loc = locs.nth(i)
            try:
                if not loc.is_visible():
                    continue
                text = loc.inner_text(timeout=500).strip()
                if not text:
                    continue
            except Exception:
                continue

            container = None

            if tag == "label":
                # Prefer the standard for/id association -- this is the
                # actual HTML mechanism for connecting a label to its field,
                # and gives an exact 1:1 match regardless of how LinkedIn
                # visually groups several questions under one shared
                # wrapper div.
                try:
                    for_attr = loc.get_attribute("for", timeout=500)
                except Exception:
                    for_attr = None
                if for_attr:
                    target = root.locator(f"xpath=//*[@id='{for_attr}']")
                    try:
                        if target.count():
                            container = target.locator("xpath=..")  # immediate parent
                    except Exception:
                        container = None

                if container is None:
                    # No for/id pairing available. Rather than grabbing the
                    # nearest ancestor div containing ANY input (several
                    # distinct text questions sharing an outer "Additional
                    # Questions" box would all resolve to the same
                    # oversized ancestor, so every question would get
                    # mapped to whichever input happened to be first) --
                    # find the nearest actual field that follows this label
                    # in document order, and use ITS parent.
                    following = loc.locator(
                        "xpath=following::*[self::input or self::select or self::textarea][1]"
                    )
                    try:
                        if following.count():
                            container = following.locator("xpath=..")
                    except Exception:
                        container = None

            if container is None:
                if tag == "legend":
                    # A <legend> scopes its enclosing <fieldset> -- check
                    # that FIRST. A <fieldset> isn't a <div>, so searching
                    # for a wrapping div first skips right past the correct
                    # small container and grabs some much bigger ancestor
                    # (e.g. an entire "Additional Questions" section
                    # covering several unrelated Yes/No groups at once).
                    container = loc.locator("xpath=ancestor::fieldset[1]")
                    if container.count() == 0:
                        container = loc.locator(
                            "xpath=ancestor::div[.//input or .//select or .//textarea][1]"
                        )
                else:
                    container = loc.locator(
                        "xpath=ancestor::div[.//input or .//select or .//textarea][1]"
                    )
                    if container.count() == 0:
                        container = loc.locator("xpath=ancestor::fieldset[1]")

            if container is None or container.count() == 0:
                continue

            try:
                handle = container.first.element_handle(timeout=500)
                key = id(handle) if handle else text
            except Exception:
                key = text
            if key in seen_containers:
                continue
            if _is_site_chrome(text, loc):
                continue
            seen_containers.add(key)
            results.append((text, container.first))

    # Newer markup has no <label>/<legend> text at all. Radio groups are
    # <fieldset role="radiogroup"> holding <div role="radio" aria-label=QUESTION>
    # wrappers, and text/select inputs carry the question as their aria-label.
    seen_text = {text for text, _ in results}
    groups = root.locator("fieldset[role='radiogroup']")
    for i in range(groups.count()):
        fs = groups.nth(i)
        try:
            if not fs.is_visible():
                continue
            question = (fs.locator("[role='radio']").first.get_attribute("aria-label", timeout=500) or "").strip()
        except Exception:
            continue
        if re.search(r"\.(pdf|docx?|rtf|txt)$", question, re.I):
            continue  # the resume picker: a radio group of file names, not a question
        if question and question not in seen_text and not _is_site_chrome(question, fs):
            seen_text.add(question)
            results.append((question, fs))

    fields = root.locator("input[aria-label], textarea[aria-label], select[aria-label]")
    for i in range(fields.count()):
        field = fields.nth(i)
        try:
            if not field.is_visible():
                continue
            if (field.get_attribute("type", timeout=500) or "") in ("radio", "checkbox", "file", "hidden"):
                continue
            question = (field.get_attribute("aria-label", timeout=500) or "").strip()
        except Exception:
            continue
        if question and question not in seen_text and not _is_site_chrome(question, field):
            seen_text.add(question)
            results.append((question, field.locator("xpath=..")))
    return results


def _handle_step(root, store: Store) -> None:
    """Fill every visible question on the current modal step."""
    for question, container in _iterate_questions(root):
        _note(f"handling question '{question[:60]}'")
        try:
            _fill_field(root, container, question, store)
            _note("  handled")
        except Exception as e:
            _note(f"  failed: {str(e)[:80]}")
            print(f"    (couldn't auto-fill \"{question[:60]}\": {e})")


def _widget_value(container) -> tuple[str, str]:
    """(kind, current value) of the answer widget inside `container`."""
    try:
        text_in = container.locator(", ".join(S["text_input"])).first
        if text_in.count():
            return "text", text_in.input_value(timeout=1500).strip()
        select_in = container.locator(", ".join(S["select_input"])).first
        if select_in.count():
            return "select", (select_in.evaluate(
                "e => e.options[e.selectedIndex] ? e.options[e.selectedIndex].text : ''", timeout=1500
            ) or "").strip()
        radios = container.locator(", ".join(S["radio_input"]))
        if radios.count():
            labels = container.locator("label").all_inner_texts()
            if not any(t.strip() for t in labels):
                labels = container.locator("[role='radio']").all_inner_texts()
            for i in range(radios.count()):
                if radios.nth(i).is_checked(timeout=1500):
                    return "radio", (labels[i] if i < len(labels) else "").strip()
            return "radio", ""
    except Exception:
        pass
    return "", ""


def _await_correction(page: Page, root, store: Store, timeout: int = 600) -> bool:
    """LinkedIn rejected an answer (e.g. "Yes" typed into a number-only box).
    Leave the window open, let the user fix it in the browser, and save what
    they changed to the answer bank -- replacing the cached value that was
    wrong. Returns True once the error has cleared, False if the form went away
    or time ran out."""
    before = {q: _widget_value(c) for q, c in _iterate_questions(root)}
    print("  Fix the highlighted answer in the browser window -- I'll save what you "
          "enter for next time.", flush=True)
    deadline = time.time() + timeout
    clear = gone = 0
    while True:
        if time.time() > deadline:
            print("  Timed out waiting for the correction.")
            return False
        if _modal_root(page) is None:
            gone += 1
            if gone >= 4:
                print("  The form went away while waiting for the correction.")
                return False
        else:
            gone = 0
            clear = 0 if _visible(page, S["error_feedback"]) else clear + 1
            if clear >= 3:  # error gone and stayed gone for a few seconds
                break
        time.sleep(1)

    saved = 0
    for q, c in _iterate_questions(root):
        kind, val = _widget_value(c)
        if val and val != before.get(q, ("", ""))[1]:
            store.put_answer(canonicalize(q) or q, val, kind or "text", display_question=q)
            print(f"    Saved your corrected answer for \"{q[:60]}\": {val}")
            saved += 1
    if not saved:
        print("  The error cleared, but no changed answer was found to save.")
    return True


_RESUME_STEP_MARKERS = [
    "button:has-text('Show more resumes')",
    "button:has-text('Upload resume')",
    "text=/include an updated resume/i",
]


def _card_selected(card) -> bool:
    """True if this resume option is actually checked, per its aria-checked or
    the <input type=radio> inside it."""
    try:
        return bool(card.evaluate(
            "e => e.getAttribute('aria-checked') === 'true' || !!e.querySelector('input:checked')"
        ))
    except Exception:
        return False


def _upload_tailored_resume(page: Page, root, resume_path: str) -> str:
    """Upload a job-specific tailored resume at this step, if this step offers
    a resume choice.

    LinkedIn does NOT always expose a plain <input type=file> sitting in the
    DOM -- often the resume section shows an "Upload resume" button that opens
    the OS's native file-chooser dialog instead, with no file input to target
    directly. Handle both: a plain input if one is present, otherwise trigger
    the button and intercept the resulting file-chooser via Playwright.

    Returns 'uploaded' (attached and LinkedIn shows it selected), 'unverified'
    (attached but not shown as selected), or 'not_a_resume_step'.
    """
    file_input = root.locator(", ".join(S["resume_upload_input"])).first
    upload_btn = root.locator("button:has-text('Upload resume')").first
    if not upload_btn.count():
        upload_btn = page.locator("button:has-text('Upload resume')").first

    try:
        if file_input.count():
            file_input.set_input_files(resume_path, timeout=8000)
        elif upload_btn.count() and upload_btn.is_visible():
            with page.expect_file_chooser(timeout=8000) as fc_info:
                upload_btn.click(timeout=3000)
            fc_info.value.set_files(resume_path)
        else:
            return "not_a_resume_step"
    except Exception:
        return "not_a_resume_step"

    pause(2, 3.5)  # LinkedIn takes a moment to process and auto-select the new upload
    basename = Path(resume_path).name
    try:
        card = page.get_by_role("radio", name=basename, exact=True)
        if card.count() and _card_selected(card.first):
            return "uploaded"
    except Exception:
        pass
    return "unverified"


def _select_saved_resume(page: Page, name: str) -> str:
    """Pick the saved resume called `name` on the Easy Apply resume step.

    Returns 'selected' (clicked and verified checked), 'unverified' (clicked but
    not shown as checked), 'not_a_resume_step' or 'not_found'. LinkedIn shows a
    few saved resumes and hides the rest behind "Show more resumes", so
    expand that until the wanted file appears.
    """
    # Only the resume *option* qualifies (a [role=radio] named after the file),
    # never the same file name shown as plain text on the Review page.
    wanted = page.get_by_role("radio", name=name, exact=True)
    show_more = page.locator("button:has-text('Show more resumes')")
    for _ in range(4):
        try:
            if wanted.count() and wanted.first.is_visible():
                card = wanted.first
                card.scroll_into_view_if_needed(timeout=3000)
                card.click(timeout=3000)
                pause(0.5, 1)
                if not _card_selected(card):
                    try:
                        card.locator("label").first.click(force=True, timeout=3000)
                        pause(0.5, 1)
                    except Exception:
                        pass  # fall through to the verification below
                return "selected" if _card_selected(card) else "unverified"
            if show_more.count() and show_more.first.is_visible():
                show_more.first.click(timeout=3000)
                pause(0.8, 1.5)
                continue
        except Exception:
            pass
        break
    if _visible(page, _RESUME_STEP_MARKERS):
        return "not_found"
    return "not_a_resume_step"


def apply_to_job(page: Page, store: Store, job, auto_submit: bool = False, resume_name: str | None = None) -> str:
    """Returns one of: 'submitted', 'awaiting_review', 'already_applied', 'skipped', 'error'.

    Wraps the real work in a watcher that records page navigations / reloads /
    crashes for the whole application, for the failure diagnostics."""
    events: list[str] = []
    started = time.time()
    attached = []

    def make(name):
        def handler(arg=None):
            if name == "framenavigated" and arg is not page.main_frame:
                return  # ignore sub-frames (ads, tracking iframes)
            url = getattr(arg, "url", "") or ""
            events.append(f"{time.time() - started:.1f}s {name} {url[:90]}".rstrip())
        return handler

    for name in ("framenavigated", "load", "domcontentloaded", "crash", "close"):
        h = make(name)
        page.on(name, h)
        attached.append((name, h))
    try:
        return _apply_to_job_impl(page, store, job, auto_submit, resume_name, events)
    finally:
        for name, h in attached:
            try:
                page.remove_listener(name, h)
            except Exception:
                pass


def _apply_to_job_impl(page: Page, store: Store, job, auto_submit: bool, resume_name: str | None,
                       events: list[str]) -> str:
    print(f"\n--- {job['title']} | {job['company']} ---")
    _NOTES.clear()
    _NOTE_T0[0] = time.time()
    page.goto(job["url"], wait_until="domcontentloaded", timeout=60_000)
    try:
        page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass  # some job pages keep background requests running indefinitely; don't block on it
    pause(2, 4)

    # LinkedIn redirects expired/removed postings to a generic search-results
    # page. That page has its own "Easy Apply" filter chip and "Next" buttons,
    # which look like real targets to text-based matching -- so bail out
    # before looking for any buttons at all.
    if "/jobs/view/" not in page.url:
        print(f"  Posting no longer available (redirected to {page.url[:80]}) -- skipping.")
        return "skipped"

    # Jobs already applied to (by an earlier run or by hand) show an
    # "Application status: Application submitted" card and no Easy Apply
    # button. Recognise that so it isn't misreported as expired/external.
    try:
        if "application submitted" in page.locator("body").inner_text(timeout=3000).lower():
            print("  Already applied -- LinkedIn shows 'Application submitted' for this job.")
            return "already_applied"
    except Exception:
        pass

    # Job pages render the apply button a beat after DOMContentLoaded, so
    # retry for a few seconds before concluding it isn't there.
    btn = _wait_visible(page, S["easy_apply_button"], attempts=8, delay=0.5)
    if not btn:
        print("  No Easy Apply button found -- may have expired or requires external site.")
        debug_path = "debug_no_easy_apply.png"
        try:
            page.screenshot(path=debug_path, full_page=True)
            found = page.locator("button, a, [role='button']").evaluate_all(
                "els => els.filter(e => /apply/i.test((e.innerText || '') + ' ' + "
                "(e.getAttribute('aria-label') || ''))).slice(0, 8).map(e => "
                "e.tagName + ' | aria=' + (e.getAttribute('aria-label') || '') + ' | text=' + "
                "(e.innerText || '').trim().slice(0, 40) + ' | visible=' + (e.offsetParent !== null))"
            )
            print(f"  URL: {page.url}")
            print(f"  Apply-related elements on page: {found or 'none'}")
            print(f"  Saved screenshot to {debug_path}.")
        except Exception as e:
            print(f"  (debug capture failed: {e})")
        return "skipped"
    btn.click(timeout=5000)
    pause(1.5, 3)

    root = None
    for _ in range(40):  # up to ~20s -- the modal sometimes sits on a spinner for a while
        root = _modal_root(page)
        if root:
            break
        page.wait_for_timeout(500)

    if not root:
        debug_path = "debug_easy_apply_failure.png"
        try:
            page.screenshot(path=debug_path, full_page=True)
            print(f"  Easy Apply modal didn't open (no Next/Review/Submit button found after 20s).")
            print(f"  Saved a screenshot to {debug_path} -- open it and share it so we can see "
                  f"what's actually on screen.")
        except Exception:
            print("  Easy Apply modal didn't open, and the debug screenshot also failed.")
        return "error"

    # Resume priority: this job's tailored resume (better ATS match against
    # this specific posting) beats the fixed saved resume. Only fall back to
    # the saved one if there's no tailored file, or the file went missing.
    tailored_path = job["resume_path"]
    if tailored_path and not Path(tailored_path).exists():
        print(f"  Note: tailored resume is missing on disk ({tailored_path}) -- "
              f"falling back to your saved resume.")
        tailored_path = None

    for step in range(MAX_STEPS):
        _install_trace(page)
        _note(f"step {step + 1} begins")
        if tailored_path:
            picked = _upload_tailored_resume(page, root, tailored_path)
            if picked == "uploaded":
                _note("tailored resume uploaded")
                print(f"    Uploaded tailored resume: {Path(tailored_path).name}")
            elif picked == "unverified":
                debug_path = "debug_resume_not_found.png"
                try:
                    page.screenshot(path=debug_path, full_page=True)
                except Exception:
                    pass
                print(f"  Uploaded '{Path(tailored_path).name}' but LinkedIn didn't show it as "
                      f"selected -- stopping rather than risk the wrong resume (see {debug_path}).")
                return "error"
            # 'not_a_resume_step' -- no upload widget on this step, nothing to do
        elif resume_name:
            picked = _select_saved_resume(page, resume_name)
            if picked == "selected":
                _note("resume selected")
                print(f"    Selected resume: {resume_name}")
            elif picked in ("not_found", "unverified"):
                debug_path = "debug_resume_not_found.png"
                try:
                    page.screenshot(path=debug_path, full_page=True)
                except Exception:
                    pass
                why = ("wasn't found among your saved resumes" if picked == "not_found"
                       else "was clicked but LinkedIn didn't show it as selected")
                print(f"  Resume step reached but '{resume_name}' {why} -- stopping rather than "
                      f"send a different resume (see {debug_path}).")
                return "error"
        questions = _iterate_questions(root)
        print(f"  Step {step + 1}: {len(questions)} question(s) detected")
        _handle_step(root, store)
        pause(0.6, 1.4)

        err = _visible_in(root, S["error_feedback"]) or _visible(page, S["error_feedback"])
        if err:
            print(f"  LinkedIn flagged a validation error: {err.inner_text()[:200]}")
            try:
                page.screenshot(path="debug_validation_error.png", full_page=True)
                print("  Saved a screenshot of the form to debug_validation_error.png.")
            except Exception:
                pass
            if _await_correction(page, root, store):
                _note("user corrected the rejected answer(s)")
            else:
                print("  Leaving this application open for you to finish by hand.")
                _dump_diagnostics(page, events)
                return "awaiting_review"

        has_submit = bool(_visible_in(root, S["submit_button"]))
        has_next = bool(_visible_in(root, S["next_button"]))
        has_review = bool(_visible_in(root, S["review_button"]))
        print(f"  Step {step + 1}: submit={has_submit} next={has_next} review={has_review}")

        submit = _visible_in(root, S["submit_button"]) or _visible(page, S["submit_button"])
        if submit:
            if auto_submit:
                submit.click(timeout=5000)
                pause(1, 2)
                print("  Submitted.")
                return "submitted"
            print("  Form complete. Review it in the browser and click Submit yourself,")
            print("  then come back here and press Enter.")
            input("  Press Enter once you've submitted (or closed the modal to skip)... ")
            return "awaiting_review"

        # Fall back to an unscoped page-wide search if the root-scoped one
        # comes up empty -- this happens when the derived root turns out
        # not to actually contain the button as a descendant (e.g. the
        # button lives in a separate fixed footer while questions sit in a
        # scrollable body, and our ancestor-walk picked the wrong branch).
        nxt = (
            _visible_in(root, S["next_button"])
            or _visible_in(root, S["review_button"])
            or _visible(page, S["next_button"])
            or _visible(page, S["review_button"])
        )
        if nxt:
            nxt.scroll_into_view_if_needed(timeout=3000)
            _note("clicking Next/Review")
            nxt.click(timeout=5000)
            pause(1, 2)
            # After clicking Next, re-acquire root -- LinkedIn sometimes
            # replaces the dialog's inner content wholesale between steps.
            for _ in range(10):
                new_root = _modal_root(page)
                if new_root:
                    root = new_root
                    break
                page.wait_for_timeout(300)
            continue

        debug_path = "debug_no_next_button.png"
        try:
            page.screenshot(path=debug_path, full_page=True)
            print(f"  No Next/Submit button found -- saved {debug_path}, please share it.")
        except Exception:
            print("  No Next/Submit button found -- form may need manual completion.")
        _dump_diagnostics(page, events)
        return "awaiting_review"

    debug_path = "debug_max_steps.png"
    try:
        page.screenshot(path=debug_path, full_page=True)
        print(f"  Hit the {MAX_STEPS}-step safety limit without finding Submit -- "
              f"saved {debug_path}, please share it.")
    except Exception:
        print(f"  Hit the {MAX_STEPS}-step safety limit without finding Submit -- stopping.")
    return "awaiting_review"
