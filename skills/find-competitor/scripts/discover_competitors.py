#!/usr/bin/env python3
"""
discover_competitors.py — Competitor Discovery step of the find-competitor pipeline.

Step 2 turns the own product (``product.json``, ``entity_ref: "self"``) into a
classified roster of competitors (``competitors.json``). Unlike Step 1, discovery
is *not* deterministic: the actual finding happens through web research
(``WebSearch`` / ``WebFetch``) driven by an agent following
``references/competitor-discovery.md``. This script owns the two halves of that
workflow that *can* be made deterministic and contract-safe:

  plan   Read product.json and emit a structured **search plan** — the queries
         and fetch hints the agent should run, derived from the product's
         identity, features, and customers. No network access.

  build  Take the agent's collected **candidates** (a flat JSON list of raw
         findings) and normalize them into a schema-conformant competitors.json:
         slugged join-key ``id``s, deduplication across name/website, confidence
         wrapping, classification validation, and the ``unknown`` fallbacks the
         InsightKit data contract requires (``references/data-schema.md``).

Both halves keep the messy judgment (which products are competitors, how close)
with the researcher, and keep the contract (field shapes, the
``unknown: true ⇒ value: null, confidence: 0`` invariant, valid slugs, no dupes)
with the code — mirroring how Step 1 split heuristics from the schema envelope.

Usage:
    # 1. derive the research plan from the own product
    python scripts/discover_competitors.py plan --product product.json

    # 2. after collecting candidates via WebSearch/WebFetch into candidates.json:
    python scripts/discover_competitors.py build \
        --product product.json --candidates candidates.json --validate
    # writes ./competitors.json (conforming to schemas/competitors.schema.json)

Candidates may also be piped in (``--candidates -``). With no candidates the
build still succeeds, writing an empty-but-valid roster and warning on stderr —
the graceful "search yielded nothing" fallback.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import urlparse

SCHEMA_VERSION = "1.0.0"
DEFAULT_GENERATOR = "find-competitor/discover_competitors 0.1.0"

# The classification taxonomy from competitors.schema.json. 'unknown' is the
# explicit fallback and is expressed via the `unknown: true` flag, not the value.
CLASSIFICATIONS = {
    "direct",
    "indirect",
    "enterprise",
    "open_source",
    "emerging_startup",
    "adjacent",
}

# Tokens that carry no discriminating meaning when seeding search queries or
# slugs from free-text product copy.
STOPWORDS = {
    "the", "a", "an", "and", "or", "for", "to", "of", "in", "on", "with",
    "your", "our", "their", "its", "is", "are", "be", "that", "this", "it",
    "into", "then", "from", "by", "as", "at", "you", "we", "they", "not",
    "just", "also", "can", "will", "build", "builds", "building", "using",
    "use", "uses", "used", "tool", "tools", "app", "application", "platform",
    "software", "solution", "product", "products", "service", "services",
    # Non-topical verbs / framing words that crowd out the real subject.
    "analyze", "analyzes", "analyzing", "identify", "identifies", "discover",
    "discovers", "turn", "turns", "current", "complete", "spanning", "across",
    "help", "helps", "make", "makes", "get", "gets", "current", "new",
}


# ---------------------------------------------------------------------------
# Confidence-wrapped field helpers (shared contract shape — see
# references/data-schema.md). Kept locally so this script stays import-free of
# analyze_repo.py and runnable on its own.
# ---------------------------------------------------------------------------

def _today() -> str:
    return date.today().isoformat()


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
    the invariant ``unknown: true ⇒ value is null and confidence == 0``. An empty
    *list* is preserved (a confident "none", distinct from "not determined").
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
    return {
        "value": None,
        "confidence": 0,
        "unknown": True,
        "source": None,
        "notes": notes,
    }


_SOURCE_TYPES = {
    "repository", "official_website", "official_docs", "pricing_page",
    "github_api", "package_registry", "social_profile", "press_release",
    "third_party_db", "search_result", "inference", "manual", "unknown",
}


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


# ---------------------------------------------------------------------------
# product.json readers (tolerant of the unknown form on every field)
# ---------------------------------------------------------------------------

def fv(node: Optional[dict]) -> Any:
    """Unwrap a confidence-wrapped field to its value, or None when unknown."""
    if not isinstance(node, dict):
        return None
    if node.get("unknown"):
        return None
    return node.get("value")


def load_json(path: str) -> Any:
    if path == "-":
        return json.loads(sys.stdin.read())
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Plan: derive the search query plan from the own product
# ---------------------------------------------------------------------------

def _content_tokens(text: str, exclude: set[str]) -> list[tuple[int, str]]:
    """(original-position, word) for content words, dropping stop/name tokens.

    A hyphenated token (e.g. the product's own ``find-competitor`` name) is
    dropped when *every* alnum part of it is excluded, so the name never leaks
    into the topic even though it survives the word regex as one token.
    """
    out: list[tuple[int, str]] = []
    for i, w in enumerate(re.findall(r"[A-Za-z][A-Za-z0-9+#.-]{2,}", text.lower())):
        if w in STOPWORDS or w in exclude or len(w) < 3:
            continue
        parts = [p for p in re.split(r"[^a-z0-9]+", w) if p]
        if parts and all(p in exclude or p in STOPWORDS for p in parts):
            continue
        out.append((i, w))
    return out


def _rank_phrases(text: Optional[str], exclude: set[str], limit: int) -> list[str]:
    """Surface the most topical phrase(s) from product copy.

    Prefers **bigrams of adjacent content words** — noun phrases like "competitive
    intelligence" or "market research" describe a product's category far better
    than isolated high-frequency words. Frequency is the primary key (repetition
    is signal), original order breaks ties so the phrase still reads naturally.
    Falls back to top unigrams when no adjacent content pair exists.
    """
    if not text:
        return []
    toks = _content_tokens(text, exclude)
    bi_count: dict[str, int] = {}
    bi_order: dict[str, int] = {}
    for (i1, w1), (i2, w2) in zip(toks, toks[1:]):
        if i2 == i1 + 1:  # adjacent in the original text
            bg = f"{w1} {w2}"
            bi_count[bg] = bi_count.get(bg, 0) + 1
            bi_order.setdefault(bg, i1)
    if bi_count:
        best = sorted(bi_count, key=lambda b: (-bi_count[b], bi_order[b]))
        return best[:limit]
    uni_count: dict[str, int] = {}
    uni_order: dict[str, int] = {}
    for i, w in toks:
        uni_count[w] = uni_count.get(w, 0) + 1
        uni_order.setdefault(w, i)
    return sorted(uni_count, key=lambda w: (-uni_count[w], uni_order[w]))[:limit]


def derive_topic(product: dict) -> tuple[str, float]:
    """Best one-line description of *what the product is*, for query seeding.

    Prefers an explicit category; else a short phrase-like tagline; else the
    most-repeated domain keywords mined across all the product copy. Returns
    (topic, confidence-in-topic). Degrades to the bare name as a last resort.
    """
    ident = product.get("identity", {})
    feats = product.get("features", {})
    cat = fv(ident.get("category"))
    if cat:
        return cat, 0.8

    name = fv(ident.get("name")) or ""
    # The product's own name tokens are not a useful topic seed.
    name_tokens = {t for t in re.findall(r"[a-z0-9]+", name.lower()) if t}

    tagline = fv(ident.get("tagline"))
    if tagline and len(tagline.split()) <= 7:  # phrase-like, not a clipped sentence
        return tagline.rstrip(".,"), 0.5

    corpus = " ".join(filter(None, [
        tagline, fv(ident.get("description")),
        " ".join(fv(feats.get("core_features")) or []),
    ]))
    phrases = _rank_phrases(corpus, exclude=name_tokens, limit=1)
    if phrases:
        return phrases[0], 0.4
    return (name or "software"), 0.2


def build_plan(product: dict) -> dict:
    """Construct the structured research plan an agent executes with WebSearch /
    WebFetch. Pure function of product.json — degrades gracefully as fields are
    unknown (an early-stage repo with only a name still yields useful seeds).
    """
    ident = product.get("identity", {})
    feats = product.get("features", {})
    cust = product.get("customers", {})

    name = fv(ident.get("name")) or "this product"
    topic, topic_conf = derive_topic(product)
    core = fv(feats.get("core_features")) or []
    ai_caps = fv(feats.get("ai_capabilities")) or []
    users = fv(cust.get("target_users")) or []

    # (query, intent, which categories this query tends to surface)
    queries: list[dict] = []

    def add(q: str, intent: str, targets: list[str]) -> None:
        q = re.sub(r"\s+", " ", q).strip()
        if q and not any(existing["q"].lower() == q.lower() for existing in queries):
            queries.append({"q": q, "intent": intent, "target_categories": targets})

    # Direct / named-product angle.
    add(f"{name} alternatives", "direct competitors and 'alternatives to' listicles",
        ["direct", "indirect", "emerging_startup"])
    add(f"alternatives to {name}", "direct competitors", ["direct", "indirect"])
    add(f"{name} vs", "head-to-head comparisons naming rivals", ["direct"])

    # Category / topic angle.
    add(f"{topic} competitors", "the core competitive set", ["direct", "indirect", "enterprise"])
    add(f"best {topic} tools 2026", "current market leaders and review roundups",
        ["enterprise", "direct"])
    add(f"open source {topic}", "open-source alternatives", ["open_source"])
    add(f"AI {topic} startup", "emerging AI-native entrants", ["emerging_startup"])
    add(f"{topic} enterprise platform", "incumbent enterprise vendors", ["enterprise"])

    # Feature angle — each differentiating capability has its own competitive set.
    for f in core[:4]:
        add(f"{f} {topic} tool", f"competitors strong on '{f}'", ["direct", "adjacent"])
    if ai_caps:
        add(f"{' '.join(ai_caps[:2])} {topic}", "AI-capability overlap",
            ["direct", "emerging_startup"])

    # Audience angle — same job for the same buyer is a real competitor.
    for u in users[:2]:
        add(f"{topic} for {u}", f"products targeting {u}", ["direct", "indirect", "adjacent"])

    # Structured aggregators worth fetching directly (high recall per request).
    topic_slug = slugify(topic) or "software"
    fetch_hints = [
        {"url": f"https://www.g2.com/search?query={topic.replace(' ', '+')}",
         "via": "WebFetch", "why": "G2 category + each product's 'Competitors/Alternatives' tab"},
        {"url": f"https://github.com/topics/{topic_slug}",
         "via": "WebFetch", "why": "open-source projects tagged with the topic"},
        {"url": "https://www.producthunt.com/search?q=" + topic.replace(" ", "%20"),
         "via": "WebFetch", "why": "emerging startups and recent launches"},
        {"note": "For each promising 'alternatives to …' article a search returns, "
                 "WebFetch it and extract the full vendor list with one-line positioning."},
    ]

    return {
        "product": name,
        "topic": topic,
        "topic_confidence": round(topic_conf, 2),
        "queries": queries,
        "fetch_hints": fetch_hints,
        "classification_rubric": _RUBRIC,
        "instructions": (
            "Run the queries with WebSearch and the hints with WebFetch. For each "
            "distinct product found, record a candidate object (see "
            "references/competitor-discovery.md → 'Candidate format'); classify it "
            "with the rubric; score similarity to the own product 0..1. Then: "
            "python scripts/discover_competitors.py build --product product.json "
            "--candidates candidates.json --validate"
        ),
    }


_RUBRIC = {
    "direct": "Same core job for the same buyer; a user would choose it instead of us.",
    "indirect": "Solves the same problem a different way, or covers one dimension of it.",
    "enterprise": "Large incumbent, sales-led, 'contact us' pricing, broad suite.",
    "open_source": "Source-available / self-hostable project (often a GitHub repo).",
    "emerging_startup": "Young (≲3 yrs), AI-native or niche, still establishing itself.",
    "adjacent": "Neighboring category with partial overlap; a complement more than a rival.",
}


# ---------------------------------------------------------------------------
# Build: normalize collected candidates → competitors.json
# ---------------------------------------------------------------------------

def slugify(name: str) -> str:
    """Produce an entityRef-safe slug: ``^[a-z0-9][a-z0-9-]{0,63}$``."""
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    s = re.sub(r"-{2,}", "-", s)
    s = re.sub(r"^[^a-z0-9]+", "", s)
    return s[:64].rstrip("-")


def _domain(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    u = url if re.match(r"^https?://", url, re.I) else "https://" + url
    host = (urlparse(u).netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host or None


def _norm_url(url: Optional[str]) -> Optional[str]:
    if not url or not isinstance(url, str):
        return None
    url = url.strip()
    if not url:
        return None
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    return url


def _dedupe_key(raw: dict) -> str:
    """Identity for collision: prefer the registrable domain, else the
    alnum-folded name. GitHub repos key on owner/repo so two repos on the same
    host stay distinct."""
    url = _norm_url(raw.get("website"))
    host = _domain(url)
    if host == "github.com":
        path = urlparse(url).path.strip("/").lower()
        parts = path.split("/")[:2]
        if parts and parts[0]:
            return "github.com/" + "/".join(parts)
    if host:
        return host
    return re.sub(r"[^a-z0-9]+", "", (raw.get("name") or "").lower()) or "?"


def _merge(primary: dict, other: dict) -> dict:
    """Fold a duplicate ``other`` into ``primary`` (the higher-confidence find).

    Unions aliases, keeps the most-confident classification, the highest
    similarity, the richest notes, and concatenates discovery sources so the
    provenance of the merge is auditable.
    """
    p, o = dict(primary), other
    # Aliases: union of both names + both alias lists, minus the kept name.
    aliases = set(p.get("aliases") or []) | set(o.get("aliases") or [])
    if o.get("name") and o["name"] != p.get("name"):
        aliases.add(o["name"])
    p["aliases"] = sorted(a for a in aliases if a and a != p.get("name"))

    # Prefer a present website.
    if not p.get("website") and o.get("website"):
        p["website"] = o["website"]

    # Classification: keep whichever record was more confident about it.
    if _conf(o, "confidence") > _conf(p, "confidence"):
        p["classification"], p["confidence"] = o.get("classification"), o.get("confidence")

    # Similarity: keep the max (and its confidence).
    if _num(o.get("similarity_score")) is not None and (
        _num(p.get("similarity_score")) is None
        or _num(o["similarity_score"]) > _num(p["similarity_score"])
    ):
        p["similarity_score"] = o["similarity_score"]
        p["similarity_confidence"] = o.get("similarity_confidence")

    # Notes: keep the longer.
    if len(str(o.get("relationship_notes") or "")) > len(str(p.get("relationship_notes") or "")):
        p["relationship_notes"] = o.get("relationship_notes")

    # Discovery sources: concatenate distinct.
    srcs = [s for s in [p.get("discovery_source"), o.get("discovery_source")] if s]
    seen, joined = set(), []
    for s in srcs:
        for part in re.split(r"\s*;\s*", s):
            if part and part not in seen:
                seen.add(part)
                joined.append(part)
    if joined:
        p["discovery_source"] = "; ".join(joined)
    return p


def _conf(raw: dict, key: str, default: float = 0.5) -> float:
    v = raw.get(key)
    try:
        return _clamp(float(v))
    except (TypeError, ValueError):
        return default


def _num(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def dedupe(candidates: list[dict]) -> tuple[list[dict], int]:
    """Collapse duplicate candidates. Returns (merged, num_collapsed)."""
    # Process in descending classification-confidence so the primary of each
    # group is the best-evidenced record.
    ordered = sorted(candidates, key=lambda c: _conf(c, "confidence"), reverse=True)
    groups: dict[str, dict] = {}
    collapsed = 0
    for raw in ordered:
        key = _dedupe_key(raw)
        if key in groups:
            groups[key] = _merge(groups[key], raw)
            collapsed += 1
        else:
            groups[key] = dict(raw)
    return list(groups.values()), collapsed


def normalize_candidate(raw: dict, used_ids: set[str]) -> dict:
    """Turn one raw candidate dict into a schema-valid competitor object."""
    name = (raw.get("name") or "").strip()
    if not name:
        raise ValueError("candidate is missing required 'name'")

    # Stable, unique join-key slug.
    base = slugify(name) or "competitor"
    cid, n = base, 2
    while cid in used_ids:
        cid = f"{base[:60]}-{n}"
        n += 1
    used_ids.add(cid)

    website = _norm_url(raw.get("website"))
    src = raw.get("source") or website or raw.get("discovery_source")
    src_type = raw.get("source_type") or ("official_website" if website else "search_result")

    out: dict = {"id": cid, "name": name}

    aliases = [a for a in (raw.get("aliases") or []) if a and a != name]
    if aliases:
        out["aliases"] = sorted(set(aliases))

    # website (urlField)
    out["website"] = (
        field(website, _conf(raw, "website_confidence", 0.85 if website else 0.0),
              source=website, source_type="official_website",
              method="official site / search result")
        if website else unknown("No official website found.")
    )

    # classification (enumField) — invalid/missing → explicit unknown fallback.
    cls = raw.get("classification")
    if cls in CLASSIFICATIONS:
        out["classification"] = field(
            cls, _conf(raw, "confidence"),
            source=src, source_type=src_type,
            method="classified per competitor-discovery rubric",
            notes=raw.get("classification_notes"),
        )
    elif cls in (None, "", "unknown"):
        out["classification"] = unknown("Classification not determined from available evidence.")
    else:
        out["classification"] = unknown(f"Unrecognized classification {cls!r}; left unknown.")

    # similarity_score (numberField) — clamp to [0,1].
    sim = _num(raw.get("similarity_score"))
    if sim is not None:
        out["similarity_score"] = field(
            round(_clamp(sim), 2),
            _conf(raw, "similarity_confidence", _conf(raw, "confidence")),
            source=src, source_type="inference",
            method="overlap estimate vs. own product (identity/features/customers)",
        )
    else:
        out["similarity_score"] = unknown("Similarity to own product not estimated.")

    # relationship_notes (textField)
    out["relationship_notes"] = (
        field(raw["relationship_notes"], _conf(raw, "confidence"),
              source=src, source_type=src_type, method="analyst note")
        if raw.get("relationship_notes") else unknown()
    )

    # discovery_source (stringField)
    out["discovery_source"] = (
        field(raw["discovery_source"], 0.9, source=src, source_type=src_type,
              method="recorded at discovery time")
        if raw.get("discovery_source") else unknown("Discovery path not recorded.")
    )
    return out


def build(product: dict, candidates: list[dict], generator: str) -> tuple[dict, dict]:
    """Assemble competitors.json. Returns (dataset, stats)."""
    deduped, collapsed = dedupe(candidates)
    # Rank by similarity (desc) so the executive dashboard's default order is
    # the most-relevant competitors first; unknown similarity sorts last.
    deduped.sort(key=lambda c: _num(c.get("similarity_score")) or -1.0, reverse=True)

    used_ids: set[str] = set()
    competitors = [normalize_candidate(c, used_ids) for c in deduped]

    dataset = {
        "meta": {
            "schema_version": SCHEMA_VERSION,
            "dataset": "competitors",
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "generator": generator,
        },
        "competitors": competitors,
    }
    stats = {"input": len(candidates), "collapsed": collapsed, "output": len(competitors)}
    return dataset, stats


# ---------------------------------------------------------------------------
# Validation & reporting
# ---------------------------------------------------------------------------

def validate(instance: dict, schemas_dir: Path) -> Optional[str]:
    """Validate against competitors.schema.json; return an error string or None."""
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

    schema = json.loads((schemas_dir / "competitors.schema.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, registry=registry)
    errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.path))
    if errors:
        return "; ".join(f"{list(e.path)}: {e.message}" for e in errors[:5])
    return None


def summarize(dataset: dict, stats: dict) -> str:
    comps = dataset["competitors"]
    by_class: dict[str, int] = {}
    for c in comps:
        cls = c["classification"]
        key = "unknown" if cls.get("unknown") else cls.get("value")
        by_class[key] = by_class.get(key, 0) + 1
    lines = [
        f"Competitor Discovery: {stats['output']} competitor(s) "
        f"from {stats['input']} candidate(s) "
        f"({stats['collapsed']} duplicate(s) merged).",
    ]
    if by_class:
        lines.append("  by classification: " + ", ".join(
            f"{k}={v}" for k, v in sorted(by_class.items(), key=lambda kv: -kv[1])))
    for c in comps[:12]:
        cls = c["classification"]
        cls_s = "unknown" if cls.get("unknown") else cls["value"]
        sim = c["similarity_score"]
        sim_s = "—" if sim.get("unknown") else f"{sim['value']:.2f}"
        lines.append(f"  [{cls_s:<16}] {c['name']:<22} sim={sim_s}  ({c['id']})")
    if len(comps) > 12:
        lines.append(f"  … and {len(comps) - 12} more")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_plan = sub.add_parser("plan", help="Emit the WebSearch/WebFetch research plan from product.json.")
    p_plan.add_argument("--product", default="product.json", help="Path to product.json (default: ./product.json).")
    p_plan.add_argument("--output", default=None, help="Write the plan JSON here (default: stdout).")

    p_build = sub.add_parser("build", help="Normalize collected candidates into competitors.json.")
    p_build.add_argument("--product", default="product.json", help="Path to product.json (default: ./product.json).")
    p_build.add_argument("--candidates", default=None,
                         help="Path to the candidates JSON list ('-' for stdin). Omit/empty for the graceful no-results fallback.")
    p_build.add_argument("--output", default="competitors.json", help="Output path (default: ./competitors.json).")
    p_build.add_argument("--generator", default=DEFAULT_GENERATOR, help="meta.generator string.")
    p_build.add_argument("--validate", action="store_true", help="Validate against schemas/ before writing.")
    p_build.add_argument("--quiet", action="store_true", help="Suppress the summary.")

    args = parser.parse_args(argv)
    schemas_dir = Path(__file__).resolve().parent.parent / "schemas"

    if args.cmd == "plan":
        try:
            product = load_json(args.product)
        except FileNotFoundError:
            print(f"error: --product {args.product} not found (run analyze_repo.py first)", file=sys.stderr)
            return 2
        plan = build_plan(product)
        text = json.dumps(plan, indent=2, ensure_ascii=False)
        if args.output:
            Path(args.output).write_text(text + "\n", encoding="utf-8")
            print(f"wrote plan to {args.output} ({len(plan['queries'])} queries)", file=sys.stderr)
        else:
            print(text)
        return 0

    # build
    try:
        product = load_json(args.product)
    except FileNotFoundError:
        print(f"error: --product {args.product} not found (run analyze_repo.py first)", file=sys.stderr)
        return 2

    candidates: list[dict] = []
    if args.candidates:
        try:
            raw = load_json(args.candidates)
        except FileNotFoundError:
            print(f"error: --candidates {args.candidates} not found", file=sys.stderr)
            return 2
        # Accept either a bare list or {"candidates": [...]}.
        candidates = raw.get("candidates", []) if isinstance(raw, dict) else raw
        if not isinstance(candidates, list):
            print("error: candidates must be a JSON list (or {\"candidates\": [...]})", file=sys.stderr)
            return 2

    if not candidates:
        print("warning: no candidates supplied — writing an empty-but-valid roster "
              "(graceful 'search yielded nothing' fallback).", file=sys.stderr)

    try:
        dataset, stats = build(product, candidates, args.generator)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if args.validate and schemas_dir.is_dir():
        err = validate(dataset, schemas_dir)
        if err and not err.startswith("SKIP"):
            print(f"error: output failed schema validation: {err}", file=sys.stderr)
            return 1
        if not args.quiet:
            print(f"validation: {'passed' if not err else err}", file=sys.stderr)

    Path(args.output).write_text(json.dumps(dataset, indent=2, ensure_ascii=False) + "\n",
                                 encoding="utf-8")
    if not args.quiet:
        print(summarize(dataset, stats), file=sys.stderr)
        print(f"\nwrote {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
