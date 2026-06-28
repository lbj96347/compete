# Product Intelligence

**Step 1 of the compete pipeline.** Analyze the *local repository* to
extract the product whose competitive landscape we are about to map, and emit
`product.json` — the `entity_ref: "self"` record that every later phase compares
competitors against.

This step reads only files that already exist in the repo (no network). It is a
deterministic heuristic extractor: anything it cannot establish from concrete
evidence is written as the **`unknown`** form required by the data contract
(`references/data-schema.md`), never guessed or fabricated.

## Run it

```bash
python scripts/analyze_repo.py --repo . --validate
# writes ./product.json (conforming to schemas/product.schema.json)
```

| Flag | Default | Meaning |
| --- | --- | --- |
| `--repo PATH` | `.` | Repository to analyze. |
| `--output PATH` | `<repo>/product.json` | Where to write the dataset. |
| `--generator NAME` | `compete/analyze_repo 0.1.0` | Recorded in `meta.generator`. |
| `--validate` | off | Validate output against `schemas/` before writing (needs `jsonschema` + `referencing`). |
| `--quiet` | off | Suppress the human-readable summary. |

Pure standard library (uses `tomllib` on Python 3.11+ for `pyproject.toml` /
`Cargo.toml`). `--validate` is the only path that needs third-party packages.

## What it reads

- **README** — `README.md`/`.rst`/`.txt`. The H1 → product name (fallback);
  the first prose line → tagline; the first paragraph → description; bullets
  under a *Features / Highlights / Capabilities* heading → `core_features`;
  `for <audience>` phrasing → `target_users`. Badges, images, and HTML are
  stripped before extraction.
- **Package manifests** — `package.json`, `pyproject.toml` (PEP 621 + Poetry),
  `requirements.txt`, `setup.py`, `go.mod`, `Cargo.toml`, plus presence of
  `Gemfile`, `composer.json`, `pom.xml`, `build.gradle(.kts)`, `pubspec.yaml`,
  `Package.swift`. These yield the authoritative product **name**,
  **description**, **version**, the **language/runtime** set, and the union of
  **declared dependencies**.
- **Config / deployment files** — `LICENSE`/`COPYING`, `Dockerfile`,
  `docker-compose*`/`compose*`, Helm `charts/`+`Chart.yaml`, and API specs
  (`openapi.*`, `swagger.*`, `schema.graphql`).

## How fields are derived

| Field | Signal | Confidence |
| --- | --- | --- |
| `identity.name` | manifest `name` → README H1 → dir name | 0.9 / 0.6 / 0.3 |
| `identity.tagline` | manifest `description` → first README line | 0.8 / 0.5 |
| `identity.description` | README intro paragraph → manifest `description` | 0.55 / 0.6 |
| `identity.category` | LLM+vector deps → LLM deps → README keywords | 0.3–0.4 |
| `identity.stage` | semver: `0.0.x`→prototype, `0.x`→beta, `≥1.0`→ga, alpha/beta/rc tags | 0.4–0.55 |
| `identity.deployment_model` | OSS license ± container/Helm config | 0.35–0.45 |
| `identity.business_model` | open-source license present → "open source" | 0.4 |
| `identity.pricing_model` | **unknown** — not in source; collected from pricing page later | — |
| `features.core_features` | README Features bullets | 0.55 |
| `features.ai_capabilities` | AI/LLM/vector-DB SDK dependencies | 0.7 |
| `features.platforms` | manifest languages + UI/desktop/mobile framework deps | 0.6 |
| `features.integrations` | third-party service SDKs (Stripe, Slack, AWS, …) | 0.6 |
| `features.apis` | API framework deps + OpenAPI/GraphQL spec files | 0.5–0.8 |
| `features.target_workflow` | **unknown** — narrative judgment, left for report synthesis | — |
| `customers.target_users` | `for <audience>` README cues | 0.35 |
| `customers.industries` / `company_size` / `personas` / `use_cases` | **unknown** — require market research, collected in later phases | — |

The dependency → signal maps (`AI_DEPS`, `API_FRAMEWORK_DEPS`,
`INTEGRATION_DEPS`, `PLATFORM_DEPS`) live at the top of `analyze_repo.py`; extend
them there as new ecosystems need covering.

## Contract compliance

Output is a single object with `meta` (`dataset: "product"`,
`schema_version: 1.0.0`), `entity_ref: "self"`, and the `identity` / `features`
/ `customers` blocks. Every leaf is a confidence-wrapped field and the
**`unknown` invariant** holds throughout:

- **Not determined** → `{ "value": null, "confidence": 0, "unknown": true }`
  with `provenance` omitted (the contract types it as an object; an unknown
  field has no evidence trail).
- **Confident "none"** is *distinct* from unknown: e.g. a repo with no AI
  dependencies emits `ai_capabilities` as `{ "value": [], "confidence": 0.5,
  "unknown": false }` — a confident empty list, not `unknown`.
- Determined values carry `source` plus a `provenance` block
  (`source_type`, `as_of`, `method`) so a reviewer can audit every value.

Run with `--validate` to confirm the result passes
`schemas/product.schema.json` (which `$ref`s `schemas/common.schema.json`).

> Tech-stack signals surfaced here (`platforms`, `apis`, `ai_capabilities`,
> `integrations`) describe **the own product only**, inline in `product.json`.
> The dedicated, per-competitor `techstack.json` dataset is produced later by
> the Intelligence Collection step — see `references/data-schema.md`.
