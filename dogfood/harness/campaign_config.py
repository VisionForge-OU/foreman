"""Real-run configuration: the 5-feature backlog, the seed scratch project, and a
``setup_scratch`` that builds a ready-to-drive target repo (git + seed code +
dedicated venv + ``foreman init`` + a tuned, guardrailed config.yaml).
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import yaml

from .conductor import FeatureSpec

WORKER_MODEL = "claude-haiku-4-5-20251001"

# Backlog — one per work type. Mandatory request-changes cycle assigned to F2/PRD.
BACKLOG = [
    FeatureSpec(key="F1", ftype="greenfield",
                title="Daily plan endpoint",
                request="Add a GET /plan endpoint that returns the day's plan: the open "
                        "tasks (status != 'done') ordered for the user to work through. "
                        "Return JSON list of tasks. Keep it simple and tested."),
    FeatureSpec(key="F2", ftype="brownfield",
                title="Task priority and due date",
                request="Add an integer `priority` (1=high..3=low, default 2) and an "
                        "optional `due_date` (ISO date string) to tasks, accepted on "
                        "create and returned on list. The GET /plan ordering must respect "
                        "them: higher priority first, then earlier due_date.",
                force_rc_gate="prd"),
    FeatureSpec(key="F3", ftype="multi",
                title="Backlog aging",
                request="Backlog aging: compute an aging score per open task (older "
                        "created_at => higher score), add a GET /stale endpoint returning "
                        "tasks whose aging score exceeds a threshold, and a function that "
                        "applies a daily decay to de-prioritise ignored tasks. Slice it."),
    FeatureSpec(key="F4", ftype="vague",
                title="Easier mornings",
                request="Make mornings easier to start."),
    FeatureSpec(key="F5", ftype="trivial",
                title="Return created_at in task list",
                request="The GET /tasks list response should include each task's "
                        "created_at timestamp (it is already stored, just not returned)."),
]

# --------------------------------------------------------------------------- #
# Seed scratch project (minimal but working: SQLite task store + FastAPI,
# created_at stored but NOT returned — so F5 is a genuine trivial fix).
# --------------------------------------------------------------------------- #
SEED_FILES = {
    "dayplan/__init__.py": "",
    "dayplan/db.py": '''\
"""SQLite-backed task store for dayplan."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "dayplan.db"


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path or DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            created_at TEXT NOT NULL
        )
        """
    )
    conn.commit()


def add_task(conn: sqlite3.Connection, title: str) -> int:
    created = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        "INSERT INTO tasks (title, status, created_at) VALUES (?, 'open', ?)",
        (title, created),
    )
    conn.commit()
    return int(cur.lastrowid)


def list_tasks(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT id, title, status, created_at FROM tasks ORDER BY id").fetchall()
    # NOTE: created_at is intentionally omitted from the API response (see app.py).
    return [dict(r) for r in rows]
''',
    "dayplan/app.py": '''\
"""FastAPI app for dayplan."""
from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel

from dayplan import db


class TaskIn(BaseModel):
    title: str


def create_app(conn=None) -> FastAPI:
    app = FastAPI(title="dayplan")
    app.state.conn = conn or db.connect()
    db.init_db(app.state.conn)

    @app.post("/tasks")
    def create_task(task: TaskIn):
        tid = db.add_task(app.state.conn, task.title)
        return {"id": tid, "title": task.title, "status": "open"}

    @app.get("/tasks")
    def get_tasks():
        # Returns id/title/status only — created_at is stored but not exposed yet.
        return [{"id": t["id"], "title": t["title"], "status": t["status"]}
                for t in db.list_tasks(app.state.conn)]

    return app


app = create_app()
''',
    "tests/__init__.py": "",
    "tests/conftest.py": '''\
import sqlite3
import pytest
from fastapi.testclient import TestClient
from dayplan.app import create_app


@pytest.fixture
def client():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return TestClient(create_app(conn))
''',
    "tests/test_tasks.py": '''\
def test_create_and_list_task(client):
    r = client.post("/tasks", json={"title": "buy milk"})
    assert r.status_code == 200
    assert r.json()["title"] == "buy milk"
    tasks = client.get("/tasks").json()
    assert len(tasks) == 1
    assert tasks[0]["title"] == "buy milk"
    assert tasks[0]["status"] == "open"
''',
    "README.md": "# dayplan\n\nA tiny self-hostable daily-planning backend (FastAPI + SQLite).\n",
    ".gitignore": "__pycache__/\n*.db\n.venv*/\n.foreman/\n",
}


def _run(cmd: list[str], cwd: str | Path, env=None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd), env=env, capture_output=True, text=True)


def setup_scratch(scratch: Path, *, foreman_repo: Path, permission_mode: str = "acceptEdits",
                  cost_ceiling: float = 60.0) -> dict:
    """Build the target repo. Returns {ok, venv_python, notes:[...]}.

    Idempotent-ish: safe to re-run; recreates seed + config but keeps the venv.
    """
    notes = []
    scratch = Path(scratch)
    scratch.mkdir(parents=True, exist_ok=True)
    for rel, content in SEED_FILES.items():
        p = scratch / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)

    # git
    if not (scratch / ".git").exists():
        _run(["git", "init", "-b", "main"], scratch)
    _run(["git", "add", "-A"], scratch)
    _run(["git", "-c", "user.email=dogfood@local", "-c", "user.name=dogfood",
          "commit", "-m", "seed dayplan"], scratch)

    # dedicated venv with the third-party deps the seed/tests need
    venv = scratch / ".venv-dayplan"
    venv_python = venv / "bin" / "python"
    if not venv_python.exists():
        cp = _run(["python3", "-m", "venv", str(venv)], scratch)
        if cp.returncode != 0:
            notes.append(f"venv create failed: {cp.stderr[:200]}")
        cp = _run([str(venv_python), "-m", "pip", "install", "-q", "--disable-pip-version-check",
                   "fastapi", "httpx", "pytest", "anyio"], scratch)
        if cp.returncode != 0:
            notes.append(f"pip install failed: {cp.stderr[:300]}")
    # sanity: do the seed tests pass with this venv?
    cp = _run([str(venv_python), "-m", "pytest", "-q"], scratch)
    notes.append(f"seed pytest rc={cp.returncode}: {(cp.stdout + cp.stderr).strip().splitlines()[-1] if (cp.stdout+cp.stderr).strip() else ''}")

    # foreman init (installs skills + agents + default config into scratch/.foreman)
    env = os.environ.copy()
    cp = _run(["uv", "run", "foreman", "init", str(scratch)], foreman_repo, env=env)
    notes.append(f"foreman init rc={cp.returncode}: {(cp.stdout+cp.stderr).strip().splitlines()[-1] if (cp.stdout+cp.stderr).strip() else ''}")

    # patch config.yaml with tuned, guardrailed settings
    cfg_path = scratch / ".foreman" / "config.yaml"
    ok = cfg_path.exists()
    if ok:
        cfg = yaml.safe_load(cfg_path.read_text()) or {}
        cfg.update({
            "model_planner": WORKER_MODEL, "model_worker": WORKER_MODEL,
            "model_evaluator": WORKER_MODEL, "model_auditor": WORKER_MODEL,
            "effort": "low", "permission_mode": permission_mode,
            "e2e_enabled": False, "janitor_enabled": False,
            "retry_strategy": "fresh",
        })
        cfg["commands"] = {"test": f"{venv_python} -m pytest -q", "lint": "",
                           "typecheck": "", "e2e": ""}
        cfg["limits"] = {"max_parallel": 1, "max_retries": 2, "daily_cost_usd": cost_ceiling}
        cfg["run_budget"] = {"max_turns": 30, "max_cost_usd": 1.50, "timeout_min": 15}
        cfg["evaluator_budget"] = {"max_turns": 20, "max_cost_usd": 0.80, "timeout_min": 10}
        cfg["git"] = {"integration_branch": "main", "merge_strategy": "merge", "open_pr": False}
        cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False))
    else:
        notes.append("config.yaml missing after foreman init")
    return {"ok": ok, "venv_python": str(venv_python), "notes": notes}
