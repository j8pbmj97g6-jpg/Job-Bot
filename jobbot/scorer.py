"""Scores collected jobs against the user's resume using the Claude API.

This is what turns "semi-auto" from a slogan into something real: only jobs
scoring at or above `auto_apply_threshold` get marked 'queued' (safe for the
future auto-applier to act on without asking); everything else is marked
'scored' and waits in your manual review queue.

Requires an Anthropic API key in the ANTHROPIC_API_KEY environment variable.
Get one at https://console.anthropic.com/settings/keys -- never hardcode it
into config.yaml or any file that might get shared or committed.
"""

from __future__ import annotations

import json
import os
import time

import requests

from .db import Store

API_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-sonnet-5"
MAX_DESC_CHARS = 6000  # keep prompts small and cheap

SYSTEM_PROMPT = """You screen job postings against a candidate's resume for fit.

Respond with ONLY a JSON object, no other text, no markdown fences:
{"score": <integer 0-100>, "reason": "<one sentence, under 25 words>"}

Scoring guide:
- 85-100: strong match on role, seniority, and domain
- 60-84: reasonable match, some gaps in seniority/domain/skills
- 30-59: weak match, wrong seniority band or unrelated domain
- 0-29: not a match at all, or a listed deal-breaker is present

Be honest and specific in the reason -- name the actual gap or strength,
don't hedge with generic phrases."""


def _build_user_prompt(resume_text: str, job: dict, profile: dict) -> str:
    desc = (job.get("description") or "")[:MAX_DESC_CHARS]
    must_have = ", ".join(profile.get("must_have", [])) or "none specified"
    deal_breakers = ", ".join(profile.get("deal_breakers", [])) or "none specified"

    return f"""CANDIDATE RESUME:
{resume_text}

CANDIDATE MUST-HAVES: {must_have}
CANDIDATE DEAL-BREAKERS: {deal_breakers}

JOB POSTING:
Title: {job.get('title', '')}
Company: {job.get('company', '')}
Location: {job.get('location', '')}
Experience level: {job.get('experience', '') or 'not specified'}
Description:
{desc}

Score this job's fit for this candidate."""


def _call_claude(resume_text: str, job: dict, profile: dict, api_key: str) -> tuple[int, str]:
    body = {
        "model": MODEL,
        "max_tokens": 300,
        "system": SYSTEM_PROMPT,
        "messages": [
            {"role": "user", "content": _build_user_prompt(resume_text, job, profile)}
        ],
    }
    resp = requests.post(
        API_URL,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json=body,
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    text = "".join(
        block.get("text", "") for block in data.get("content", []) if block.get("type") == "text"
    ).strip()
    text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        # Model occasionally wraps the JSON in stray prose or leaves an
        # unescaped quote inside the reason string. Pull out the first
        # {...} block and repair the common case of a bare " inside reason.
        import re

        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise
        candidate = match.group(0)
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            # Last resort: pull score and reason out with regex directly.
            score_m = re.search(r'"score"\s*:\s*(\d+)', candidate)
            reason_m = re.search(r'"reason"\s*:\s*"(.*)"\s*\}?\s*$', candidate, re.DOTALL)
            if not score_m:
                raise
            parsed = {
                "score": int(score_m.group(1)),
                "reason": (reason_m.group(1) if reason_m else "").replace('"', "'")[:300],
            }

    score = int(parsed["score"])
    reason = str(parsed["reason"])[:300]
    return score, reason


def score_pending(
    store: Store,
    resume_text: str,
    profile: dict,
    limit: int | None = None,
    threshold: int = 75,
    rescore: bool = False,
) -> dict:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit(
            "ANTHROPIC_API_KEY is not set. Run:\n"
            "  export ANTHROPIC_API_KEY=your-key-here\n"
            "Get a key at https://console.anthropic.com/settings/keys"
        )

    statuses = ("new", "scored") if rescore else ("new",)
    jobs = [j for s in statuses for j in store.jobs(status=s, limit=limit or 10_000)]
    if not jobs:
        print("No jobs to score. Pass rescore=True to redo already-scored jobs.")
        return {"scored": 0, "queued": 0, "errors": 0}

    scored = queued = errors = 0
    for job in jobs:
        try:
            score, reason = _call_claude(resume_text, dict(job), profile, api_key)
            store.set_score(job["id"], score, reason)
            if score >= threshold:
                store.set_status(job["id"], "queued")
                queued += 1
            scored += 1
            tag = "QUEUED" if score >= threshold else "review"
            print(f"  {job['id']:>5} [{score:>3}] {tag:7} {job['title'][:50]:50} — {reason}")
        except Exception as e:
            errors += 1
            print(f"  {job['id']:>5} !! scoring failed: {e}")
        time.sleep(0.5)  # be gentle with the API too

    return {"scored": scored, "queued": queued, "errors": errors}
