#!/usr/bin/env python3
"""
analyze_repo.py — Product Intelligence step of the find-competitor pipeline.

Scans a local repository (README, package manifests, config files, and source)
to extract the analyzed product's identity, features, tech stack signals, and
target customers, then writes `product.json` conforming to
`schemas/product.schema.json`.

This is a *deterministic heuristic* extractor: it only reads files that already
exist in the repo. Everything it cannot establish from evidence is emitted as
the `unknown` form mandated by the InsightKit data contract
(`references/data-schema.md`): `unknown: true` ⇒ `value: null` and
`confidence: 0`. Fields are always present, never omitted, so downstream phases
branch only on the `unknown` flag.

Usage:
    python scripts/analyze_repo.py [--repo PATH] [--output PATH]
                                   [--generator NAME] [--validate]

By default `--repo` is the current working directory and the output is written
to `<repo>/product.json`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

try:  # Python 3.11+
    import tomllib  # type: ignore
except ModuleNotFoundError:  # pragma: no cover - older interpreters
    tomllib = None  # type: ignore

SCHEMA_VERSION = "1.0.0"
DEFAULT_GENERATOR = "find-competitor/analyze_repo 0.1.0"

# Directories never worth walking for source-level signals.
IGNORE_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "bower_components", "vendor",
    "dist", "build", "out", "target", ".next", ".nuxt", ".svelte-kit",
    "venv", ".venv", "env", "__pycache__", ".mypy_cache", ".pytest_cache",
    ".idea", ".vscode", "coverage", ".cache", "tmp", ".terraform",
}

# ---------------------------------------------------------------------------
# Dependency → signal maps. Keys are matched case-insensitively against the
# union of declared dependency names across every manifest in the repo.
# ---------------------------------------------------------------------------

AI_DEPS = {
    "openai": "OpenAI",
    "anthropic": "Anthropic Claude",
    "@anthropic-ai/sdk": "Anthropic Claude",
    "langchain": "LangChain",
    "langchain-core": "LangChain",
    "langgraph": "LangGraph",
    "llama-index": "LlamaIndex",
    "llamaindex": "LlamaIndex",
    "transformers": "HuggingFace Transformers",
    "huggingface_hub": "HuggingFace Hub",
    "sentence-transformers": "Sentence Transformers",
    "torch": "PyTorch",
    "tensorflow": "TensorFlow",
    "cohere": "Cohere",
    "google-generativeai": "Google Gemini",
    "@google/generative-ai": "Google Gemini",
    "mistralai": "Mistral",
    "groq": "Groq",
    "ollama": "Ollama",
    "replicate": "Replicate",
    "ai": "Vercel AI SDK",
    "@ai-sdk/openai": "Vercel AI SDK",
    "pinecone-client": "Pinecone (vector DB)",
    "pinecone": "Pinecone (vector DB)",
    "chromadb": "Chroma (vector DB)",
    "weaviate-client": "Weaviate (vector DB)",
    "qdrant-client": "Qdrant (vector DB)",
    "faiss-cpu": "FAISS (vector search)",
    "tiktoken": "Token handling (tiktoken)",
    "instructor": "Structured LLM output (instructor)",
}

API_FRAMEWORK_DEPS = {
    "express": "REST API (Express)",
    "fastify": "REST API (Fastify)",
    "koa": "REST API (Koa)",
    "@nestjs/core": "REST API (NestJS)",
    "hapi": "REST API (hapi)",
    "fastapi": "REST API (FastAPI)",
    "flask": "REST API (Flask)",
    "django": "REST API (Django)",
    "djangorestframework": "REST API (Django REST Framework)",
    "starlette": "ASGI API (Starlette)",
    "sanic": "REST API (Sanic)",
    "gin": "REST API (Gin)",
    "github.com/gin-gonic/gin": "REST API (Gin)",
    "echo": "REST API (Echo)",
    "fiber": "REST API (Fiber)",
    "graphql": "GraphQL API",
    "apollo-server": "GraphQL API (Apollo)",
    "@apollo/server": "GraphQL API (Apollo)",
    "strawberry-graphql": "GraphQL API (Strawberry)",
    "graphene": "GraphQL API (Graphene)",
    "grpc": "gRPC API",
    "@grpc/grpc-js": "gRPC API",
    "grpcio": "gRPC API",
}

INTEGRATION_DEPS = {
    "stripe": "Stripe",
    "@stripe/stripe-js": "Stripe",
    "twilio": "Twilio",
    "@slack/web-api": "Slack",
    "slack_sdk": "Slack",
    "slack-sdk": "Slack",
    "@octokit/rest": "GitHub",
    "octokit": "GitHub",
    "pygithub": "GitHub",
    "aws-sdk": "AWS",
    "@aws-sdk/client-s3": "AWS S3",
    "boto3": "AWS",
    "google-cloud-storage": "Google Cloud",
    "firebase": "Firebase",
    "firebase-admin": "Firebase",
    "@supabase/supabase-js": "Supabase",
    "supabase": "Supabase",
    "sendgrid": "SendGrid",
    "@sendgrid/mail": "SendGrid",
    "algoliasearch": "Algolia",
    "sentry-sdk": "Sentry",
    "@sentry/node": "Sentry",
    "redis": "Redis",
    "ioredis": "Redis",
    "pg": "PostgreSQL",
    "psycopg2": "PostgreSQL",
    "psycopg2-binary": "PostgreSQL",
    "mysql2": "MySQL",
    "mongoose": "MongoDB",
    "pymongo": "MongoDB",
    "prisma": "Prisma",
    "@prisma/client": "Prisma",
    "sqlalchemy": "SQLAlchemy",
}

# Dependency → platform/runtime hint (web/desktop/mobile).
PLATFORM_DEPS = {
    "react": "Web (React)",
    "react-dom": "Web (React)",
    "next": "Web (Next.js)",
    "vue": "Web (Vue)",
    "nuxt": "Web (Nuxt)",
    "svelte": "Web (Svelte)",
    "@angular/core": "Web (Angular)",
    "solid-js": "Web (SolidJS)",
    "react-native": "Mobile (React Native)",
    "expo": "Mobile (Expo)",
    "electron": "Desktop (Electron)",
    "tauri": "Desktop (Tauri)",
    "@tauri-apps/api": "Desktop (Tauri)",
}


# ---------------------------------------------------------------------------
# Confidence-wrapped field constructors (enforce the data-contract invariant)
# ---------------------------------------------------------------------------

def _today() -> str:
    return date.today().isoformat()


def field(
    value: Any,
    confidence: float,
    *,
    source: Optional[str] = None,
    source_type: str = "repository",
    method: Optional[str] = None,
    notes: Optional[str] = None,
) -> dict:
    """A determined confidence-wrapped field.

    Treats a None or empty-string `value` as 'not determined' and automatically
    degrades to the `unknown` form, guaranteeing the invariant
    `unknown: true ⇒ value is null and confidence == 0`. An empty *list* is NOT
    degraded: `[]` is a confident "none" and is contractually distinct from
    unknown — callers that mean "couldn't determine" pass None or call unknown().
    """
    if value is None or value == "":
        return unknown(notes=notes)
    return {
        "value": value,
        "confidence": round(float(confidence), 2),
        "unknown": False,
        "source": source,
        "provenance": {
            "source": source,
            "source_type": source_type,
            "as_of": _today(),
            "method": method,
        },
        "notes": notes,
    }


def unknown(notes: Optional[str] = None) -> dict:
    """The canonical 'not determined' field.

    `provenance` is omitted entirely (not null): the contract types it as an
    object, and an unknown field has no evidence trail to record.
    """
    return {
        "value": None,
        "confidence": 0,
        "unknown": True,
        "source": None,
        "notes": notes,
    }


# ---------------------------------------------------------------------------
# Filesystem helpers
# ---------------------------------------------------------------------------

def read_text(path: Path, limit: int = 1_000_000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except (OSError, UnicodeError):
        return ""


def find_first(repo: Path, names: Iterable[str]) -> Optional[Path]:
    """Return the first existing file matching any of `names` (case-insensitive)."""
    lower = {n.lower(): n for n in names}
    try:
        for child in sorted(repo.iterdir()):
            if child.is_file() and child.name.lower() in lower:
                return child
    except OSError:
        return None
    return None


def walk_files(repo: Path, max_files: int = 5000):
    count = 0
    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS and not d.startswith(".")]
        for name in files:
            yield Path(root) / name
            count += 1
            if count >= max_files:
                return


# ---------------------------------------------------------------------------
# Manifest parsing → declared dependencies + identity hints
# ---------------------------------------------------------------------------

def parse_toml(text: str) -> dict:
    if tomllib is not None:
        try:
            return tomllib.loads(text)
        except Exception:
            return {}
    return {}


def collect_manifests(repo: Path) -> dict:
    """Read recognised manifests and return identity hints + dependency set.

    Returns a dict with:
      - deps:   set of lowercased dependency names found anywhere
      - name / description / version: identity hints (value, source) tuples
      - sources: list of manifest paths read (for provenance)
      - languages: set of language/runtime names implied by manifest presence
    """
    deps: set[str] = set()
    languages: set[str] = set()
    sources: list[str] = []
    name = description = version = None
    name_src = desc_src = ver_src = None

    def rel(p: Path) -> str:
        try:
            return str(p.relative_to(repo))
        except ValueError:
            return str(p)

    # package.json (Node / web)
    pkg = find_first(repo, ["package.json"])
    if pkg:
        sources.append(rel(pkg))
        languages.add("JavaScript/TypeScript (Node.js)")
        try:
            data = json.loads(read_text(pkg))
        except json.JSONDecodeError:
            data = {}
        if isinstance(data, dict):
            if data.get("name") and not name:
                name, name_src = str(data["name"]), rel(pkg)
            if data.get("description") and not description:
                description, desc_src = str(data["description"]), rel(pkg)
            if data.get("version") and not version:
                version, ver_src = str(data["version"]), rel(pkg)
            for key in ("dependencies", "devDependencies", "peerDependencies",
                        "optionalDependencies"):
                section = data.get(key)
                if isinstance(section, dict):
                    deps.update(d.lower() for d in section)

    # pyproject.toml (Python)
    pyproject = find_first(repo, ["pyproject.toml"])
    if pyproject:
        sources.append(rel(pyproject))
        languages.add("Python")
        data = parse_toml(read_text(pyproject))
        project = data.get("project", {}) if isinstance(data, dict) else {}
        if isinstance(project, dict):
            if project.get("name") and not name:
                name, name_src = str(project["name"]), rel(pyproject)
            if project.get("description") and not description:
                description, desc_src = str(project["description"]), rel(pyproject)
            if project.get("version") and not version:
                version, ver_src = str(project["version"]), rel(pyproject)
            for dep in project.get("dependencies", []) or []:
                deps.add(_dep_name(str(dep)))
            opt = project.get("optional-dependencies", {})
            if isinstance(opt, dict):
                for group in opt.values():
                    for dep in group or []:
                        deps.add(_dep_name(str(dep)))
        # Poetry layout
        poetry = (data.get("tool", {}) or {}).get("poetry", {}) if isinstance(data, dict) else {}
        if isinstance(poetry, dict):
            if poetry.get("name") and not name:
                name, name_src = str(poetry["name"]), rel(pyproject)
            if poetry.get("description") and not description:
                description, desc_src = str(poetry["description"]), rel(pyproject)
            for dep in (poetry.get("dependencies", {}) or {}):
                if dep.lower() != "python":
                    deps.add(_dep_name(dep))

    # requirements.txt (Python)
    reqs = find_first(repo, ["requirements.txt", "requirements-dev.txt"])
    if reqs:
        sources.append(rel(reqs))
        languages.add("Python")
        for line in read_text(reqs).splitlines():
            line = line.strip()
            if line and not line.startswith(("#", "-")):
                deps.add(_dep_name(line))

    # setup.py (Python) — best-effort dependency sniff
    setup = find_first(repo, ["setup.py"])
    if setup:
        sources.append(rel(setup))
        languages.add("Python")
        for m in re.finditer(r"""['"]([A-Za-z0-9_.\-]+)\s*(?:[<>=!~]=?[^'"]*)?['"]""",
                             read_text(setup)):
            deps.add(m.group(1).lower())

    # go.mod (Go)
    gomod = find_first(repo, ["go.mod"])
    if gomod:
        sources.append(rel(gomod))
        languages.add("Go")
        text = read_text(gomod)
        mod = re.search(r"^module\s+(\S+)", text, re.MULTILINE)
        if mod and not name:
            name, name_src = mod.group(1).rsplit("/", 1)[-1], rel(gomod)
        for m in re.finditer(r"^\s+([\w./\-]+)\s+v[\d]", text, re.MULTILINE):
            deps.add(m.group(1).lower())

    # Cargo.toml (Rust)
    cargo = find_first(repo, ["Cargo.toml"])
    if cargo:
        sources.append(rel(cargo))
        languages.add("Rust")
        data = parse_toml(read_text(cargo))
        pkg_tbl = data.get("package", {}) if isinstance(data, dict) else {}
        if isinstance(pkg_tbl, dict):
            if pkg_tbl.get("name") and not name:
                name, name_src = str(pkg_tbl["name"]), rel(cargo)
            if pkg_tbl.get("description") and not description:
                description, desc_src = str(pkg_tbl["description"]), rel(cargo)
        for dep in (data.get("dependencies", {}) if isinstance(data, dict) else {}):
            deps.add(dep.lower())

    # Other language manifests (presence → language hint only)
    for fname, lang in [
        ("Gemfile", "Ruby"),
        ("composer.json", "PHP"),
        ("pom.xml", "Java/JVM"),
        ("build.gradle", "Java/JVM"),
        ("build.gradle.kts", "Kotlin/JVM"),
        ("pubspec.yaml", "Dart/Flutter"),
        ("Package.swift", "Swift"),
    ]:
        found = find_first(repo, [fname])
        if found:
            sources.append(rel(found))
            languages.add(lang)

    return {
        "deps": deps,
        "languages": languages,
        "sources": sources,
        "name": (name, name_src),
        "description": (description, desc_src),
        "version": (version, ver_src),
    }


def _dep_name(spec: str) -> str:
    """Strip version specifiers/extras from a dependency spec → bare lower name."""
    spec = spec.strip().strip('"\'')
    spec = re.split(r"[<>=!~;\[ @]", spec, maxsplit=1)[0]
    return spec.strip().lower()


# ---------------------------------------------------------------------------
# README parsing
# ---------------------------------------------------------------------------

def find_readme(repo: Path) -> Optional[Path]:
    return find_first(repo, [
        "README.md", "README.rst", "README.txt", "README",
        "readme.md", "Readme.md",
    ])


def readme_title(text: str) -> Optional[str]:
    for line in text.splitlines():
        line = line.strip()
        m = re.match(r"^#\s+(.+)", line)
        if m:
            return _clean_inline(m.group(1))
    return None


def readme_tagline(text: str, title: Optional[str]) -> Optional[str]:
    """First substantive prose line after the H1 (skipping badges/blank lines)."""
    lines = text.splitlines()
    seen_title = False
    for line in lines:
        s = line.strip()
        if not seen_title:
            if re.match(r"^#\s+", s):
                seen_title = True
            continue
        if not s:
            continue
        if s.startswith("#"):  # next heading before any prose
            break
        if _is_noise_line(s):
            continue
        cleaned = _clean_inline(s)
        if cleaned and cleaned.lower() != (title or "").lower():
            return cleaned
    return None


def readme_description(text: str) -> Optional[str]:
    """First full prose paragraph (joined) after the H1."""
    lines = text.splitlines()
    seen_title = False
    buf: list[str] = []
    for line in lines:
        s = line.strip()
        if not seen_title:
            if re.match(r"^#\s+", s):
                seen_title = True
            continue
        if s.startswith("#") and buf:
            break
        if s.startswith("#"):
            continue
        if not s:
            if buf:
                break
            continue
        if _is_noise_line(s):
            continue
        buf.append(_clean_inline(s))
    if not buf:
        return None
    para = " ".join(buf).strip()
    return para or None


def readme_feature_bullets(text: str) -> list[str]:
    """Bullets under the first Features/Highlights/Capabilities heading."""
    lines = text.splitlines()
    in_section = False
    bullets: list[str] = []
    heading_re = re.compile(
        r"^#{1,6}\s+.*\b(features?|highlights?|capabilities|what\s+it\s+does|"
        r"key\s+features|why)\b", re.IGNORECASE)
    for line in lines:
        s = line.strip()
        if re.match(r"^#{1,6}\s+", s):
            if in_section:  # reached the next heading
                break
            in_section = bool(heading_re.match(s))
            continue
        if in_section:
            m = re.match(r"^[-*+]\s+(.+)", s) or re.match(r"^\d+[.)]\s+(.+)", s)
            if m:
                item = _clean_inline(m.group(1))
                # keep the lead clause of "**Bold** — detail" style bullets
                item = re.split(r"\s[—–-]\s", item, maxsplit=1)[0].strip() if len(item) > 80 else item
                if item:
                    bullets.append(item)
    # de-duplicate, cap length
    seen = set()
    out = []
    for b in bullets:
        key = b.lower()
        if key not in seen:
            seen.add(key)
            out.append(b)
    return out[:20]


def _is_noise_line(s: str) -> bool:
    """Badges, image-only lines, HTML, table rules, blockquotes."""
    if s.startswith((">", "|", "<", "![")):
        return True
    if re.match(r"^[-=*_]{3,}$", s):
        return True
    # line consisting only of shield/badge links
    if re.fullmatch(r"(\[!\[.*?\]\(.*?\)\]\(.*?\)\s*)+", s):
        return True
    return False


def _clean_inline(s: str) -> str:
    s = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", s)          # images
    s = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", s)        # links → text
    s = re.sub(r"[*_`]+", "", s)                          # emphasis/code ticks
    s = re.sub(r"<[^>]+>", "", s)                          # stray HTML
    s = re.sub(r"\s+", " ", s).strip()
    return s


# ---------------------------------------------------------------------------
# Inference helpers
# ---------------------------------------------------------------------------

def infer_stage(version: Optional[str]) -> dict:
    if not version:
        return unknown(notes="No version found in any manifest.")
    v = version.strip().lstrip("vV")
    low = v.lower()
    if any(tag in low for tag in ("alpha", "a0", "-a")):
        return field("alpha", 0.55, source="manifest version", source_type="inference",
                     method=f"version '{version}' contains alpha tag")
    if any(tag in low for tag in ("beta", "rc", "-b")):
        return field("beta", 0.55, source="manifest version", source_type="inference",
                     method=f"version '{version}' contains beta/rc tag")
    m = re.match(r"^(\d+)\.(\d+)", v)
    if not m:
        return unknown(notes=f"Unparseable version '{version}'.")
    major, minor = int(m.group(1)), int(m.group(2))
    if major == 0 and minor == 0:
        return field("prototype", 0.4, source="manifest version", source_type="inference",
                     method=f"0.0.x version '{version}'")
    if major == 0:
        return field("beta", 0.4, source="manifest version", source_type="inference",
                     method=f"0.x version '{version}' (pre-1.0)")
    return field("ga", 0.45, source="manifest version", source_type="inference",
                 method=f"version '{version}' is >= 1.0")


def infer_deployment(repo: Path, has_license: bool, license_name: Optional[str]) -> dict:
    has_docker = bool(find_first(repo, ["Dockerfile"]))
    has_compose = bool(find_first(repo, [
        "docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"]))
    has_helm = (repo / "charts").is_dir() or bool(find_first(repo, ["Chart.yaml"]))
    if has_license and (has_docker or has_compose or has_helm):
        return field("self-hosted (open source)", 0.45, source=license_name or "LICENSE",
                     source_type="inference",
                     method="open-source license + container/deploy config present")
    if has_license:
        return field("oss", 0.45, source=license_name or "LICENSE", source_type="inference",
                     method="open-source license file present")
    if has_docker or has_compose or has_helm:
        return field("self-hosted", 0.35, source="container config", source_type="inference",
                     method="Dockerfile/compose/helm present, no OSS license")
    return unknown(notes="No license or deployment manifests to infer from.")


def map_deps(deps: set[str], mapping: dict) -> list[tuple[str, str]]:
    """Return [(label, matched_dep)] for every dependency present in `mapping`."""
    out = []
    seen_labels = set()
    for dep in sorted(deps):
        label = mapping.get(dep)
        if label and label not in seen_labels:
            seen_labels.add(label)
            out.append((label, dep))
    return out


def detect_apis(repo: Path, deps: set[str]) -> dict:
    labels = [lbl for lbl, _ in map_deps(deps, API_FRAMEWORK_DEPS)]
    # OpenAPI / GraphQL schema files are strong direct evidence.
    spec = find_first(repo, [
        "openapi.yaml", "openapi.yml", "openapi.json",
        "swagger.yaml", "swagger.yml", "swagger.json"])
    method_bits = []
    conf = 0.5
    if labels:
        method_bits.append("API framework dependency detected")
    if spec:
        labels.append("OpenAPI specification")
        method_bits.append(f"found {spec.name}")
        conf = 0.8
    if find_first(repo, ["schema.graphql", "schema.gql"]):
        labels.append("GraphQL schema")
        method_bits.append("found GraphQL schema file")
        conf = max(conf, 0.7)
    labels = list(dict.fromkeys(labels))
    if not labels:
        return unknown(notes="No API framework dependency or API spec file found.")
    return field(labels, conf, source="manifests/specs", source_type="inference",
                 method="; ".join(method_bits))


def detect_target_users(readme_text: str) -> dict:
    """Pull 'for <audience>' style cues from README prose."""
    if not readme_text:
        return unknown()
    audiences: list[str] = []
    patterns = [
        r"\bfor\s+(developers?|engineers?|teams?|startups?|enterprises?|"
        r"data\s+scientists?|designers?|product\s+managers?|marketers?|"
        r"businesses?|individuals?|researchers?|analysts?|ops\s+teams?|"
        r"devops|sres?|founders?)\b",
        r"\b(developer|engineer|team|startup|enterprise)-(?:first|focused|friendly)\b",
    ]
    text = readme_text[:8000]
    for pat in patterns:
        for m in re.finditer(pat, text, re.IGNORECASE):
            aud = m.group(1).strip().lower().rstrip("s")
            if aud:
                audiences.append(aud)
    if not audiences:
        return unknown(notes="No explicit target-audience cues in README.")
    # normalise + dedupe, keep order
    norm = []
    seen = set()
    for a in audiences:
        label = a.capitalize() + "s"
        if label.lower() not in seen:
            seen.add(label.lower())
            norm.append(label)
    return field(norm[:8], 0.35, source="README", source_type="inference",
                 method="matched 'for <audience>' phrasing in README prose")


def detect_category(deps: set[str], readme_text: str, ai_caps: list[str]) -> dict:
    """Coarse product category from the strongest available signal."""
    blob = (readme_text or "").lower()
    if ai_caps:
        if any("vector" in c.lower() for c in ai_caps):
            return field("AI / RAG application", 0.4, source="dependencies",
                         source_type="inference", method="LLM + vector DB dependencies")
        return field("AI / LLM application", 0.4, source="dependencies",
                     source_type="inference", method="LLM SDK dependencies present")
    pairs = [
        (("cli", "command-line", "command line"), "Developer CLI tool"),
        (("sdk", "client library", "library for"), "Developer library / SDK"),
        (("dashboard", "analytics"), "Analytics / dashboard"),
        (("e-commerce", "ecommerce", "storefront"), "E-commerce"),
        (("design system", "component library", "ui kit"), "UI / design tooling"),
        (("api ", "rest api", "graphql"), "API / backend service"),
    ]
    for needles, label in pairs:
        if any(n in blob for n in needles):
            return field(label, 0.3, source="README", source_type="inference",
                         method=f"README mentions {needles[0]!r}")
    return unknown(notes="Insufficient signal to classify product category.")


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def analyze(repo: Path, generator: str) -> dict:
    manifests = collect_manifests(repo)
    deps: set[str] = manifests["deps"]
    readme_path = find_readme(repo)
    readme_text = read_text(readme_path) if readme_path else ""
    readme_rel = readme_path.name if readme_path else None

    # ---- Identity ----
    m_name, m_name_src = manifests["name"]
    r_title = readme_title(readme_text)
    if m_name:
        name_field = field(m_name, 0.9, source=m_name_src, source_type="repository",
                           method="parsed package manifest")
    elif r_title:
        name_field = field(r_title, 0.6, source=readme_rel, source_type="repository",
                           method="README H1 heading")
    else:
        name_field = field(repo.resolve().name, 0.3, source=str(repo),
                           source_type="inference", method="repository directory name")

    m_desc, m_desc_src = manifests["description"]
    tagline = m_desc or readme_tagline(readme_text, r_title)
    tagline_src = m_desc_src if m_desc else readme_rel
    tagline_field = field(
        tagline,
        0.8 if m_desc else 0.5,
        source=tagline_src,
        source_type="repository",
        method="manifest description" if m_desc else "first README prose line",
    )

    description = readme_description(readme_text) or m_desc
    description_field = field(
        description,
        0.55 if readme_description(readme_text) else (0.6 if m_desc else 0),
        source=readme_rel if readme_description(readme_text) else m_desc_src,
        source_type="repository",
        method="README intro paragraph" if readme_description(readme_text)
        else "manifest description",
    )

    m_version = manifests["version"][0]
    has_license = bool(find_first(repo, ["LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING"]))
    license_path = find_first(repo, ["LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING"])
    license_name = license_path.name if license_path else None

    identity = {
        "name": name_field,
        "tagline": tagline_field,
        "description": description_field,
        "category": detect_category(deps, readme_text,
                                    [lbl for lbl, _ in map_deps(deps, AI_DEPS)]),
        "stage": infer_stage(m_version),
        "deployment_model": infer_deployment(repo, has_license, license_name),
        # Business/pricing model are commercial facts rarely present in a repo.
        "business_model": (
            field("open source", 0.4, source=license_name, source_type="inference",
                  method="open-source license present")
            if has_license else unknown(notes="No commercial/business-model signal in repo.")
        ),
        "pricing_model": unknown(notes="Pricing is not derivable from source; collected later from the website/pricing page."),
    }

    # ---- Features ----
    ai_pairs = map_deps(deps, AI_DEPS)
    ai_caps = [lbl for lbl, _ in ai_pairs]
    integ_pairs = map_deps(deps, INTEGRATION_DEPS)
    integrations = [lbl for lbl, _ in integ_pairs]
    platform_pairs = map_deps(deps, PLATFORM_DEPS)
    platforms = sorted(set(manifests["languages"]) | {lbl for lbl, _ in platform_pairs})

    feature_bullets = readme_feature_bullets(readme_text)

    features = {
        "core_features": (
            field(feature_bullets, 0.55, source=readme_rel, source_type="repository",
                  method="bullets under README Features heading")
            if feature_bullets else
            unknown(notes="No Features/Highlights bullet list found in README.")
        ),
        "ai_capabilities": (
            field(ai_caps, 0.7, source=", ".join(manifests["sources"]) or None,
                  source_type="inference", method="AI/LLM SDK dependencies in manifests")
            if ai_caps else
            field([], 0.5, source=", ".join(manifests["sources"]) or None,
                  source_type="inference",
                  notes="No AI/LLM dependencies detected — confident 'none', not unknown.",
                  method="scanned manifest dependencies for AI SDKs")
        ),
        "platforms": (
            field(platforms, 0.6, source=", ".join(manifests["sources"]) or None,
                  source_type="inference",
                  method="languages/runtimes implied by manifests + UI framework deps")
            if platforms else
            unknown(notes="No recognised manifests to infer platforms from.")
        ),
        "integrations": (
            field(integrations, 0.6, source=", ".join(manifests["sources"]) or None,
                  source_type="inference", method="third-party service SDKs in dependencies")
            if integrations else
            unknown(notes="No recognised third-party integration SDKs in dependencies.")
        ),
        "apis": detect_apis(repo, deps),
        "target_workflow": unknown(
            notes="Target workflow is a narrative judgment; left for analyst/report synthesis."),
    }

    # ---- Customers ----
    customers = {
        "target_users": detect_target_users(readme_text),
        "industries": unknown(notes="Industry targeting is not reliably derivable from source."),
        "company_size": unknown(notes="Customer company size is not derivable from source."),
        "personas": unknown(notes="Buyer personas require market research, collected later."),
        "use_cases": unknown(notes="Use cases require website/marketing analysis, collected later."),
    }

    return {
        "meta": {
            "schema_version": SCHEMA_VERSION,
            "dataset": "product",
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "generator": generator,
        },
        "entity_ref": "self",
        "identity": identity,
        "features": features,
        "customers": customers,
    }


def validate(instance: dict, schemas_dir: Path) -> Optional[str]:
    """Validate `instance` against product.schema.json; return error or None."""
    try:
        from jsonschema import Draft202012Validator
        from referencing import Registry, Resource
    except ModuleNotFoundError:
        return "SKIP: jsonschema/referencing not installed"

    registry = Registry()
    for sf in schemas_dir.glob("*.json"):
        doc = json.loads(read_text(sf))
        resource = Resource.from_contents(doc)
        registry = registry.with_resource(uri=sf.name, resource=resource)
        if "$id" in doc:
            registry = registry.with_resource(uri=doc["$id"], resource=resource)

    schema = json.loads(read_text(schemas_dir / "product.schema.json"))
    validator = Draft202012Validator(schema, registry=registry)
    errors = sorted(validator.iter_errors(instance), key=lambda e: e.path)
    if errors:
        return "; ".join(f"{list(e.path)}: {e.message}" for e in errors[:5])
    return None


def summarize(product: dict) -> str:
    lines = ["Product Intelligence extracted:"]
    for section in ("identity", "features", "customers"):
        for key, fld in product[section].items():
            if fld.get("unknown"):
                rendered = "— unknown"
            else:
                val = fld["value"]
                if isinstance(val, list):
                    rendered = f"[{len(val)}] " + ", ".join(map(str, val[:4]))
                    if len(val) > 4:
                        rendered += ", …"
                else:
                    rendered = str(val)
                    if len(rendered) > 70:
                        rendered = rendered[:67] + "…"
                rendered += f"  (conf {fld['confidence']})"
            lines.append(f"  {section}.{key}: {rendered}")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", default=".", help="Path to the repository to analyze (default: cwd).")
    parser.add_argument("--output", default=None,
                        help="Output path (default: <repo>/product.json).")
    parser.add_argument("--generator", default=DEFAULT_GENERATOR,
                        help="Generator string recorded in meta.generator.")
    parser.add_argument("--validate", action="store_true",
                        help="Validate the result against schemas/ before writing.")
    parser.add_argument("--quiet", action="store_true", help="Suppress the summary.")
    args = parser.parse_args(argv)

    repo = Path(args.repo).resolve()
    if not repo.is_dir():
        print(f"error: --repo {repo} is not a directory", file=sys.stderr)
        return 2

    product = analyze(repo, args.generator)

    schemas_dir = Path(__file__).resolve().parent.parent / "schemas"
    if args.validate and schemas_dir.is_dir():
        err = validate(product, schemas_dir)
        if err and not err.startswith("SKIP"):
            print(f"error: output failed schema validation: {err}", file=sys.stderr)
            return 1
        if not args.quiet:
            print(f"validation: {'passed' if not err else err}", file=sys.stderr)

    out_path = Path(args.output) if args.output else (repo / "product.json")
    out_path.write_text(json.dumps(product, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")

    if not args.quiet:
        print(summarize(product), file=sys.stderr)
        print(f"\nwrote {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
