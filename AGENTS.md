# Repository Guidelines

## Project Structure & Module Organization

This repository is a Claude Code Skill for competitive intelligence. `skills/compete/SKILL.md` defines triggers and workflow, while `PRD.md` and `skills/compete/references/` describe the product scope and stage-specific rules. Python pipeline helpers live in `skills/compete/scripts/`: `analyze_repo.py`, `discover_competitors.py`, `collect_intelligence.py`, and `build_report.py`. JSON datasets at the repo root, such as `product.json`, `competitors.json`, and `pricing.json`, are the normalized data contract. Schemas are in `skills/compete/schemas/`, and the report UI template is `skills/compete/templates/report.html`.

## Build, Test, and Development Commands

Use Python 3.9+; the scripts rely on the standard library, with optional `jsonschema` support.

```bash
python skills/compete/scripts/analyze_repo.py --repo . --validate
```

Analyzes this repository and validates `product.json`.

```bash
python skills/compete/scripts/build_report.py --input-dir . --output-dir ./insightkit-output
```

Builds `report.json` and the standalone `report.html` output. Use `--open` only when you want the script to launch a browser.

## Coding Style & Naming Conventions

Follow the existing Python style: 4-space indentation, type hints where useful, small deterministic helpers, and explicit docstrings for pipeline entry points. Keep file names and dataset names lowercase with underscores where needed. Preserve the confidence-envelope pattern for collected values: include `value`, `confidence`, `unknown`, `source`, and `provenance`; when `unknown` is `true`, `value` must be `null`.

## Testing Guidelines

There is no separate test suite currently. Validation is schema-driven, so run the relevant script with `--validate` after changing data extraction, normalization, or schemas. If you touch `skills/compete/templates/report.html`, `skills/compete/schemas/`, or `skills/compete/scripts/build_report.py`, regenerate `insightkit-output/` and inspect the rendered report in a browser.

## Commit & Pull Request Guidelines

Git history currently contains only `Initial commit`, so no project-specific commit convention is established. Use concise, imperative commit messages such as `Update pricing schema validation`. Pull requests should explain what changed, why it changed, and which validation commands were run. For schema or data contract changes, update the matching reference document in `skills/compete/references/` in the same PR.

## Agent-Specific Instructions

Do not overwrite normalized JSON fields with unverified claims. Prefer `unknown: true` over guessing, keep joins based on `entity_ref`, and never move collection logic into the report template.
