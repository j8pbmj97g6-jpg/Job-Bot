"""Read-only diagnostic: open a job's Easy Apply, click Next until the resume
step, then dump where the file input sits relative to our computed `_modal_root`
and the Next button -- to find out why root-scoped search missed it.
Never clicks Review/Submit."""
import sys

sys.path.insert(0, "/Users/rooney/Documents/jobbot")

from jobbot import apply_linkedin as A
from jobbot.browser import pause, session
from jobbot.selectors import LINKEDIN as S

JOB_URL = "https://www.linkedin.com/jobs/view/4466443268/"  # job 5, Hired

OUTLINE_JS = """
() => {
  const inputs = [...document.querySelectorAll("input[type='file']")];
  const nextBtn = [...document.querySelectorAll('button')].find(b =>
    /^(Next|Review)$/.test((b.innerText||'').trim()));
  const out = {fileInputCount: inputs.length, nextBtnFound: !!nextBtn};
  out.fileInputs = inputs.map(inp => {
    // walk up from the input to the nearest ancestor also containing the Next button
    let commonAncestor = null;
    if (nextBtn) {
      let cur = inp;
      while (cur && cur !== document.body) {
        if (cur.contains(nextBtn)) { commonAncestor = cur; break; }
        cur = cur.parentElement;
      }
    }
    return {
      visible: inp.offsetParent !== null,
      name: inp.getAttribute('name'),
      id: inp.id,
      className: (inp.className || '').toString().slice(0, 60),
      parentChain: (() => {
        let chain = []; let cur = inp.parentElement; let i = 0;
        while (cur && i < 6) { chain.push(cur.tagName + '.' + (cur.className||'').toString().slice(0,40)); cur = cur.parentElement; i++; }
        return chain;
      })(),
      sharesAncestorWithNextButton: !!commonAncestor,
      ancestorTag: commonAncestor ? commonAncestor.tagName : null,
    };
  });
  return out;
}
"""

with session() as ctx:
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto(JOB_URL, wait_until="domcontentloaded", timeout=60_000)
    pause(3, 5)
    btn = A._wait_visible(page, S["easy_apply_button"], attempts=8, delay=0.5)
    print("easy apply button:", bool(btn))
    if not btn:
        sys.exit(1)
    btn.click()
    pause(2, 3)
    for _ in range(30):
        if A._modal_root(page) is not None:
            break
        page.wait_for_timeout(500)

    for step in range(6):
        root = A._modal_root(page)
        print(f"--- step {step + 1}: _modal_root found={root is not None}")
        page.screenshot(path=f"/private/tmp/claude-501/-Users-rooney-Documents-jobbot/"
                              f"0bf09172-0c1f-4076-8d8a-3c5e4eedc871/scratchpad/diag_step{step+1}.png",
                         full_page=True)
        d = page.evaluate(OUTLINE_JS)
        print(f"    fileInputCount={d['fileInputCount']} nextBtnFound={d['nextBtnFound']}")
        for fi in d["fileInputs"]:
            print(f"    file input: visible={fi['visible']} name={fi['name']} id={fi['id']} "
                  f"class={fi['className']!r} sharesAncestorWithNext={fi['sharesAncestorWithNextButton']} "
                  f"ancestorTag={fi['ancestorTag']}")
            print(f"      parent chain: {fi['parentChain']}")
        if d["fileInputCount"] > 0:
            print("    >>> FOUND a file input at this step -- this is the resume step.")
            if root is not None:
                in_root = root.locator("input[type='file']").count()
                print(f"    file inputs found when searched WITHIN our computed root: {in_root}")
            break
        nxt = (A._visible_in(root, S["next_button"]) if root is not None else None) \
            or A._visible(page, S["next_button"]) \
            or (A._visible_in(root, S["review_button"]) if root is not None else None) \
            or A._visible(page, S["review_button"])
        if not nxt:
            print("    no Next/Review button (via applier's own selectors) -- stopping")
            break
        nxt.click()
        for _ in range(20):
            if A._modal_root(page) is not None:
                break
            page.wait_for_timeout(300)
        pause(1, 1.5)

    print("done -- did NOT click Review/Submit")
