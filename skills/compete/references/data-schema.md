# InsightKit Data Contract

**Status: authoritative.** Every later phase — intelligence collection, knowledge
graph normalization, visualizations, and the report — reads and writes the
datasets defined here. Do not introduce ad-hoc fields; extend the schemas in
`schemas/` and update this document in the same change.

The contract lives as JSON Schema (Draft 2020-12) files:

| Dataset | Schema file | Cardinality | Join key |
| --- | --- | --- | --- |
| `product.json` | `schemas/product.schema.json` | one (own product) | `entity_ref = "self"` |
| `competitors.json` | `schemas/competitors.schema.json` | many | defines `id` |
| `companies.json` | `schemas/companies.schema.json` | many | `entity_ref` |
| `social.json` | `schemas/social.schema.json` | many | `entity_ref` |
| `marketing.json` | `schemas/marketing.schema.json` | many | `entity_ref` |
| `techstack.json` | `schemas/techstack.schema.json` | many | `entity_ref` |
| `seo.json` | `schemas/seo.schema.json` | many | `entity_ref` |
| `pricing.json` | `schemas/pricing.schema.json` | many | `entity_ref` |
| `report.json` | `schemas/report.schema.json` | one (synthesis) | `entity_ref` |

Shared building blocks live in `schemas/common.schema.json` and are referenced by
every dataset via relative `$ref` (e.g. `common.schema.json#/$defs/stringField`).

---

## 1. The confidence-wrapped field (the core rule)

**No raw scalar is ever stored bare.** Every collected value is wrapped in an
object that always carries three required keys:

```json
{
  "value": "Vercel",
  "confidence": 0.82,
  "unknown": false,
  "source": "https://acme.com (x-powered-by header)",
  "provenance": {
    "source": "https://acme.com",
    "source_type": "inference",
    "as_of": "2026-06-28",
    "method": "response header sniff"
  },
  "notes": null
}
```

| Key | Required | Meaning |
| --- | --- | --- |
| `value` | yes | The data, typed per field. **`null` whenever `unknown` is true.** |
| `confidence` | yes | `number` in `[0, 1]`. 0 = guess/none, 1 = stated by an authoritative first-party source. |
| `unknown` | yes | `boolean`. `true` ⇒ value could not be determined. |
| `source` | no | Convenience pointer (URL/path) to the primary source. |
| `provenance` | no | Richer evidence trail: `source`, `source_type`, `as_of`, `method`. |
| `notes` | no | Free-form analyst annotation. |

### The `unknown` fallback — invariants

1. `unknown: true` ⇒ `value` is `null` **and** `confidence` is `0`.
2. A confident negative is **not** unknown. "This product has no free plan" is
   `{ "value": false, "confidence": 0.9, "unknown": false }`, distinct from
   "we couldn't tell" `{ "value": null, "confidence": 0, "unknown": true }`.
3. For list fields the same distinction holds: `value: []` (confident "none")
   ≠ `value: null, unknown: true` ("not determined").
4. Every field is therefore always *present* in output — collectors emit the
   `unknown` form rather than omitting the key — so consumers never branch on
   key existence, only on the `unknown` flag.

### How consumers must treat it

- **Knowledge graph / normalization:** carry `confidence` and `provenance`
  through unchanged; never fabricate a value to replace `unknown`.
- **Visualizations:** render `unknown` fields as an explicit "—"/"Unknown" state,
  never as `0`, blank, or `false`. Low-confidence values (< ~0.5) should be
  visually de-emphasized or badged.
- **Report:** analytic judgments are themselves confidence-wrapped, so the report
  can surface its own uncertainty.

### Field types (from `common.schema.json`)

`stringField`, `textField` (long-form), `urlField` (`format: uri`),
`numberField`, `integerField`, `booleanField` (tri-state: `true`/`false`/`null`),
`dateField` (`format: date`), `enumField` (categorical; allowed set is
constrained at each use site), `stringArrayField` (list of strings), and
`metricField` (numeric with `is_estimate`, `range_low`, `range_high`, `unit` for
soft figures like ARR, headcount, follower counts).

`enumField` carries the literal `"unknown"` as a permitted value in addition to
the `unknown: true` flag, so categorical gaps can be expressed either way; prefer
the flag for consistency.

---

## 2. Envelope & joins

Every dataset is a JSON object with a `meta` block conforming to
`common.schema.json#/$defs/datasetMeta`:

```json
{
  "schema_version": "1.0.0",
  "dataset": "techstack",
  "generated_at": "2026-06-28T12:00:00Z",
  "generator": "compete/0.1.0"
}
```

- `schema_version` is the SemVer of this contract (currently **1.0.0**). Bump
  minor for additive fields, major for breaking changes.
- `dataset` equals the file stem and is enumerated.

**`entity_ref` is the universal join key.** It is a slug matching
`^(self|[a-z0-9][a-z0-9-]{0,63})$`. `competitors.json` *defines* the set of `id`
values; every other per-competitor dataset references one via `entity_ref`. The
reserved literal `"self"` denotes the analyzed product, so per-competitor
datasets may also profile the own product with the same machinery.

Datasets are intentionally split by concern (one file per collection domain) so a
single slow or failed collector degrades one file, not the whole graph. A missing
per-competitor record is equivalent to all of its fields being `unknown`.

---

## 3. Dataset reference

### product.json — own product (`entity_ref: "self"`)
Extracted from the local repository.
- **identity** — `name`, `tagline`, `description`, `category`, `stage`,
  `deployment_model`, `business_model`, `pricing_model`.
- **features** — `core_features`, `ai_capabilities`, `platforms`,
  `integrations`, `apis`, `target_workflow`.
- **customers** — `target_users`, `industries`, `company_size`, `personas`,
  `use_cases`.

