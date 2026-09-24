"""Offline test: question_matcher.find_matching_question(). The Claude API
call is mocked (no network, no real API key needed) -- this tests the
caching contract and response handling, not Claude's actual judgment."""
import os
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, "/Users/rooney/Documents/jobbot")

from jobbot import question_matcher as QM
from jobbot.db import Store

ok = True


def check(label, cond, detail=""):
    global ok
    ok &= bool(cond)
    print(("PASS" if cond else "FAIL") + f": {label} {detail}")


def fresh_store() -> Store:
    return Store(os.path.join(tempfile.mkdtemp(), "t.db"))


def mock_response(match, status=200):
    import json as _json

    body = _json.dumps({"match": match, "reason": "x"})

    class R:
        ok = status == 200
        status_code = status

        def json(self):
            return {"content": [{"text": body}]}

    return R()


# 1) No API key -> returns None, nothing cached (so a later real key can
#    still try), no network call attempted.
store = fresh_store()
store.put_answer("current_salary", "2400000", "text", display_question="Current CTC")
with patch.dict(os.environ, {}, clear=True), patch("jobbot.question_matcher.requests.post") as m:
    result = QM.find_matching_question(store, "What's your last drawn salary in LPA?")
    check("no API key -> None, no call made", result is None and not m.called)
    check("no API key -> nothing cached", store.get_question_alias("What's your last drawn salary in LPA?") is None)

# 2) No known questions yet -> None, no call.
store2 = fresh_store()
with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "fake-key"}), patch("jobbot.question_matcher.requests.post") as m:
    result = QM.find_matching_question(store2, "Any question at all?")
    check("no known questions yet -> None, no call made", result is None and not m.called)

# 3) Genuine match found -- cached, and the API is not called again for the
#    exact same wording.
store3 = fresh_store()
store3.put_answer("current_salary", "2400000", "text", display_question="Current CTC")
with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "fake-key"}):
    with patch("jobbot.question_matcher.requests.post", return_value=mock_response("current_salary")) as m:
        r1 = QM.find_matching_question(store3, "What's your last drawn salary in LPA?")
        check("genuine match returned", r1 == "current_salary", f"(got {r1!r})")
        check("API called exactly once", m.call_count == 1)
    with patch("jobbot.question_matcher.requests.post", return_value=mock_response("current_salary")) as m2:
        r2 = QM.find_matching_question(store3, "What's your last drawn salary in LPA?")
        check("second call for same wording uses cache, no API call", r2 == "current_salary" and not m2.called)

# 4) Confident "no match" -- also cached (must not re-query forever), and
#    is correctly distinguished from "never checked".
store4 = fresh_store()
store4.put_answer("current_salary", "2400000", "text", display_question="Current CTC")
with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "fake-key"}):
    with patch("jobbot.question_matcher.requests.post", return_value=mock_response(None)) as m:
        r1 = QM.find_matching_question(store4, "What is your expected CTC?")
        check("confident no-match returns None", r1 is None)
        check("API called once for the no-match case", m.call_count == 1)
    row = store4.get_question_alias("What is your expected CTC?")
    check("no-match IS cached as a found row (not absent)", row is not None and row["matched_key"] is None)
    with patch("jobbot.question_matcher.requests.post", return_value=mock_response(None)) as m2:
        r2 = QM.find_matching_question(store4, "What is your expected CTC?")
        check("cached no-match short-circuits, no repeat API call", r2 is None and not m2.called)

# 5) Model returns a key that isn't actually in the candidate list --
#    treated as no match, not trusted blindly.
store5 = fresh_store()
store5.put_answer("current_salary", "2400000", "text", display_question="Current CTC")
with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "fake-key"}):
    with patch("jobbot.question_matcher.requests.post", return_value=mock_response("made_up_key_not_real")):
        r = QM.find_matching_question(store5, "Some new question")
        check("hallucinated/invalid key is rejected, not trusted", r is None)

# 6) API call raises (network error, bad JSON, etc.) -- never crashes the
#    run, just falls back to "no match" (still caching so it isn't retried
#    every single time within the same bad-network window... actually this
#    caches the failure too, which is intentional -- see module docstring).
store6 = fresh_store()
store6.put_answer("current_salary", "2400000", "text", display_question="Current CTC")
with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "fake-key"}):
    with patch("jobbot.question_matcher.requests.post", side_effect=Exception("network down")):
        r = QM.find_matching_question(store6, "Some question during an outage")
        check("API exception handled gracefully, no crash", r is None)

# 7) Answer VALUES never appear in what gets sent to the API -- only
#    question text and keys.
store7 = fresh_store()
store7.put_answer("current_salary", "2400000-SECRET-VALUE", "text", display_question="Current CTC")
with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "fake-key"}):
    with patch("jobbot.question_matcher.requests.post", return_value=mock_response("current_salary")) as m:
        QM.find_matching_question(store7, "What's your last drawn salary?")
        sent = str(m.call_args)
        check("answer value never sent to the API", "2400000-SECRET-VALUE" not in sent)

print("ALL PASS" if ok else "SOME FAILED")
