# Competitor Discovery

**Step 2 of the find-competitor pipeline.** Take the own product
(`product.json`, `entity_ref: "self"`, produced by
[Product Intelligence](product-intelligence.md)) and turn it into a **classified
roster of competitors** — `competitors.json`, whose `id`s are the canonical
join keys every later per-competitor dataset references.

Unlike Step 1, discovery is **web-driven and judgment-heavy**: the competitive
set lives on the open web, not in the repo. So this step is split in two:

| Half | Owner | What it does |
| --- | --- | --- |
| **Research** | the agent, with `WebSearch` / `WebFetch` | find candidate products, classify them, estimate overlap |
| **Normalization** | `scripts/discover_competitors.py` | slug join-keys, dedup, confidence-wrap, validate against the schema |

Keep the messy judgment with the researcher and the data contract with the
script — the same separation Step 1 used between heuristics and the schema
envelope.

```bash
# 1. derive the search plan from the own product (no network)
python scripts/discover_competitors.py plan --product product.json

# 2. ...run the plan with WebSearch/WebFetch, write findings to candidates.json...

# 3. normalize → competitors.json (validated)
python scripts/discover_competitors.py build \
    --product product.json --candidates candidates.json --validate
```

---

## 1. Derive the search plan

`plan` reads `product.json` and emits a JSON plan: a **topic** (what the product
*is*), a list of **queries** each tagged with the categories it tends to
surface, **fetch hints** (aggregators worth `WebFetch`ing directly), and the
classification **rubric**. It degrades gracefully — a repo with only a name
still yields the named-product and topic queries.