### competitors.json — discovered roster
Array of `competitors[]`, each: `id` (defines the join key), `name`, `aliases`,
`website`, `classification` (enum: `direct` | `indirect` | `enterprise` |
`open_source` | `emerging_startup` | `adjacent` | `unknown`), `similarity_score`
(0..1), `relationship_notes`, `discovery_source`.

### companies.json — company profiles
Array of `companies[]` keyed by `entity_ref`: `company_name`, `website`,
`headquarters`, `founded_year`, `founders`, `employee_estimate` (metric),
`funding_stage` (enum), `total_funding` (metric, `unit` = currency), `investors`,
`estimated_arr` (metric), `commercial_model` (enum).

### social.json — online presence
Array of `presence[]` keyed by `entity_ref`:
- **website** — `homepage`, `pricing`, `documentation`, `blog`, `changelog`,
  `careers`, `api_docs`.
- **social** — one `socialAccount` per platform (`x`, `linkedin`, `github`,
  `youtube`, `discord`, `slack`, `reddit`, `facebook`, `instagram`, `tiktok`,
  `product_hunt`); each has `url`, `handle`, `followers` (metric),
  `posting_frequency`, `content_categories`, `engagement_style`.
- **developer_channels** — `github_repository`, `github_stars`/`forks`/
  `issues`/`releases`, `npm_package`, `pypi_package`, `docker_hub`,
  `vscode_marketplace`.

### marketing.json — marketing, content, sales motion, hiring
Array of `marketing[]` keyed by `entity_ref`:
- **positioning** — `positioning_statement`, `hero_headline`,
  `value_proposition`, `primary_cta`, `pricing_strategy`, `launch_style`.
- **programs** — `newsletter`, `webinar`, `referral_program`,
  `affiliate_program`, `community`, `case_studies`, `customer_stories`,
  `email_capture_strategy`.
- **content** — `blog_topics`, `uses_ai_generated_content`,
  `documentation_quality`, `tutorials`, `videos`, `podcasts`, `release_cadence`.
- **sales_motion** — `free_trial`, `freemium`, `self_service`,
  `enterprise_contact`, `annual_discount`, `demo_booking`, `has_sales_team`,
  `partner_program`, `marketplace_presence`.
- **hiring** — `careers_page`, `open_positions`, `engineering_hiring`,
  `ai_hiring`, `remote_policy`, `tech_hints_from_jobs`.

### techstack.json — technology intelligence
Array of `techstack[]` keyed by `entity_ref`. Top-level string-array fields:
`frontend`, `backend`, `framework`, `database`, `hosting`, `cloud_provider`,
`cdn`, `deployment`, `authentication`, `analytics`, `payments`, `monitoring`,
`cms`, `llm_provider`, `inference_provider`. Plus **product_surface**:
`ai_models`, `api_availability`, `sdks`, `mobile_apps`, `browser_extension`,
`integrations`. Values here are usually inferred — lean on `confidence` and
`provenance.method`.

### seo.json — SEO intelligence
Array of `seo[]` keyed by `entity_ref`: `meta_title`, `meta_description`,
`has_sitemap`, `sitemap_url`, `has_robots_txt`, `indexed_pages` (metric),
`keyword_focus`, `blog_frequency` (enum), `landing_pages`,
`documentation_quality` (enum), `internal_linking` (enum).

### pricing.json — pricing intelligence
Array of `pricing[]` keyed by `entity_ref`: `pricing_page`, `pricing_model`
(enum), `currency`, `has_free_plan`, `has_enterprise_plan`,
`lowest_paid_monthly` (metric, denormalized for the matrix), and `plans[]`. Each
plan: `name`, `monthly_price` (metric — use `is_estimate`/`range_*` for
"contact us" tiers), `billing_period` (enum), `is_free`, `is_enterprise`,
`key_features`, `limits`.

### report.json — synthesized analysis
Consumes every other dataset.
- **executive_summary** — `summary`, `market_overview`, `key_findings`,
  `competitor_count`.
- **competitor_analysis[]** — per `entity_ref`: `swot`
  (`strengths`/`weaknesses`/`opportunities`/`threats`), `threat_level` (enum),
  `differentiators`.
- **positioning_matrix** — `x_axis_label`, `y_axis_label`, `points[]`
  (`entity_ref`, `x`, `y`).
- **opportunity_gaps[]** — `title`, `description`, `impact` (enum),
  `related_entities[]`.
- **recommendations[]** — `title`, `rationale`, `priority` (enum), `confidence`.

---

## 4. Worked example

A high-confidence string, a metric estimate, and an unknown, all on one
`companies.json` record:

```json
{
  "entity_ref": "acme",
  "company_name": { "value": "Acme Inc.", "confidence": 0.95, "unknown": false,
                    "source": "https://acme.com/about" },
  "employee_estimate": { "value": 120, "confidence": 0.4, "unknown": false,
                         "is_estimate": true, "range_low": 80, "range_high": 160,
                         "unit": "people", "source": "linkedin" },
  "estimated_arr": { "value": null, "confidence": 0, "unknown": true }
}
```

A renderer shows "Acme Inc.", an "~120 (80–160)" badge dimmed for low confidence,
and "ARR: Unknown" — never "$0".

---

## 5. Validation

Schemas are Draft 2020-12 and cross-reference `common.schema.json` by relative
filename. To validate datasets locally:

```bash
pip install jsonschema referencing
# load all schemas/*.json into a referencing Registry keyed by both $id and
# bare filename, then Draft202012Validator(<dataset-schema>, registry).validate(instance)
```

When you add a field: update the relevant `schemas/*.schema.json`, bump
`schema_version`, document it here, and confirm the schema still passes
`Draft202012Validator.check_schema`.
