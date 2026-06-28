# End-to-End Pipeline Test Report

**Date:** 2026-06-28 · **Runner:** manual end-to-end harness · **Python:** 3.13
(scripts target 3.9+, stdlib + optional `jsonschema`) · **Status:** ✅ pass

This records a full run of the `find-competitor` pipeline against sample inputs,
verifying that every phase emits schema-valid JSON, the report renders, and the
`unknown` / confidence fallbacks degrade gracefully. Two bugs were found and
fixed; one prompt/UX gap was tightened.

## Method

The pipeline was driven outside the skill directory (a clean working dir) so the
shipped sample in `insightkit-output/` was never touched. Inputs:

- **Sample repo** — a synthetic SaaS project (`TaskFlow`: README + `package.json`
  + TS source) plus a self-analysis of this repository itself.
- **Candidates** — 4 hand-written discovery candidates (Jira, Linear, Trello,
  and a duplicate Linear) to exercise classification, similarity, and dedup.
- **Findings** — multi-dimensional findings for the roster: one competitor fully
  populated, one partial, one entirely absent (to force all-`unknown` records),
  with some leaves supplied as bare scalars and some as confidence envelopes.
- **Edge case** — empty candidates → empty roster → no findings → report, to
  confirm the "search/collection yielded nothing" path.

## Results by phase

| Phase | Script | Check | Result |
| --- | --- | --- | --- |
| 1 Product Intelligence | `analyze_repo.py` | `--validate` on synthetic repo **and** self | `validation: passed`; unknowns emitted for unestablished fields |
| 2 Discovery (plan) | `discover_competitors.py plan` | 13 query groups + rubric derived from `product.json` | ok |
| 2 Discovery (build) | `discover_competitors.py build` | 4 candidates → 3 competitors (duplicate Linear merged), validates | ok |
| 2 Discovery (empty) | `discover_competitors.py build` | `[]` → empty-but-valid roster, warns on stderr | ok |
| 3 Collection (plan) | `collect_intelligence.py plan` | per-competitor 6-dimension task list + input format | ok |
| 3 Collection (build) | `collect_intelligence.py build` | all 6 datasets validate; absent competitor → all-`unknown` record | ok |
| 4 Report (synthesize) | `build_report.py` | `report.json` validates against `report.schema.json` (0 errors) | ok |
| 4 Report (render) | `build_report.py` | self-contained `report.html`; headless-Chrome DOM contains every competitor, SWOT, positioning, threat, 4 chart canvases, inlined `INSIGHTKIT` | ok |
| — Edge (zero competitors) | full chain | 0 competitors / all-`unknown` → report still builds and renders | ok |

**Fallback behavior verified:** omitted fields, explicit `unknown: true`, and
`null`/`""` values all normalize to the canonical envelope
(`value: null, confidence: 0, unknown: true`); the report shows graceful
placeholders (em-dash KPI, "no public pricing") rather than crashing or
fabricating. Every competitor in the roster gets exactly one record per dataset,
even with no findings.

## Bugs found and fixed

1. **Pricing summary not derived from tiers (report under-counted free plans &
   showed no entry price).** The overview dashboard reads
   `pricing.has_free_plan` and `pricing.lowest_paid_monthly`, but a researcher who
   fills the richer `plans[]` array (with `is_free` / `monthly_price`) and omits
   those redundant top-level summaries left them `unknown` — so free tiers and
   entry prices disappeared ("0/3 offer a free tier", "AVG. ENTRY PRICE —")
   despite the data being present in `plans`.
   **Fix:** `collect_intelligence.py` now derives `has_free_plan` (any plan with
   `is_free` or `$0/mo`) and `lowest_paid_monthly` (min `monthly_price > 0`) from
   `plans[]` when those fields are still `unknown`, carrying a transparent
   `method` note. An explicit finding always wins. After the fix the same inputs
   render "FREE PLANS 2/3" and "AVG. ENTRY PRICE $8.07 (range $8–$8.15/mo)".

## Tightening

2. **Unrecognized finding keys were silently dropped.** `wrap_group` only reads
   keys present in a dimension's spec, so a typo or wrong nesting (e.g.
   `legal_name` instead of `company_name`, an unschema'd `domain_authority`, or
   `marketing.content_themes` instead of `marketing.content.blog_topics`) was
   discarded with no signal — the field just appeared `unknown`, masking the
   mistake. `collect_intelligence.py build` now scans each ref/dimension's
   findings against the spec and prints a stderr warning listing every
   unrecognized dotted path (capped, pointing back at
   `references/intelligence-dimensions.md`). Clean, correctly-named findings
   produce no warnings. This caught real field-name mistakes in the test findings
   during the run.

No doc/spec mismatch was found: `references/intelligence-dimensions.md` and the
schemas agree on field names (`company_name`, etc.) — the warnings above were
test-input errors, which is exactly what the new check is meant to surface.

## Regression notes

- The shipped `insightkit-output/` sample is unaffected: its findings supplied
  the pricing summary fields explicitly (16/17 records), so it never hit bug #1.
- Both fixes are additive and only act when a field is otherwise `unknown` /
  unrecognized; all six dataset schemas and `report.schema.json` still validate.
- `python3 -m py_compile scripts/collect_intelligence.py` passes.
