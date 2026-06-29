#!/usr/bin/env python3
"""
collect_intelligence.py — Intelligence Collection step of the compete pipeline.

Step 3 takes the discovered roster (``competitors.json``, whose ``id``s are the
join keys) and, for every competitor, gathers a multi-dimensional intelligence
profile: **company**, **pricing**, **techstack**, **social/online presence**,
**marketing**, (v1-light) **SEO**, and a **features & services** matrix scored
against a fixed canonical taxonomy. Each dimension is its own dataset file so a
single slow or failed collector degrades one file, not the whole graph
(``references/data-schema.md`` §2).

Like Step 2, the actual finding is web research — an agent runs ``WebSearch`` /
``WebFetch`` per the plan and per ``references/intelligence-dimensions.md`` — and
this script owns the two halves that *can* be deterministic and contract-safe:

  plan   Read competitors.json and emit a structured, per-competitor research
         plan: the WebSearch queries and WebFetch hints for each of the seven
         dimensions, plus the fixed feature/service taxonomy and the findings
         input format. No network access.

  build  Take the agent's collected **findings** (keyed by ``entity_ref``) and
         normalize them into the seven schema-conformant datasets — wrapping every
         raw value in the confidence envelope, applying the
         ``unknown: true ⇒ value null, confidence 0`` invariant, and emitting an
         explicit record for *every* competitor in the roster (missing findings
         degrade to all-``unknown``, never a fabricated value).

The messy judgment (what's true about each competitor, how sure) stays with the
researcher; the contract (field shapes, the unknown invariant, the join keys, one
record per competitor) stays with the code — the same split Steps 1 and 2 used.

Usage:
    # 1. derive the per-competitor research plan from the roster
    python scripts/collect_intelligence.py plan --competitors competitors.json

    # 2. after collecting findings via WebSearch/WebFetch into findings.json:
    python scripts/collect_intelligence.py build \
        --competitors competitors.json --findings findings.json --validate
    # writes ./companies.json ./pricing.json ./techstack.json ./social.json
    #        ./marketing.json ./seo.json ./features.json  (each conforming to schemas/)

Findings may be piped in (``--findings -``). With no findings the build still
succeeds, writing seven valid all-``unknown`` skeletons keyed to the roster — the
graceful "collection yielded nothing" fallback.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

try:  # sibling module in scripts/ — shared progress reporter
    from _progress import Progress
except ImportError:  # pragma: no cover - allow import from another cwd
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _progress import Progress

SCHEMA_VERSION = "1.2.0"
DEFAULT_GENERATOR = "compete/collect_intelligence 0.1.0"

_SOURCE_TYPES = {
    "repository", "official_website", "official_docs", "pricing_page",
    "github_api", "package_registry", "social_profile", "press_release",
    "third_party_db", "search_result", "inference", "manual", "unknown",
}


# ---------------------------------------------------------------------------
# Confidence-wrapped field helpers (shared contract shape — see
# references/data-schema.md). Kept local so this script is runnable on its own.
# ---------------------------------------------------------------------------

def _today() -> str:
    return date.today().isoformat()


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def field(
    value: Any,
    confidence: float,
    *,
    source: Optional[str] = None,
    source_type: str = "search_result",
    method: Optional[str] = None,
    notes: Optional[str] = None,
) -> dict:
    """A determined confidence-wrapped field.

    A ``None`` or empty-string value degrades to the ``unknown`` form, enforcing
    the invariant ``unknown: true ⇒ value null, confidence 0``. An empty *list*
    is preserved (a confident "none", distinct from "not determined").
    """
    if value is None or value == "":
        return unknown(notes=notes)
    return {
        "value": value,
        "confidence": round(_clamp(float(confidence)), 2),
        "unknown": False,
        "source": source,
        "provenance": {
            "source": source,
            "source_type": source_type if source_type in _SOURCE_TYPES else "search_result",
            "as_of": _today(),
            "method": method,
        },
        "notes": notes,
    }


def unknown(notes: Optional[str] = None) -> dict:
    """The canonical 'not determined' field. ``provenance`` is omitted entirely
    (the contract types it as an object; an unknown field has no evidence trail).
    """
    return {"value": None, "confidence": 0, "unknown": True, "source": None, "notes": notes}


# ---------------------------------------------------------------------------
# Dataset field specs — a tiny declarative DSL the wrapper walks.
#
#   "<type>"               a leaf field, wrapped per its type
#   ("group", {subspec})   a fixed nested object; every leaf emitted (dense)
#   ("sparse", {subspec})  a nested object whose children are emitted only when
#                          the findings actually contain them (e.g. the social
#                          platform map — don't write 11 empty accounts each row)
#
# Leaf types mirror common.schema.json#/$defs/*Field. Only METRIC carries the
# extra is_estimate / range_low / range_high / unit keys.
# ---------------------------------------------------------------------------

STRING, TEXT, URL, NUMBER, INTEGER, BOOL, DATE, ENUM, ARRAY, METRIC = (
    "string", "text", "url", "number", "integer", "boolean", "date",
    "enum", "stringarray", "metric",
)

_METRIC_KEYS = ("is_estimate", "range_low", "range_high", "unit")

# Default source_type per dataset — most intelligence in each domain comes from a
# characteristic evidence kind, used only when a finding omits source_type.
_DEFAULT_SRC = {
    "company": "third_party_db",
    "pricing": "pricing_page",
    "techstack": "inference",
    "social": "social_profile",
    "marketing": "official_website",
    "seo": "inference",
    "features": "official_website",
}

COMPANY_SPEC = {
    "company_name": STRING, "website": URL, "headquarters": STRING,
    "founded_year": INTEGER, "founders": ARRAY, "employee_estimate": METRIC,
    "funding_stage": ENUM, "total_funding": METRIC, "investors": ARRAY,
    "estimated_arr": METRIC, "commercial_model": ENUM,
}

PRICING_SPEC = {  # plans handled separately (array of objects)
    "pricing_page": URL, "pricing_model": ENUM, "currency": STRING,
    "has_free_plan": BOOL, "has_enterprise_plan": BOOL, "lowest_paid_monthly": METRIC,
    "estimated_mrr": METRIC, "estimated_users": METRIC,
}
PLAN_SPEC = {  # each plan also carries a required bare-string "name"
    "monthly_price": METRIC, "billing_period": ENUM, "is_free": BOOL,
    "is_enterprise": BOOL, "key_features": ARRAY, "limits": ARRAY,
}

TECHSTACK_SPEC = {
    **{k: ARRAY for k in (
        "frontend", "backend", "framework", "database", "hosting",
        "cloud_provider", "cdn", "deployment", "authentication", "analytics",
        "payments", "monitoring", "cms", "llm_provider", "inference_provider")},
    "product_surface": ("group", {
        "ai_models": ARRAY, "api_availability": BOOL, "sdks": ARRAY,
        "mobile_apps": BOOL, "browser_extension": BOOL, "integrations": ARRAY,
    }),
}

_SOCIAL_ACCOUNT = {
    "url": URL, "handle": STRING, "followers": METRIC, "posting_frequency": ENUM,
    "content_categories": ARRAY, "engagement_style": STRING,
}
_SOCIAL_PLATFORMS = ("x", "linkedin", "github", "youtube", "discord", "slack",
                     "reddit", "facebook", "instagram", "tiktok", "product_hunt")
SOCIAL_SPEC = {
    "website": ("group", {k: URL for k in (
        "homepage", "pricing", "documentation", "blog", "changelog",
        "careers", "api_docs")}),
    "social": ("sparse", {p: ("group", _SOCIAL_ACCOUNT) for p in _SOCIAL_PLATFORMS}),
    "developer_channels": ("group", {
        "github_repository": URL, "github_stars": INTEGER, "github_forks": INTEGER,
        "github_issues": INTEGER, "github_releases": INTEGER, "npm_package": URL,
        "pypi_package": URL, "docker_hub": URL, "vscode_marketplace": URL,
    }),
}

MARKETING_SPEC = {
    "positioning": ("group", {
        "positioning_statement": TEXT, "hero_headline": STRING,
        "value_proposition": TEXT, "primary_cta": STRING,
        "pricing_strategy": STRING, "launch_style": STRING,
    }),
    "programs": ("group", {
        "newsletter": BOOL, "webinar": BOOL, "referral_program": BOOL,
        "affiliate_program": BOOL, "community": BOOL, "case_studies": BOOL,
        "customer_stories": BOOL, "email_capture_strategy": STRING,
    }),
    "content": ("group", {
        "blog_topics": ARRAY, "uses_ai_generated_content": BOOL,
        "documentation_quality": ENUM, "tutorials": BOOL, "videos": BOOL,
        "podcasts": BOOL, "release_cadence": ENUM,
    }),
    "sales_motion": ("group", {
        "free_trial": BOOL, "freemium": BOOL, "self_service": BOOL,
        "enterprise_contact": BOOL, "annual_discount": BOOL, "demo_booking": BOOL,
        "has_sales_team": BOOL, "partner_program": BOOL, "marketplace_presence": BOOL,
    }),
    "hiring": ("group", {
        "careers_page": URL, "open_positions": INTEGER, "engineering_hiring": BOOL,
        "ai_hiring": BOOL, "remote_policy": ENUM, "tech_hints_from_jobs": ARRAY,
    }),
}

SEO_SPEC = {
    "meta_title": STRING, "meta_description": TEXT, "has_sitemap": BOOL,
    "sitemap_url": URL, "has_robots_txt": BOOL, "indexed_pages": METRIC,
    "keyword_focus": ARRAY, "blog_frequency": ENUM, "landing_pages": INTEGER,
    "documentation_quality": ENUM, "internal_linking": ENUM,
}

# (dimension key in findings, dataset name, array property, top-level spec)
DATASETS = [
    ("company", "companies", "companies", COMPANY_SPEC),
    ("pricing", "pricing", "pricing", PRICING_SPEC),
    ("techstack", "techstack", "techstack", TECHSTACK_SPEC),
    ("social", "social", "presence", SOCIAL_SPEC),
    ("marketing", "marketing", "marketing", MARKETING_SPEC),
    ("seo", "seo", "seo", SEO_SPEC),
]

# ---------------------------------------------------------------------------
# Features & Services dimension — a FIXED canonical taxonomy (features.schema.json).
# Unlike the six spec-driven datasets above, features.json is a single per-competitor
# `matrix[]` of confidence-wrapped capability cells, every competitor scored against
# the same axes so the report renders a true side-by-side grid. The two taxonomies
# are closed sets; their union is the only allowed matrix axes. Order = features
# first, then services (the order cells are emitted in each matrix).
# ---------------------------------------------------------------------------

FEATURE_KEYS = [
    "competitor_tracking", "battlecards", "win_loss_analysis",
    "market_trend_analysis", "news_and_alerts", "pricing_intelligence",
    "seo_keyword_tracking", "social_listening", "website_change_monitoring",
    "ai_insights_summarization", "dashboards_and_reporting", "data_export",
    "public_api", "third_party_integrations", "browser_extension", "mobile_app",
]
SERVICE_KEYS = [
    "managed_research", "analyst_support", "onboarding_and_training",
    "custom_report_services", "consulting_advisory", "dedicated_account_manager",
    "premium_sla_support", "data_enrichment_service",
]
# Ordered union (axes) + key→category lookup. category MUST match the taxonomy a
# key belongs to (the schema enforces this with a oneOf).
CAPABILITY_KEYS = FEATURE_KEYS + SERVICE_KEYS
_CAPABILITY_CATEGORY = {
    **{k: "feature" for k in FEATURE_KEYS},
    **{k: "service" for k in SERVICE_KEYS},
}
_CAPABILITY_STATUS = {"has", "partial", "none"}  # the determined status values
# (dataset name, array property) for every emitted dataset — the six spec-driven
# ones plus features. Drives validation, writing, and the coverage summary.
FEATURES_DATASET = ("features", "features")
ALL_DATASETS = [(d[1], d[2]) for d in DATASETS] + [FEATURES_DATASET]


# ---------------------------------------------------------------------------
# Build: wrap raw findings into confidence-wrapped, schema-valid datasets
# ---------------------------------------------------------------------------

def wrap_leaf(ftype: str, raw: Any, default_src_type: str, default_conf: float = 0.6) -> dict:
    """Wrap one raw finding into the contract envelope.

    Accepts either a bare scalar/list (assigned a default confidence) or a dict
    ``{value, confidence?, source?, source_type?, method?, notes?, …metric keys}``.
    A missing value, an explicit ``unknown: true``, or a null value all collapse
    to the canonical unknown form.
    """
    if raw is None:
        return unknown()
    if not isinstance(raw, dict):
        raw = {"value": raw}
    val = raw.get("value")
    if raw.get("unknown") or val is None or val == "":
        return unknown(notes=raw.get("notes"))
    # Array fields tolerate a bare scalar finding (a single detected technology,
    # founder, investor, …) by lifting it into a one-element list.
    if ftype == ARRAY and not isinstance(val, list):
        val = [val]
    out = field(
        val, raw.get("confidence", default_conf),
        source=raw.get("source"),
        source_type=raw.get("source_type") or default_src_type,
        method=raw.get("method"),
        notes=raw.get("notes"),
    )
    if ftype == METRIC:
        for k in _METRIC_KEYS:
            if raw.get(k) is not None:
                out[k] = raw[k]
    return out


def _maybe_unwrap(node: Any) -> Any:
    """Defensively unwrap a *container* (an object group or array) that a
    collector mistakenly wrapped in a field envelope, e.g. ``{"social": {"value":
    {…platforms…}, "confidence": .6}}`` or ``{"plans": {"value": [...]}}``. A real
    group never carries the envelope's bookkeeping keys at its own level, so this
    is unambiguous."""
    if (isinstance(node, dict) and "value" in node
            and any(k in node for k in ("confidence", "unknown", "source",
                                        "source_type", "provenance", "method"))):
        return node["value"]
    return node


def wrap_group(spec: dict, raw: Optional[dict], default_src_type: str, *, sparse: bool = False) -> dict:
    """Walk a (possibly nested) spec, wrapping each leaf. ``sparse`` parents emit
    only the children present in ``raw`` (used for the social platform map)."""
    raw = raw if isinstance(raw, dict) else {}
    out: dict = {}
    for key, ftype in spec.items():
        sub = raw.get(key)
        if isinstance(ftype, tuple):
            kind, inner = ftype
            sub = _maybe_unwrap(sub)
            if sparse and not sub:
                continue
            grp = wrap_group(inner, sub, default_src_type, sparse=(kind == "sparse"))
            if sparse and not grp:
                continue
            out[key] = grp
        else:
            if sparse and sub is None:
                continue
            out[key] = wrap_leaf(ftype, sub, default_src_type)
    return out


def _build_plans(raw_plans: Any, default_src_type: str) -> list[dict]:
    """Normalize the pricing ``plans`` array. Each plan keeps a required bare
    ``name`` string and confidence-wraps its other tier fields; nameless plans
    are skipped (the schema requires a name)."""
    raw_plans = _maybe_unwrap(raw_plans)
    if not isinstance(raw_plans, list):
        return []
    plans: list[dict] = []
    for rp in raw_plans:
        if not isinstance(rp, dict):
            continue
        name = (rp.get("name") or "").strip() if isinstance(rp.get("name"), str) else ""
        if not name:
            continue
        plan = {"name": name}
        plan.update(wrap_group(PLAN_SPEC, rp, default_src_type))
        plans.append(plan)
    return plans


def _derive_pricing_summary(rec: dict, plans: list[dict]) -> None:
    """Fill the ``has_free_plan`` / ``lowest_paid_monthly`` summary fields from the
    ``plans`` array when the researcher supplied tiers but omitted the (redundant)
    top-level summaries. These two fields drive the report's free-tier and
    entry-price views, so a populated ``plans`` list with absent summaries would
    otherwise read as "no free tier / no public pricing". Only fills fields that
    are still ``unknown`` — an explicit finding always wins. Transparent: the
    derived fields carry a ``method`` note pointing back at ``plans``."""
    def pv(plan, key):  # unwrap a plan leaf to its value (None when unknown/absent)
        node = plan.get(key)
        return node.get("value") if isinstance(node, dict) and not node.get("unknown") else None

    if rec.get("has_free_plan", {}).get("unknown", True):
        has_free = any(pv(p, "is_free") is True or pv(p, "monthly_price") == 0 for p in plans)
        rec["has_free_plan"] = field(
            has_free, 0.7, source_type="pricing_page",
            method="derived from plans[] (a plan with is_free or $0/mo)")

    if rec.get("lowest_paid_monthly", {}).get("unknown", True):
        paid = [pv(p, "monthly_price") for p in plans]
        paid = [float(x) for x in paid if isinstance(x, (int, float)) and x > 0]
        if paid:
            low = round(min(paid), 2)
            rec["lowest_paid_monthly"] = field(
                low, 0.7, source_type="pricing_page",
                method="derived from plans[] (min monthly_price > 0)")
            rec["lowest_paid_monthly"]["is_estimate"] = False


def _envelope(dataset: str, array_key: str, records: list[dict], generator: str) -> dict:
    return {
        "meta": {
            "schema_version": SCHEMA_VERSION,
            "dataset": dataset,
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "generator": generator,
        },
        array_key: records,
    }


def _unrecognized_keys(spec: dict, raw: Any, prefix: str = "") -> list[str]:
    """Dotted paths of keys present in ``raw`` that map to no field in ``spec``.
    Such keys are silently dropped by ``wrap_group`` (degrading to ``unknown``),
    which masks a typo or wrong nesting in the researcher's findings — so we
    surface them as a warning. Recurses into group/sparse sub-specs; leaf value
    dicts ({value, confidence, …}) are not descended into."""
    if not isinstance(raw, dict):
        return []
    extra: list[str] = []
    for key, val in raw.items():
        ftype = spec.get(key)
        if ftype is None:
            extra.append(f"{prefix}{key}")
        elif isinstance(ftype, tuple):
            extra.extend(_unrecognized_keys(ftype[1], _maybe_unwrap(val), f"{prefix}{key}."))
    return extra


def build_dataset(dim: str, dataset: str, array_key: str, spec: dict,
                  refs: list[str], findings: dict, generator: str) -> tuple[dict, list[str]]:
    """Assemble one dataset: one record per ref, wrapping that ref's findings for
    this dimension (absent findings ⇒ all-``unknown`` record). Also returns the
    list of unrecognized finding-key warnings encountered."""
    src_type = _DEFAULT_SRC.get(dim, "search_result")
    records = []
    warnings: list[str] = []
    # "plans" is consumed specially for pricing, so it's a recognized extra there.
    extra_ok = {"plans"} if dataset == "pricing" else set()
    for ref in refs:
        raw = (findings.get(ref) or {}).get(dim) or {}
        for path in _unrecognized_keys(spec, raw):
            if path not in extra_ok:
                warnings.append(f"{ref}/{dim}: unrecognized field '{path}' — ignored")
        rec = {"entity_ref": ref}
        rec.update(wrap_group(spec, raw, src_type))
        if dataset == "pricing":
            plans = _build_plans(raw.get("plans"), src_type)
            if plans:
                rec["plans"] = plans
                _derive_pricing_summary(rec, plans)
        records.append(rec)
    return _envelope(dataset, array_key, records, generator), warnings


def _features_cell(key: str, category: str, raw: Any, default_src_type: str) -> dict:
    """Build one matrix cell from a raw finding, reusing ``field()``/``unknown()``.

    The finding carries a ``status`` (``has`` | ``partial`` | ``none``) which drives
    the tri-state ``value``: ``has``/``partial`` ⇒ ``True``, ``none`` ⇒ ``False``. A
    bare scalar finding is tolerated — a status string (``"has"``) or a boolean
    (``true`` ⇒ ``has``, ``false`` ⇒ ``none``). A missing/null status, an explicit
    ``unknown: true``, or an unparsable value all collapse to the canonical unknown
    cell (``value null, confidence 0, status null``) — the schema's invariant.
    """
    if isinstance(raw, str):
        s = raw.strip().lower()
        raw = {"status": s} if s in _CAPABILITY_STATUS else {"value": raw}
    elif isinstance(raw, bool):
        raw = {"value": raw}
    elif not isinstance(raw, dict):
        raw = {}

    def _unknown_cell(notes: Optional[str]) -> dict:
        cell = {"key": key, "category": category, "status": None}
        cell.update(unknown(notes=notes))
        return cell

    if raw.get("unknown"):
        return _unknown_cell(raw.get("notes"))

    status = raw.get("status")
    status = status.strip().lower() if isinstance(status, str) else None
    val = raw.get("value")
    if status in ("has", "partial"):
        val = True
    elif status == "none":
        val = False
    elif isinstance(val, bool):  # status omitted — infer it from a boolean value
        status = "has" if val else "none"
    else:
        return _unknown_cell(raw.get("notes"))

    cell = {"key": key, "category": category, "status": status}
    cell.update(field(
        val, raw.get("confidence", 0.6),
        source=raw.get("source"),
        source_type=raw.get("source_type") or default_src_type,
        method=raw.get("method"),
        notes=raw.get("notes"),
    ))
    return cell


def build_features(refs: list[str], findings: dict, generator: str) -> tuple[dict, list[str]]:
    """Assemble features.json: one record per ref, each holding a full ``matrix[]``
    spanning the fixed feature + service taxonomy (every key emitted, unknown where
    a finding is absent). Returns (dataset, unrecognized-key warnings)."""
    src_type = _DEFAULT_SRC.get("features", "official_website")
    records = []
    warnings: list[str] = []
    for ref in refs:
        raw = (findings.get(ref) or {}).get("features") or {}
        if isinstance(raw, dict):
            for k in raw:
                if k not in _CAPABILITY_CATEGORY:
                    warnings.append(
                        f"{ref}/features: unrecognized capability '{k}' — ignored "
                        "(not in the fixed feature/service taxonomy)")
        matrix = [
            _features_cell(key, _CAPABILITY_CATEGORY[key],
                           raw.get(key) if isinstance(raw, dict) else None, src_type)
            for key in CAPABILITY_KEYS
        ]
        records.append({"entity_ref": ref, "matrix": matrix})
    return _envelope("features", "features", records, generator), warnings


def build_all(competitors: dict, findings: dict, generator: str,
              progress: Optional[Progress] = None) -> tuple[dict, list[str]]:
    """Build all seven datasets (six spec-driven + features). Returns
    ({dataset_name: dataset_object}, warnings)."""
    refs = [c["id"] for c in competitors.get("competitors", []) if c.get("id")]
    if "self" in findings and "self" not in refs:
        refs.insert(0, "self")
    out = {}
    warnings: list[str] = []
    for dim, dataset, array_key, spec in DATASETS:
        out[dataset], warns = build_dataset(dim, dataset, array_key, spec, refs, findings, generator)
        warnings.extend(warns)
        if progress is not None:
            n, known, total = _coverage(out[dataset], array_key)
            progress.step(f"built {dataset}.json ({n} records, {known}/{total} fields known)")
    out["features"], feat_warns = build_features(refs, findings, generator)
    warnings.extend(feat_warns)
    if progress is not None:
        n, known, total = _coverage(out["features"], "features")
        progress.step(f"built features.json ({n} records, {known}/{total} fields known)")
    return out, warnings


# ---------------------------------------------------------------------------
# Plan: derive the per-competitor research plan from the roster
# ---------------------------------------------------------------------------

def fv(node: Any) -> Any:
    """Unwrap a confidence-wrapped field to its value, or None when unknown."""
    if not isinstance(node, dict):
        return None
    if node.get("unknown"):
        return None
    return node.get("value")


def _domain(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    from urllib.parse import urlparse
    u = url if url.startswith(("http://", "https://")) else "https://" + url
    host = (urlparse(u).netloc or "").lower()
    return host[4:] if host.startswith("www.") else (host or None)


def _competitor_plan(comp: dict) -> dict:
    """Per-competitor, per-dimension WebSearch/WebFetch task list."""
    name = comp.get("name") or comp.get("id")
    site = fv(comp.get("website"))
    host = _domain(site)
    base = site.rstrip("/") if site else None
    is_repo = bool(host == "github.com")

    def web(*qs):  # WebSearch queries
        return [{"via": "WebSearch", "q": q} for q in qs]

    def fetch(url, why):  # WebFetch hint
        return {"via": "WebFetch", "url": url, "why": why}

    dims: dict[str, list] = {}

    dims["company"] = web(
        f"{name} company headquarters founded year",
        f"{name} funding round investors crunchbase",
        f"{name} number of employees linkedin",
        f"{name} revenue ARR annual recurring",
    )
    if base:
        dims["company"].append(fetch(base + "/about", "company facts: HQ, founders, story"))

    dims["pricing"] = web(
        f"{name} pricing plans cost per month",
        f"{name} estimated MRR monthly recurring revenue",
        f"{name} number of customers paying users",
    )
    if base and not is_repo:
        dims["pricing"].insert(0, fetch(base + "/pricing",
                                        "published tiers, prices, free/enterprise, currency"))

    dims["techstack"] = web(
        f"{name} tech stack built with",
        f"{name} stackshare technologies",
    )
    if base:
        dims["techstack"].append(fetch(base,
            "sniff homepage markup/headers for frameworks, analytics, CDN, hosting"))
    if is_repo:
        dims["techstack"].append(fetch(base, "repo languages, manifests, and dependencies"))

    dims["social"] = web(f"{name} official twitter X linkedin github youtube")
    if base:
        dims["social"].append(fetch(base, "footer/header links to social + dev channels"))
    if is_repo:
        dims["social"].append(fetch(base, "GitHub stars / forks / issues / releases"))

    dims["marketing"] = web(
        f"{name} newsletter blog case studies",
        f"{name} free trial demo affiliate program",
    )
    if base:
        dims["marketing"].append(fetch(base,
            "hero headline, primary CTA, value prop, launch style"))
        dims["marketing"].append(fetch(base + "/careers", "open roles + hiring signals"))

    dims["seo"] = []
    if base:
        dims["seo"].append(fetch(base + "/sitemap.xml", "sitemap presence + indexed-page scale"))
        dims["seo"].append(fetch(base + "/robots.txt", "robots.txt presence + directives"))
        dims["seo"].append(fetch(base, "<title> + meta description + keyword focus"))
    dims["seo"] += web(f"{name} blog frequency how often publish")

    # Features & Services — map evidence onto the FIXED capability_taxonomy (below).
    # Mark each as has | partial | none; only assert `none` when you've verified an
    # absence, else leave the key out (it normalizes to unknown).
    dims["features"] = web(
        f"{name} features list capabilities",
        f"{name} integrations API export mobile app",
        f"{name} managed research analyst services support plans",
    )
    if base and not is_repo:
        dims["features"].insert(0, fetch(base + "/features",
            "feature list → map onto the feature/service taxonomy (has/partial/none)"))
        dims["features"].append(fetch(base + "/pricing",
            "per-plan feature gating + professional/managed services tiers"))
    if base:
        dims["features"].append(fetch(base, "homepage capability claims + API/extension/mobile signals"))

    return {"entity_ref": comp.get("id"), "name": name, "website": site, "dimensions": dims}


def build_plan(competitors: dict, progress: Optional[Progress] = None) -> dict:
    comps = competitors.get("competitors", [])
    if progress is not None:
        progress.set_total(len(comps))
    per_competitor = []
    for c in comps:
        per_competitor.append(_competitor_plan(c))
        if progress is not None:
            progress.step(f"planned {c.get('name') or c.get('id')}")
    return {
        "roster_size": len(comps),
        "dimensions": [d[0] for d in DATASETS] + ["features"],
        "capability_taxonomy": {
            "note": "Fixed, closed axes for the features dimension — score EVERY "
                    "competitor (and self) against these exact keys. category is "
                    "implied by the list a key appears in.",
            "feature": FEATURE_KEYS,
            "service": SERVICE_KEYS,
        },
        "per_competitor": per_competitor,
        "input_format": _INPUT_FORMAT,
        "instructions": (
            "For each competitor, run its dimension tasks with WebSearch/WebFetch. "
            "Record findings keyed by entity_ref in findings.json (see input_format "
            "and references/intelligence-dimensions.md). Every value is a dict "
            "{value, confidence, source, source_type, method}; omit a field (or set "
            "unknown:true) when you cannot verify it — never guess. Then: "
            "python scripts/collect_intelligence.py build --competitors competitors.json "
            "--findings findings.json --validate"
        ),
    }


_INPUT_FORMAT = {
    "shape": "{ <entity_ref>: { company:{}, pricing:{}, techstack:{}, social:{}, "
             "marketing:{}, seo:{}, features:{} } }",
    "field_value": "Each leaf is either a bare scalar or "
                   "{value, confidence:0..1, source, source_type, method, notes}. "
                   "Metric fields (employee_estimate, total_funding, estimated_arr, "
                   "estimated_mrr, estimated_users, followers, monthly_price, "
                   "indexed_pages) also accept is_estimate/range_low/range_high/unit. "
                   "For soft figures like estimated_mrr/estimated_users set "
                   "is_estimate:true with a range_low/range_high band and low "
                   "confidence; prefer unknown:true over guessing.",
    "source_types": sorted(_SOURCE_TYPES),
    "pricing.plans": "list of { name (required), monthly_price, billing_period, "
                     "is_free, is_enterprise, key_features, limits }",
    "social.social": "object keyed by platform (" + ", ".join(_SOCIAL_PLATFORMS) +
                     "); each value is { url, handle, followers, posting_frequency, "
                     "content_categories, engagement_style }",
    "features": "object keyed by a capability key from capability_taxonomy; each "
                "value is a status string ('has'|'partial'|'none') or "
                "{ status, confidence, source, source_type, method, notes }. status "
                "drives the cell value (has/partial=true, none=false). Score the "
                "whole taxonomy; OMIT a key (or set unknown:true) when you can't "
                "verify it — only assert 'none' for a verified absence. Keys outside "
                "the taxonomy are ignored.",
    "omission": "An omitted field, a null value, or unknown:true all normalize to "
                "the canonical unknown envelope (value null, confidence 0).",
}


# ---------------------------------------------------------------------------
# Validation & reporting
# ---------------------------------------------------------------------------

def validate(instance: dict, schema_name: str, schemas_dir: Path) -> Optional[str]:
    """Validate ``instance`` against ``schema_name``; return an error string or None."""
    try:
        from jsonschema import Draft202012Validator
        from referencing import Registry, Resource
    except ModuleNotFoundError:
        return "SKIP: jsonschema/referencing not installed"

    registry = Registry()
    for sf in schemas_dir.glob("*.json"):
        doc = json.loads(sf.read_text(encoding="utf-8"))
        resource = Resource.from_contents(doc)
        registry = registry.with_resource(uri=sf.name, resource=resource)
        if "$id" in doc:
            registry = registry.with_resource(uri=doc["$id"], resource=resource)

    schema = json.loads((schemas_dir / schema_name).read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, registry=registry)
    errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.path))
    if errors:
        return "; ".join(f"{list(e.path)}: {e.message}" for e in errors[:5])
    return None


def _coverage(dataset: dict, array_key: str) -> tuple[int, int, int]:
    """(records, known_leaves, total_leaves) across a dataset, counting only
    leaf confidence-wrapped fields (those carrying the `unknown` flag)."""
    known = total = 0

    def walk(node: Any) -> None:
        nonlocal known, total
        if isinstance(node, dict):
            if "unknown" in node and "value" in node and "confidence" in node:
                total += 1
                if not node.get("unknown"):
                    known += 1
                return
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    records = dataset.get(array_key, [])
    for rec in records:
        walk(rec)
    return len(records), known, total


def summarize(built: dict) -> str:
    lines = ["Intelligence Collection — per-dataset coverage (known / total leaf fields):"]
    for dataset, array_key in ALL_DATASETS:
        n, known, total = _coverage(built[dataset], array_key)
        pct = (100 * known / total) if total else 0.0
        lines.append(f"  {dataset:<11} {n:>2} records  {known:>4}/{total:<4} known ({pct:4.0f}%)")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def load_json(path: str) -> Any:
    if path == "-":
        return json.loads(sys.stdin.read())
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_plan = sub.add_parser("plan", help="Emit the per-competitor WebSearch/WebFetch research plan.")
    p_plan.add_argument("--competitors", default="competitors.json",
                        help="Path to competitors.json (default: ./competitors.json).")
    p_plan.add_argument("--output", default=None, help="Write the plan JSON here (default: stdout).")

    p_build = sub.add_parser("build", help="Normalize collected findings into the six datasets.")
    p_build.add_argument("--competitors", default="competitors.json",
                         help="Path to competitors.json (default: ./competitors.json).")
    p_build.add_argument("--findings", default=None,
                         help="Path to findings JSON keyed by entity_ref ('-' for stdin). "
                              "Omit for the graceful all-unknown skeleton.")
    p_build.add_argument("--output-dir", default=".", help="Directory to write the datasets (default: .).")
    p_build.add_argument("--generator", default=DEFAULT_GENERATOR, help="meta.generator string.")
    p_build.add_argument("--validate", action="store_true", help="Validate each dataset against schemas/.")
    p_build.add_argument("--quiet", action="store_true", help="Suppress the summary.")

    args = parser.parse_args(argv)
    schemas_dir = Path(__file__).resolve().parent.parent / "schemas"

    if args.cmd == "plan":
        try:
            competitors = load_json(args.competitors)
        except FileNotFoundError:
            print(f"error: --competitors {args.competitors} not found (run discover_competitors.py first)",
                  file=sys.stderr)
            return 2
        progress = Progress("Intelligence Collection — plan", script="collect_intelligence",
                            enabled=not getattr(args, "quiet", False)).start(
            "deriving per-competitor research plan")
        plan = build_plan(competitors, progress)
        progress.finish(f"plan ready for {plan['roster_size']} competitor(s)")
        text = json.dumps(plan, indent=2, ensure_ascii=False)
        if args.output:
            Path(args.output).write_text(text + "\n", encoding="utf-8")
            print(f"wrote plan to {args.output} ({plan['roster_size']} competitors)", file=sys.stderr)
        else:
            print(text)
        return 0

    # build
    try:
        competitors = load_json(args.competitors)
    except FileNotFoundError:
        print(f"error: --competitors {args.competitors} not found (run discover_competitors.py first)",
              file=sys.stderr)
        return 2

    findings: dict = {}
    if args.findings:
        try:
            raw = load_json(args.findings)
        except FileNotFoundError:
            print(f"error: --findings {args.findings} not found", file=sys.stderr)
            return 2
        # Accept either {entity_ref: {...}} or {"findings": {entity_ref: {...}}}.
        findings = raw.get("findings", raw) if isinstance(raw, dict) else {}
        if not isinstance(findings, dict):
            print('error: findings must be a JSON object keyed by entity_ref', file=sys.stderr)
            return 2

    if not findings:
        print("warning: no findings supplied — writing all-unknown skeletons keyed to the "
              "roster (graceful 'collection yielded nothing' fallback).", file=sys.stderr)

    progress = Progress("Intelligence Collection — build", script="collect_intelligence",
                        total=len(ALL_DATASETS), enabled=not args.quiet).start(
        f"normalizing findings into {len(ALL_DATASETS)} datasets")
    built, key_warnings = build_all(competitors, findings, args.generator, progress)
    if key_warnings:
        shown = key_warnings[:15]
        print(f"warning: {len(key_warnings)} unrecognized finding field(s) ignored "
              "(typo or wrong nesting? — see references/intelligence-dimensions.md):",
              file=sys.stderr)
        for w in shown:
            print(f"  - {w}", file=sys.stderr)
        if len(key_warnings) > len(shown):
            print(f"  … and {len(key_warnings) - len(shown)} more", file=sys.stderr)

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.validate and schemas_dir.is_dir():
        for dataset, _key in ALL_DATASETS:
            err = validate(built[dataset], f"{dataset}.schema.json", schemas_dir)
            if err and not err.startswith("SKIP"):
                print(f"error: {dataset}.json failed schema validation: {err}", file=sys.stderr)
                return 1
            if not args.quiet:
                print(f"validation: {dataset}.json {'passed' if not err else err}", file=sys.stderr)
        progress.log(f"schema validation done ({len(ALL_DATASETS)} datasets)")

    for dataset, _key in ALL_DATASETS:
        path = out_dir / f"{dataset}.json"
        path.write_text(json.dumps(built[dataset], indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    progress.finish(f"Intelligence Collection complete: {len(ALL_DATASETS)} datasets written")

    if not args.quiet:
        print(summarize(built), file=sys.stderr)
        print(f"\nwrote {', '.join(d[0] + '.json' for d in ALL_DATASETS)} to {out_dir}/", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
