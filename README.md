# pom-scout

Explores a web app, including menus that only open on click, and writes Page Object JSON. Every selector is checked to match exactly one element on the live page. Every page it could not reach gets a reason, and its output is scored for recall against a hand-written golden list.

```
explore ──> site_map.json ──> judge ──> dispositions.jsonl ──> collect ──> pages/*.pom.json
 (link BFS + clicks)          (8 verdicts + control gate)       (selectors)          │
                                                                              score ─┘  (vs golden/*.yaml)
```

## Results (measured 2026-09-29, 07:54–08:01 UTC)

**Page recall: following links only vs. also clicking href-less triggers**

| site | golden pages | link BFS only | BFS + click exploration |
|---|---:|---:|---:|
| saucedemo.com | 14 | 1 (7.14%) | 12 (85.71%) |
| the-internet.herokuapp.com | 44 | 44 (100%) | 44 (100%) |

On saucedemo, product titles are `<a href="#">`, the cart link has no `href` at all, and the side menu opens on click. A link crawler sees only the entry page. The two pages still missing (`checkout-step-two`, `checkout-complete`) need a submitted form. pom-scout does not fill forms. On the-internet every page is a plain link, so clicking adds nothing. That is the expected control result.

**Verdicts on the-internet vs. a hand-written expectation (52 URLs)**: 50/52 match (96.15%)

| expected → got | count |
|---|---:|
| REACHED → REACHED | 41 |
| PERMISSION_WALL → PERMISSION_WALL | 3 |
| GONE → GONE | 2 |
| LOGIN_WALL → LOGIN_WALL | 1 |
| DUPLICATE → DUPLICATE | 1 |
| SKIPPED_UNSAFE → SKIPPED_UNSAFE | 1 |
| UNDECIDED → UNDECIDED | 1 |
| REACHED → EMPTY | 1 (`/nested_frames`: a frameset has no body text; frames are not read) |
| REACHED → SKIPPED_UNSAFE | 1 (`/add_remove_elements/`: the word "remove" trips the guard, which errs on refusing) |

Sources: `examples/out/*/score.json`, produced by the commands in `examples/out/run.log`
(macOS, Python 3.12.13, Playwright 1.63.0, headless Chromium). Public sites change, so re-running may give different numbers.
Recall means "share of a golden list that was found". It is not test coverage.

## Why click?

- A link graph has no edge to a menu item whose `href` is `#` or missing. No BFS depth reaches it.
- Accordion menus close the previous submenu when you open the next one. pom-scout harvests links **right after each click** and does not take one snapshot at the end. In the local fixture (`tests/test_fixtures.py`), harvesting after every click finds 6 submenu links; one snapshot after all clicks finds 2.
- `a.href` resolves `"#"` to the current page URL. pom-scout reads `getAttribute('href')` and makes it absolute with `urljoin`.

## Zero is not "none"

A run that finds nothing is more often a broken lens than an empty site. So:

- **Every candidate URL gets exactly one verdict.** Nothing is dropped:

  | verdict | meaning |
  |---|---|
  | `REACHED` | landed on the page and it has visible text (the only verdict that gets a POM) |
  | `LOGIN_WALL` | bounced to the login page (configured URL or text marker) |
  | `PERMISSION_WALL` | 401/403, or a configured "no permission" marker |
  | `GONE` | 404/410, DNS failure, or a configured "not found" text (soft-404) |
  | `EMPTY` | landed, but no visible text |
  | `DUPLICATE` | redirected to a URL that is already a candidate |
  | `SKIPPED_UNSAFE` | visiting would change the session or data (logout, reset, delete …), so it is never visited |
  | `UNDECIDED` | we don't know: timeout, TLS error, redirect loop, 5xx, download, or a failed control |

- **The judge is checked before it judges.** A known-good URL (positive control) and a known-missing URL (negative control) are probed at start, and the positive one again every N URLs.
  If the negative control is `REACHED`, the run aborts with exit 3, because the judge cannot tell missing from present.
  If the positive control fails, every later verdict becomes `UNDECIDED` (`control_positive_failed`), and the verdict the judge would have given is kept as `ungated_verdict`.
  Measured on saucedemo with no session (`examples/out/saucedemo-no-session`): with the gate, 13/13 URLs are `UNDECIDED`. Without it they would have been 13 × `LOGIN_WALL`. With a session the same 13 URLs are 12 `REACHED` + 1 `GONE`, so 0/13 of the ungated verdicts were right.
- **Click triggers that would end the session are never clicked.** saucedemo: `Logout` and `Reset App State` are listed in `skipped_triggers`.
- **Clicks that do nothing are reported.** `barren_triggers` is non-empty → exit 2 (PARTIAL).
  saucedemo: `All Items` on the page it already shows. the-internet: `#restart-ad`, which is covered by a modal.
- saucedemo answers **HTTP 404 for every deep link**, even pages that render normally, and a logged-out deep link stays on the same URL showing an error. Status codes and URLs carry no signal there. The target file says so (`http_status_reliable: false`) and uses text markers instead.

## Run it

