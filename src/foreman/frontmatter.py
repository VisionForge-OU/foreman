"""Parse and serialize markdown files with a YAML frontmatter block.

Every durable Foreman document (plans, ADRs, PRDs, issues, skills) is a markdown
file optionally prefixed with a ``---`` delimited YAML frontmatter block. This
module is the single authority for that format so reads and writes round-trip
losslessly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import yaml

_DELIM = "---"


@dataclass
class Document:
    """A parsed frontmatter document: a metadata mapping plus a markdown body."""

    meta: dict[str, Any]
    body: str

    def get(self, key: str, default: Any = None) -> Any:
        return self.meta.get(key, default)


def parse(text: str) -> Document:
    """Split ``text`` into (frontmatter mapping, body).

    A document with no leading ``---`` block parses to an empty mapping and the
    whole text as the body. Tolerant: a malformed/non-mapping frontmatter block
    is treated as body text rather than raising, so a half-written file on crash
    recovery never explodes.
    """
    if not text.startswith(_DELIM):
        return Document(meta={}, body=text)

    # Find the closing delimiter on its own line after the opening one.
    lines = text.splitlines(keepends=True)
    # lines[0] is the opening "---". Search for the next bare "---" line.
    closing_idx = None
    for i in range(1, len(lines)):
        if lines[i].rstrip("\n") == _DELIM:
            closing_idx = i
            break
    if closing_idx is None:
        return Document(meta={}, body=text)

    raw_meta = "".join(lines[1:closing_idx])
    body = "".join(lines[closing_idx + 1 :])
    # Drop a single leading newline after the closing delimiter for cleanliness.
    if body.startswith("\n"):
        body = body[1:]

    try:
        loaded = yaml.safe_load(raw_meta) if raw_meta.strip() else {}
    except yaml.YAMLError:
        return Document(meta={}, body=text)

    if not isinstance(loaded, dict):
        return Document(meta={}, body=text)
    return Document(meta=loaded, body=body)


def serialize(meta: dict[str, Any], body: str) -> str:
    """Render a frontmatter document. Empty ``meta`` yields just the body."""
    if not meta:
        return body
    front = yaml.safe_dump(meta, sort_keys=False, default_flow_style=False).rstrip("\n")
    body = body.lstrip("\n")
    return f"{_DELIM}\n{front}\n{_DELIM}\n\n{body}"
