---
description: Run the find-competitor skill end-to-end to produce a competitive intelligence report.
argument-hint: "[competitor URL or name (optional)]"
---

Run the **find-competitor** skill end-to-end on this repository, executing all
stages of its pipeline. Follow `skills/find-competitor/SKILL.md` for the exact
commands and reference docs.

Seed handling — `$ARGUMENTS`:
- **If a URL or competitor name is provided**, treat it as a seed: include it as a
  known competitor candidate during Discovery (and use it to anchor the product's
  market/category) rather than relying solely on auto-detection.
- **If empty**, auto-detect the product from the current repo in Stage 1.

Stages (see SKILL.md for full commands and the `plan` → web research → `build`
two-phase steps):
1. **Product Intelligence** — `analyze_repo.py --repo . --validate` → `product.json`.
2. **Competitor Discovery** — `discover_competitors.py plan` → run the plan with
   WebSearch/WebFetch → `build --validate` → `competitors.json` (fold in the
   `$ARGUMENTS` seed if given).
3. **Intelligence Collection** — `collect_intelligence.py plan` → web research →
   `build --validate` → the 6 per-competitor datasets.
4. **Report** — `build_report.py --input-dir . --output-dir ./insightkit-output --open`
   → `report.json` + `report.html`.

Aim for 0 schema errors on every `--validate`, prefer `unknown: true` over guessing,
then summarize the key competitors, threat levels, and where the report was written.