How the topic is derived (in priority order): explicit `identity.category` →
a short, phrase-like `identity.tagline` → the most topical **bigram** mined from
the description + features (adjacent content words, frequency-ranked, the
product's own name filtered out) → the bare name. For this very repo, whose
README never states a category, the bigram heuristic recovers
`"competitive intelligence"` at confidence 0.4.

The query families (each maps to the categories below):

- **Named-product** — `"<name> alternatives"`, `"alternatives to <name>"`,
  `"<name> vs"` → direct rivals and "alternatives to …" listicles.
- **Topic** — `"<topic> competitors"`, `"best <topic> tools 2026"`,
  `"open source <topic>"`, `"AI <topic> startup"`,
  `"<topic> enterprise platform"` → the core set across every category.
- **Feature** — one `"<feature> <topic> tool"` per differentiating capability.
- **Audience** — `"<topic> for <target user>"` → same job, same buyer.

Fetch hints point at high-recall aggregators: the **G2** category and each
product's *Competitors/Alternatives* tab, **GitHub** `topics/<topic>` for
open-source projects, **Product Hunt** search for emerging launches, and every
promising *"alternatives to …"* article a search returns.

## 2. Run the research

Execute the queries with `WebSearch` and the hints with `WebFetch`. The goal is
**recall first** (cast wide, gather every plausible product) then **precision**
(classify and score). Prompt patterns that work well:

> **WebSearch** — `"<topic> competitors alternatives 2026"`, then for each
> product named in the results, a follow-up `"<product> pricing"` /
> `"<product> open source"` to fix its classification.

> **WebFetch** an "alternatives to X" article with:
> *"List every product mentioned as a competitor or alternative. For each give:
> name, official website, one-line positioning, whether it's open-source, and
> whether pricing is self-serve or 'contact sales'."*

> **WebFetch** `https://github.com/topics/<topic>` with:
> *"List repositories that are competitor-analysis / market-intelligence tools.
> For each give the repo URL, a one-line description, and star count if shown."*

When search yields little, widen the topic (use the next-best bigram, or a
parent category), try a synonym, and consult one aggregator directly. If it is
*still* thin, that is a real finding — record what you have and let the script
emit the graceful fallback (§5); **do not invent competitors**.

## 3. Classify every competitor

Assign exactly one `classification` per the rubric (also embedded in the plan):

| Value | Meaning |
| --- | --- |
| `direct` | Same core job for the same buyer; a user would choose it *instead of* us. |
| `indirect` | Solves the same problem a different way, or covers only one dimension of it. |
| `enterprise` | Large incumbent, sales-led, "contact us" pricing, broad suite. |
| `open_source` | Source-available / self-hostable project (often a GitHub repo). |
| `emerging_startup` | Young (≲3 yrs), AI-native or niche, still establishing itself. |
| `adjacent` | Neighboring category with partial overlap; a complement more than a rival. |
| `unknown` | Could not be classified — the explicit fallback (set via `unknown: true`). |

The categories are **mutually exclusive**; pick the *defining* axis. A product
that is both open-source and a close functional twin is classified `open_source`
(its distinguishing trait as an alternative) with the functional closeness noted
in `relationship_notes` and reflected in a high `similarity_score`. Reserve
`direct` for the hosted/commercial twin.

**`similarity_score` (0..1)** is overlap with the own product across identity,
features, and customers — it drives ranking on the executive dashboard. Calibrate
roughly: `≥0.7` near-twin, `0.4–0.6` strong overlap on the core job, `0.2–0.4`
one shared dimension, `<0.2` adjacent. Score honestly and attach a confidence;
a guess gets a low confidence, not a fabricated value.

## 4. Candidate format

Record each finding as one object in a flat JSON list (`candidates.json`). Only
`name` is required; everything else is optional and degrades to the `unknown`
form when omitted. The script wraps these raw scalars into the confidence-wrapped
fields the contract requires — you supply values + confidences, not envelopes.

```json
[
  {
    "name": "Competely",
    "website": "https://competely.ai",
    "aliases": ["Competely.ai"],
    "classification": "direct",
    "confidence": 0.7,
    "similarity_score": 0.78,
    "similarity_confidence": 0.6,
    "relationship_notes": "Generative-AI 'instant competitive analysis' — input competitors, get a structured comparison across positioning, features, and pricing. Same job, but hosted SaaS, not repo-driven or open source.",
    "discovery_source": "WebSearch: AI competitor analysis tool 2026",
    "source_type": "official_website"
  }
]
```

| Key | Required | Notes |
| --- | --- | --- |
| `name` | ✔ | Display name; also the seed for the slug `id`. |
| `website` | | Official URL; bare domains are upgraded to `https://`. |
| `aliases` | | Other names; dedup unions these. |
| `classification` | | One of the rubric values. Missing/invalid → `unknown`. |
| `confidence` | | Trust in the classification, `[0,1]` (default `0.5`). |
| `similarity_score` | | Overlap `[0,1]` (clamped). |
| `similarity_confidence` | | Trust in the score (defaults to `confidence`). |
| `relationship_notes` | | Why it competes / how it differs. |
| `discovery_source` | | The query or page it surfaced from — keep it; it's the provenance. |
| `source` / `source_type` | | Primary evidence URL and its `sourceType` (default `search_result`). |

Accepted on input either as a bare list or as `{"candidates": [...]}`. Pipe via
`--candidates -` to read stdin.

## 5. Normalize → competitors.json

`build` performs the deterministic, contract-bound half:

- **Slug `id`** — `name` → `^[a-z0-9][a-z0-9-]{0,63}$`, made unique with a
  numeric suffix on collision. This is the join key every other dataset uses.
- **Dedup** — candidates are collapsed on **registrable domain** (GitHub repos
  key on `owner/repo`, so two repos on `github.com` stay distinct), else on the
  alnum-folded name. The most-confident record is the primary; merges **union
  aliases**, keep the **higher-confidence classification**, the **max
  similarity**, the **richest notes**, and **concatenate discovery sources** so
  the merge stays auditable.
- **Ranking** — output is sorted by `similarity_score` descending (unknown
  last), so the dashboard's default order is most-relevant-first.
- **Confidence wrapping** — every scalar becomes a confidence-wrapped field with
  `provenance`. A present website is treated as `official_website` evidence;
  similarity is `inference`.
- **`unknown` fallbacks** — a missing/invalid `classification`, an absent
  `website`, or an unscored similarity each emit the canonical
  `{ "value": null, "confidence": 0, "unknown": true }` form (with a `notes`
  explaining why), never a guess. An **invalid** classification string is
  downgraded to `unknown` rather than rejected, with the offending value recorded
  in `notes`.
- **Empty roster** — with no candidates the build still writes a *valid*
  `competitors.json` with `competitors: []` and warns on stderr. An empty roster
  (confident "found nothing yet") is contractually distinct from a malformed one.

`--validate` checks the result against `schemas/competitors.schema.json` (which
`$ref`s `common.schema.json`) before writing — needs `jsonschema` + `referencing`.

### Worked run (this repo)

Running the plan against `find-competitor`'s own `product.json` and feeding the
researched candidates back through `build` produced a 17-competitor roster from
18 candidates (one duplicate merged):

```
enterprise=5, indirect=4, open_source=2, emerging_startup=2, adjacent=2, direct=1, unknown=1
```

`direct`: Competely · `open_source`: Comperator, competitor-analyst ·
`enterprise`: Crayon, Klue, Kompyte, Contify, Valona · `emerging_startup`:
RivalSense, Unkover · `indirect`: Similarweb, Owler, Semrush, Visualping ·
`adjacent`: Crunchbase, AlphaSense · `unknown`: NexusAI (surfaced in a roundup
but with no confirmable URL — website and classification correctly left
`unknown`). The two `RivalSense` candidates (`rivalsense.co` and a deeper page on
the same domain) collapsed into one entry, unioning the alias and keeping the
higher-confidence `emerging_startup` classification.

## 6. Contract & hand-off

`competitors.json` is `{ meta, competitors[] }` with
`meta.dataset: "competitors"`, `schema_version: 1.0.0`. Each entry carries `id`,
`name`, optional `aliases`, and the confidence-wrapped `website`,
`classification`, `similarity_score`, `relationship_notes`, `discovery_source`.

The `id` set defined here is the **universe of competitors** for the rest of the
pipeline: Intelligence Collection (Step 3) keys every `companies` / `social` /
`marketing` / `techstack` / `seo` / `pricing` record off these `id`s via
`entity_ref`. Add or rename an entry here and the downstream join follows; a
missing per-competitor record downstream is equivalent to all-`unknown`, so an
incomplete roster degrades gracefully rather than breaking the graph.
