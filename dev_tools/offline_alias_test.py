"""Offline test: answer_aliases.canonicalize() -- differently-worded
questions that mean the same thing must map to the same key, and
questions that only look similar but mean different things must NOT.
Pure function, no browser needed."""
import sys

sys.path.insert(0, "/Users/rooney/Documents/jobbot")

from jobbot.answer_aliases import canonicalize

ok = True


def check(label, cond, detail=""):
    global ok
    ok &= bool(cond)
    print(("PASS" if cond else "FAIL") + f": {label} {detail}")


# Groups of questions that MUST all canonicalize to the same key.
SAME_GROUPS = [
    (
        "current_salary",
        [
            "What is your current CTC?",
            "What's your last drawn salary package in LPA?",
            "What's your current / last drawn salary package in LPA?",
            "Current/Last CTC",
            "Current CTC",
        ],
    ),
    (
        "phone_number",
        ["Mobile phone number", "Phone Number", "Contact Number"],
    ),
    (
        "notice_period",
        ["What is your notice period?", "Notice Period"],
    ),
]

# Pairs that must NOT share a key -- guards against over-matching.
DIFFERENT_PAIRS = [
    ("What is your current CTC?", "What is your expected CTC?"),
    ("Are you serving your notice period?", "What is your notice period?"),
    ("Are you authorized to work in the United States?", "Do you require visa sponsorship?"),
    ("What is Your CTC?", "What is your current CTC?"),  # bare CTC stays ambiguous/unaliased
    ("Are you an immediate joiner?", "Are you an immediate joiner? If not, specify notice period"),
]

for key, questions in SAME_GROUPS:
    keys = [canonicalize(q) for q in questions]
    check(
        f"all phrasings of '{key}' share one canonical key",
        all(k == key for k in keys),
        f"(got {dict(zip(questions, keys))})" if not all(k == key for k in keys) else "",
    )

for q1, q2 in DIFFERENT_PAIRS:
    k1, k2 = canonicalize(q1), canonicalize(q2)
    check(
        f"'{q1}' and '{q2}' must NOT share a key",
        k1 != k2 or k1 is None and k2 is None and q1 != q2,
        f"(got {k1!r} vs {k2!r})",
    )

print("ALL PASS" if ok else "SOME FAILED")
