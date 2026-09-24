"""Read-only diagnostic: dump the DOM around a 'Location (city)' typeahead
field on LinkedIn's Easy Apply contact-info page. Never clicks Next/Review/
Submit. Tries several queued jobs until it finds one with this field."""
import sqlite3
import sys

sys.path.insert(0, "/Users/rooney/Documents/jobbot")

from jobbot import apply_linkedin as A
from jobbot.browser import pause, session
from jobbot.selectors import LINKEDIN as S

OUTLINE_JS = """
() => {
  const label = [...document.querySelectorAll('label')].find(l =>
    /location\\s*\\(city\\)/i.test(l.innerText || ''));
  if (!label) return null;
  const forId = label.getAttribute('for');
  const input = forId ? document.getElementById(forId) : null;
  const keep = ['id','for','type','name','role','aria-label','aria-autocomplete',
                'aria-expanded','aria-owns','aria-controls','autocomplete','value'];
  const dump = (el, depth) => {
    if (!el || depth > 5) return [];
    const attrs = keep.filter(k => el.hasAttribute && el.hasAttribute(k))
      .map(k => k + '=' + JSON.stringify(el.getAttribute(k))).join(' ');
    let line = '  '.repeat(depth) + '<' + el.tagName.toLowerCase() + (attrs ? ' ' + attrs : '') + '>';
    let out = [line];
    [...(el.children || [])].forEach(c => out.push(...dump(c, depth + 1)));
    return out;
  };
  return {
    inputFound: !!input,
    inputOuter: input ? input.outerHTML.slice(0, 300) : null,
    containerDump: dump(label.closest('div') || label.parentElement, 0),
  };
}
"""

LISTBOX_JS = """
() => {
  const boxes = [...document.querySelectorAll("[role='listbox'], ul[role], [role='option']")];
  return boxes.filter(b => b.offsetParent !== null).map(b => ({
    tag: b.tagName, role: b.getAttribute('role'),
    id: b.id, text: (b.innerText || '').trim().slice(0, 80),
  }));
}
"""

store_jobs = sqlite3.connect("/Users/rooney/Documents/jobbot/jobs.db")
store_jobs.row_factory = sqlite3.Row
candidates = store_jobs.execute(
    "SELECT id, url, title, company FROM jobs WHERE status='queued' ORDER BY id LIMIT 65"
).fetchall()

with session() as ctx:
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    for job in candidates:
        print(f"\n=== trying job {job['id']}: {job['title']} | {job['company']} ===")
        page.goto(job["url"], wait_until="domcontentloaded", timeout=60_000)
        pause(2, 3)
        if "/jobs/view/" not in page.url:
            print("  redirected -- skip")
            continue
        btn = A._wait_visible(page, S["easy_apply_button"], attempts=6, delay=0.4)
        if not btn:
            print("  no easy apply button -- skip")
            continue
        btn.click()
        pause(1.5, 2.5)
        root = None
        for _ in range(20):
            root = A._modal_root(page)
            if root is not None:
                break
            page.wait_for_timeout(400)
        if root is None:
            print("  modal didn't open -- skip")
            continue
        d = page.evaluate(OUTLINE_JS)
        if not d:
            print("  no 'Location (city)' label on this job -- skip")
            continue
        print("  FOUND a Location (city) field. input found via for/id:", d["inputFound"])
        print("  input outerHTML:", d["inputOuter"])
        print("  container structure:")
        for line in d["containerDump"]:
            print("   ", line)

        # Now actually type into it and see what shows up (read-only: never submits)
        loc = page.get_by_label("Location (city)", exact=False)
        if not loc.count():
            loc = page.locator("input[id*=city], input[aria-label*='city' i]").first
        print("  typing 'Beng' into the field...")
        loc.first.click()
        loc.first.type("Beng", delay=120)
        page.wait_for_timeout(1200)
        boxes = page.evaluate(LISTBOX_JS)
        print("  visible listbox/option elements after typing:")
        for b in boxes:
            print("   ", b)
        page.screenshot(path="/private/tmp/claude-501/-Users-rooney-Documents-jobbot/"
                              "0bf09172-0c1f-4076-8d8a-3c5e4eedc871/scratchpad/diag_location.png",
                         full_page=True)
        print("  screenshot saved. NOT submitting -- stopping diagnostic here.")
        break
    else:
        print("\nNo candidate job among the first 12 had a Location (city) field.")
