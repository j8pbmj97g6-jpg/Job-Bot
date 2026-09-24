"""Maps differently-worded questions that mean the same thing to one
canonical key, so an answer given once (phone number, email, expected
salary, years of experience, etc.) gets reused automatically no matter how
a particular company's form happens to phrase it.

Falls back to None for anything not recognized -- the caller then keys off
the raw question text instead (still cached, just not shared across
different phrasings of an unrecognized question).
"""

from __future__ import annotations

import re

# (pattern, canonical_key). Checked in order, first match wins.
# Patterns are matched case-insensitively against the question text.
_PATTERNS: list[tuple[str, str]] = [
    (r"phone\s*country\s*code|country\s*code", "phone_country_code"),
    (r"mobile\s*(phone)?\s*number|phone\s*number|contact\s*number", "phone_number"),
    (r"email\s*address|e-?mail\b", "email_address"),
    (r"expected\s*(ctc|salary|compensation|pay)", "expected_salary"),
    # "current CTC", "last drawn CTC", "current / last drawn salary" are all
    # the same question (your existing/most recent pay) -- "drawn" is an
    # optional word between last/current and the pay term. A BARE "What is
    # Your CTC?" (no current/last/expected qualifier) is deliberately left
    # unaliased -- it's genuinely ambiguous (could mean current or expected),
    # so it still asks rather than guessing.
    (r"(current|last)(\s*drawn)?(\s*/\s*(current|last)(\s*drawn)?)?\s*(ctc|salary|compensation|pay)",
     "current_salary"),
    # "Are you an immediate joiner? If not, specify your notice period..." and
    # "Can you start immediately?" are one question; keep it apart from notice_period.
    # ...but the variant that also asks for the notice period in days is a
    # number field ("Yes" is rejected as invalid input), so it gets its own key.
    (r"immediate\s*(joiner|joining|join).*notice|notice.*immediate\s*(joiner|joining|join)",
     "immediate_joiner_notice_days"),
    (r"immediate\s*(joiner|joining|join)|(start|join)\s*immediately", "immediate_joiner"),
    # Yes/No "are you serving notice?" must not share a key with the
    # "what is your notice period?" text question (would type "No" there).
    (r"(serving|currently).{0,30}notice\s*period", "serving_notice_period"),
    (r"notice\s*period", "notice_period"),
    (r"(are you|willing)\s*(to)?\s*relocat", "willing_to_relocate"),
    # Only visa/sponsorship questions share a key. "Are you authorized to work
    # in <country>?" is deliberately NOT aliased: its answer is the opposite of
    # the sponsorship question's and differs per country, so it keys on its own text.
    (r"visa\s*status|sponsorship", "visa_sponsorship"),
    (r"total\s*(years?|yrs?)\s*(of)?\s*experience", "total_experience_years"),
    (r"linkedin\s*(profile|url)", "linkedin_url"),
    (r"portfolio\s*(url|link)", "portfolio_url"),
    (r"^first\s*name$", "first_name"),
    (r"^last\s*name$", "last_name"),
]

# Handled separately since the canonical key needs to embed the role name,
# e.g. "years of experience as Business Analyst" -> experience_as_business_analyst
_ROLE_EXPERIENCE = re.compile(
    r"experience\b.{0,30}?\b(?:as|in|with)\s+(?:an?\s+)?(.+?)[\?\.]?$"
)


def canonicalize(question: str) -> str | None:
    q = question.strip().lower()

    role_match = _ROLE_EXPERIENCE.search(q)
    if role_match:
        role = role_match.group(1).strip()
        role_slug = re.sub(r"[^a-z0-9]+", "_", role).strip("_")
        if role_slug:
            return f"experience_as_{role_slug}"

    for pattern, key in _PATTERNS:
        if re.search(pattern, q):
            return key

    return None
