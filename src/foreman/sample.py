"""Create a tiny real git repo as a demo/test target (§11.5, §12).

The sample is a minimal Python "todo" package skeleton. The demo's mock workers
fill in the implementation and tests; Foreman independently runs ``pytest`` to
verify, exercising the full state machine without any real agent.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

_FILES = {
    "todo/__init__.py": '"""Demo todo package."""\n',
    "conftest.py": "# Ensures the repo root is importable by pytest.\n",
    "README.md": "# Demo todo CLI\n\nA tiny target project for Foreman's demo.\n",
    ".gitignore": ".foreman/\n__pycache__/\n*.pyc\n",
}


def create_sample_repo(path: Path | str) -> Path:
    root = Path(path)
    root.mkdir(parents=True, exist_ok=True)
    for rel, content in _FILES.items():
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content)
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=Demo", "-c", "user.email=demo@localhost",
         "commit", "-q", "-m", "scaffold todo CLI")
    return root


def pytest_command() -> str:
    """A test command that works regardless of PATH (uses the running interpreter)."""
    return f"{sys.executable} -m pytest -q"


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
