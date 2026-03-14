"""Extract just the PRD sections an issue references (P2.3 WS2/WS3).

Issues carry ``prd_refs`` like ``"PRD §User Flows"`` or ``"Story #1"``. To keep
context minimal (context rot — WS3.4), Foreman feeds an agent ONLY the referenced
sections, never the whole PRD. This module maps refs → section text.
"""

from __future__ import annotations

import re
from typing import Iterable

_SECTION_REF = re.compile(r"§\s*(.+?)\s*$")
_STORY_REF = re.compile(r"stor(?:y|ies)\s*#?\s*(\d+)", re.IGNORECASE)


def _heading_level(line: str) -> int:
    m = re.match(r"^(#+)\s", line)
    return len(m.group(1)) if m else 0


def _section(body: str, name: str) -> str:
    """Return the markdown section whose heading contains ``name`` (case-insensitive),
    up to the next heading of the same or higher level. '' if not found."""
    lines = body.splitlines()
    name_low = name.strip().lower()
    start = None
    level = 0
    for i, line in enumerate(lines):
        lvl = _heading_level(line)
        if lvl and name_low in line.lower():
            start = i
            level = lvl
            break
    if start is None:
        return ""
    out = [lines[start]]
    for line in lines[start + 1:]:
        lvl = _heading_level(line)
        if lvl and lvl <= level:
            break
        out.append(line)
    return "\n".join(out).strip()


def extract_sections(body: str, refs: Iterable[str]) -> str:
    """Concatenate the distinct PRD sections referenced by ``refs``.

    Recognises ``§Section Name`` and ``Story #N`` (which pulls the User Stories
    section once). Returns '' if nothing matched (caller decides the fallback).
    """
    chunks: list[str] = []
    seen: set[str] = set()
    want_stories = False
    for ref in refs or []:
        ref = str(ref)
        if _STORY_REF.search(ref):
            want_stories = True
        m = _SECTION_REF.search(ref)
        if m:
            name = m.group(1).strip()
            if name and name.lower() not in seen:
                sec = _section(body, name)
                if sec:
                    chunks.append(sec)
                    seen.add(name.lower())
    if want_stories and "user stories" not in seen:
        sec = _section(body, "User Stories")
        if sec:
            chunks.append(sec)
    return "\n\n".join(chunks).strip()
