"""Semantic question matching, for wordings answer_aliases.py's hand-written
patterns don't catch.

answer_aliases.canonicalize() is the first line of defense -- fast, free,
deterministic, and covers every wording we've specifically seen and fixed.
This module is the fallback for the ones we haven't: when a question hits
no known pattern and has no exact-text cache hit, it asks Claude whether
the NEW question means the same thing as one already answered.

Privacy: only question TEXT is ever sent to the API -- never an answer
value. The model sees "does X mean the same as Y?", not what Y's answer
was.

Cost/latency: every distinct question wording is resolved AT MOST ONCE,
ever -- the result (a matched key, or a confident "no match") is cached in
question_aliases and reused instantly after that, including across every
future job and every future run.
"""

from __future__ import annotations

import json
import os

import requests

from .db import Store

API_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-sonnet-5"

SYSTEM_PROMPT = """You match job-application form questions to a list of
questions a candidate has already answered, so they never have to answer
the same underlying question twice just because a company phrased it
differently.

Respond with ONLY a JSON object, no other text, no markdown fences:
{"match": "<exact key from the list>" or null, "reason": "<one short sentence>"}

Rules:
- Only return a key if the NEW question asks for the exact same piece of
  information as that known question: same topic, same qualifier (e.g.
  "current" / "last drawn" count as the same; "current" vs "expected" do
  NOT), same subject (a different skill/technology/time-period is a
  different question even if the sentence looks similar).
- Opposite-polarity questions are never a match (e.g. "authorized to work
  in India?" vs "need visa sponsorship?").
- A different required answer FORMAT is a different question (e.g. a
  yes/no question never matches a question that wants a number).
- When genuinely unsure, return null. A wrong guess that silently reuses
  the wrong answer is worse than asking the person once more."""


def find_matching_question(store: Store, question: str, api_key: str | None = None) -> str | None:
    """Return an existing answers.question_key this question means the same
    as, or None if there's no confident match (or no API key available).

    Checks the question_aliases cache first -- a cached row is returned
    as-is even if its matched_key is None (a previously-confirmed
    "no match" must not trigger a repeat API call). Only a genuinely new
    wording reaches the API.
    """
    existing = store.get_question_alias(question)
    if existing is not None:
        return existing["matched_key"]

    api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None  # no key configured -- skip silently; caller falls back to asking

    known = store.known_questions()
    if not known:
        return None

    candidates = [{"key": r["question_key"], "question": r["question_raw"]} for r in known]
    valid_keys = {c["key"] for c in candidates}
    user_prompt = f'NEW QUESTION: "{question}"\n\nALREADY-ANSWERED QUESTIONS:\n{json.dumps(candidates, indent=2)}'

    match: str | None = None
    try:
        resp = requests.post(
            API_URL,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": MODEL,
                "max_tokens": 200,
                "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": user_prompt}],
            },
            timeout=20,
        )
        if resp.ok:
            text = resp.json()["content"][0]["text"].strip()
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:].strip()
            data = json.loads(text)
            candidate_match = data.get("match")
            if candidate_match in valid_keys:
                match = candidate_match
    except Exception:
        match = None  # any failure (network, bad JSON, ...) -- fall back to asking, don't crash the run

    store.put_question_alias(question, match)
    return match