```bash
pip install -e ".[test]" && python -m playwright install chromium
export SAUCE_USERNAME=standard_user SAUCE_PASSWORD=secret_sauce   # public demo account shown on the saucedemo login page
pom-scout explore targets/saucedemo.yaml        # add --no-click for link BFS only
pom-scout judge   targets/saucedemo.yaml        # add --no-login to see the control gate trip
pom-scout collect targets/saucedemo.yaml
pom-scout score   targets/saucedemo.yaml --pages golden/saucedemo.pages.yaml --elements golden/saucedemo.elements.yaml
```

Exit codes: `0` OK, `2` PARTIAL (barren triggers, `UNDECIDED` verdicts, or failed pages), `3` judge untrustworthy, `1` error.
Outputs go to `out/<name>/` (or `--out`). A new site needs only a `targets/<site>.yaml`.

## Page Object output

Example: [`examples/out/saucedemo/pages/inventory.pom.json`](examples/out/saucedemo/pages/inventory.pom.json). Elements are grouped into zones by their nearest HTML landmark (`header`, `nav`, `main`, `footer`, `form`, dialog).

Selector policy, per element:
1. Candidates by strategy: `test_id` (`data-test`/`data-testid`/`data-qa`) → `id` → `name` → `aria_label` → `href` (raw attribute).
2. Drop forbidden ones: position-dependent (`nth-child`, `:first-child`, XPath index), absolute XPath, more than 3 levels deep.
3. Keep only those matching **exactly 1** element in the live page.
4. Pick up to 3, **each from a different strategy**. `selectors[0]` is primary. Dynamic-looking IDs (5+ digit runs, React `:r…:`, UUIDs) are demoted to fallback.
5. Fewer than 3 → kept with `fallback_short: true`. None unique → listed in `unresolved_elements` with the candidates and their match counts.

Consuming a POM (`pom_scout/locate.py`, 17 lines): try the selectors in order and return the first that matches exactly once. Otherwise raise with every count.

```python
from pom_scout.locate import locate
locate(page, pom, "header", "shopping_cart_link").click()
```

| selector quality | saucedemo (7 POMs) | the-internet (51 POMs) |
|---|---:|---:|
| elements with a selector | 69 | 307 |
| unresolved (no unique candidate) | 0 | 67 |
| matching exactly 1 | 100% | 100% |
| primary + ≥2 fallbacks | 21.74% | 0% |
| primary is Tier 1 (`test_id`/`id`) | 69 / 69 | 13 / 307 |
| forbidden patterns | 0 | 0 |

Element recall on saucedemo (4 pages, 19 hand-picked elements, `golden/saucedemo.elements.yaml`): 19/19.
The-internet pages rarely carry ids or test attributes, so most links have only their `href`. The table shows that as-is.

## Limitations

- The golden files are hand-written (criteria are in each file's header). They must never be regenerated from pom-scout output, or recall would compare the tool with itself.
- No form filling, so pages behind a submitted form are not reached. No frame traversal (`/nested_frames` → `EMPTY`).
- Verdicts reproduced correctly on a live site: 7 of 8. `EMPTY` appears live only as the frameset misjudgment above. All 8 are reproduced offline in `tests/test_fixtures.py::test_all_eight_verdicts_with_controls`.
- 92 of the-internet's 162 candidates are file links from `/download`. Navigating to a file starts a download, and that is reported as `UNDECIDED (download)`, not guessed.
- Some pages on the-internet are randomized (`/disappearing_elements`, `/infinite_scroll`). Discovered-URL counts varied between 112 and 115 across runs.
- An `aria-label` can carry state (saucedemo's cart link is labelled `Cart, empty`). The uniqueness check cannot tell a stable label from a stateful one.
- The unsafe-word guard is a vocabulary list. It refuses a harmless page (`/add_remove_elements/`) rather than risk a destructive one.

## Tests

```bash
pytest -q            # rules + local fixture server (no internet; needs Chromium)
pytest -q -m live    # public demo sites (network)
```

CI (`.github/workflows/tests.yml`) runs the offline suite on every push. The live suite runs weekly or on manual dispatch, and it never blocks.

## Extension

- An LLM-driven explorer (the model decides what to click) could plug in behind the same `site_map.json` contract. It is left out on purpose to keep runs deterministic.

## Related

- [ko-tc-playwright](https://github.com/tmkim0512-max/ko-tc-playwright) — turns Korean manual test cases into Playwright (pytest) code; reports conversion rate and real run results separately
- [evidence-gated-e2e-loop](https://github.com/tmkim0512-max/evidence-gated-e2e-loop) — accepts AI-written Playwright tests only on file evidence, then replays them without AI
- [false-green-guard](https://github.com/tmkim0512-max/false-green-guard) — detects diffs that turn tests green by neutralizing them and re-judges fixes on an isolated copy
- [parking-api-qa-lab](https://github.com/tmkim0512-max/parking-api-qa-lab) — a small parking API tested with pytest, a hand-built mock server, k6 thresholds and GitHub Actions

## License

MIT © Taemin Kim
