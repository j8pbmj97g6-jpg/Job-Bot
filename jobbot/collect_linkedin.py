"""LinkedIn jobs collector.

Walks a saved search, clicks each result card to load the detail pane,
and stores title / company / location / full description in SQLite.

Only jobs marked Easy Apply are worth collecting if your applier targets
LinkedIn's in-platform flow -- set easy_apply_only in config.
"""

from __future__ import annotations

from urllib.parse import urlencode

from playwright.sync_api import Page

from .browser import first_attr, first_text, human_scroll, pause
from .db import Store
from .selectors import LINKEDIN as S

BASE = "https://www.linkedin.com/jobs/search/"
RESULTS_PER_PAGE = 25

# LinkedIn's filter params, mapped to readable config keys
WORKPLACE = {"onsite": "1", "remote": "2", "hybrid": "3"}
POSTED_WINDOW = {"day": "r86400", "week": "r604800", "month": "r2592000"}
JOB_TYPE = {"fulltime": "F", "parttime": "P", "contract": "C", "internship": "I"}
EXPERIENCE = {
    "internship": "1",
    "entry": "2",
    "associate": "3",
    "mid-senior": "4",
    "director": "5",
}


def build_url(search: dict, start: int = 0) -> str:
    params = {"keywords": search["keywords"], "start": start}
    if search.get("location"):
        params["location"] = search["location"]
    if search.get("easy_apply_only", True):
        params["f_AL"] = "true"
    if search.get("posted_within"):
        params["f_TPR"] = POSTED_WINDOW.get(search["posted_within"], "r604800")
    if search.get("workplace"):
        params["f_WT"] = ",".join(
            WORKPLACE[w] for w in search["workplace"] if w in WORKPLACE
        )
    if search.get("job_type"):
        params["f_JT"] = ",".join(
            JOB_TYPE[j] for j in search["job_type"] if j in JOB_TYPE
        )
    if search.get("experience"):
        params["f_E"] = ",".join(
            EXPERIENCE[e] for e in search["experience"] if e in EXPERIENCE
        )
    return f"{BASE}?{urlencode(params)}"


def _card_job_id(card) -> str | None:
    for attr in S["job_id_attrs"]:
        try:
            val = card.get_attribute(attr, timeout=1000)
            if val:
                return val
        except Exception:
            continue
    # Fall back to parsing the href
    href = first_attr(card, ["a[href*='/jobs/view/']"], "href")
    if href and "/jobs/view/" in href:
        return href.split("/jobs/view/")[1].split("/")[0].split("?")[0]
    return None


def _expand_description(page: Page) -> None:
    for sel in S["see_more_button"]:
        try:
            btn = page.locator(sel).first
            if btn.count() and btn.is_visible():
                btn.click(timeout=2000)
                pause(0.4, 1.0)
                return
        except Exception:
            continue


def collect(page: Page, store: Store, search: dict, max_jobs: int = 50) -> dict:
    """Collect up to max_jobs results for one saved search."""
    label = search.get("name", search["keywords"])
    new_count = dupe_count = 0
    start = 0

    while new_count + dupe_count < max_jobs:
        url = build_url(search, start)
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        pause(2.5, 5.0)

        # Force lazy-loaded cards to render
        human_scroll(page, steps=8)
        pause(1.0, 2.0)

        cards = None
        for sel in S["job_card"]:
            loc = page.locator(sel)
            if loc.count():
                cards = loc
                break

        if not cards or cards.count() == 0:
            print(f"  [{label}] no cards at start={start} -- end of results "
                  f"(or selectors need updating in selectors.py)")
            break

        count = cards.count()
        print(f"  [{label}] page {start // RESULTS_PER_PAGE + 1}: {count} cards")

        for i in range(count):
            if new_count + dupe_count >= max_jobs:
                break

            card = cards.nth(i)
            job_id = _card_job_id(card)
            if not job_id:
                continue

            if store.seen("linkedin", job_id):
                dupe_count += 1
                continue

            # Click the card to load the detail pane, rather than opening a
            # new tab -- fewer requests, closer to real browsing.
            try:
                card.scroll_into_view_if_needed(timeout=3000)
                card.click(timeout=5000)
            except Exception:
                continue
            pause(1.5, 3.2)
            _expand_description(page)

            title = (
                first_text(page, S["detail_title"])
                or first_text(card, S["card_title"])
            )
            company = (
                first_text(page, S["detail_company"])
                or first_text(card, S["card_company"])
            )
            description = first_text(page, S["detail_description"])
            meta = first_text(page, S["detail_meta"]) or ""
            location = first_text(card, S["card_location"])
            easy = False
            for sel in S["easy_apply_button"]:
                try:
                    btn = page.locator(sel).first
                    if btn.count() and "easy apply" in (btn.inner_text(timeout=1500) or "").lower():
                        easy = True
                        break
                except Exception:
                    continue

            store.upsert_job(
                {
                    "source": "linkedin",
                    "external_id": job_id,
                    "url": f"https://www.linkedin.com/jobs/view/{job_id}/",
                    "title": title,
                    "company": company,
                    "location": location,
                    "posted": meta,
                    "easy_apply": easy,
                    "description": description,
                    "search_query": label,
                    "raw": {"meta": meta},
                }
            )
            new_count += 1
            flag = "EA" if easy else "  "
            print(f"    + [{flag}] {(title or '?')[:48]:48} | {(company or '?')[:28]}")

        start += RESULTS_PER_PAGE
        pause(4, 8)  # deliberate gap between result pages

    return {"new": new_count, "duplicates": dupe_count}
