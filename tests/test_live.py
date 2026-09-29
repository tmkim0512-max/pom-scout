"""Live public demo sites. Optional: run with `pytest -m live` (network + SAUCE_USERNAME/SAUCE_PASSWORD)."""
import os
from pathlib import Path

import pytest
import yaml
from playwright.sync_api import sync_playwright

from pom_scout.cli import login
from pom_scout.disposition import judge
from pom_scout.explore import explore

pytestmark = pytest.mark.live
TARGETS = Path(__file__).parent.parent / "targets"


@pytest.fixture
def page():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        yield browser.new_page()
        browser.close()


def test_the_internet_walls_and_gone(page):
    t = yaml.safe_load((TARGETS / "the_internet.yaml").read_text())
    base = t["base_url"]
    rows = judge(page, [base + "/secure", base + "/basic_auth", base + "/status_codes/404", base + "/checkboxes"], t)
    assert {r["url"].removeprefix(base): r["verdict"] for r in rows} == {
        "/secure": "LOGIN_WALL", "/basic_auth": "PERMISSION_WALL", "/status_codes/404": "GONE", "/checkboxes": "REACHED"}


@pytest.mark.skipif(not os.environ.get("SAUCE_PASSWORD"), reason="SAUCE_USERNAME/SAUCE_PASSWORD not set")
def test_saucedemo_clicks_find_what_links_cannot(page):
    t = yaml.safe_load((TARGETS / "saucedemo.yaml").read_text()) | {"max_pages": 3, "max_depth": 1}
    login(page, t)
    link_only = explore(page, t, click=False)
    with_clicks = explore(page, t)
    assert len(link_only["visited"]) == 1
    assert any("inventory-item.html?id=" in v["url"] for v in with_clicks["visited"])
    assert {s["text"] for s in with_clicks["skipped_triggers"]} == {"Logout", "Reset App State"}
