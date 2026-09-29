"""Browser tests against a local server: no internet needed (Chromium must be installed)."""
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from pom_scout.collect import collect
from pom_scout.disposition import JudgeBlind, judge
from pom_scout.explore import explore
from pom_scout.locate import NotLocated, locate

FIXTURES = Path(__file__).parent / "fixtures"


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        routes = {"/private": (401, None), "/members": (302, "/login.html"), "/old": (302, "/plain.html")}
        if self.path in routes:
            code, loc = routes[self.path]
            self.send_response(code)
            if loc:
                self.send_header("Location", loc)
            self.end_headers()
            return
        if self.path == "/slow":
            time.sleep(3)
        if self.path.startswith("/sub/") or self.path == "/slow":
            body = f"<!doctype html><body><p>{self.path}</p></body>".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def base():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(FIXTURES)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


@pytest.fixture(scope="module")
def page():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        yield browser.new_page()
        browser.close()


def target(base, **kw):
    return {"base_url": base, "entry": "/accordion.html", "max_depth": 1, "settle_ms": 50, "click_wait_ms": 50, **kw}


def paths(urls, base):
    return sorted(u.removeprefix(base) for u in urls)


def test_link_only_misses_what_clicks_reveal(page, base):
    link = explore(page, target(base), click=False)
    both = explore(page, target(base))
    link_urls = {v["url"] for v in link["visited"]} | {d["url"] for d in link["discovered"]}
    both_urls = {v["url"] for v in both["visited"]} | {d["url"] for d in both["discovered"]}
    assert paths(link_urls, base) == ["/accordion.html", "/logout", "/plain.html"]
    assert paths(both_urls - link_urls, base) == ["/detail.html"] + [f"/sub/{i}-{x}.html" for i in (1, 2, 3) for x in "ab"]
    assert both["axis_stats"] == {"link_only": 2, "click_only": 7, "both": 0}
    assert base + "/logout" not in {v["url"] for v in both["visited"]}  # kept as a candidate, never visited


def test_harvest_after_each_click_beats_one_snapshot(page, base):
    each = explore(page, target(base))
    end = explore(page, target(base), harvest_each=False)
    subs = lambda sm: [u for u in [v["url"] for v in sm["visited"]] + [d["url"] for d in sm["discovered"]] if "/sub/" in u]
    assert len(subs(each)) == 6
    assert paths(subs(end), base) == ["/sub/3-a.html", "/sub/3-b.html"]  # only the last opened menu survives


def test_session_ending_trigger_is_never_clicked(page, base):
    sm = explore(page, target(base))
    assert [s["trigger"] for s in sm["skipped_triggers"]] == ["#logout"]
    assert sm["status"] == "OK" and sm["barren_triggers"] == []


def test_all_eight_verdicts_with_controls(page, base):
    t = target(base, timeout_ms=1000, markers={"LOGIN_WALL": {"url": "/login.html$"}},
               controls={"positive": "/plain.html", "negative": "/nope.html"})
    urls = [base + p for p in ["/plain.html", "/members", "/private", "/nope.html", "/empty.html", "/old", "/logout", "/slow"]]
    got = {r["url"].removeprefix(base): r["verdict"] for r in judge(page, urls, t)}
    assert got == {"/plain.html": "REACHED", "/members": "LOGIN_WALL", "/private": "PERMISSION_WALL", "/nope.html": "GONE",
                   "/empty.html": "EMPTY", "/old": "DUPLICATE", "/logout": "SKIPPED_UNSAFE", "/slow": "UNDECIDED"}


def test_blind_judge_aborts(page, base):
    t = target(base, controls={"positive": "/plain.html", "negative": "/detail.html"})
    with pytest.raises(JudgeBlind):
        list(judge(page, [base + "/plain.html"], t))


def test_failed_positive_control_turns_verdicts_undecided(page, base):
    t = target(base, controls={"positive": "/private", "negative": "/nope.html"})
    rows = list(judge(page, [base + "/plain.html", base + "/nope.html"], t))
    assert [(r["verdict"], r["reason"], r["ungated_verdict"]) for r in rows] == [
        ("UNDECIDED", "control_positive_failed", "REACHED"), ("UNDECIDED", "control_positive_failed", "GONE")]


def test_collect_then_locate(page, base):
    pom = collect(page, base + "/form.html", settle_ms=50)
    header, form = pom["zones"]["header"]["elements"], pom["zones"]["form"]["elements"]
    assert header["cart_link"]["strategies"] == ["test_id", "id", "aria_label"]
    assert form["submit"]["selectors"] == ['[data-test="submit"]', "#submit_48213977"]  # dynamic id demoted to fallback
    assert form["field_8f3a91c2"]["strategies"] == ["id", "name"] and form["field_8f3a91c2"]["fallback_short"]
    assert [u["key"] for u in pom["unresolved_elements"]] == ["help", "help_2"]
    assert locate(page, pom, "form", "email").get_attribute("name") == "email"
    page.evaluate("document.querySelector('[data-test=submit]').removeAttribute('data-test')")
    assert locate(page, pom, "form", "submit").get_attribute("id") == "submit_48213977"  # falls back in order
    page.evaluate("document.querySelector('#email').remove()")
    with pytest.raises(NotLocated):
        locate(page, pom, "form", "email")
