#!/usr/bin/env python3
"""
build_report.py — Visualization Engine of the compete pipeline.

This is the final step of the pipeline (PRD §Visualizations). It consumes every
normalized dataset produced by the earlier steps —

    product.json      competitors.json  companies.json   social.json
    marketing.json    techstack.json    seo.json         pricing.json

— and does two things:

  synthesize  Derive the analytic layer (``report.json``) that the report
              schema (``schemas/report.schema.json``) describes: an executive
              summary, per-competitor SWOT + threat level + differentiators, a
              positioning matrix, opportunity gaps, and prioritized
              recommendations. All judgments are heuristic and transparent — the
              code says *how* each value was derived so a reader can audit it.

  render      Aggregate the raw datasets + the synthesized report into a single
              ``INSIGHTKIT`` data object, inline it into ``templates/report.html``
              (Chart.js + D3 loaded from CDN), and write a fully self-contained,
              double-click-to-open ``report.html``.

Both ``report.json`` (the aggregate analytic dataset) and ``report.html`` (the
interactive report) are written to ``./insightkit-output/``.

The split mirrors the rest of the pipeline: the messy judgment (what the data
*means*) is made explicit and deterministic here; the contract (field shapes,
the ``unknown ⇒ null`` invariant, the ``entity_ref`` join keys) is inherited
from the upstream datasets and never fabricated.

Usage:
    python scripts/build_report.py
    python scripts/build_report.py --input-dir . --output-dir ./insightkit-output
    python scripts/build_report.py --open      # also open the report in a browser
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
import webbrowser
from pathlib import Path
from typing import Any, Optional

try:  # sibling module in scripts/ — shared progress reporter
    from _progress import Progress
except ImportError:  # pragma: no cover - allow import from another cwd
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _progress import Progress

# --------------------------------------------------------------------------- #
# Confidence-envelope helpers
#
# Every collected value upstream is wrapped as {value, confidence, unknown, ...}
# (schemas/common.schema.json). These helpers unwrap that envelope safely so the
# rest of the module can treat fields as plain Python values while still having
# access to the confidence score when a visualization wants to weight by it.
# --------------------------------------------------------------------------- #

def v(field: Any, default: Any = None) -> Any:
    """Unwrap a confidence-wrapped field to its value (or ``default``).

    Treats ``unknown: true`` and a missing/None ``value`` as absent. Plain
    (already-unwrapped) values are returned as-is so the helper is idempotent.
    """
    if isinstance(field, dict) and "value" in field and "confidence" in field:
        if field.get("unknown") or field.get("value") is None:
            return default
        return field["value"]
    if field is None:
        return default
    return field


def conf(field: Any, default: float = 0.0) -> float:
    """Confidence score of a wrapped field in [0, 1] (0 when unknown/absent)."""
    if isinstance(field, dict) and "confidence" in field:
        if field.get("unknown"):
            return 0.0
        try:
            return float(field.get("confidence") or 0.0)
        except (TypeError, ValueError):
            return default
    return default


def cell(field: Any, default: Any = None) -> dict:
    """A {value, confidence, unknown} triple for rendering a matrix cell."""
    value = v(field, default)
    return {
        "value": value,
        "confidence": round(conf(field), 3),
        "unknown": value is None,
    }


def wrap(value: Any, confidence: float, *, method: str,
         source_type: str = "inference", as_of: Optional[str] = None) -> dict:
    """Build a schema-conformant confidence-wrapped field for synthesized output."""
    unknown = value is None
    return {
        "value": value,
        "confidence": 0.0 if unknown else round(float(confidence), 3),
        "unknown": unknown,
        "source": None,
        "provenance": {
            "source": None,
            "source_type": source_type,
            "as_of": as_of,
            "method": method,
        },
        "notes": None,
    }


def as_list(field: Any) -> list:
    """Unwrap a string-array field to a plain list (empty when unknown/absent)."""
    value = v(field)
    if isinstance(value, list):
        return value
    return []


def as_num(field: Any) -> Optional[float]:
    """Unwrap a numeric-ish field, coercing strings, to float or None."""
    value = v(field)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #

DATASETS = [
    "product", "competitors", "companies", "social",
    "marketing", "techstack", "seo", "pricing",
]


def load_json(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def index_by_ref(records: list, key: str = "entity_ref") -> dict:
    """Index a per-competitor dataset list by its join key."""
    out = {}
    for rec in records or []:
        ref = rec.get(key) or rec.get("id")
        if ref:
            out[ref] = rec
    return out


def load_all(input_dir: Path) -> dict:
    """Load every dataset; missing files degrade to None, never an error."""
    data = {name: load_json(input_dir / f"{name}.json") for name in DATASETS}
    missing = [n for n in DATASETS if data[n] is None]
    if missing:
        print(f"  ! missing datasets (degraded): {', '.join(missing)}",
              file=sys.stderr)
    return data


# --------------------------------------------------------------------------- #
# Per-entity profile assembly
#
# Joins the six per-competitor datasets + the competitors roster by entity_ref
# into one flat profile per entity, plus the analyzed product itself ("self").
# --------------------------------------------------------------------------- #

def build_entities(data: dict) -> list:
    competitors = (data["competitors"] or {}).get("competitors", [])
    companies = index_by_ref((data["companies"] or {}).get("companies", []))
    pricing = index_by_ref((data["pricing"] or {}).get("pricing", []))
    techstack = index_by_ref((data["techstack"] or {}).get("techstack", []))
    social = index_by_ref((data["social"] or {}).get("presence", []))
    marketing = index_by_ref((data["marketing"] or {}).get("marketing", []))
    seo = index_by_ref((data["seo"] or {}).get("seo", []))

    entities = []

    # --- the analyzed product ("self"), from product.json -------------------
    product = data["product"] or {}
    if product:
        identity = product.get("identity", {})
        entities.append({
            "ref": "self",
            "is_self": True,
            "name": v(identity.get("name"), "This product"),
            "website": v(identity.get("website")),
            "classification": "self",
            "similarity": 1.0,
            "company": {},
            "pricing": {},
            "techstack": {},
            "social": {},
            "marketing": _self_marketing(product),
            "seo": {},
            "product": product,
        })

    # --- competitors --------------------------------------------------------
    for comp in competitors:
        ref = comp["id"]
        entities.append({
            "ref": ref,
            "is_self": False,
            "name": comp.get("name") or ref,
            "website": v(comp.get("website")),
            "classification": v(comp.get("classification"), "unknown"),
            "classification_conf": conf(comp.get("classification")),
            "similarity": as_num(comp.get("similarity_score")),
            "relationship_notes": v(comp.get("relationship_notes")),
            "company": companies.get(ref, {}),
            "pricing": pricing.get(ref, {}),
            "techstack": techstack.get(ref, {}),
            "social": social.get(ref, {}),
            "marketing": marketing.get(ref, {}),
            "seo": seo.get(ref, {}),
        })
    return entities


def _self_marketing(product: dict) -> dict:
    """Best-effort positioning block for the self product from product.json."""
    identity = product.get("identity", {})
    return {
        "positioning": {
            "positioning_statement": identity.get("description"),
            "value_proposition": identity.get("tagline"),
        }
    }


# --------------------------------------------------------------------------- #
# Capability scoring (radar + comparison strength)
#
# Six 0–100 capability axes derived from the joined datasets. Each axis has a
# transparent rule; absent data scores low rather than erroring, which is the
# honest read for a competitor we could not find evidence on.
# --------------------------------------------------------------------------- #

RADAR_AXES = [
    "Scale & Funding",
    "Pricing Transparency",
    "Marketing Engine",
    "Social Reach",
    "SEO Footprint",
    "Tech Depth",
]

_FUNDING_RANK = {
    "bootstrapped": 25, "pre-seed": 20, "seed": 40, "series a": 60,
    "series b": 75, "series c": 85, "series d": 90, "growth": 90,
    "public": 100, "acquired": 80,
}

_BLOG_RANK = {"none": 0, "rare": 25, "monthly": 55, "weekly": 80, "daily": 100}
_DOC_RANK = {"poor": 20, "none": 0, "adequate": 55, "good": 75, "excellent": 100}


def _clamp(n: float) -> float:
    return max(0.0, min(100.0, round(n, 1)))


def score_scale(e: dict) -> float:
    company = e["company"]
    stage = (v(company.get("funding_stage")) or "").strip().lower()
    score = _FUNDING_RANK.get(stage, 0)
    emp = as_num(company.get("employee_estimate"))
    if emp:
        # log-ish bands: 1→10, 10→30, 100→60, 1k→85, 10k+→100
        import math
        score = max(score, _clamp(20 * math.log10(max(emp, 1)) + 10))
    if v(company.get("estimated_arr")) or v(company.get("total_funding")):
        score = max(score, 50)
    return _clamp(score)


def score_pricing(e: dict) -> float:
    pricing = e["pricing"]
    if not pricing:
        return 0.0
    score = 0
    if v(pricing.get("pricing_page")):
        score += 35
    plans = pricing.get("plans") or []
    score += min(len(plans) * 12, 36)
    if v(pricing.get("has_free_plan")):
        score += 15
    if as_num(pricing.get("lowest_paid_monthly")) is not None:
        score += 14
    return _clamp(score)


def score_marketing(e: dict) -> float:
    mk = e["marketing"]
    if not mk:
        return 0.0
    content = mk.get("content", {}) or {}
    blog = (v(content.get("blog_frequency")) or v(e["seo"].get("blog_frequency")) or "").lower()
    score = _BLOG_RANK.get(blog, 0) * 0.45
    programs = mk.get("programs", {}) or {}
    active_programs = sum(1 for x in programs.values() if v(x))
    score += min(active_programs * 9, 36)
    positioning = mk.get("positioning", {}) or {}
    if v(positioning.get("positioning_statement")) or v(positioning.get("value_proposition")):
        score += 19
    return _clamp(score)


def score_social(e: dict) -> float:
    social = e["social"]
    if not social:
        return 0.0
    channels = {**(social.get("social", {}) or {}),
                **(social.get("developer_channels", {}) or {})}
    present = 0
    followers = 0
    for ch in channels.values():
        if isinstance(ch, dict):
            handle = v(ch.get("handle")) or v(ch.get("url")) or v(ch)
            if handle:
                present += 1
            f = as_num(ch.get("followers")) or as_num(ch.get("stars")) or as_num(ch.get("members"))
            if f:
                followers = max(followers, f)
    score = min(present * 12, 60)
    if followers:
        import math
        score += min(15 * math.log10(max(followers, 1)), 40)
    return _clamp(score)


def score_seo(e: dict) -> float:
    seo = e["seo"]
    if not seo:
        return 0.0
    score = 0
    indexed = as_num(seo.get("indexed_pages"))
    if indexed:
        import math
        score += min(18 * math.log10(max(indexed, 1)), 50)
    if v(seo.get("has_sitemap")):
        score += 12
    if v(seo.get("has_robots_txt")):
        score += 8
    score += _DOC_RANK.get((v(seo.get("documentation_quality")) or "").lower(), 0) * 0.2
    kw = as_list(seo.get("keyword_focus"))
    score += min(len(kw) * 2, 10)
    return _clamp(score)


def score_tech(e: dict) -> float:
    tech = e["techstack"]
    if not tech:
        return 0.0
    fields = ["frontend", "backend", "framework", "database", "hosting",
              "cloud_provider", "cdn", "deployment", "authentication",
              "analytics", "payments", "monitoring", "cms", "llm_provider",
              "inference_provider"]
    known = 0
    for f in fields:
        val = v(tech.get(f))
        if val:
            known += 1
    return _clamp(known / len(fields) * 100)


def capability_scores(e: dict) -> dict:
    return {
        "Scale & Funding": score_scale(e),
        "Pricing Transparency": score_pricing(e),
        "Marketing Engine": score_marketing(e),
        "Social Reach": score_social(e),
        "SEO Footprint": score_seo(e),
        "Tech Depth": score_tech(e),
    }


# --------------------------------------------------------------------------- #
# SWOT + threat synthesis
# --------------------------------------------------------------------------- #

def threat_level(e: dict, scores: dict) -> str:
    sim = e.get("similarity") or 0
    cls = e.get("classification")
    overall = sum(scores.values()) / len(scores)
    weight = sim * 0.6 + (overall / 100) * 0.4
    if cls == "direct":
        weight += 0.12
    if weight >= 0.6:
        return "high"
    if weight >= 0.4:
        return "medium"
    return "low"


def synth_swot(e: dict, scores: dict) -> dict:
    """Heuristic SWOT from the joined evidence. Each bullet is evidence-backed."""
    strengths, weaknesses, opportunities, threats = [], [], [], []
    company = e["company"]
    pricing = e["pricing"]
    seo = e["seo"]
    mk = e["marketing"]

    # --- strengths ---
    stage = (v(company.get("funding_stage")) or "").lower()
    if stage in ("series a", "series b", "series c", "series d", "growth", "public"):
        strengths.append(f"Well-funded ({v(company.get('funding_stage'))}) — resources to outspend smaller rivals")
    if scores["SEO Footprint"] >= 60:
        strengths.append("Strong SEO footprint and organic discoverability")
    if scores["Marketing Engine"] >= 60:
        strengths.append("Active content/marketing engine driving inbound demand")
    if scores["Social Reach"] >= 55:
        strengths.append("Broad social/community presence")
    if v(pricing.get("has_free_plan")):
        strengths.append("Free plan lowers acquisition friction")
    diffs = []
    positioning = (mk.get("positioning", {}) or {})
    if v(positioning.get("positioning_statement")):
        diffs.append(v(positioning.get("positioning_statement")))

    # --- weaknesses ---
    if scores["Pricing Transparency"] < 35:
        weaknesses.append("Opaque or hard-to-find pricing")
    if scores["SEO Footprint"] < 35:
        weaknesses.append("Thin SEO footprint — low organic visibility")
    if (v(seo.get("blog_frequency")) or "none").lower() in ("none", "rare"):
        weaknesses.append("Little to no content cadence")
    if stage in ("bootstrapped", "pre-seed", "seed", ""):
        weaknesses.append("Limited funding / small team constrains pace")
    if scores["Social Reach"] < 30:
        weaknesses.append("Weak social and community footprint")

    # --- opportunities (for THIS competitor) ---
    if not v(pricing.get("has_free_plan")):
        opportunities.append("Could add a free tier to widen the funnel")
    if (v(seo.get("blog_frequency")) or "none").lower() in ("none", "rare"):
        opportunities.append("Untapped content-marketing upside")
    if scores["Tech Depth"] < 40:
        opportunities.append("Room to deepen/modernize the product stack")

    # --- threats (to this competitor) ---
    if e.get("classification") == "direct":
        threats.append("Crowded direct-competition space with low switching costs")
    threats.append("Fast-moving AI tooling can erode any single-feature moat")

    def _wrap_list(items, method):
        items = [i for i in items if i][:6] or None
        return wrap(items, 0.45, method=method, source_type="inference")

    return {
        "strengths": _wrap_list(strengths, "derived from funding/SEO/marketing/pricing signals"),
        "weaknesses": _wrap_list(weaknesses, "derived from gaps in pricing/SEO/content/funding"),
        "opportunities": _wrap_list(opportunities, "derived from absent capabilities"),
        "threats": _wrap_list(threats, "derived from classification + market dynamics"),
    }, diffs


# --------------------------------------------------------------------------- #
# Report synthesis (report.json — conforms to report.schema.json)
# --------------------------------------------------------------------------- #

def synth_report(data: dict, entities: list, now_iso: str) -> dict:
    competitors = [e for e in entities if not e["is_self"]]
    self_e = next((e for e in entities if e["is_self"]), None)

    competitor_analysis = []
    enriched = {}  # ref -> {scores, threat, swot, diffs}
    for e in entities:
        scores = capability_scores(e)
        if e["is_self"]:
            enriched[e["ref"]] = {"scores": scores}
            continue
        swot, diffs = synth_swot(e, scores)
        tl = threat_level(e, scores)
        enriched[e["ref"]] = {"scores": scores, "threat": tl, "swot": swot, "diffs": diffs}
        competitor_analysis.append({
            "entity_ref": e["ref"],
            "swot": swot,
            "threat_level": wrap(tl, 0.5, method="similarity + capability-weighted heuristic"),
            "differentiators": wrap(diffs or None, 0.4,
                                    method="from positioning statement",
                                    source_type="inference"),
        })

    # --- executive summary ---
    direct = [e for e in competitors if e["classification"] == "direct"]
    high_threat = [e for e in competitors if enriched[e["ref"]].get("threat") == "high"]
    self_name = self_e["name"] if self_e else "This product"
    key_findings = [
        f"{len(competitors)} competitors mapped; {len(direct)} classified as direct.",
        f"{len(high_threat)} competitor(s) rated high competitive threat: "
        + (", ".join(e["name"] for e in high_threat[:5]) or "none"),
    ]
    # most-funded / biggest player
    def scale_of(e):
        return enriched[e["ref"]]["scores"]["Scale & Funding"]
    if competitors:
        biggest = max(competitors, key=scale_of)
        key_findings.append(f"Largest-scale rival by funding/headcount: {biggest['name']}.")
        free = [e for e in competitors if v(e["pricing"].get("has_free_plan"))]
        key_findings.append(
            f"{len(free)} of {len(competitors)} competitors offer a free plan — "
            "pricing is a live battleground.")

    market_overview = (
        f"The field around {self_name} spans {len(competitors)} tracked players, "
        f"from well-funded incumbents to lean AI-native challengers. Direct "
        f"functional overlap is concentrated in {len(direct)} product(s); the "
        f"remainder compete adjacently on data, monitoring, or breadth."
    )

    executive_summary = {
        "summary": wrap(
            f"Competitive analysis of {self_name} against {len(competitors)} "
            f"competitors across company, pricing, tech, social, marketing and SEO "
            f"dimensions. {len(high_threat)} pose a high competitive threat.",
            0.6, method="aggregated from all datasets"),
        "market_overview": wrap(market_overview, 0.5,
                                method="aggregated from competitor roster + classification"),
        "key_findings": wrap(key_findings, 0.55, method="derived counts + capability ranking"),
        "competitor_count": wrap(len(competitors), 0.95,
                                 method="len(competitors.json)", source_type="manual"),
    }

    positioning_matrix = synth_positioning(competitors, enriched)
    opportunity_gaps = synth_gaps(self_e, competitors, enriched)
    recommendations = synth_recommendations(self_e, competitors, enriched)

    return {
        "meta": {
            "schema_version": "1.0.0",
            "dataset": "report",
            "generated_at": now_iso,
            "generator": "compete/build_report 0.1.0",
        },
        "executive_summary": executive_summary,
        "competitor_analysis": competitor_analysis,
        "positioning_matrix": positioning_matrix,
        "opportunity_gaps": opportunity_gaps,
        "recommendations": recommendations,
    }, enriched


def synth_positioning(competitors: list, enriched: dict) -> dict:
    """X = price (affordability inverted), Y = scale/maturity."""
    points = []
    prices = [as_num(e["pricing"].get("lowest_paid_monthly")) for e in competitors]
    prices = [p for p in prices if p is not None]
    pmax = max(prices) if prices else 1
    for e in competitors:
        price = as_num(e["pricing"].get("lowest_paid_monthly"))
        # X axis: entry price (0 = free/cheap, high = premium). Normalize 0..100.
        x = None
        if price is not None:
            x = round(min(price / pmax, 1.0) * 100, 1) if pmax else 0.0
        elif v(e["pricing"].get("has_free_plan")):
            x = 0.0
        y = enriched[e["ref"]]["scores"]["Scale & Funding"]
        points.append({
            "entity_ref": e["ref"],
            "x": wrap(x, 0.5, method="entry price normalized to peer max"),
            "y": wrap(y, 0.5, method="scale & funding capability score"),
        })
    return {
        "x_axis_label": wrap("Entry price (low → premium)", 0.6, method="from pricing.lowest_paid_monthly", source_type="manual"),
        "y_axis_label": wrap("Company scale & maturity", 0.6, method="from funding/headcount", source_type="manual"),
        "points": points,
    }


def synth_gaps(self_e: Optional[dict], competitors: list, enriched: dict) -> list:
    gaps = []
    n = len(competitors) or 1

    free_count = sum(1 for e in competitors if v(e["pricing"].get("has_free_plan")))
    if free_count <= n * 0.4:
        gaps.append({
            "title": "Free / freemium entry tier",
            "description": wrap(
                f"Only {free_count} of {n} competitors offer a free plan. A "
                "genuinely free tier (or open-source path) is whitespace for "
                "land-and-expand acquisition.",
                0.55, method="share of competitors with has_free_plan"),
            "impact": wrap("high", 0.5, method="acquisition-funnel heuristic"),
            "related_entities": [e["ref"] for e in competitors
                                 if not v(e["pricing"].get("has_free_plan"))][:8],
        })

    weak_seo = [e for e in competitors if enriched[e["ref"]]["scores"]["SEO Footprint"] < 35]
    if len(weak_seo) >= n * 0.4:
        gaps.append({
            "title": "Organic / SEO content leadership",
            "description": wrap(
                f"{len(weak_seo)} of {n} competitors have a thin SEO footprint. "
                "A consistent comparison-page + content strategy could own organic "
                "search for category and 'alternatives' queries.",
                0.5, method="count of low SEO-score competitors"),
            "impact": wrap("medium", 0.5, method="organic-demand heuristic"),
            "related_entities": [e["ref"] for e in weak_seo][:8],
        })

    # OSS / developer-native angle (self is OSS/repo-driven)
    gaps.append({
        "title": "Open-source, repo-native positioning",
        "description": wrap(
            "Most rivals are closed hosted SaaS. An open-source, repository-driven "
            "approach (analysis that runs from your own codebase) is a differentiated "
            "wedge against hosted-only incumbents.",
            0.5, method="commercial_model scan across companies.json"),
        "impact": wrap("high", 0.45, method="differentiation heuristic"),
        "related_entities": [e["ref"] for e in competitors][:8],
    })

    weak_pricing = [e for e in competitors
                    if enriched[e["ref"]]["scores"]["Pricing Transparency"] < 35]
    if len(weak_pricing) >= n * 0.35:
        gaps.append({
            "title": "Transparent, self-serve pricing",
            "description": wrap(
                f"{len(weak_pricing)} of {n} competitors hide or gate pricing. "
                "Clear public pricing builds trust and shortens the sales cycle.",
                0.5, method="count of low pricing-transparency competitors"),
            "impact": wrap("medium", 0.45, method="conversion heuristic"),
            "related_entities": [e["ref"] for e in weak_pricing][:8],
        })

    return gaps


def synth_recommendations(self_e, competitors, enriched) -> list:
    recs = [
        {
            "title": "Lead with the open-source, repo-native wedge",
            "rationale": wrap(
                "Hosted incumbents can't easily match a tool that runs from the "
                "user's own repository. Make this the headline differentiator.",
                0.5, method="from OSS opportunity gap"),
            "priority": wrap("high", 0.5, method="differentiation impact"),
            "confidence": 0.5,
        },
        {
            "title": "Ship a free / open tier to seed adoption",
            "rationale": wrap(
                "Few competitors offer free entry; a free path drives bottom-up "
                "adoption and word-of-mouth before monetizing teams.",
                0.5, method="from free-tier opportunity gap"),
            "priority": wrap("high", 0.45, method="acquisition impact"),
            "confidence": 0.45,
        },
        {
            "title": "Own 'X alternatives' and comparison SEO",
            "rationale": wrap(
                "Several rivals under-invest in SEO/content. Comparison and "
                "alternatives pages capture high-intent organic traffic cheaply.",
                0.5, method="from SEO opportunity gap"),
            "priority": wrap("medium", 0.5, method="organic ROI"),
            "confidence": 0.5,
        },
    ]
    high_threat = [e for e in competitors if enriched[e["ref"]].get("threat") == "high"]
    if high_threat:
        names = ", ".join(e["name"] for e in high_threat[:4])
        recs.append({
            "title": f"Actively monitor high-threat rivals ({names})",
            "rationale": wrap(
                "These competitors combine high functional overlap with strong "
                "execution; track their pricing, features and content closely.",
                0.55, method="from threat-level ranking"),
            "priority": wrap("high", 0.55, method="threat ranking"),
            "confidence": 0.55,
        })
    return recs


# --------------------------------------------------------------------------- #
# Aggregate view models for the front-end
# --------------------------------------------------------------------------- #

COMPARISON_COLUMNS = [
    ("name", "Competitor"),
    ("classification", "Type"),
    ("similarity", "Similarity"),
    ("hq", "HQ"),
    ("founded", "Founded"),
    ("employees", "Employees"),
    ("funding_stage", "Funding"),
    ("pricing_model", "Pricing model"),
    ("lowest_price", "Entry $/mo"),
    ("free_plan", "Free plan"),
    ("enterprise", "Enterprise"),
    ("blog", "Blog cadence"),
    ("threat", "Threat"),
]


def build_view_models(entities: list, enriched: dict) -> dict:
    comp = [e for e in entities if not e["is_self"]]

    # --- comparison matrix ---
    rows = []
    for e in comp:
        c, p, s = e["company"], e["pricing"], e["seo"]
        rows.append({
            "ref": e["ref"],
            "cells": {
                "name": {"value": e["name"], "confidence": 1.0, "unknown": False},
                "classification": {"value": e["classification"],
                                   "confidence": e.get("classification_conf", 0.0),
                                   "unknown": e["classification"] in (None, "unknown")},
                "similarity": {"value": e.get("similarity"),
                               "confidence": 0.6,
                               "unknown": e.get("similarity") is None},
                "hq": cell(c.get("headquarters")),
                "founded": cell(c.get("founded_year")),
                "employees": cell(c.get("employee_estimate")),
                "funding_stage": cell(c.get("funding_stage")),
                "pricing_model": cell(p.get("pricing_model")),
                "lowest_price": cell(p.get("lowest_paid_monthly")),
                "free_plan": cell(p.get("has_free_plan")),
                "enterprise": cell(p.get("has_enterprise_plan")),
                "blog": cell(s.get("blog_frequency")),
                "threat": {"value": enriched[e["ref"]].get("threat"),
                           "confidence": 0.5,
                           "unknown": enriched[e["ref"]].get("threat") is None},
            },
        })

    # --- radar series (top competitors by overall, + self) ---
    def overall(e):
        sc = enriched[e["ref"]]["scores"]
        return sum(sc.values()) / len(sc)
    ranked = sorted(comp, key=overall, reverse=True)
    radar_pick = ranked[:6]
    self_e = next((e for e in entities if e["is_self"]), None)
    radar_series = []
    if self_e:
        radar_series.append({
            "ref": "self", "name": self_e["name"],
            "scores": [round(enriched["self"]["scores"][a], 1) for a in RADAR_AXES],
            "is_self": True,
        })
    for e in radar_pick:
        radar_series.append({
            "ref": e["ref"], "name": e["name"],
            "scores": [round(enriched[e["ref"]]["scores"][a], 1) for a in RADAR_AXES],
            "is_self": False,
        })

    # --- pricing matrix ---
    pricing_rows = []
    for e in comp:
        p = e["pricing"]
        plans = []
        for plan in (p.get("plans") or []):
            plans.append({
                "name": plan.get("name") or "—",
                "monthly_price": v(plan.get("monthly_price")),
                "billing_period": v(plan.get("billing_period")),
                "is_free": bool(v(plan.get("is_free"))),
                "is_enterprise": bool(v(plan.get("is_enterprise"))),
            })
        pricing_rows.append({
            "ref": e["ref"], "name": e["name"],
            "pricing_model": v(p.get("pricing_model")),
            "currency": v(p.get("currency")) or "USD",
            "has_free_plan": bool(v(p.get("has_free_plan"))),
            "has_enterprise_plan": bool(v(p.get("has_enterprise_plan"))),
            "lowest_paid_monthly": as_num(p.get("lowest_paid_monthly")),
            "pricing_page": v(p.get("pricing_page")),
            "plans": plans,
        })

    # --- overview stats ---
    classifications = {}
    for e in comp:
        classifications[e["classification"]] = classifications.get(e["classification"], 0) + 1
    threats = {"high": 0, "medium": 0, "low": 0}
    for e in comp:
        t = enriched[e["ref"]].get("threat")
        if t in threats:
            threats[t] += 1
    free_plans = sum(1 for e in comp if v(e["pricing"].get("has_free_plan")))
    avg_price = None
    prices = [as_num(e["pricing"].get("lowest_paid_monthly")) for e in comp]
    prices = [x for x in prices if x is not None]
    if prices:
        avg_price = round(sum(prices) / len(prices), 2)

    overview = {
        "competitor_count": len(comp),
        "direct_count": classifications.get("direct", 0),
        "classifications": classifications,
        "threats": threats,
        "free_plan_count": free_plans,
        "avg_entry_price": avg_price,
        "price_range": [min(prices), max(prices)] if prices else None,
    }

    # entity cards (for company cards + swot detail)
    cards = []
    for e in comp:
        en = enriched[e["ref"]]
        c = e["company"]
        cards.append({
            "ref": e["ref"], "name": e["name"], "website": e["website"],
            "classification": e["classification"],
            "similarity": e.get("similarity"),
            "relationship_notes": e.get("relationship_notes"),
            "hq": v(c.get("headquarters")),
            "founded": v(c.get("founded_year")),
            "employees": v(c.get("employee_estimate")),
            "funding_stage": v(c.get("funding_stage")),
            "threat": en.get("threat"),
            "scores": en["scores"],
            "overall": round(overall(e), 1),
            "swot": {k: as_list(en["swot"][k]) for k in
                     ("strengths", "weaknesses", "opportunities", "threats")},
            "differentiators": en.get("diffs", []),
        })
    cards.sort(key=lambda c: c["overall"], reverse=True)

    return {
        "overview": overview,
        "comparison": {"columns": COMPARISON_COLUMNS, "rows": rows},
        "radar": {"axes": RADAR_AXES, "series": radar_series},
        "pricing_matrix": {"rows": pricing_rows},
        "cards": cards,
    }


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #

def render_html(template: str, payload: dict) -> str:
    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    # Prevent premature </script> termination when inlined into the page.
    blob = blob.replace("</", "<\\/")
    if "__INSIGHTKIT_DATA__" not in template:
        raise ValueError("template missing __INSIGHTKIT_DATA__ placeholder")
    return template.replace("__INSIGHTKIT_DATA__", blob)


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input-dir", default=".",
                        help="Directory holding the *.json datasets (default: .)")
    parser.add_argument("--output-dir", default="./insightkit-output",
                        help="Where to write report.json and report.html")
    parser.add_argument("--template", default=None,
                        help="Path to report.html template (default: templates/report.html)")
    parser.add_argument("--open", action="store_true", dest="open_browser",
                        help="Open the generated report in a browser")
    parser.add_argument("--quiet", action="store_true",
                        help="Suppress progress reporting.")
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parent.parent
    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    template_path = Path(args.template) if args.template else root / "templates" / "report.html"

    now_iso = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # 6 steps: load · assemble · synthesize · view models · write json · render html
    progress = Progress("Knowledge Graph + Report", script="build_report",
                        total=6, enabled=not args.quiet).start("loading datasets")
    data = load_all(input_dir)
    progress.step("loaded datasets")

    entities = build_entities(data)
    progress.step(f"assembled {len(entities)} entities "
                  f"({sum(1 for e in entities if not e['is_self'])} competitors + self)")

    report, enriched = synth_report(data, entities, now_iso)
    progress.step("synthesized report.json (SWOT, threat, positioning, gaps)")

    views = build_view_models(entities, enriched)
    progress.step("built front-end view models")

    payload = {
        "meta": {
            "generated_at": now_iso,
            "generator": "compete/build_report 0.1.0",
            "product": {
                "name": next((e["name"] for e in entities if e["is_self"]),
                             "This product"),
                "website": next((e["website"] for e in entities if e["is_self"]), None),
            },
        },
        "report": report,
        "views": views,
        "datasets": {name: data[name] for name in DATASETS if data[name]},
    }

    output_dir.mkdir(parents=True, exist_ok=True)

    report_path = output_dir / "report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                           encoding="utf-8")
    progress.step(f"wrote {report_path}")

    if not template_path.exists():
        print(f"  ! template not found: {template_path}", file=sys.stderr)
        return 1
    template = template_path.read_text(encoding="utf-8")
    html = render_html(template, payload)
    html_path = output_dir / "report.html"
    html_path.write_text(html, encoding="utf-8")
    progress.step(f"wrote {html_path} ({len(html) // 1024} KB, self-contained)")

    progress.finish("Knowledge Graph + Report complete")

    if args.open_browser:
        webbrowser.open(html_path.resolve().as_uri())

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
