"""Score collected output against hand-written golden files. Recall here is 'share of a golden list found', not test coverage."""
from collections import Counter
from urllib.parse import urlparse

from .selector import TIER, is_forbidden


def rel(url):
    u = urlparse(url)
    return (u.path or "/") + (f"?{u.query}" if u.query else "")


def page_recall(golden, site_map):
    ok = [v for v in site_map["visited"] if not v.get("error")]
    got = {rel(v[k]) for v in ok for k in ("url", "final_url")} | {rel(d["url"]) for d in site_map["discovered"]}
    want = set(golden)
    hit = want & got
    return {"golden": len(want), "found": len(hit), "recall": round(len(hit) / len(want), 4),
            "missing": sorted(want - got), "not_in_golden": len(got - want)}


def disposition_accuracy(golden, rows):
    by_url = {rel(r["url"]): r["verdict"] for r in rows}
    pairs = [(want, by_url.get(url, "NOT_JUDGED")) for url, want in golden.items()]
    confusion = Counter(f"{w} -> {g}" for w, g in pairs)
    match = sum(w == g for w, g in pairs)
    return {"golden": len(golden), "match": match, "accuracy": round(match / len(golden), 4),
            "confusion": dict(sorted(confusion.items())),
            "mismatches": {u: by_url.get(u, "NOT_JUDGED") for u, w in golden.items() if by_url.get(u) != w}}


def element_recall(golden, poms):
    out = {}
    for pid, keys in golden.items():
        have = {k for z in poms.get(pid, {}).get("zones", {}).values() for k in z["elements"]}
        out[pid] = {"golden": len(keys), "found": len(set(keys) & have), "missing": sorted(set(keys) - have)}
    total = sum(v["golden"] for v in out.values())
    return {"pages": out, "golden": total, "found": sum(v["found"] for v in out.values()),
            "recall": round(sum(v["found"] for v in out.values()) / total, 4) if total else None}


def selector_quality(poms):
    els = [e for p in poms.values() for z in p["zones"].values() for e in z["elements"].values()]
    unresolved = sum(len(p.get("unresolved_elements", [])) for p in poms.values())
    tiers = Counter(f"tier{TIER[e['strategies'][0]]}" for e in els)
    return {"elements": len(els), "unresolved": unresolved,
            "unique_ratio": round(sum(e["match_count"] == 1 for e in els) / len(els), 4) if els else None,
            "fallback_ge_2_ratio": round(sum(len(e["selectors"]) >= 3 for e in els) / len(els), 4) if els else None,
            "primary_tier": dict(sorted(tiers.items())),
            "forbidden": sum(is_forbidden(s) for e in els for s in e["selectors"])}
