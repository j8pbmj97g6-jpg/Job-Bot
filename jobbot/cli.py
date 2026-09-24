"""Command line interface.

    python -m jobbot.cli collect --source linkedin
    python -m jobbot.cli collect --source naukri --max 30
    python -m jobbot.cli list --status new
    python -m jobbot.cli stats
    python -m jobbot.cli export --out jobs.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import yaml

from . import apply_linkedin, apply_naukri, collect_linkedin, collect_naukri, scorer, tailor_resume
from .browser import ensure_logged_in, session
from .db import Store
from .resume import extract_resume_text


def load_config(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        sys.exit(f"Config not found: {p}. Copy config.example.yaml to config.yaml.")
    return yaml.safe_load(p.read_text())


def cmd_collect(args: argparse.Namespace) -> None:
    cfg = load_config(args.config)
    store = Store(cfg.get("database", "jobs.db"))
    sources = [args.source] if args.source != "all" else ["linkedin", "naukri"]

    with session(
        profile_dir=cfg.get("profile_dir", "~/.jobbot/chrome-profile"),
        headless=False,
    ) as ctx:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        for source in sources:
            searches = cfg.get("searches", {}).get(source, [])
            if not searches:
                print(f"No searches configured for {source}, skipping.")
                continue

            print(f"\n=== {source.upper()} ===")
            if source == "linkedin":
                ensure_logged_in(
                    page, "https://www.linkedin.com/jobs/", "/login", "LinkedIn"
                )
            else:
                ensure_logged_in(
                    page, "https://www.naukri.com/mnjuser/homepage", "/nlogin", "Naukri"
                )

            for search in searches:
                mod = collect_linkedin if source == "linkedin" else collect_naukri
                try:
                    result = mod.collect(page, store, search, max_jobs=args.max)
                    print(
                        f"  -> {result['new']} new, {result['duplicates']} already seen"
                    )
                except Exception as e:  # keep going across searches
                    print(f"  !! {search.get('name', '?')} failed: {e}")

    print("\n", store.stats())
    store.close()


def cmd_list(args: argparse.Namespace) -> None:
    cfg = load_config(args.config)
    store = Store(cfg.get("database", "jobs.db"))
    rows = store.jobs(status=args.status, limit=args.limit)
    for r in rows:
        score = f"{r['fit_score']:>3}" if r["fit_score"] is not None else "  -"
        print(
            f"{r['id']:>5} {score} {r['source'][:2]} "
            f"{(r['title'] or '')[:42]:42} | {(r['company'] or '')[:24]:24} | {r['status']}"
        )
    print(f"\n{len(rows)} rows")
    store.close()


def cmd_score(args: argparse.Namespace) -> None:
    cfg = load_config(args.config)
    store = Store(cfg.get("database", "jobs.db"))
    profile = cfg.get("profile", {})

    resume_path = profile.get("resume_path")
    if not resume_path:
        sys.exit("Set profile.resume_path in config.yaml first.")
    resume_text = extract_resume_text(resume_path)
    print(f"Loaded resume: {len(resume_text)} characters from {resume_path}\n")

    threshold = profile.get("auto_apply_threshold", 75)
    result = scorer.score_pending(
        store, resume_text, profile, limit=args.limit, threshold=threshold, rescore=args.rescore
    )
    print(
        f"\nScored {result['scored']}, queued {result['queued']} "
        f"(threshold {threshold}), {result['errors']} errors."
    )
    store.close()


def cmd_apply(args: argparse.Namespace) -> None:
    cfg = load_config(args.config)
    store = Store(cfg.get("database", "jobs.db"))
    daily_cap = cfg.get("limits", {}).get("applications_per_day", 12)
    auto_submit = cfg.get("applier", {}).get("auto_submit", False)

    # Name of the saved LinkedIn resume to pick on the Easy Apply resume step.
    resume_name = cfg.get("profile", {}).get("linkedin_resume_name")

    already_today = store.applications_today()
    remaining = max(0, daily_cap - already_today)
    if remaining == 0:
        print(f"Daily cap reached ({already_today}/{daily_cap} already applied today). Stopping.")
        store.close()
        return

    # `limit` counts real application attempts. Expired / already-applied
    # postings are skipped without using it up, but MAX_VISITS bounds how many
    # pages one run may open so a queue full of dead jobs can't run unbounded.
    limit = min(args.limit, remaining) if args.limit else remaining
    max_visits = 400
    all_queued = store.jobs(status="queued", limit=10_000)
    source = args.source

    if source == "linkedin":
        jobs = [j for j in all_queued if j["source"] == "linkedin"][:max_visits]
        other_count = sum(1 for j in all_queued if j["source"] == "naukri")
        if other_count:
            print(f"Skipping {other_count} queued Naukri job(s) -- pass --source naukri "
                  f"to apply to those instead.\n")
        if not jobs:
            print("No LinkedIn jobs with status='queued'. Run 'score' first -- only jobs at or "
                  "above your auto_apply_threshold get queued.")
            store.close()
            return

        print(f"Going for {limit} application attempt(s), visiting at most {len(jobs)} queued job(s) "
              f"({already_today} already done today, cap {daily_cap}). auto_submit={auto_submit}\n")

        with session(profile_dir=cfg.get("profile_dir", "~/.jobbot/chrome-profile")) as ctx:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            ensure_logged_in(page, "https://www.linkedin.com/jobs/", "/login", "LinkedIn")

            import random
            import time as _t

            attempts = 0
            for job in jobs:
                if attempts >= limit:
                    break
                try:
                    outcome = apply_linkedin.apply_to_job(
                        page, store, job, auto_submit=auto_submit, resume_name=resume_name
                    )
                except Exception as e:
                    print(f"  !! error: {e}")
                    outcome = "error"
                store.log_application(job["id"], mode="auto" if auto_submit else "manual", outcome=outcome)
                if outcome in ("skipped", "already_applied"):
                    _t.sleep(random.uniform(3, 6))  # nothing was filled in; short human-ish gap
                    continue
                attempts += 1
                _t.sleep(20)  # deliberate gap between real applications
            print(f"\nDone: {attempts} application attempt(s) this run.")

    elif source == "naukri":
        jobs = [j for j in all_queued if j["source"] == "naukri"][:max_visits]
        other_count = sum(1 for j in all_queued if j["source"] == "linkedin")
        if other_count:
            print(f"Skipping {other_count} queued LinkedIn job(s) -- pass --source linkedin "
                  f"(or omit --source) to apply to those instead.\n")
        if not jobs:
            print("No Naukri jobs with status='queued'.")
            store.close()
            return

        print(f"Going for {limit} application attempt(s), visiting at most {len(jobs)} queued job(s). "
              f"auto_submit={auto_submit}\n")

        with session(profile_dir=cfg.get("profile_dir", "~/.jobbot/chrome-profile")) as ctx:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            ensure_logged_in(page, "https://www.naukri.com/mnjuser/homepage", "/nlogin", "Naukri")

            import random
            import time as _t

            attempts = 0
            for job in jobs:
                if attempts >= limit:
                    break
                try:
                    outcome = apply_naukri.apply_to_job(page, store, job, auto_submit=auto_submit)
                except Exception as e:
                    print(f"  !! error: {e}")
                    outcome = "error"
                store.log_application(job["id"], mode="auto" if auto_submit else "manual", outcome=outcome)
                if outcome == "skipped":
                    _t.sleep(random.uniform(3, 6))
                    continue
                attempts += 1
                _t.sleep(15)
            print(f"\nDone: {attempts} application attempt(s) this run.")

    store.close()


def cmd_tailor(args: argparse.Namespace) -> None:
    cfg = load_config(args.config)
    store = Store(cfg.get("database", "jobs.db"))
    profile = cfg.get("profile", {})

    resume_path = profile.get("resume_path")
    if not resume_path:
        sys.exit("Set profile.resume_path in config.yaml first.")
    resume_text = extract_resume_text(resume_path)

    contact = profile.get("contact")
    if not contact or not contact.get("name"):
        sys.exit(
            "Add a profile.contact block to config.yaml first, e.g.:\n"
            "  contact:\n"
            "    name: Your Name\n"
            '    line: "you@email.com | +91-XXXXXXXXXX | Bangalore | linkedin.com/in/you"'
        )

    result = tailor_resume.tailor_pending(store, resume_text, contact, limit=args.limit)
    print(f"\nTailored {result['done']} resumes, {result['errors']} errors.")
    store.close()


def cmd_stats(args: argparse.Namespace) -> None:
    cfg = load_config(args.config)
    store = Store(cfg.get("database", "jobs.db"))
    for k, v in sorted(store.stats().items()):
        print(f"  {k:12} {v}")
    store.close()


def cmd_export(args: argparse.Namespace) -> None:
    cfg = load_config(args.config)
    store = Store(cfg.get("database", "jobs.db"))
    rows = store.jobs(status=args.status, limit=10_000)
    fields = [
        "id", "source", "title", "company", "location", "salary",
        "experience", "easy_apply", "fit_score", "status", "url",
    ]
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r[k] for k in fields})
    print(f"Wrote {len(rows)} rows to {args.out}")
    store.close()


def main() -> None:
    ap = argparse.ArgumentParser(prog="jobbot")
    ap.add_argument("--config", default="config.yaml")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("collect", help="run configured searches")
    c.add_argument("--source", choices=["linkedin", "naukri", "all"], default="all")
    c.add_argument("--max", type=int, default=50, help="max jobs per search")
    c.set_defaults(func=cmd_collect)

    l = sub.add_parser("list", help="show stored jobs")
    l.add_argument("--status", default=None)
    l.add_argument("--limit", type=int, default=50)
    l.set_defaults(func=cmd_list)

    sc = sub.add_parser("score", help="score jobs against your resume via Claude")
    sc.add_argument("--limit", type=int, default=None, help="max jobs to score this run")
    sc.add_argument(
        "--rescore", action="store_true",
        help="also re-score jobs already marked 'scored' (e.g. after fixing config)"
    )
    sc.set_defaults(func=cmd_score)

    ap_ = sub.add_parser("apply", help="apply to queued (high-fit) jobs")
    ap_.add_argument("--limit", type=int, default=None, help="max applications this run")
    ap_.add_argument("--source", choices=["linkedin", "naukri"], default="linkedin")
    ap_.set_defaults(func=cmd_apply)

    t = sub.add_parser("tailor", help="generate a tailored resume per queued job")
    t.add_argument("--limit", type=int, default=None)
    t.set_defaults(func=cmd_tailor)

    s = sub.add_parser("stats")
    s.set_defaults(func=cmd_stats)

    e = sub.add_parser("export")
    e.add_argument("--out", default="jobs.csv")
    e.add_argument("--status", default=None)
    e.set_defaults(func=cmd_export)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
