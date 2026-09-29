"""Two exploration axes in one pass: follow real hrefs (BFS) and click href-less triggers."""
from urllib.parse import urljoin, urlparse

from playwright.sync_api import Error as PlaywrightError

from .disposition import is_unsafe, norm
from .selector import is_dynamic

LINKS_JS = "() => [...document.querySelectorAll('a[href]')].map(a => a.getAttribute('href'))"
# Visible configured triggers + every visible <a>. Only elements addressable by #id or data-test are kept.
TRIGGERS_JS = """(sels) => {
  const out = [], seen = new Set();
  const add = el => {
    if (seen.has(el) || !el.checkVisibility({visibilityProperty: true, opacityProperty: true})) return; seen.add(el);
    const t = el.getAttribute('data-test');
    const sel = el.id ? '#' + CSS.escape(el.id) : t ? `[data-test="${t}"]` : null;
    out.push({sel, configured: sels.some(s => el.matches(s)), href: el.getAttribute('href'),
              text: (el.innerText || '').trim().slice(0, 40)});
  };
  sels.forEach(s => document.querySelectorAll(s).forEach(add));
  document.querySelectorAll('a').forEach(add);
  return out;
}"""


def classify_trigger(href):
    """Raw href attribute -> click | goto | skip. Clicking a real href leaves the page, so we goto it instead."""
    if href is None or href.strip() == "" or href.strip().startswith("#"):
        return "click"
    if href.strip().lower().startswith("javascript:"):
        return "skip"
    return "goto"


def harvest(page, t):
    """Read href *attributes* (a.href would turn '#' into the current URL) and make them absolute."""
    out = set()
    for raw in page.evaluate(LINKS_JS):
        if classify_trigger(raw) != "goto" or raw.strip().lower().startswith(("mailto:", "tel:")):
            continue
        url = norm(urljoin(page.url, raw.strip()))
        if urlparse(url).netloc == urlparse(t["base_url"]).netloc:
            out.add(url)  # unsafe URLs are kept: never visited, but judged SKIPPED_UNSAFE
    return out


def load(page, url, t):
    resp = page.goto(url, timeout=t.get("timeout_ms", 15000))
    page.wait_for_timeout(t.get("settle_ms", 500))  # client-rendered apps paint after 'load'
    return resp


def _triggers(page, t, state):
    found = []
    for el in page.evaluate(TRIGGERS_JS, t.get("click_triggers", [])):
        if not el["configured"] and classify_trigger(el["href"]) != "click":
            continue
        if is_unsafe(f"{el['sel']} {el['text']}", t.get("unsafe_words", ())):
            state["skipped"].setdefault(el["sel"] or el["text"], {"page": page.url, "trigger": el["sel"], "text": el["text"]})
        elif el["sel"] and not is_dynamic(el["sel"]):
            found.append(el["sel"])
        else:  # no stable handle to click it again after a reload
            state["unaddressable"].add((page.url, el["text"]))
    return found


def click_explore(page, url, t, state, harvest_each=True):
    """Replay click chains from a fresh load; record where each chain lands and what it reveals."""
    wait = t.get("click_wait_ms", 500)
    load(page, url, t)
    base_trig, base_links = set(_triggers(page, t, state)), harvest(page, t)
    queue, landed, revealed, last_snapshot = [(s,) for s in sorted(base_trig)], set(), set(), set()
    while queue and len(state["done"]) < t.get("max_clicks", 80):
        chain = queue.pop(0)
        if chain in state["done"]:
            continue
        state["done"].add(chain)
        load(page, url, t)
        try:
            for sel in chain:
                page.click(sel, timeout=3000)
                page.wait_for_timeout(wait)
        except PlaywrightError as e:
            state["barren"].append({"page": url, "trigger": " > ".join(chain), "reason": "click_failed: " + str(e).splitlines()[0]})
            continue
        if norm(page.url) != url:
            landed.add(norm(page.url))
            continue
        new_trig = [s for s in _triggers(page, t, state) if s not in base_trig and s not in chain]
        queue += [chain + (s,) for s in new_trig]
        base_trig.update(new_trig)  # each revealed trigger is queued once, under the first chain that revealed it
        now_links = harvest(page, t) - base_links
        new_links = now_links if harvest_each else set()
        last_snapshot = now_links  # naive mode keeps only whatever is on screen after the last in-page click
        revealed |= new_links
        if not new_trig and not new_links:
            state["barren"].append({"page": url, "trigger": " > ".join(chain), "reason": "no_effect_after_click"})
    if not harvest_each:  # the naive way: one snapshot after all clicks
        revealed |= last_snapshot
    return landed | revealed


def explore(page, t, click=True, harvest_each=True):
    start = norm(urljoin(t["base_url"], t.get("entry", "/")))
    frontier, visited, found = [(start, 0, "entry", None)], [], {}
    state = {"done": set(), "barren": [], "skipped": {}, "unaddressable": set()}
    while frontier and len(visited) < t.get("max_pages", 30):
        url, depth, via, src = frontier.pop(0)
        if any(v["url"] == url for v in visited):
            continue
        try:
            resp = load(page, url, t)
        except PlaywrightError as e:
            visited.append({"url": url, "final_url": url, "status": None, "via": via, "from": src, "error": str(e).splitlines()[0]})
            continue
        visited.append({"url": url, "final_url": page.url, "status": resp.status if resp else None, "via": via, "from": src})
        new = {u: "link" for u in harvest(page, t)}
        if click:
            for u in click_explore(page, url, t, state, harvest_each):
                new[u] = "both" if new.get(u) == "link" else "click"
        for u, axis in new.items():
            rec = found.setdefault(u, {"axes": set(), "seen_on": url})
            rec["axes"] |= {"link", "click"} if axis == "both" else {axis}
            if depth + 1 <= t.get("max_depth", 2) and not is_unsafe(u, t.get("unsafe_words", ())):
                frontier.append((u, depth + 1, "click" if axis == "click" else "link", url))
    seen = {v["url"] for v in visited}
    axes = [found[u]["axes"] for u in found]
    return {
        "target": t["base_url"],
        "visited": visited,
        "discovered": [{"url": u, "seen_on": r["seen_on"]} for u, r in found.items() if u not in seen],
        "barren_triggers": state["barren"],
        "skipped_triggers": list(state["skipped"].values()),
        "unaddressable_triggers": len(state["unaddressable"]),
        "axis_stats": {"link_only": sum(a == {"link"} for a in axes), "click_only": sum(a == {"click"} for a in axes),
                       "both": sum(a == {"link", "click"} for a in axes)},
        "status": "PARTIAL" if state["barren"] else "OK",
    }
