"""Turn one REACHED page into a zone-shaped Page Object JSON."""
from datetime import datetime, timezone
from urllib.parse import urlparse

from .selector import CANDIDATES_JS, pick, snake


def page_id(url):
    path = urlparse(url).path.strip("/")
    return snake(path.removesuffix(".html")) if path else "home"


def build_pom(url, elements):
    zones, unresolved, taken = {}, [], set()
    for el in elements:
        key = base = snake(el["name"])
        n = 2
        while key in taken:
            key, n = f"{base}_{n}", n + 1
        taken.add(key)
        chosen = pick(el["cands"])
        if not chosen:
            unresolved.append({"key": key, "tag": el["tag"], "reason": "no_unique_selector",
                               "candidates": [{"sel": c["sel"], "count": c["count"]} for c in el["cands"]]})
            continue
        entry = {"role": el["role"], "selectors": [c["sel"] for c in chosen],
                 "strategies": [c["strategy"] for c in chosen], "match_count": 1}
        if len(chosen) < 3:
            entry["fallback_short"] = True
        zones.setdefault(el["zone"], {"elements": {}})["elements"][key] = entry
    return {"$schema": "pom-scout/pom-v1", "version": "1.0.0", "page_id": page_id(url),
            "url_pattern": urlparse(url).path or "/",
            "last_verified": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "zones": zones, "unresolved_elements": unresolved}


def collect(page, url, settle_ms=500):
    page.goto(url)
    page.wait_for_timeout(settle_ms)
    return build_pom(url, page.evaluate(CANDIDATES_JS))
