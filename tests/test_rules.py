"""Pure rules: no browser, no network."""
from pathlib import Path

import pytest
import yaml

from pom_scout.collect import build_pom, page_id
from pom_scout.disposition import VERDICTS, decide, is_unsafe
from pom_scout.explore import classify_trigger
from pom_scout.score import disposition_accuracy, element_recall, page_recall, selector_quality
from pom_scout.selector import is_dynamic, is_forbidden, pick, snake


@pytest.mark.parametrize("href,expected", [
    (None, "click"), ("", "click"), ("#", "click"), ("#none", "click"), ("  # ", "click"),
    ("javascript:void(0)", "skip"), ("JavaScript:go()", "skip"),
    ("/cart", "goto"), ("detail.html", "goto"), ("https://example.com/", "goto"),
])
def test_classify_trigger(href, expected):
    assert classify_trigger(href) == expected


@pytest.mark.parametrize("text", ["/logout", "/log-out", "Sign out", "#reset_sidebar_link Reset App State", "/items/delete/3"])
def test_unsafe_words(text):
    assert is_unsafe(text)


def test_safe_and_extra_words():
    assert not is_unsafe("/inventory.html")
    assert is_unsafe("/wipe", extra=["wipe"])


BASE = {"markers": {"LOGIN_WALL": {"url": "/login$"}, "GONE": {"text": "ITEM NOT FOUND"}}}
KNOWN = {"https://x.test/a", "https://x.test/b"}


def ev(status=200, final="https://x.test/a", text="hello", **kw):
    return {"status": status, "final_url": final, "text": text, **kw}


DECIDE_CASES = [
    ("https://x.test/a", ev(), BASE, ("REACHED", "ok")),
    ("https://x.test/secure", ev(final="https://x.test/login"), BASE, ("LOGIN_WALL", "url_marker")),
    ("https://x.test/login", ev(final="https://x.test/login"), BASE, ("REACHED", "ok")),  # the login page itself is not a wall
    ("https://x.test/p", ev(401, "https://x.test/p"), BASE, ("PERMISSION_WALL", "http_401")),
    ("https://x.test/p", ev(403, "https://x.test/p"), BASE, ("PERMISSION_WALL", "http_403")),
    ("https://x.test/g", ev(404, "https://x.test/g"), BASE, ("GONE", "http_404")),
    ("https://x.test/g", ev(410, "https://x.test/g"), BASE, ("GONE", "http_410")),
    ("https://x.test/i?id=9", ev(final="https://x.test/i?id=9", text="ITEM NOT FOUND"), BASE, ("GONE", "text_marker")),  # soft-404
    ("https://x.test/e", ev(final="https://x.test/e", text="  "), BASE, ("EMPTY", "no_visible_text")),
    ("https://x.test/old", ev(final="https://x.test/b"), BASE, ("DUPLICATE", "redirected_to_known")),
    ("https://x.test/new", ev(final="https://x.test/elsewhere"), BASE, ("REACHED", "ok")),
    ("https://x.test/logout", {"final_url": "https://x.test/logout"}, BASE, ("SKIPPED_UNSAFE", "guard_word")),
    ("https://x.test/s", ev(500, "https://x.test/s"), BASE, ("UNDECIDED", "http_500")),
    ("https://x.test/s", {"error": "Timeout 1000ms exceeded.", "final_url": "https://x.test/s"}, BASE, ("UNDECIDED", "timeout")),
    ("https://x.test/s", {"error": "net::ERR_CERT_DATE_INVALID", "final_url": "u"}, BASE, ("UNDECIDED", "ssl")),
    ("https://x.test/s", {"error": "net::ERR_TOO_MANY_REDIRECTS", "final_url": "u"}, BASE, ("UNDECIDED", "redirect_loop")),
    ("https://nx.test/", {"error": "net::ERR_NAME_NOT_RESOLVED", "final_url": "u"}, BASE, ("GONE", "dns")),
    # A host that answers 404 for pages that render: status is ignored, text decides.
    ("https://x.test/a", ev(404), {**BASE, "http_status_reliable": False}, ("REACHED", "ok")),
    ("https://x.test/z", ev(404, "https://x.test/z", ""), {**BASE, "http_status_reliable": False}, ("EMPTY", "no_visible_text")),
]


@pytest.mark.parametrize("url,evidence,cfg,expected", DECIDE_CASES)
def test_decide(url, evidence, cfg, expected):
    assert decide(url, evidence, cfg, KNOWN) == expected


def test_decide_covers_all_eight_verdicts():
    assert {case[3][0] for case in DECIDE_CASES} == set(VERDICTS)


@pytest.mark.parametrize("sel", ["li:nth-child(2) a", "ul > li:first-child", "//div[2]", "/html/body/div", "div a span b em"])
def test_forbidden(sel):
    assert is_forbidden(sel)


