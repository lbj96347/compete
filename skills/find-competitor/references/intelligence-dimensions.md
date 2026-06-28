# Intelligence Dimensions

**Step 3 of the find-competitor pipeline.** Take the classified roster
(`competitors.json`, whose `id`s are the join keys — produced by
[Competitor Discovery](competitor-discovery.md)) and, for **every** competitor,
collect a multi-dimensional intelligence profile across six datasets:

| Dataset | File | Schema | What it captures |
| --- | --- | --- | --- |
| **Company** | `companies.json` | `companies.schema.json` | HQ, founding, headcount, funding, ARR, commercial model |
| **Pricing** | `pricing.json` | `pricing.schema.json` | model, currency, free/enterprise flags, per-tier `plans[]` |
| **Tech stack** | `techstack.json` | `techstack.schema.json` | inferred frontend/backend/cloud/LLM stack + product surface |
| **Social / presence** | `social.json` | `social.schema.json` | website sections, social accounts, developer channels |
| **Marketing** | `marketing.json` | `marketing.schema.json` | positioning, programs, content, sales motion, hiring |
| **SEO** (v1-light) | `seo.json` | `seo.schema.json` | meta tags, sitemap/robots, indexed-page scale, keyword focus |

Each dimension is its **own file** so a single slow or failed collector degrades
one dataset, not the whole graph (`references/data-schema.md` §2). Every record is
keyed by `entity_ref` (a competitor `id`, or the reserved `self`), and **every
field is confidence-wrapped** — unverifiable data is the explicit `unknown` form,
never a guess.

Like Step 2, the work splits into a web-research half (the agent) and a
deterministic, contract-bound half (`scripts/collect_intelligence.py`):

| Half | Owner | What it does |
| --- | --- | --- |
| **Research** | the agent, with `WebSearch` / `WebFetch` | gather facts per competitor across the six dimensions |
| **Normalization** | `scripts/collect_intelligence.py` | confidence-wrap, apply the `unknown` invariant, one record per competitor, validate against `schemas/` |

```bash
# 1. derive the per-competitor research plan from the roster (no network)
python scripts/collect_intelligence.py plan --competitors competitors.json

# 2. ...run the plan with WebSearch/WebFetch, write findings to findings.json...

# 3. normalize → the six datasets (validated)
python scripts/collect_intelligence.py build \
    --competitors competitors.json --findings findings.json --validate
```

---

## 1. Derive the research plan

`plan` reads `competitors.json` and emits, **per competitor**, a dimension-keyed
list of concrete `WebSearch` queries and `WebFetch` hints — tuned to that
competitor's name and website (e.g. a GitHub-hosted project gets repo-metric and
manifest tasks instead of a `/pricing` fetch). It also embeds the **input format**
and the build command. Pure function of the roster; no network.

```jsonc
"per_competitor": [
  { "entity_ref": "competely", "name": "Competely", "website": "https://competely.ai",
    "dimensions": {
      "company":  [ {"via":"WebSearch","q":"Competely funding round investors crunchbase"}, … ],
      "pricing":  [ {"via":"WebFetch","url":"https://competely.ai/pricing","why":"published tiers…"}, … ],
      "techstack":[ {"via":"WebFetch","url":"https://competely.ai","why":"sniff homepage markup/headers…"}, … ],
      …
    } }
]
```

## 2. Run the research

Execute each competitor's dimension tasks with `WebSearch` / `WebFetch`. **Prefer
first-party evidence** (the official site, `/pricing`, docs, the GitHub repo) over
third-party databases; reserve search results / inference for what first-party
sources don't state. Prompt patterns that work well:

> **WebFetch** `https://<site>/pricing` — *"List every published plan. For each:
> name, monthly price + currency, billing period, whether it's free or
> enterprise/'contact us', and the headline features and limits."*

> **WebFetch** the homepage — *"Extract the hero headline, the primary CTA, the
> one-line value proposition, and any signals of launch style (e.g. Product Hunt,
> waitlist). Note social and developer links in the header/footer."*

> **WebSearch** `"<name> funding crunchbase"`, `"<name> employees linkedin"`,
> `"<name> founded headquarters"` — company facts that aren't on the site.

> **WebFetch** `https://<site>/sitemap.xml` and `/robots.txt` — SEO presence and
> the rough scale of indexed pages.

When a site is unreachable (e.g. a Cloudflare bot challenge), fall back to
reputable third-party profiles (Product Hunt, G2, Tracxn, press) and **lower the
confidence** accordingly — record the third-party `source` honestly rather than
claiming a first-party read.

## 3. The six dimensions

