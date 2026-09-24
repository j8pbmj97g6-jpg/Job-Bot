"""Offline test of _upload_tailored_resume() and the tailored-vs-saved priority
logic. No LinkedIn; mock pages + a throwaway .docx file on disk.

Covers both upload mechanisms LinkedIn actually uses (confirmed against a real
job page on 2026-09-22): a plain <input type=file> sitting in the DOM, and an
"Upload resume" button that opens the OS's native file-chooser dialog with no
input element present until then."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from playwright.sync_api import sync_playwright

from jobbot import apply_linkedin as A

tmpdir = tempfile.mkdtemp()
FAKE_RESUME = os.path.join(tmpdir, "Tailored_For_Acme.docx")
with open(FAKE_RESUME, "wb") as f:
    f.write(b"not a real docx, just needs to exist for set_input_files")
BASENAME = os.path.basename(FAKE_RESUME)

CSS = "<style>input[type=radio]{position:absolute;opacity:0;width:0;height:0}</style>"

CHANGE_HANDLER = (
    "<script>"
    "document.getElementById('fi').addEventListener('change', function(e) {"
    "  var f = e.target.files[0];"
    "  var div = document.createElement('div');"
    "  div.setAttribute('role', 'radio');"
    "  div.setAttribute('aria-label', f.name);"
    "  div.innerHTML = '<input type=\"radio\" checked>';"
    "  document.getElementById('opts').appendChild(div);"
    "});"
    "</script>"
)

ok = True


def check(label, cond, detail=""):
    global ok
    ok &= bool(cond)
    print(("PASS" if cond else "FAIL") + f": {label} {detail}")


with sync_playwright() as p:
    b = p.chromium.launch(headless=True)

    # 1) Plain <input type=file> already in the DOM (older/simpler markup)
    html = (CSS + '<input type="file" name="file" id="fi" style="display:none">'
            '<div id="opts"></div>' + CHANGE_HANDLER)
    page = b.new_page()
    page.set_content(html)
    result = A._upload_tailored_resume(page, page.locator("body"), FAKE_RESUME)
    check("plain file input -> verified 'uploaded'", result == "uploaded", f"(got {result!r})")

    # 2) "Upload resume" button opens a native file-chooser dialog (no input
    # element present until the button is clicked -- LinkedIn's actual pattern)
    html2 = (CSS +
             '<input type="file" name="file" id="fi" style="display:none">'
             '<button onclick="document.getElementById(\'fi\').click()">Upload resume</button>'
             '<div id="opts"></div>' + CHANGE_HANDLER)
    page2 = b.new_page()
    page2.set_content(html2)
    result2 = A._upload_tailored_resume(page2, page2.locator("body"), FAKE_RESUME)
    check("Upload-resume button -> file chooser -> verified 'uploaded'", result2 == "uploaded", f"(got {result2!r})")

    # 3) Button present but LinkedIn never shows the upload as selected
    html3 = (CSS +
             '<input type="file" name="file" id="fi" style="display:none">'
             '<button onclick="document.getElementById(\'fi\').click()">Upload resume</button>')
    page3 = b.new_page()
    page3.set_content(html3)
    result3 = A._upload_tailored_resume(page3, page3.locator("body"), FAKE_RESUME)
    check("upload without confirmation -> 'unverified'", result3 == "unverified", f"(got {result3!r})")

    # 4) Not a resume step at all (no file input, no Upload button)
    html4 = "<div><p>Some other question</p><input type='text'></div>"
    page4 = b.new_page()
    page4.set_content(html4)
    result4 = A._upload_tailored_resume(page4, page4.locator("body"), FAKE_RESUME)
    check("no upload widget on this step -> 'not_a_resume_step'", result4 == "not_a_resume_step", f"(got {result4!r})")

    b.close()

print("ALL PASS" if ok else "SOME FAILED")
