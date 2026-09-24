"""Browser session management.

Design decision: we use a *persistent* Playwright context pointed at a real
profile directory. You log in by hand once; cookies and local storage survive
between runs. No credentials ever touch this codebase.

Headless mode is deliberately not offered as the default -- it is one of the
easiest automation signals to detect.
"""

from __future__ import annotations

import random
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from playwright.sync_api import BrowserContext, Page, sync_playwright

DEFAULT_PROFILE = Path.home() / ".jobbot" / "chrome-profile"

# A real, current UA string. Keep this matched to the Chromium version
# Playwright ships, otherwise the mismatch is itself a signal.
LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-first-run",
    "--no-default-browser-check",
    "--start-maximized",
]


@contextmanager
def session(
    profile_dir: str | Path = DEFAULT_PROFILE,
    headless: bool = False,
    slow_mo: int = 50,
) -> Iterator[BrowserContext]:
    profile_dir = Path(profile_dir)
    profile_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=headless,
            slow_mo=slow_mo,
            args=LAUNCH_ARGS,
            viewport={"width": 1440, "height": 900},
            locale="en-IN",
            timezone_id="Asia/Kolkata",
        )
        # Strip the most obvious automation tell.
        ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        try:
            yield ctx
        finally:
            ctx.close()


def pause(lo: float = 1.2, hi: float = 3.5) -> None:
    """Randomised delay. Used between every navigation and card click."""
    time.sleep(random.uniform(lo, hi))


def human_scroll(page: Page, steps: int = 6, container: str | None = None) -> None:
    """Scroll in irregular increments so lazy-loaded cards render."""
    for _ in range(steps):
        delta = random.randint(300, 700)
        if container:
            page.eval_on_selector(
                container, f"el => el.scrollBy(0, {delta})"
            )
        else:
            page.mouse.wheel(0, delta)
        time.sleep(random.uniform(0.3, 0.9))


def ensure_logged_in(page: Page, probe_url: str, logged_out_marker: str, name: str) -> bool:
    """Navigate to probe_url and check we aren't bounced to a login wall."""
    page.goto(probe_url, wait_until="domcontentloaded", timeout=60_000)
    pause(2, 4)
    if logged_out_marker in page.url or page.locator("input[name='session_key']").count():
        print(
            f"\n  Not logged in to {name}.\n"
            f"  A browser window is open -- log in manually, then press Enter here.\n"
        )
        input("  Press Enter once you're logged in... ")
        page.goto(probe_url, wait_until="domcontentloaded", timeout=60_000)
        pause(2, 4)
    return True


def first_text(page_or_el, selectors: list[str], default: str | None = None) -> str | None:
    """Try a list of selectors, return the first one that yields text.

    Both sites rewrite their DOM regularly, so every extraction goes through
    a fallback chain rather than a single brittle selector.
    """
    for sel in selectors:
        try:
            loc = page_or_el.locator(sel).first
            if loc.count():
                txt = loc.inner_text(timeout=2000).strip()
                if txt:
                    return txt
        except Exception:
            continue
    return default


def first_attr(page_or_el, selectors: list[str], attr: str) -> str | None:
    for sel in selectors:
        try:
            loc = page_or_el.locator(sel).first
            if loc.count():
                val = loc.get_attribute(attr, timeout=2000)
                if val:
                    return val.strip()
        except Exception:
            continue
    return None