Every leaf below is a confidence-wrapped field. The agent supplies the value,
a confidence, and provenance; the script wraps it into the contract envelope.

- **Company** (`companies.json`) — `company_name`, `website`, `headquarters`,
  `founded_year`, `founders[]`, `employee_estimate` (metric, `unit:"people"`),
  `funding_stage`, `total_funding` (metric, `unit:"USD"`), `investors[]`,
  `estimated_arr` (metric), `commercial_model`. Funding/headcount/ARR are almost
  always **estimates** — set `is_estimate:true` and a `range_low`/`range_high`.
- **Pricing** (`pricing.json`) — `pricing_page`, `pricing_model`, `currency`,
  `has_free_plan`, `has_enterprise_plan`, `lowest_paid_monthly` (metric,
  denormalized for the matrix), and `plans[]` — each `{name (required),
  monthly_price, billing_period, is_free, is_enterprise, key_features[],
  limits[]}`. A "contact us" tier is an honest metric with `is_estimate`/`range`,
  not a fabricated number.
- **Tech stack** (`techstack.json`) — list fields `frontend`, `backend`,
  `framework`, `database`, `hosting`, `cloud_provider`, `cdn`, `deployment`,
  `authentication`, `analytics`, `payments`, `monitoring`, `cms`, `llm_provider`,
  `inference_provider`, plus a `product_surface` group (`ai_models`,
  `api_availability`, `sdks`, `mobile_apps`, `browser_extension`,
  `integrations`). Most values are **inferred** from markup/headers/manifests —
  use `source_type:"inference"` and modest confidence.
- **Social / presence** (`social.json`) — a `website` group (`homepage`,
  `pricing`, `documentation`, `blog`, `changelog`, `careers`, `api_docs`); a
  `social` map keyed by platform (`x`, `linkedin`, `github`, `youtube`, `discord`,
  `slack`, `reddit`, `facebook`, `instagram`, `tiktok`, `product_hunt`), each an
  account `{url, handle, followers (metric), posting_frequency,
  content_categories[], engagement_style}`; and `developer_channels`
  (`github_repository`, `github_stars`/`forks`/`issues`/`releases`,
  `npm_package`, `pypi_package`, `docker_hub`, `vscode_marketplace`). Only
  platforms the competitor **actually has** are emitted — see §5.
- **Marketing** (`marketing.json`) — `positioning` (`positioning_statement`,
  `hero_headline`, `value_proposition`, `primary_cta`, `pricing_strategy`,
  `launch_style`); `programs` (`newsletter`, `webinar`, `referral_program`,
  `affiliate_program`, `community`, `case_studies`, `customer_stories`,
  `email_capture_strategy`); `content` (`blog_topics[]`,
  `uses_ai_generated_content`, `documentation_quality`, `tutorials`, `videos`,
  `podcasts`, `release_cadence`); `sales_motion` (`free_trial`, `freemium`,
  `self_service`, `enterprise_contact`, `annual_discount`, `demo_booking`,
  `has_sales_team`, `partner_program`, `marketplace_presence`); and `hiring`
  (`careers_page`, `open_positions`, `engineering_hiring`, `ai_hiring`,
  `remote_policy`, `tech_hints_from_jobs[]`).
- **SEO** (`seo.json`, v1-light) — `meta_title`, `meta_description`,
  `has_sitemap`, `sitemap_url`, `has_robots_txt`, `indexed_pages` (metric,
  estimated), `keyword_focus[]`, `blog_frequency`, `landing_pages`,
  `documentation_quality`, `internal_linking`.

**Confident negatives matter.** "This product has no free plan" is
`{value:false, confidence:0.8, unknown:false}` — *distinct* from "couldn't tell"
(`unknown:true`). Verify and record absences; don't conflate them with gaps.

## 4. Findings input format

Record findings in one `findings.json` object keyed by `entity_ref`. Each
competitor maps to up to six dimension blocks; within a block, each leaf is either
a bare scalar or a rich object — you supply values + confidences, the script wraps
them into the contract envelope.

```json
{
  "crayon": {
    "company": {
      "company_name": { "value": "Crayon", "confidence": 0.9,
                        "source": "https://www.crayon.co", "source_type": "official_website" },
      "employee_estimate": { "value": 180, "confidence": 0.4, "is_estimate": true,
                             "range_low": 150, "range_high": 250, "unit": "people",
                             "source": "linkedin", "source_type": "third_party_db" }
    },
    "pricing": {
      "pricing_model": { "value": "contact_sales", "confidence": 0.7 },
      "has_free_plan": { "value": false, "confidence": 0.7 },
      "plans": [ { "name": "Enterprise", "is_enterprise": true,
                   "billing_period": { "value": "custom", "confidence": 0.6 } } ]
    },
    "social": {
      "social": { "linkedin": { "url": {"value":"https://linkedin.com/company/…","confidence":0.7},
                                "followers": {"value":30000,"confidence":0.4,"is_estimate":true} } }
    }
  }
}
```

