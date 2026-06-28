# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`find-competitor` is itself a **Claude Code Skill** (`skills/find-competitor/SKILL.md` at the root), not a
conventional app. It turns the repository Claude is invoked in into a competitive
intelligence report. The deliverables are: Python helper scripts (stdlib only), JSON
Schemas, reference docs, and a self-contained HTML report template. There is no
package manager, build system, or test framework — just `python` and a browser.

When the skill *runs*, Claude itself is part of the pipeline: the scripts emit
research *plans*, Claude executes them with `WebSearch`/`WebFetch`, and the scripts
normalize Claude's findings back into schema-valid JSON.

## Commands

Requirements: Python 3.9+ (stdlib only; `jsonschema`/`referencing` optional — scripts
fall back to built-in structural checks when absent).

The pipeline runs in five stages. The two middle stages are two-phase (`plan` →
Claude does web research → `build`):

```bash
# 1. Product Intelligence — repo → product.json
python skills/find-competitor/scripts/analyze_repo.py --repo . --validate

# 2. Competitor Discovery (two-phase)
python skills/find-competitor/scripts/discover_competitors.py plan  --product product.json        # → research plan (stdout)
#   ...Claude runs the plan with WebSearch/WebFetch → candidates.json...
python skills/find-competitor/scripts/discover_competitors.py build --product product.json \
  --candidates candidates.json --validate                                  # → competitors.json

# 3. Intelligence Collection (two-phase)
python skills/find-competitor/scripts/collect_intelligence.py plan  --competitors competitors.json
#   ...Claude runs the plan → findings.json...
python skills/find-competitor/scripts/collect_intelligence.py build --competitors competitors.json \
  --findings findings.json --validate                                      # → companies/pricing/techstack/social/marketing/seo.json

# 4 + 5. Knowledge Graph + Report — all datasets → report.json + report.html
python skills/find-competitor/scripts/build_report.py --input-dir . --output-dir ./insightkit-output --open
```

There is no test suite. **`--validate`** on each builder is the closest thing to a
test — it checks output against `skills/find-competitor/schemas/`. Aim for 0 schema errors. Regenerate the
checked-in sample with the `build_report.py` command above (output dir
`./insightkit-output`); the sample must stay in sync with the template and schemas.

## Architecture

The **normalized JSON datasets are the contract between every stage** — collection,
analysis, and presentation are decoupled and never talk directly. The authoritative
spec is `skills/find-competitor/references/data-schema.md`; the schemas are in `skills/find-competitor/schemas/` (JSON Schema Draft
2020-12, all `$ref`-ing `common.schema.json` by bare filename).

Two invariants govern all data and override convenience:

1. **Confidence-wrapped fields — no bare scalars.** Every collected value is an
   object: `{value, confidence, unknown, source?, provenance?, notes?}`. The hard
   rule: `unknown: true` ⇒ `value: null` **and** `confidence: 0`. A confident
   negative (`{value: false, confidence: 0.9, unknown: false}`) is distinct from
   "couldn't determine" (`unknown: true`). Fields are always *present* (emit the
   `unknown` form, never omit the key). The leaf builders live in each script
   (`field()`, `unknown()`, `wrap_leaf()`/`wrap_group()` in `collect_intelligence.py`,
   `wrap()`/`cell()` in `build_report.py`).

2. **`entity_ref` is the universal join key** — slug `^(self|[a-z0-9][a-z0-9-]{0,63})$`.
   `competitors.json` *defines* the `id` set; every other per-competitor dataset
   references one via `entity_ref`. The literal `"self"` is the analyzed product.
   Don't introduce a parallel join key.

Datasets are deliberately split one-file-per-domain so a single failed collector
degrades one file, not the graph; a missing record == all fields `unknown`.

The four scripts map 1:1 to pipeline stages (see `CONTRIBUTING.md` table):

| Script | Stage | Output | Reference doc |
| --- | --- | --- | --- |
| `analyze_repo.py` | Product Intelligence | `product.json` | `skills/find-competitor/references/product-intelligence.md` |
| `discover_competitors.py` | Discovery (`plan`/`build`) | `competitors.json` | `skills/find-competitor/references/competitor-discovery.md` |
| `collect_intelligence.py` | Collection (`plan`/`build`) | 6 per-competitor datasets | `skills/find-competitor/references/intelligence-dimensions.md` |
| `build_report.py` | Graph + Visualization | `report.json`, `report.html` | `skills/find-competitor/references/data-schema.md` |

`build_report.py` is the synthesis layer: it loads all datasets, builds entity
records, scores capabilities (`score_scale/pricing/marketing/social/seo/tech`),
derives `threat_level`, SWOT, positioning, gaps, and recommendations, then inlines
the result into `skills/find-competitor/templates/report.html` at the `__INSIGHTKIT_DATA__` placeholder to
produce a standalone file. **No scraping at render time** — the report only consumes
the normalized datasets. Charts use Chart.js and D3 from CDN; keep external
dependencies to those two only.

## Conventions when changing things

- **Schema, reference doc, and code change together.** If you add/change a field,
  update the relevant `skills/find-competitor/schemas/*.schema.json`, bump `schema_version` (minor =
  additive, major = breaking), document it in `skills/find-competitor/references/data-schema.md`, and run the
  matching `--validate`.
- **Heuristic judgments must show their work.** Any derived value (threat level,
  SWOT, positioning, recommendations) carries a `method` note explaining how it was
  derived. New heuristics follow the same rule.
- Prefer `unknown: true` over guessing when a value can't be determined.
- Scripts must stay on the Python standard library (treat `jsonschema` as optional).
- The repo-root `*.json` files (`product.json`, `competitors.json`, `findings.json`,
  etc.) are committed sample data — the worked example that produces `insightkit-output/`.
