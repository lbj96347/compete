# compete

> A Claude Code Skill that turns your repository into a complete competitive
> intelligence report.

`compete` analyzes the current repository to identify your product, then
discovers competitors and builds a complete competitive intelligence database
spanning product, technology, business, marketing, SEO, social media, customers,
sales, hiring, funding, and brand positioning. It is an AI product research
assistant — not just a comparison generator.

Everything is collected into normalized, **confidence-annotated** JSON datasets
and rendered into a self-contained interactive HTML report (the **InsightKit**
report) with dashboards, comparison matrices, capability radars, pricing
ladders, a positioning scatter, SWOT accordions, and prioritized
recommendations.

![InsightKit competitive landscape report](insightkit-output/screenshots/overview.png)

---

## Highlights

- **Repo-aware** — reads your code, manifests, and README to identify the
  product before searching the web. No manual product brief required.
- **Multi-dimensional intelligence** — company profile, pricing, tech stack,
  social presence, marketing, and SEO for every competitor.
- **Confidence everywhere** — every field is wrapped with a confidence score,
  source, provenance, and an explicit `unknown` fallback. Nothing is asserted
  without showing its work. Verify before acting.
- **Normalized data contract** — all stages write JSON validated against the
  schemas in [`skills/compete/schemas/`](skills/compete/schemas/), joined by `entity_ref`. Visualizations
  consume the data, never scrape directly.
- **Self-contained report** — one `report.html` file (~570 KB) opens standalone
  in any browser. The only external dependencies are the Chart.js and D3 CDN
  bundles.

---

## Installation

`compete` is packaged as a **Claude Code plugin**. The plugin bundles
three things that work together:

- the **`compete` skill** (`skills/compete/`) — the workflow,
  scripts, schemas, and report template;
- the **`/compete` slash command** (`commands/compete.md`) — a one-shot
  entry point that drives the whole pipeline;
- the plugin manifest (`.claude-plugin/plugin.json`).

### Recommended — install from the marketplace

```text
/plugin marketplace add forthrighttech/compete
/plugin install compete
```

The first command registers this repository as a plugin marketplace (it ships a
[`.claude-plugin/marketplace.json`](.claude-plugin/marketplace.json)); the second
installs the plugin. Restart or start a new session and confirm with `/plugin`
— `compete` should be listed as enabled, the `/compete` command
available, and the `compete` skill discoverable via `/skills`.

### Manual — clone into your plugins folder

```bash
git clone https://github.com/forthrighttech/compete.git \
  ~/.claude/plugins/compete
```

The plugin must live at a directory whose root contains
`.claude-plugin/plugin.json` (with `skills/` and `commands/` alongside it).
Claude Code discovers it on the next session.

### Skill-only (no plugin)

If you only want the skill without the slash command, clone just the skill
subtree into your skills folder:

```bash
# Personal (all projects)
git clone https://github.com/forthrighttech/compete.git /tmp/compete && \
  cp -r /tmp/compete/skills/compete ~/.claude/skills/compete

# Project-scoped (one repository)
cp -r /tmp/compete/skills/compete \
  /path/to/your-repo/.claude/skills/compete
```

The skill must live at `.../.claude/skills/compete/` with `SKILL.md` at
its root. Confirm with `/skills` (it should appear as `compete`). You
trigger it in natural language rather than with `/compete`.

### Requirements

- **Claude Code** with web access (`WebSearch` / `WebFetch`) for competitor
  discovery and intelligence collection.
- **Python 3.9+** for the helper scripts in [`skills/compete/scripts/`](skills/compete/scripts/). The
  collection and rendering scripts use only the standard library — no `pip
  install` required.

---

## Usage

### The `/compete` command (recommended)

Open Claude Code in the repository you want analyzed and run:

```text
/compete
```

With no argument, Stage 1 **auto-detects** the product from the current repo and
the command runs the full pipeline end to end, writing the report to
`./insightkit-output/`.

You can also pass an **optional seed** — a competitor URL or name:

```text
/compete https://www.crayon.co
/compete Klue
```

A seed is folded into Discovery as a known competitor candidate and is used to
anchor the product's market/category, instead of relying solely on
auto-detection. The seed is slugified into an `entity_ref` (e.g. `crayon`),
classified, and appears in the roster alongside the auto-discovered competitors.

### Natural language

If you installed the skill without the command — or just prefer plain language —
ask Claude directly. The skill triggers on phrases such as:

> "Find my competitors"
> "Run a competitive analysis on this repo"
> "Who are my competitors and how do I compare?"
> "Build me a competitive landscape / positioning matrix / SWOT"

### Run any stage manually

Claude runs the pipeline end to end. You can also drive any stage yourself:

```bash
# 1. Product Intelligence — analyze the repo, write product.json
python skills/compete/scripts/analyze_repo.py --repo . --validate

# 2. Competitor Discovery — plan searches, then normalize results
python skills/compete/scripts/discover_competitors.py plan  --product product.json
#    (Claude runs the plan with WebSearch/WebFetch → candidates.json)
python skills/compete/scripts/discover_competitors.py build --product product.json \
  --candidates candidates.json --validate

# 3. Intelligence Collection — per-competitor company/pricing/tech/social/marketing/SEO
python skills/compete/scripts/collect_intelligence.py plan  --competitors competitors.json
#    (Claude runs the plan with WebSearch/WebFetch → findings.json)
python skills/compete/scripts/collect_intelligence.py build --competitors competitors.json \
  --findings findings.json --validate

# 4 + 5. Knowledge Graph + Visualization — synthesize report.json and render report.html
python skills/compete/scripts/build_report.py --input-dir . --output-dir ./insightkit-output
#    add --open to also launch the report in a browser
```