| Key on a leaf | Notes |
| --- | --- |
| `value` | The datum. `null`, `""`, omitting the key, or `unknown:true` all → the unknown envelope. |
| `confidence` | `[0,1]`. ≈0.85–0.95 first-party stated, 0.5–0.7 reputable third-party, 0.3–0.45 inferred/estimated. Defaults to 0.6. |
| `source` / `source_type` | Primary evidence URL and its kind (`official_website`, `pricing_page`, `repository`, `third_party_db`, `inference`, …). |
| `method` / `notes` | Short evidence note / free-form annotation. |
| `is_estimate`, `range_low`, `range_high`, `unit` | Metric fields only (headcount, funding, ARR, followers, prices, indexed pages). |

Accepted as a bare object or `{"findings": {...}}`; pipe via `--findings -` to read
stdin. The script is **forgiving of shape drift** — a bare scalar in an array field
is lifted to a one-element list, and a container a collector accidentally wrapped
in a field envelope (`"social": {"value": {…}}`) is transparently unwrapped.

## 5. Normalize → the six datasets

`build` performs the deterministic, contract-bound half:

- **One record per competitor.** Every `id` in `competitors.json` gets a record in
  every dataset, in the roster's (similarity-ranked) order. A competitor with **no
  findings** (e.g. `nexusai`, which has no confirmable site) emits a complete
  **all-`unknown`** record — a missing profile degrades to "all fields unknown,"
  never a broken join. `self` is included only if `findings` contains it.
- **Confidence wrapping + the `unknown` invariant.** Every leaf becomes
  `{value, confidence, unknown, source, provenance, notes}`; a determined value
  carries a `provenance` block, while the unknown form is exactly
  `{value:null, confidence:0, unknown:true}` with **no** provenance.
- **Dense vs. sparse groups.** Fixed groups (`website`, `developer_channels`,
  `product_surface`, `positioning`, …) emit *all* their leaves (unknown where
  absent) so consumers never branch on key existence. The `social` platform map is
  **sparse** — only platforms with at least one finding are written, so a record
  isn't padded with eleven empty accounts.
- **Per-dataset validation.** `--validate` checks each output against its schema
  (each `$ref`s `common.schema.json`) before writing — needs `jsonschema` +
  `referencing`. Any failure aborts the whole build.
- **Graceful empty.** With no `--findings`, the build still writes six **valid**
  all-`unknown` skeletons keyed to the roster — the "collection yielded nothing"
  fallback, contractually distinct from a malformed file.

### Worked run (this repo's roster)

Running the plan against the 17-competitor roster, researching the 16 with a
confirmable site (16 parallel collectors, one per competitor), and feeding
`findings.json` back through `build` produced six schema-valid datasets. Coverage
(known / total leaf fields), highest where data is public, lowest where it's
inferred or private:

```
companies   17 records   141/187  (75%)
pricing     17 records   281/330  (85%)
techstack   17 records   137/357  (38%)
social      17 records   193/554  (35%)
marketing   17 records   356/612  (58%)
seo         17 records   128/187  (68%)
```

The contract's hard cases all appear in the live data: `nexusai` (no site) is a
full all-`unknown` record in every dataset; soft figures like Semrush's headcount
and ARR are `is_estimate` metrics with third-party provenance and reduced
confidence; verified absences (AlphaSense `freemium:false`, `self_service:false`)
are confident negatives, not gaps; and `contact_sales` enterprise tiers (Crayon,
Valona, AlphaSense) keep an honest `unknown`/`custom` price rather than a
fabricated number.

## 6. Contract & hand-off

Each dataset is `{ meta, <array>[] }` with `meta.dataset` equal to the file stem
and `schema_version: 1.0.0`. The six files join to `competitors.json` (and to each
other) by `entity_ref`. They are the substrate the rest of the pipeline consumes:
the knowledge-graph / normalization step carries `confidence` and `provenance`
through unchanged, the visualizations render `unknown` as an explicit "—" and
de-emphasize low-confidence values, and the report synthesizes across all six
dimensions per competitor (`schemas/report.schema.json`). Add or rename a
competitor upstream and the per-dimension records follow via the same join.
