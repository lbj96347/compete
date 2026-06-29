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
| `features.json` | `schemas/features.schema.json` | many | `entity_ref` |
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

- `schema_version` is the SemVer of this contract (latest **1.3.0** —
  `report.json`'s `feature_analysis`). Bump minor for additive fields, major for
  breaking changes.
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
`lowest_paid_monthly` (metric, denormalized for the matrix), `estimated_mrr`
and `estimated_users` (metrics — soft figures, set `is_estimate: true` with a
`range_low`/`range_high` band, an optional `unit`, and low `confidence`; prefer
`unknown: true` over guessing), and `plans[]`. Each plan: `name`, `monthly_price`
(metric — use `is_estimate`/`range_*` for "contact us" tiers), `billing_period`
(enum), `is_free`, `is_enterprise`, `key_features`, `limits`. *(v1.1.0:
`estimated_mrr`/`estimated_users` added.)*

### features.json — feature & service matrix *(v1.2.0)*
Array of `features[]` keyed by `entity_ref`. Each record carries a single
`matrix[]` — **one matrix that holds both product features and human/professional
services**, distinguished by the `category` tag (enum: `feature` | `service`).
Every cell is drawn from a **configurable taxonomy** of capability keys
(`capability_taxonomy.json`; override with `--taxonomy` / `$COMPETE_TAXONOMY`), so
all competitors are scored against the same axes and the report can render a true
side-by-side grid. The schema validates a key's *shape* (snake_case); the taxonomy
file owns the closed set, so you retarget the matrix to a new market by editing
data, not code.

Each cell is a confidence-wrapped field (the standard `value` / `confidence` /
`unknown` / `source?` / `provenance?` / `notes?` envelope) extended with three
discriminators:
- **`key`** — one taxonomy key (see below). A feature key MUST carry
  `category: "feature"`; a service key MUST carry `category: "service"` (enforced
  by the schema's `oneOf`).
- **`category`** — `feature` | `service`.
- **`status`** — support level enum: **`has`** (fully supported) | **`partial`**
  (limited / beta / paid add-on) | **`none`** (confidently absent) | `null` (only
  when `unknown: true`).
- **`value`** — the tri-state boolean "supported at all": `true` ⇔ `status` is
  `has` or `partial`; `false` ⇔ `status` is `none`; `null` ⇔ `unknown: true`.

The unknown invariant is enforced **in-schema** here (via `if`/`then`):
`unknown: true` ⇒ `value: null` **and** `confidence: 0` **and** `status: null`.
Fields are always present in the `unknown` form; a key omitted from `matrix` is
equivalent to an all-`unknown` cell (a missing record == all fields unknown).

**Canonical taxonomy** (fixed; defined in `schemas/features.schema.json` as
`featureKey` / `serviceKey`):

- **Features (`category: feature`)** — `competitor_tracking`, `battlecards`,
  `win_loss_analysis`, `market_trend_analysis`, `news_and_alerts`,
  `pricing_intelligence`, `seo_keyword_tracking`, `social_listening`,
  `website_change_monitoring`, `ai_insights_summarization`,
  `dashboards_and_reporting`, `data_export`, `public_api`,
  `third_party_integrations`, `browser_extension`, `mobile_app`.
- **Services (`category: service`)** — `managed_research`, `analyst_support`,
  `onboarding_and_training`, `custom_report_services`, `consulting_advisory`,
  `dedicated_account_manager`, `premium_sla_support`, `data_enrichment_service`.

Extending the taxonomy is an additive change: add the key to the relevant enum in
`schemas/features.schema.json`, document it here, and bump `schema_version`
(minor).

### report.json — synthesized analysis *(v1.3.0)*
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
- **feature_analysis** *(v1.3.0)* — the feature/service union matrix derived from
  `features.json` over the active capability taxonomy (axes come from the data, no
  render-time scraping):
  - **`axes[]`** — the taxonomy rows present in the data: `key`, `category` (`feature`|`service`),
    `label`.
  - **`columns[]`** — entity columns (self first): `entity_ref`, `name`,
    `is_self`, `threat_level`.
  - **`matrix[]`** — one row per taxonomy item: `key`, `category`, `label`,
    `cells[]` (per-entity `{entity_ref, status, value, confidence, unknown}` — the
    same tri-state as `features.json`), and a **`difference`** block computed vs.
    `self`: `self_status`, `is_gap` (self ∈ {none,partial} ∧ ≥1 rival `has`),
    `self_leads` (self `has` ∧ ≤50% of rivals offer it), `alternatives[]` (rivals
    offering it, with `status` + `threat_level`), `competitors_without[]`, a
    threat-weighted `score`, and a `method` note.
  - **`alternatives[]`** — per competitor: the capabilities they offer
    (`has`|`partial`) and a `capability_count`, ranked — what a buyer evaluating
    that alternative would get.
  - **`opportunities[]`** — threat-weighted gap records (highest `score` first):
    `key`, `category`, `title`, `description`, `impact` (enum, tiered **relative**
    to the strongest gap this run), `score`, `self_status`, `related_entities[]`,
    and a `method` note. **Weighting:** `score = Σ threat_weight(rival) ×
    status_weight` over rivals offering the capability, where `threat_weight` is
    high=3 / medium=2 / low=1 (unknown→low) and `status_weight` is has=1.0 /
    partial=0.5 — so a gap held by high-threat rivals outranks the same gap from
    low-threat ones.

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
