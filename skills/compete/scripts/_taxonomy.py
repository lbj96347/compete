#!/usr/bin/env python3
"""Shared loader for the capability taxonomy (the features.json matrix axes).

The taxonomy is **data, not code**: edit ``capability_taxonomy.json`` (shipped
next to the skill), or point ``--taxonomy <path>`` / the ``$COMPETE_TAXONOMY``
env var at your own file, to retarget compete to any market without changing a
script. ``collect_intelligence.py`` uses it to know which axes to score; the
report derives its axes straight from the produced ``features.json`` so it always
matches whatever taxonomy generated the data.

File shape::

    {
      "name": "social-media-management",          # optional, informational
      "features": ["key_a", "key_b", ...],         # product-capability axes
      "services": ["svc_a", ...],                  # human/professional-service axes
      "labels":   {"key_a": "Custom Label"}        # optional display overrides
    }

Keys are snake_case (``^[a-z][a-z0-9_]*$``). ``features`` come before ``services``
in display order; a missing/empty file degrades to an empty taxonomy (the
features dataset then builds as empty matrices — valid, just no axes).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Optional

# The default taxonomy ships beside the skill: …/skills/compete/capability_taxonomy.json
# (this module lives in …/skills/compete/scripts/).
DEFAULT_PATH = Path(__file__).resolve().parent.parent / "capability_taxonomy.json"

KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


class Taxonomy:
    """Ordered capability axes plus a key→category map and display labels."""

    def __init__(self, feature_keys, service_keys, labels=None, name=None, source=None):
        self.feature_keys = [k for k in feature_keys if KEY_PATTERN.match(k or "")]
        self.service_keys = [k for k in service_keys if KEY_PATTERN.match(k or "")]
        self.keys = self.feature_keys + self.service_keys
        self.category = {
            **{k: "feature" for k in self.feature_keys},
            **{k: "service" for k in self.service_keys},
        }
        self.labels = dict(labels or {})
        self.name = name
        self.source = source

    def label(self, key: str) -> str:
        """Display label for a key (explicit override, else humanized)."""
        return self.labels.get(key) or key.replace("_", " ").title()


def resolve_path(explicit: Optional[str] = None) -> Path:
    """Resolve the taxonomy file: ``--taxonomy`` flag > ``$COMPETE_TAXONOMY`` > default."""
    if explicit:
        return Path(explicit).expanduser()
    env = os.environ.get("COMPETE_TAXONOMY")
    if env:
        return Path(env).expanduser()
    return DEFAULT_PATH


def load(explicit: Optional[str] = None) -> Taxonomy:
    """Load the taxonomy (flag > env > default). A missing file → empty taxonomy."""
    path = resolve_path(explicit)
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return Taxonomy([], [], source=None)
    if not isinstance(data, dict):
        return Taxonomy([], [], source=str(path))
    feats = data.get("features") if isinstance(data.get("features"), list) else []
    svcs = data.get("services") if isinstance(data.get("services"), list) else []
    labels = data.get("labels") if isinstance(data.get("labels"), dict) else {}
    return Taxonomy(feats, svcs, labels, name=data.get("name"), source=str(path))
