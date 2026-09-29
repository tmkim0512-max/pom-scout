"""pom-scout explore | judge | collect | score.  Exit codes: 0 OK, 2 PARTIAL, 3 judge untrustworthy, 1 error."""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml
from playwright.sync_api import sync_playwright

from . import score as sc
from .collect import collect, page_id
from .disposition import JudgeBlind, judge, norm
from .explore import explore


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def login(page, t):
    lg = t["login"]
    page.goto(t["base_url"] + lg["url"])
    for sel, raw in lg["fill"].items():
        value = os.path.expandvars(raw)
        if "$" in value:
            sys.exit(f"login: environment variable in {raw!r} is not set")
        page.fill(sel, value)
    page.click(lg["submit"])
    page.wait_for_url(lg["wait_url"])


def with_page(t, fn, do_login=True):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        if do_login and t.get("login"):
            login(page, t)
        try:
            return fn(page)
        finally:
            browser.close()


def cmd_explore(a, t, out):
    started = now()
    sm = with_page(t, lambda pg: explore(pg, t, click=not a.no_click, harvest_each=not a.harvest_at_end))
    sm = {"target": sm.pop("target"), "started_at": started, "finished_at": now(), **sm}
    (out / "site_map.json").write_text(json.dumps(sm, indent=2, ensure_ascii=False))
    print(f"visited={len(sm['visited'])} discovered={len(sm['discovered'])} axis={sm['axis_stats']} "
          f"barren={len(sm['barren_triggers'])} skipped_unsafe_triggers={len(sm['skipped_triggers'])} status={sm['status']}")
    return 2 if sm["status"] == "PARTIAL" else 0


def cmd_judge(a, t, out):
    sm = json.loads((out / "site_map.json").read_text())
    urls = list(dict.fromkeys([v["url"] for v in sm["visited"]] + [d["url"] for d in sm["discovered"]]
                              + [norm(t["base_url"] + u) for u in t.get("extra_candidates", [])]))
    try:
        rows = with_page(t, lambda pg: list(judge(pg, urls, t)), do_login=not a.no_login)
    except JudgeBlind as e:
        print(f"ABORT: {e}", file=sys.stderr)
        return 3
    with open(out / "dispositions.jsonl", "w") as f:
        f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    counts = {v: sum(r["verdict"] == v for r in rows) for v in sorted({r["verdict"] for r in rows})}
    assert sum(counts.values()) == len(urls) == len(rows), "every candidate must get exactly one verdict"
    print(f"candidates={len(urls)} {counts}")
    return 2 if counts.get("UNDECIDED") else 0


def cmd_collect(a, t, out):
    rows = [json.loads(line) for line in (out / "dispositions.jsonl").read_text().splitlines()]
    targets = {}
    for r in rows:  # one POM per path
        if r["verdict"] == "REACHED":
            targets.setdefault(page_id(r["final_url"]), r["final_url"])
    (out / "pages").mkdir(exist_ok=True)

    def run(pg):
        failed = []
        for pid, url in targets.items():
            try:
                pom = collect(pg, url, t.get("settle_ms", 500))
            except Exception as e:  # keep going, but report and exit PARTIAL
                failed.append(pid)
                print(f"collect failed {url}: {e}", file=sys.stderr)
                continue
            (out / "pages" / f"{pid}.pom.json").write_text(json.dumps(pom, indent=2, ensure_ascii=False))
        return failed

    failed = with_page(t, run)
    print(f"pages={len(targets) - len(failed)}/{len(targets)} failed={failed}")
    return 2 if failed else 0


def cmd_score(a, t, out):
    load = lambda p: yaml.safe_load(Path(p).read_text())
    res = {}
    if a.pages:
        res["pages"] = sc.page_recall(load(a.pages), json.loads((out / "site_map.json").read_text()))
    if a.dispositions:
        rows = [json.loads(line) for line in (out / "dispositions.jsonl").read_text().splitlines()]
        res["dispositions"] = sc.disposition_accuracy(load(a.dispositions), rows)
    poms = {p.name.removesuffix(".pom.json"): json.loads(p.read_text()) for p in sorted((out / "pages").glob("*.pom.json"))}
    if a.elements:
        res["elements"] = sc.element_recall(load(a.elements), poms)
    if poms:
        res["selectors"] = sc.selector_quality(poms)
    (out / "score.json").write_text(json.dumps(res, indent=2, ensure_ascii=False))
    print(json.dumps(res, indent=2, ensure_ascii=False))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="pom-scout")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("explore", "judge", "collect", "score"):
        s = sub.add_parser(name)
        s.add_argument("target", help="targets/<site>.yaml")
        s.add_argument("--out", help="output dir (default out/<name>)")
    sub.choices["explore"].add_argument("--no-click", action="store_true", help="link BFS only")
    sub.choices["explore"].add_argument("--harvest-at-end", action="store_true", help="harvest once after all clicks (naive)")
    sub.choices["judge"].add_argument("--no-login", action="store_true", help="judge without a session")
    for g in ("pages", "dispositions", "elements"):
        sub.choices["score"].add_argument(f"--{g}", help=f"golden {g} yaml")
    a = ap.parse_args(argv)
    t = yaml.safe_load(Path(a.target).read_text())
    out = Path(a.out or f"out/{t['name']}")
    out.mkdir(parents=True, exist_ok=True)
    return globals()[f"cmd_{a.cmd}"](a, t, out)


if __name__ == "__main__":
    sys.exit(main())
