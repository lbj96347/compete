---
name: compete
description: >-
  Analyze the current repository to identify the product, then discover, profile,
  and compare competitors into a complete competitive intelligence database.
  Use when the user says "find competitors", "competitor analysis", "competitive
  intelligence", "who are my competitors", "compare my product", "market research",
  "competitor comparison", "alternatives to my product", "competitive landscape",
  "SWOT", "positioning matrix", "comparison matrix", or asks to research rival
  products, their pricing, tech stack, SEO, marketing, social media, hiring,
  funding, or company profiles. Produces normalized JSON datasets and interactive
  visual reports.
---

# compete

An AI product research assistant that turns the current repository into a
competitive intelligence report. It identifies your product, discovers
competitors, collects multi-dimensional intelligence about each, normalizes
everything into JSON, and renders interactive visualizations.

## Model Selection (auto by default, manually overridable)

This skill spans cheap mechanical work and judgment-heavy synthesis, so it
assigns models **per stage** instead of running everything on the session model.
The default mapping is automatic — the user does **not** need to touch the model
picker:

| Stage | Default model | Why |
| --- | --- | --- |
| 1. Product Intelligence | **session model** (inline) | one-shot repo reasoning; cheap |
| 2. Competitor Discovery — web research | **Haiku subagent** | bulky `WebSearch`/`WebFetch`; isolatable |
| 3. Intelligence Collection — web research | **Haiku subagent, one per competitor** | biggest token sink; parallelizable |
| 4. Knowledge Graph | **session model** (inline) | scoring/threat/SWOT judgment |
| 5–6. Visualization + Report | **session model** (inline) | synthesis quality |

Rules:

- **Delegate the research in Stages 2 & 3 to subagents** (`Agent` tool,
  `subagent_type: "claude"`, `model: "haiku"`). Each subagent runs the `plan`
  output with `WebSearch`/`WebFetch` in its **own context** and returns only the
  compact `candidates.json` / `findings.json`-shaped data. The bulky fetched
  pages never enter the main context — this is where the token savings come from.
  Spawn Stage 3 subagents **in parallel, one per competitor**.
- **Keep Stages 1, 4, 5–6 on the main session model** — they need reasoning
  quality and are already cheap.
- **Guardrail for cheaper collectors:** instruct every research subagent to
  **prefer `unknown: true` over guessing**. `--validate` catches structural
  errors but not confident fabrication, and weaker models fabricate more.
- **Manual override:** if the user specifies a model (e.g. "use sonnet for
  research", or the `model=` / `research-model=` argument on `/compete`),
  use that model for the research subagents instead of the Haiku default. A
  request for "higher fidelity" research should bump collectors to Sonnet.
  "fast"/"cheap" keeps Haiku. The user may also pin the whole run to one model.
- This applies only inside **Claude Code** (where the `Agent` tool and per-spawn
  `model` overrides exist). Running the Python scripts standalone has no subagent
  layer, so model selection does not apply there.

## High-Level Workflow

1. **Product Intelligence** — Analyze the local repository to extract product
   identity, features, tech-stack signals, and target customers, writing
   `product.json` (`entity_ref: "self"`). Run
   `python scripts/analyze_repo.py --repo . --validate`.
   See `references/product-intelligence.md`.
2. **Competitor Discovery** — Discover and classify direct, indirect, enterprise,
   open-source, emerging, and adjacent competitors, writing `competitors.json`.
   Derive the search plan with
   `python scripts/discover_competitors.py plan --product product.json`, run it
   with WebSearch/WebFetch, then normalize with
   `python scripts/discover_competitors.py build --product product.json --candidates candidates.json --validate`.
   See `references/competitor-discovery.md`.
3. **Intelligence Collection** — For each competitor, gather company profile,
   pricing, technology, online/social presence, marketing, and SEO signals,
   writing `companies.json`, `pricing.json`, `techstack.json`, `social.json`,
   `marketing.json`, and `seo.json`. Derive the per-competitor plan with
   `python scripts/collect_intelligence.py plan --competitors competitors.json`,
   run it with WebSearch/WebFetch, then normalize with
   `python scripts/collect_intelligence.py build --competitors competitors.json --findings findings.json --validate`.
   See `references/intelligence-dimensions.md`.
4. **Knowledge Graph** — Normalize all findings into the JSON datasets defined in
   `schemas/`. The data contract (confidence-wrapped fields, `unknown` fallback,
   `entity_ref` joins) is authoritative — see `references/data-schema.md`.
5. **Visualization** — Render the dashboards, matrices, and charts that consume
   the normalized data. See `references/visualizations.md`.
6. **Report** — Assemble the interactive competitive intelligence report.

## Outputs

Normalized datasets written to the working directory:
`product.json`, `competitors.json`, `companies.json`, `social.json`,
`marketing.json`, `techstack.json`, `seo.json`, `pricing.json`, `report.json`.

Every visualization consumes these datasets rather than scraping directly.

## Directory Layout

- `references/` — Detailed instructions for each pipeline stage.
- `scripts/` — Helper scripts for collection, normalization, and rendering.
- `templates/` — Report and visualization templates.
- `schemas/` — JSON schemas for the normalized datasets.

> Status: The full v1 pipeline is implemented — Product Intelligence
> (`scripts/analyze_repo.py`), Competitor Discovery
> (`scripts/discover_competitors.py`), Intelligence Collection
> (`scripts/collect_intelligence.py`), and Knowledge Graph + Visualization
> (`scripts/build_report.py`, rendering the self-contained `report.html`). A
> sample report ships in `insightkit-output/`. Deferred to v2: deeper
> per-dimension intelligence (deep SEO/social/hiring/sales) and a
> `/watch-competitors` continuous-monitoring mode — see the README roadmap.
