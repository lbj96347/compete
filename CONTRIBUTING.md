# Contributing to compete

Thanks for your interest in improving `compete`. This is a Claude Code
Skill: a set of Python helper scripts, JSON schemas, reference docs, and a
report template, glued together by [`skills/compete/SKILL.md`](skills/compete/SKILL.md). Contributions of all
kinds are welcome — bug fixes, new intelligence dimensions, better heuristics,
schema refinements, and docs.

## Ground rules

- **The data contract is authoritative.** Every stage reads and writes JSON that
  validates against a schema in [`skills/compete/schemas/`](skills/compete/schemas/). If you change a field,
  change the schema and the reference doc in the same PR. The contract is
  documented in [`skills/compete/references/data-schema.md`](skills/compete/references/data-schema.md).
- **Confidence, not assertion.** Every value is wrapped with a `confidence`
  score, a `source`, `provenance`, and an explicit `unknown` flag. The
  invariant: `unknown: true` ⇒ `value: null`. Never emit a bare value. When you
  cannot determine something, say so honestly with `unknown: true` rather than
  guessing.
- **Show your work.** Heuristic judgments (threat level, SWOT, positioning,
  recommendations) must carry a `method` note that explains *how* the value was
  derived, so a reader can audit it. New heuristics follow the same rule.
- **Datasets join by `entity_ref`.** `"self"` is the analyzed product; each
  competitor has a stable `entity_ref`. Don't introduce a parallel join key.
- **No scraping in the visualization layer.** The report consumes the normalized
  datasets only. Collection happens in the collection stage, not at render time.

## Development setup

```bash
git clone https://github.com/forthrighttech/compete.git
cd compete
```

Requirements:

- **Python 3.9+** — the scripts use only the standard library (no
  `pip install` needed). `jsonschema` is optional; if absent, the scripts fall
  back to their built-in structural checks.
- A browser, to eyeball the rendered report.

To exercise the skill end to end inside Claude Code, symlink or clone the repo
into `~/.claude/skills/compete/` (see the README's Installation section).

## Pipeline & where things live

| Stage | Script | Output | Reference |
| --- | --- | --- | --- |
| 1. Product Intelligence | `skills/compete/scripts/analyze_repo.py` | `product.json` | `skills/compete/references/product-intelligence.md` |
| 2. Competitor Discovery | `skills/compete/scripts/discover_competitors.py` | `competitors.json` | `skills/compete/references/competitor-discovery.md` |
| 3. Intelligence Collection | `skills/compete/scripts/collect_intelligence.py` | `companies/pricing/techstack/social/marketing/seo.json` | `skills/compete/references/intelligence-dimensions.md` |
| 4–5. Graph + Report | `skills/compete/scripts/build_report.py` | `report.json`, `report.html` | `skills/compete/references/data-schema.md` |

The report UI lives in [`skills/compete/templates/report.html`](skills/compete/templates/report.html) with an
`__INSIGHTKIT_DATA__` placeholder that `build_report.py` inlines. Charts use
Chart.js and D3 from CDN — keep external dependencies to those two.

## Validating your changes

Each builder script accepts `--validate` to check its output against the schema:

```bash
python skills/compete/scripts/analyze_repo.py --repo . --validate
python skills/compete/scripts/discover_competitors.py build --product product.json \
  --candidates candidates.json --validate
python skills/compete/scripts/collect_intelligence.py build --competitors competitors.json \
  --findings findings.json --validate
```

Aim for **0 schema errors** before opening a PR.

## Regenerating the sample report

The checked-in sample in [`insightkit-output/`](insightkit-output/) should stay
in sync with the template and schemas. To regenerate it from the datasets at the
repo root:

```bash
python skills/compete/scripts/build_report.py --input-dir . --output-dir ./insightkit-output
```

Then verify it renders. To refresh the screenshot used in the README (macOS):

```bash
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --headless --disable-gpu --hide-scrollbars --window-size=1440,2200 \
  --virtual-time-budget=4000 \
  --screenshot=insightkit-output/screenshots/overview.png \
  "file://$(pwd)/insightkit-output/report.html"
```

Confirm the report opens standalone (no local server) and that the only external
requests are the two CDN bundles.

## Submitting a pull request

1. Branch off `main`.
2. Keep changes focused; update the relevant schema and reference doc alongside
   any code change.
3. Run the matching `--validate` and regenerate the sample if you touched the
   template, schemas, or `build_report.py`.
4. Describe **what** changed and **why** in the PR — and, for new heuristics,
   the `method` reasoning behind them.

## Roadmap & larger contributions

Bigger features are tracked in the README's **Roadmap** and in
[`PRD.md`](PRD.md): deeper per-dimension intelligence (deep SEO, deep social,
hiring, sales) and the `/watch-competitors` continuous-monitoring mode. If you
want to take one on, please open an issue first so we can align on the data
contract before you build.

## License

By contributing, you agree that your contributions are licensed under the
project's [MIT License](LICENSE).
