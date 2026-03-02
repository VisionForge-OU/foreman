"""Manage Foreman's vendored ``foreman-*`` skills (§4 distribution mechanics).

Responsibilities:
- Enumerate the skills packaged inside Foreman (``src/foreman/skills/``).
- Read each skill's ``foreman_skill_version`` marker.
- Install/update them into a target repo's ``.claude/skills/`` — only ever
  touching Foreman's own ``foreman-*`` namespaced directories.
- Report installed/missing/outdated status for the TUI and the pipeline gate.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

from . import frontmatter
from .paths import RepoPaths


def packaged_skills_dir() -> Path:
    """Directory holding the vendored skills shipped inside the package."""
    return Path(__file__).resolve().parent / "skills"


def _skill_version(skill_md: Path) -> Optional[int]:
    if not skill_md.exists():
        return None
    doc = frontmatter.parse(skill_md.read_text())
    v = doc.get("foreman_skill_version")
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def packaged_skills() -> dict[str, int]:
    """Map of packaged ``foreman-*`` skill name -> version."""
    out: dict[str, int] = {}
    base = packaged_skills_dir()
    if not base.exists():
        return out
    for child in sorted(base.iterdir()):
        if not child.is_dir() or not child.name.startswith("foreman-"):
            continue
        ver = _skill_version(child / "SKILL.md")
        if ver is not None:
            out[child.name] = ver
    return out


class SkillState(str, Enum):
    OK = "ok"             # installed and version matches packaged
    MISSING = "missing"   # not installed in the target repo
    OUTDATED = "outdated"  # installed but older than packaged
    NEWER = "newer"       # installed version newer than packaged (don't clobber)


@dataclass
class SkillStatus:
    name: str
    state: SkillState
    installed_version: Optional[int]
    packaged_version: Optional[int]


def installed_version(repo_root: Path | str, skill_name: str) -> Optional[int]:
    paths = RepoPaths(repo_root)
    return _skill_version(paths.skills_install_dir / skill_name / "SKILL.md")


def status(repo_root: Path | str) -> list[SkillStatus]:
    """Compare packaged vs installed versions for every vendored skill."""
    pkg = packaged_skills()
    out: list[SkillStatus] = []
    for name, pver in pkg.items():
        iver = installed_version(repo_root, name)
        if iver is None:
            state = SkillState.MISSING
        elif iver == pver:
            state = SkillState.OK
        elif iver < pver:
            state = SkillState.OUTDATED
        else:
            state = SkillState.NEWER
        out.append(SkillStatus(name, state, iver, pver))
    return out


def install(repo_root: Path | str, *, force: bool = False) -> list[str]:
    """Install/update vendored skills into the target repo's ``.claude/skills/``.

    Only ``foreman-*`` directories are ever written or overwritten — a
    user-installed upstream skill (e.g. ``grill-with-docs``) is never touched
    (R2). Returns the list of skill names that were (re)installed.
    """
    paths = RepoPaths(repo_root)
    dest_base = paths.skills_install_dir
    dest_base.mkdir(parents=True, exist_ok=True)
    src_base = packaged_skills_dir()

    written: list[str] = []
    for name, pver in packaged_skills().items():
        src = src_base / name
        dest = dest_base / name
        iver = _skill_version(dest / "SKILL.md")
        # Skip if already current and not forced; never clobber a newer install.
        if not force and iver is not None and iver >= pver:
            continue
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src, dest)
        written.append(name)
    return written


def missing_required(repo_root: Path | str, required: list[str]) -> list[str]:
    """Required skills that are not installed in the target repo (pipeline gate)."""
    out = []
    for name in required:
        if installed_version(repo_root, name) is None:
            out.append(name)
    return out
