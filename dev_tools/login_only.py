"""Open the bot's persistent Chrome profile at LinkedIn and let the user log in
by hand. Applies to nothing. Exits once the session is confirmed logged in."""
import sys

sys.path.insert(0, "/Users/rooney/Documents/jobbot")

from jobbot.browser import ensure_logged_in, pause, session

with session() as ctx:
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    ensure_logged_in(page, "https://www.linkedin.com/jobs/", "/login", "LinkedIn")
    page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=60_000)
    pause(3, 5)
    logged_out = bool(page.locator("input[name='session_key']").count()) or "/login" in page.url \
        or "/authwall" in page.url or bool(page.get_by_text("Join now").count())
    print("LOGGED_IN" if not logged_out else "STILL_LOGGED_OUT", page.url, flush=True)
    pause(2, 3)