Open the result:

```bash
open ./insightkit-output/report.html
```

---

## Sample output

A fully rendered example ships in [`insightkit-output/`](insightkit-output/) so
you can see the report without running the pipeline:

| File | What it is |
| --- | --- |
| [`report.html`](insightkit-output/report.html) | Self-contained interactive report — open in any browser. |
| [`report.json`](insightkit-output/report.json) | Synthesized analytic layer (executive summary, SWOT, positioning, gaps, recommendations), schema-valid. |
| [`screenshots/overview.png`](insightkit-output/screenshots/overview.png) | The overview dashboard pictured above. |

The sample analyzes this very repository (`compete`) against **17
discovered competitors**, classifying each by type and competitive threat. The
report has seven tabbed views:

- **Overview** — stat cards, competitor-classification and threat-distribution
  doughnuts, and the executive summary.
- **Comparison** — sortable matrix with confidence-bar cells per dimension.
- **Radar** — six transparent 0–100 capability axes, self vs. competitors,
  toggleable series.
- **Pricing** — entry-price bar chart plus a plan-ladder table.
- **Positioning** — D3 scatter of price × scale, bubble size = similarity,
  color = threat.
- **SWOT** — expandable strengths/weaknesses/opportunities/threats per
  competitor.
- **Opportunities** — market gaps and prioritized recommendations.

Every judgment in the report carries a `method` note explaining the heuristic
behind it.

---

## How it works

```
repo ──▶ analyze_repo.py ──▶ product.json
                                 │
                                 ▼
        discover_competitors.py ──▶ competitors.json
                                 │
                                 ▼
        collect_intelligence.py ──▶ companies/pricing/techstack/
                                 │   social/marketing/seo.json
                                 ▼
            build_report.py ──▶ report.json ──▶ report.html
```

The normalized JSON datasets are the contract between stages. Each is validated
against a schema in [`skills/compete/schemas/`](skills/compete/schemas/); the rules (confidence-wrapped
fields, `unknown` fallback, `entity_ref` joins) are documented in
[`skills/compete/references/data-schema.md`](skills/compete/references/data-schema.md).

---

## Project structure

| Path | Purpose |
| --- | --- |
| [`.claude-plugin/plugin.json`](.claude-plugin/plugin.json) | Plugin manifest (name, version, author, license). |
| [`.claude-plugin/marketplace.json`](.claude-plugin/marketplace.json) | Marketplace entry for one-step `/plugin install`. |
| [`commands/compete.md`](commands/compete.md) | The `/compete` slash command (optional seed argument). |
| [`skills/compete/SKILL.md`](skills/compete/SKILL.md) | Skill definition, trigger keywords, and high-level workflow. |
| [`PRD.md`](PRD.md) | Full research scope and product vision. |
| [`skills/compete/references/`](skills/compete/references/) | Detailed instructions for each pipeline stage. |
| [`skills/compete/scripts/`](skills/compete/scripts/) | Python helpers for collection, normalization, and rendering. |
| [`skills/compete/templates/`](skills/compete/templates/) | The self-contained `report.html` template. |
| [`skills/compete/schemas/`](skills/compete/schemas/) | JSON schemas for every normalized dataset. |
| [`insightkit-output/`](insightkit-output/) | Sample rendered report. |

---

## Roadmap

`compete` v1 ships the full **one-shot** pipeline: product
intelligence → discovery → multi-dimensional collection → knowledge graph →
interactive report. The following are deliberately deferred to **v2**.

### Deeper intelligence per dimension

v1 collects a solid breadth-first profile across all dimensions. v2 goes deep:

- **Deep SEO** — keyword universe and rankings, backlink graph and authority,
  content gap analysis, SERP-feature ownership, and traffic-trend estimates
  (beyond v1's meta/structure snapshot).
- **Deep social** — engagement-rate and follower-growth time series, share-of-
  voice, sentiment, and channel-mix breakdowns per competitor.
- **Hiring intelligence** — open-roles tracking, team-growth and org-shape
  signals, and the strategic bets implied by what each competitor is hiring for.
- **Sales intelligence** — go-to-market motion (PLG vs. sales-led), funnel and
  packaging signals, win/loss themes, and target-segment inference.

### `/watch-competitors` — continuous monitoring

v1 produces a point-in-time report. v2 adds a monitoring mode that re-runs the
pipeline on a schedule and surfaces **diffs** — turning InsightKit from a
one-time report generator into a continuous competitive-intelligence platform.
Daily or weekly it would track:

- pricing changes
- new features
- new blog posts
- hiring trends
- GitHub releases
- social-media activity
- funding news
- SEO changes

See [`PRD.md`](PRD.md) for the complete v1 research scope and the v2 vision.

---

## Contributing

Contributions are welcome — see [`CONTRIBUTING.md`](CONTRIBUTING.md) for the
data-contract rules, schema-validation workflow, and how to regenerate the
sample report.

## License

[MIT](LICENSE) © 2026 forthrighttech
