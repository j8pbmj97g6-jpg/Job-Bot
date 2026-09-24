"""Naukri jobs collector.

Naukri's search results are server-rendered with a predictable URL shape,
which makes the list pass cheap. Full descriptions live on the job detail
page, so we only open those when fetch_descriptions is on -- that's one
extra navigation per job, so keep the pace slow.
"""

from __future__ import annotations

from urllib.parse import urlencode

from playwright.sync_api import Page

from .browser import first_attr, first_text, human_scroll, pause
from .db import Store
from .selectors import NAUKRI as S

BASE = "https://www.naukri.com/jobs-in-india"


def build_url(search: dict, page_no: int = 1) -> str:
    params = {"k": search["keywords"]}
    if search.get("location"):
        params["l"] = search["location"]
    if search.get("experience_years") is not None:
        params["experience"] = str(search["experience_years"])
    if search.get("posted_within_days"):
        params["jobAge"] = str(search["posted_within_days"])
    if search.get("min_salary_lpa"):
        params["ctcFilter"] = f"{search['min_salary_lpa']}to100"
    if page_no > 1:
        params["pageNo"] = str(page_no)
    return f"{BASE}?{urlencode(params)}"


def _card_job_id(card) -> str | None:
    for attr in S["job_id_attrs"]:
        try:
            val = card.get_attribute(attr, timeout=1000)
            if val:
                return val
        except Exception:
            continue
    href = first_attr(card, S["card_title"], "href")
    if href:
        # .../job-listings-python-developer-acme-bangalore-3-to-6-years-010124500123
        tail = href.rstrip("/").split("-")[-1].split("?")[0]
        if tail.isdigit():
            return tail
    return None


def _fetch_description(page: Page, url: str) -> str | None:
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        pause(2.0, 4.0)
        return first_text(page, S["detail_description"])
    except Exception:
        return None


def collect(
    page: Page,
    store: Store,
    search: dict,
    max_jobs: int = 50,
    fetch_descriptions: bool = True,
) -> dict:
    label = search.get("name", search["keywords"])
    new_count = dupe_count = 0
    page_no = 1
    pending: list[dict] = []

    # --- pass 1: harvest the result list (cheap) ---
    while len(pending) + dupe_count < max_jobs:
        url = build_url(search, page_no)
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        pause(2.5, 5.0)
        human_scroll(page, steps=6)

        cards = None
        for sel in S["job_card"]:
            loc = page.locator(sel)
            if loc.count():
                cards = loc
                break

        if not cards or cards.count() == 0:
            print(f"  [{label}] no cards on page {page_no} -- end of results "
                  f"(or selectors need updating in selectors.py)")
            break

        count = cards.count()
        print(f"  [{label}] page {page_no}: {count} cards")

        for i in range(count):
            if len(pending) + dupe_count >= max_jobs:
                break
            card = cards.nth(i)
            job_id = _card_job_id(card)
            if not job_id:
                continue
            if store.seen("naukri", job_id):
                dupe_count += 1
                continue

            job_url = first_attr(card, S["card_title"], "href")
            if job_url and job_url.startswith("/"):
                job_url = "https://www.naukri.com" + job_url

            pending.append(
                {
                    "source": "naukri",
                    "external_id": job_id,
                    "url": job_url or f"https://www.naukri.com/job-listings-{job_id}",
                    "title": first_text(card, S["card_title"]),
                    "company": first_text(card, S["card_company"]),
                    "location": first_text(card, S["card_location"]),
                    "experience": first_text(card, S["card_experience"]),
                    "salary": first_text(card, S["card_salary"]),
                    "posted": first_text(card, S["card_posted"]),
                    "easy_apply": True,  # refined below if we open the page
                    "description": first_text(card, S["card_snippet"]),
                    "search_query": label,
                }
            )

        page_no += 1
        pause(4, 8)

    # --- pass 2: open detail pages for full JD (expensive, optional) ---
    for job in pending:
        if fetch_descriptions and job["url"]:
            full = _fetch_description(page, job["url"])
            if full:
                job["description"] = full
            # If the listing only offers "Apply on company site", the
            # in-platform applier can't handle it -- flag it now.
            external = any(
                page.locator(sel).first.count() for sel in S["company_site_button"]
            )
            job["easy_apply"] = not external
            pause(3, 6)

        store.upsert_job(job)
        new_count += 1
        flag = "EA" if job["easy_apply"] else "EX"
        print(f"    + [{flag}] {(job['title'] or '?')[:48]:48} | {(job['company'] or '?')[:28]}")

    return {"new": new_count, "duplicates": dupe_count}
