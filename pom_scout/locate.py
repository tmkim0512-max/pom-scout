"""Consumer helper: try a POM element's selectors in order and return the first that matches exactly once."""


class NotLocated(LookupError):
    pass


def locate(page, pom, zone, key):
    element = pom["zones"][zone]["elements"][key]
    tried = []
    for sel in element["selectors"]:
        loc = page.locator(sel)
        n = loc.count()
        if n == 1:
            return loc
        tried.append(f"{sel} -> {n}")
    raise NotLocated(f"{zone}.{key}: no selector matched exactly once ({'; '.join(tried)})")
