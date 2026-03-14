"""Install Foreman's read-only agent files into a target repo (P2.3 WS2/WS5).

Mirrors the vendored-skills mechanism (``vendored.py``): agent ``.md`` files are
packaged under ``agents/assets/``, namespaced ``foreman-*``, version-marked with
``foreman_agent_version``, and installed into the target repo's ``.claude/agents/``
by ``foreman init``. Only Foreman's own ``foreman-*`` agents are ever written —
a user's own agents are never touched (R2). Foreman invokes them headless with
``claude -p --agent foreman-evaluator`` (verified read-only — DECISIONS.md §P2.0).
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

from .. import frontmatter
from ..paths import RepoPaths

ASSETS = Path(__file__).resolve().parent / "assets"


def packaged_agents() -> dict[str, int]:
    """Map of packaged ``foreman-*`` agent name -> version."""
    out: dict[str, int] = {}
    if not ASSETS.exists():
        return out
    for md in sorted(ASSETS.glob("foreman-*.md")):
        v = _agent_version(md)
        if v is not None:
            out[md.stem] = v
    return out


def _agent_version(md: Path) -> Optional[int]:
    if not md.exists():
        return None
    doc = frontmatter.parse(md.read_text())
    v = doc.get("foreman_agent_version")
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


class AgentState(str, Enum):
    OK = "ok"
    MISSING = "missing"
    OUTDATED = "outdated"
    NEWER = "newer"


@dataclass
class AgentStatus:
    name: str
    state: AgentState
    installed_version: Optional[int]
    packaged_version: Optional[int]


def installed_version(repo_root: Path | str, agent_name: str) -> Optional[int]:
    paths = RepoPaths(repo_root)
    return _agent_version(paths.agents_install_dir / f"{agent_name}.md")


def status(repo_root: Path | str) -> list[AgentStatus]:
    out: list[AgentStatus] = []
    for name, pver in packaged_agents().items():
        iver = installed_version(repo_root, name)
        if iver is None:
            st = AgentState.MISSING
        elif iver == pver:
            st = AgentState.OK
        elif iver < pver:
            st = AgentState.OUTDATED
        else:
            st = AgentState.NEWER
        out.append(AgentStatus(name, st, iver, pver))
    return out


def install(repo_root: Path | str, *, force: bool = False) -> list[str]:
    """Install/update the packaged ``foreman-*`` agent files; return what changed."""
    paths = RepoPaths(repo_root)
    dest_base = paths.agents_install_dir
    dest_base.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for name, pver in packaged_agents().items():
        dest = dest_base / f"{name}.md"
        iver = _agent_version(dest)
        if not force and iver is not None and iver >= pver:
            continue
        shutil.copy2(ASSETS / f"{name}.md", dest)
        written.append(name)
    return written


def missing(repo_root: Path | str, required: list[str]) -> list[str]:
    return [n for n in required if installed_version(repo_root, n) is None]
