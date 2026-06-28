---
description: Run the compete skill end-to-end to produce a competitive intelligence report.
argument-hint: "[competitor URL or name (optional)]"
---

Run the **compete** skill end-to-end on this repository, executing all
stages of its pipeline. Follow `skills/compete/SKILL.md` for the exact
commands and reference docs.

Seed handling — `$ARGUMENTS`:
- **If a URL or competitor name is provided**, treat it as a seed: include it as a
  known competitor candidate during Discovery (and use it to anchor the product's
  market/category) rather than relying solely on auto-detection.
- **If empty**, auto-detect the product from the current repo in Stage 1.

Model selection — also parse `$ARGUMENTS` for an optional model override:
- **Default (no override): automatic.** Run Stages 1, 4–6 inline on the session
  model and delegate the Stage 2 & 3 web research to **Haiku subagents** (one per
  competitor in Stage 3). See the "Model Selection" section of SKILL.md. The user
  does **not** need to change their model picker.
- **`research-model=<model>`** (or natural language like "use sonnet for
  research", "higher-fidelity research") — use that model for the research
  subagents instead of the Haiku default.
- **`model=<model>`** (or "run everything on <model>") — pin the entire run,
  inline and subagents, to that one model.
- Strip any recognized model arguments before treating the remainder of
  `$ARGUMENTS` as the competitor seed.

Stages (see SKILL.md for full commands and the `plan` → web research → `build`
two-phase steps):
1. **Product Intelligence** — `analyze_repo.py --repo . --validate` → `product.json`.
2. **Competitor Discovery** — `discover_competitors.py plan` → run the plan with
   WebSearch/WebFetch **in a Haiku subagent** (per Model Selection) that returns
   `candidates.json`-shaped data → `build --validate` → `competitors.json` (fold
   in the `$ARGUMENTS` seed if given).
3. **Intelligence Collection** — `collect_intelligence.py plan` → run the web
   research **in parallel Haiku subagents, one per competitor** (per Model
   Selection), each returning `findings.json`-shaped data → `build --validate` →
   the 6 per-competitor datasets.
4. **Report** — `build_report.py --input-dir . --output-dir ./insightkit-output --open`
   → `report.json` + `report.html`.

Aim for 0 schema errors on every `--validate`, prefer `unknown: true` over guessing,
then summarize the key competitors, threat levels, and where the report was written.
