"""Offline test of the new question handling against a local mock of LinkedIn's
'Additional Questions' markup (copied from the live DOM outline). Uses a
throwaway DB with dummy answers -- no LinkedIn access, no real data."""
import os
import sys
import tempfile

sys.path.insert(0, "/Users/rooney/Documents/jobbot")

from playwright.sync_api import sync_playwright

from jobbot import apply_linkedin as A
from jobbot.db import Store

Q1 = "Have you completed the following level of education: Bachelor's Degree?"
Q2 = "How many years of work experience do you have with SQL?"
Q3 = "How many years of work experience do you have with REST APIs?"

HTML = f"""
<style>input[type=radio]{{position:absolute;opacity:0;width:0;height:0}}</style>
<body><div><div><p>4/5 pages</p></div>
<fieldset role="radiogroup"><div role="radio" aria-label="Rohit Prasad.pdf"><input type="radio" name="cv" id="cv1"><label for="cv1"></label></div>
<div role="radio" aria-label="Other Resume.pdf"><input type="radio" name="cv" id="cv2"><label for="cv2"></label></div></fieldset>
<div><p>Additional Questions</p>
 <div><p>{Q1}*</p>
  <fieldset role="radiogroup">
   <div><div role="radio" aria-label="{Q1}" onclick="this.querySelector('input').checked=true">
     <div><input id="r1h" type="radio" name="g"><label for="r1h"></label></div><div><p>Yes</p></div></div>
    <div role="radio" aria-label="{Q1}" onclick="this.querySelector('input').checked=true">
     <div><input id="r1i" type="radio" name="g"><label for="r1i"></label></div><div><p>No</p></div></div></div>
  </fieldset></div>
 <div><p>{Q2}*</p><div><input id="r1j" type="text" aria-label="{Q2}" required value=""></div></div>
 <div><p>{Q3}*</p><div><input id="r1k" type="text" aria-label="{Q3}" required value=""></div></div>
</div>
<footer><button type="button"><span><span>Back</span></span></button>
<button type="button"><span><span>Review</span></span></button></footer></div></body>
"""

tmp = tempfile.mkdtemp()
store = Store(os.path.join(tmp, "test.db"))
# Dummy values only -- this DB is thrown away.
store.put_answer(A.canonicalize(Q1) or Q1, "Yes", "radio", display_question=Q1)
store.put_answer(A.canonicalize(Q2) or Q2, "7", "text", display_question=Q2)
store.put_answer(A.canonicalize(Q3) or Q3, "4", "text", display_question=Q3)
print("canonical keys:", A.canonicalize(Q1), "|", A.canonicalize(Q2), "|", A.canonicalize(Q3))

with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    page = b.new_page()
    page.set_content(HTML)
    root = A._modal_root(page)
    print("root found:", root is not None)
    qs = A._iterate_questions(root)
    print("questions detected:", len(qs))
    for q, _ in qs:
        print("  -", q)
    A._handle_step(root, store)
    print("SQL value:", page.input_value("#r1j"), "| REST value:", page.input_value("#r1k"))
    print("Yes checked:", page.is_checked("#r1h"), "| No checked:", page.is_checked("#r1i"))
    b.close()