@pytest.mark.parametrize("sel", ["#cart", '[data-test="a b c d e"]', 'button[aria-label="Open the main menu now"]', "#menu a"])
def test_allowed(sel):
    assert not is_forbidden(sel)


@pytest.mark.parametrize("sel,dyn", [("#submit_48213977", True), ("#\\:r3a\\:", True), ("#:r3a:", True), ("#radix-12", True), ("#e59bcbe0-9e06-013f-0c7f-2e8ba6a5537a", True), ("#\\35 ada4a10-9e08-013f-0dd4-2e8ba6a5537a", True), ("#cart", False)])
def test_dynamic(sel, dyn):
    assert is_dynamic(sel) is dyn


def c(strategy, sel, count=1):
    return {"strategy": strategy, "sel": sel, "count": count}


def test_pick_prefers_tier1_and_diversifies():
    got = pick([c("href", 'a[href="/x"]'), c("test_id", '[data-test="x"]'), c("test_id", '[data-testid="x"]'), c("id", "#x"), c("name", 'a[name="x"]')])
    assert [g["strategy"] for g in got] == ["test_id", "id", "name"]


def test_pick_drops_non_unique_and_demotes_dynamic_id():
    got = pick([c("id", "#submit_48213977"), c("test_id", '[data-test="s"]', count=2), c("name", 'button[name="s"]')])
    assert [g["sel"] for g in got] == ['button[name="s"]', "#submit_48213977"]


def test_build_pom_shapes_and_reports_unresolved():
    els = [
        {"tag": "a", "role": "a", "zone": "header", "name": "cart-link", "cands": [c("test_id", '[data-test="cart-link"]'), c("id", "#cart")]},
        {"tag": "a", "role": "a", "zone": "header", "name": "cart-link", "cands": [c("href", 'a[href="/c"]')]},
        {"tag": "span", "role": "button", "zone": "main", "name": "Help", "cands": []},
    ]
    pom = build_pom("https://x.test/cart.html", els)
    assert pom["page_id"] == "cart" and pom["url_pattern"] == "/cart.html"
    header = pom["zones"]["header"]["elements"]
    assert set(header) == {"cart_link", "cart_link_2"}
    assert header["cart_link"]["selectors"] == ['[data-test="cart-link"]', "#cart"] and header["cart_link"]["fallback_short"]
    assert pom["unresolved_elements"][0]["key"] == "help"


def test_page_id_and_snake():
    assert page_id("https://x.test/") == "home"
    assert page_id("https://x.test/inventory-item.html?id=4") == "inventory_item"
    assert snake("add-to-cart-test.allthethings()-t-shirt-(red)") == "add_to_cart_test_allthethings_t_shirt_red"


def test_page_recall_uses_full_path_and_query():
    sm = {"visited": [{"url": "https://x.test/a", "final_url": "https://x.test/a"}, {"url": "https://x.test/r", "final_url": "https://x.test/i?id=1"},
                      {"url": "https://x.test/bad", "final_url": "https://x.test/bad", "error": "boom"}],
          "discovered": [{"url": "https://x.test/extra"}]}
    r = page_recall(["/a", "/r", "/i?id=1", "/i?id=2"], sm)  # a redirect counts for both its link and its landing page
    assert (r["found"], r["golden"], r["missing"], r["not_in_golden"]) == (3, 4, ["/i?id=2"], 1)


def test_disposition_accuracy_counts_unjudged():
    rows = [{"url": "https://x.test/a", "verdict": "REACHED"}, {"url": "https://x.test/b", "verdict": "GONE"}]
    r = disposition_accuracy({"/a": "REACHED", "/b": "EMPTY", "/c": "GONE"}, rows)
    assert r["match"] == 1 and sum(r["confusion"].values()) == 3
    assert r["confusion"]["GONE -> NOT_JUDGED"] == 1


def test_element_recall_and_selector_quality():
    pom = build_pom("https://x.test/p", [{"tag": "a", "role": "a", "zone": "main", "name": "k",
                                          "cands": [c("test_id", '[data-test="k"]'), c("id", "#k"), c("name", 'a[name="k"]')]}])
    assert element_recall({"p": ["k", "missing"]}, {"p": pom})["found"] == 1
    q = selector_quality({"p": pom})
    assert q["fallback_ge_2_ratio"] == 1.0 and q["forbidden"] == 0 and q["primary_tier"] == {"tier1": 1}


@pytest.mark.parametrize("path", sorted(Path(__file__).parent.parent.glob("*/*.yaml")), ids=lambda p: p.name)
def test_config_and_golden_files_parse(path):
    assert yaml.safe_load(path.read_text())
