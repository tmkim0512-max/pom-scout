"""Selector policy: generate candidates in the live page, drop forbidden ones, keep count==1, diversify strategies."""
import re

ORDER = ("test_id", "id", "name", "aria_label", "href")  # Tier1: test_id,id  Tier2: name,aria_label,href
TIER = {"test_id": 1, "id": 1, "name": 2, "aria_label": 2, "href": 2}
FORBIDDEN = re.compile(r":nth-(child|of-type)|:(first|last)-child|\[\d+\]\s*$|^/")  # position-dependent / absolute XPath
DYNAMIC_ID = re.compile(r"\d{5,}|\\?:r[0-9a-z]+\\?:|radix-|[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-")  # + CSS-escaped React ids, UUIDs
MAX_DEPTH = 3

CANDIDATES_JS = """() => {
  const count = s => { try { return document.querySelectorAll(s).length } catch (e) { return -1 } };
  const q = v => v.replace(/\\\\/g, '\\\\\\\\').replace(/"/g, '\\\\"');
  const out = [];
  for (const el of document.querySelectorAll('a,button,input,select,textarea,[role=button],[role=link]')) {
    if (!el.checkVisibility({visibilityProperty: true, opacityProperty: true})) continue;
    const tag = el.tagName.toLowerCase(), cands = [], attr = n => el.getAttribute(n);
    const add = (strategy, sel) => cands.push({strategy, sel, count: count(sel)});
    for (const a of ['data-test', 'data-testid', 'data-qa']) if (attr(a)) add('test_id', `[${a}="${q(attr(a))}"]`);
    if (el.id) add('id', '#' + CSS.escape(el.id));
    if (attr('name')) add('name', `${tag}[name="${q(attr('name'))}"]`);
    if (attr('aria-label')) add('aria_label', `${tag}[aria-label="${q(attr('aria-label'))}"]`);
    const href = attr('href');
    if (tag === 'a' && href && !href.startsWith('#')) add('href', `a[href="${q(href)}"]`);
    const landmark = el.closest('header,nav,main,aside,footer,form,[role=dialog]');  // HTML landmark elements
    out.push({tag, role: attr('role') || tag, cands,
              zone: landmark ? (landmark.getAttribute('role') === 'dialog' ? 'dialog' : landmark.tagName.toLowerCase()) : 'main',
              name: attr('data-test') || attr('data-testid') || attr('data-qa') || el.id || attr('name') || attr('aria-label')
                    || (el.innerText || el.value || '').trim().slice(0, 30) || tag});
  }
  return out;
}"""


def is_forbidden(sel):
    bare = re.sub(r'"(?:\\.|[^"\\])*"', '""', sel.strip())  # spaces inside attribute values are not combinators
    depth = len(re.findall(r"\s*[>+~]\s*|\s+", bare))
    return bool(FORBIDDEN.search(sel)) or depth > MAX_DEPTH


def is_dynamic(sel):
    return bool(DYNAMIC_ID.search(sel))


def pick(cands, k=3):
    """Up to k unique selectors, one per strategy. Dynamic-looking IDs are demoted to fallback, not dropped."""
    ok = [c for c in cands if c["count"] == 1 and not is_forbidden(c["sel"])]
    ok.sort(key=lambda c: (is_dynamic(c["sel"]), ORDER.index(c["strategy"])))
    chosen, used = [], set()
    for c in ok:
        if c["strategy"] not in used and c["sel"] not in (x["sel"] for x in chosen):
            chosen.append(c)
            used.add(c["strategy"])
    return chosen[:k]


def snake(text):
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "element"
