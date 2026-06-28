#!/usr/bin/env python3
"""
_progress.py — lightweight, stdlib-only progress reporting for the compete pipeline.

Every pipeline script (``analyze_repo``, ``discover_competitors``,
``collect_intelligence``, ``build_report``) reports its work through this shared
helper so the four stages emit one consistent, greppable line format that reads
well in a terminal *and* in Claude Code / AgentOps console logs.

Each emitted line carries:

  * the **stage name** (and the script that owns it),
  * **completed / total steps** with a percentage *when a total is known*
    (open-ended work omits the count rather than faking one),
  * **elapsed** wall-clock time since the stage started, and
  * a **rough ETA** extrapolated from the average time per completed step
    (shown only once at least one step has completed and a total is known).

Example::

    [compete] collect_intelligence · Intelligence Collection · 3/6 (50%) · elapsed 4.1s · eta ~4.1s · built techstack.json

Design notes
------------
* **stderr by default.** The ``plan`` subcommands write machine-readable JSON to
  stdout; progress must never contaminate that, so it goes to stderr (which
  AgentOps/Claude Code still capture).
* **No dependencies, no cross-script imports of business logic.** This module is
  the *only* thing the scripts share, and it pulls in nothing but the stdlib, so
  every script stays runnable on its own.
* **Cheap to disable.** ``--quiet`` (or ``COMPETE_NO_PROGRESS=1`` in the
  environment) yields a no-op reporter, so non-interactive runs stay silent
  without the call sites needing any conditionals.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Optional, TextIO

__all__ = ["Progress", "start"]

PREFIX = "[compete]"


def _fmt_secs(secs: float) -> str:
    """Human, compact duration: ``0.8s`` / ``4.1s`` / ``1m05s`` / ``1h02m``."""
    if secs < 0:
        secs = 0.0
    if secs < 10:
        return f"{secs:.1f}s"
    secs = int(round(secs))
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m{secs % 60:02d}s"
    return f"{secs // 3600}h{(secs % 3600) // 60:02d}m"


class Progress:
    """A single stage's progress reporter.

    Create one per stage (usually via :func:`start`), then call :meth:`step` once
    per unit of work and :meth:`finish` at the end. :meth:`log` emits a line
    without advancing the counter (for sub-steps inside a unit). When ``total`` is
    known, each line shows ``done/total (pct)`` and a rough ETA; when it is
    ``None``, the count and ETA are omitted but elapsed time is still shown.
    """

    def __init__(
        self,
        stage: str,
        *,
        script: Optional[str] = None,
        total: Optional[int] = None,
        enabled: bool = True,
        stream: Optional[TextIO] = None,
    ) -> None:
        self.stage = stage
        self.script = script
        self.total = total if (total is None or total > 0) else None
        self.done = 0
        self.enabled = enabled and os.environ.get("COMPETE_NO_PROGRESS", "") not in ("1", "true", "yes")
        self.stream = stream or sys.stderr
        self._start = time.monotonic()

    # -- mutation -----------------------------------------------------------
    def set_total(self, total: Optional[int]) -> None:
        """Set or revise the total once it becomes known (e.g. after a load)."""
        self.total = total if (total is None or total > 0) else None

    def elapsed(self) -> float:
        return time.monotonic() - self._start

    def _eta(self) -> Optional[float]:
        if not self.total or self.done <= 0 or self.done >= self.total:
            return None
        per = self.elapsed() / self.done
        return per * (self.total - self.done)

    # -- emission -----------------------------------------------------------
    def _emit(self, message: str = "") -> None:
        if not self.enabled:
            return
        tag = f"{PREFIX[:-1]}:{self.script}]" if self.script else PREFIX
        parts = [tag, self.stage]
        if self.total:
            pct = int(round(100 * self.done / self.total))
            parts.append(f"{self.done}/{self.total} ({pct}%)")
        elif self.done:
            parts.append(f"{self.done} done")
        parts.append(f"elapsed {_fmt_secs(self.elapsed())}")
        eta = self._eta()
        if eta is not None:
            parts.append(f"eta ~{_fmt_secs(eta)}")
        line = " · ".join(parts)
        if message:
            line += f" · {message}"
        print(line, file=self.stream, flush=True)

    def start(self, message: str = "started") -> "Progress":
        """Emit the opening line for the stage. Returns self for chaining."""
        self._emit(message)
        return self

    def step(self, message: str = "") -> None:
        """Advance the completed-step counter by one and emit a line."""
        self.done += 1
        self._emit(message)

    def log(self, message: str) -> None:
        """Emit a line *without* advancing the counter (a sub-step note)."""
        self._emit(message)

    def finish(self, message: str = "done") -> None:
        """Emit the closing line; clamps the counter to total when known."""
        if self.total:
            self.done = self.total
        self._emit(message)


def start(
    stage: str,
    *,
    script: Optional[str] = None,
    total: Optional[int] = None,
    enabled: bool = True,
    stream: Optional[TextIO] = None,
) -> Progress:
    """Convenience constructor that also emits the opening line."""
    return Progress(stage, script=script, total=total, enabled=enabled, stream=stream).start()
