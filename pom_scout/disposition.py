"""Give every candidate URL exactly one verdict, after checking that the judge itself can still see."""
import re
from urllib.parse import urldefrag

from playwright.sync_api import Error as PlaywrightError

VERDICTS = ("REACHED", "LOGIN_WALL", "PERMISSION_WALL", "GONE", "EMPTY", "DUPLICATE", "SKIPPED_UNSAFE", "UNDECIDED")
UNSAFE = re.compile(r"log[-_ ]?out|sign[-_ ]?out|reset|delete|remove|unsubscribe", re.I)


class JudgeBlind(Exception):
    """The negative control was REACHED: the judge cannot tell 'missing' from 'present'."""


def norm(url):
    return urldefrag(url).url


def is_unsafe(text, extra=()):
    return bool(UNSAFE.search(text or "")) or any(w in (text or "") for w in extra)


def _marker(t, verdict, requested, final, text):
    m = t.get("markers", {}).get(verdict, {})
    if m.get("url") and re.search(m["url"], final) and not re.search(m["url"], requested):
        return "url_marker"
    if m.get("text") and m["text"] in text:
        return "text_marker"
    return None


def decide(url, ev, t, known):
    """Pure mapping: evidence of one visit -> (verdict, reason). Order matters; first match wins."""
    if is_unsafe(url, t.get("unsafe_words", ())):
        return "SKIPPED_UNSAFE", "guard_word"
    err = ev.get("error")
    if err:
        if "ERR_NAME_NOT_RESOLVED" in err:
            return "GONE", "dns"
        for needle, reason in (("Timeout", "timeout"), ("ERR_CERT", "ssl"), ("ERR_TOO_MANY_REDIRECTS", "redirect_loop"), ("Download is starting", "download")):
            if needle in err:
                return "UNDECIDED", reason
        return "UNDECIDED", "navigation_error"
    status, final, text = ev.get("status"), ev["final_url"], ev.get("text", "")
    trust = t.get("http_status_reliable", True)
    if hit := _marker(t, "LOGIN_WALL", url, final, text):
        return "LOGIN_WALL", hit
    if status in (401, 403) or (hit := _marker(t, "PERMISSION_WALL", url, final, text)):
        return "PERMISSION_WALL", hit or f"http_{status}"
    if (trust and status in (404, 410)) or (hit := _marker(t, "GONE", url, final, text)):
        return "GONE", hit or f"http_{status}"
    if status is None or (trust and status >= 400):
        return "UNDECIDED", f"http_{status}"
    if norm(final) != norm(url) and norm(final) in known:
        return "DUPLICATE", "redirected_to_known"
    if not text.strip():
        return "EMPTY", "no_visible_text"
    return "REACHED", "ok"


def probe(page, url, timeout_ms=15000, settle_ms=300):
    try:
        resp = page.goto(url, timeout=timeout_ms)
    except PlaywrightError as e:
        return {"error": str(e).splitlines()[0], "final_url": url}
    chain, req = [], resp.request.redirected_from if resp else None
    while req:
        chain.insert(0, req.url)
        req = req.redirected_from
    page.wait_for_timeout(settle_ms)  # let client-side apps render before reading text
    return {"status": resp.status if resp else None, "final_url": page.url, "redirect_chain": chain,
            "text": page.locator("body").inner_text() if page.locator("body").count() else ""}


def judge(page, urls, t, gate=True):
    """Yield one row per URL. Controls are probed at start and every N URLs (positive only)."""
    known = {norm(u) for u in urls}
    base, ctl = t["base_url"], t.get("controls", {})
    to = t.get("timeout_ms", 15000)

    def control(path):
        return decide(base + path, probe(page, base + path, to), t, known)[0]

    gate_ok = True
    if gate and ctl:
        if control(ctl["negative"]) == "REACHED":
            raise JudgeBlind(f"negative control {ctl['negative']} was REACHED")
        gate_ok = control(ctl["positive"]) == "REACHED"
    for i, url in enumerate(urls):
        if gate and ctl and i and i % ctl.get("every", 10) == 0:
            gate_ok = control(ctl["positive"]) == "REACHED"
        ev = {"final_url": url} if is_unsafe(url, t.get("unsafe_words", ())) else probe(page, url, to)
        verdict, reason = decide(url, ev, t, known)
        row = {"url": url, "final_url": ev["final_url"], "verdict": verdict, "reason": reason,
               "evidence": {"status": ev.get("status"), "text_len": len(ev.get("text", "")),
                            "redirect_chain": ev.get("redirect_chain", []), "error": ev.get("error")}}
        if not gate_ok:
            row.update(verdict="UNDECIDED", reason="control_positive_failed", ungated_verdict=verdict)
        yield row
