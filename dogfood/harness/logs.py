"""Deliverable writers: cost ledger, auto-review log, transition timeline, and the
resumable campaign-state.json. All append-only and crash-tolerant so a hard stop
at a guardrail ceiling still leaves coherent artifacts.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

HARNESS_SOURCES = ("harness-judge", "baseline", "probe")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Deliverables:
    def __init__(self, dogfood_dir: Path | str):
        self.dir = Path(dogfood_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.cost_ledger_md = self.dir / "cost-ledger.md"
        self.cost_ledger_json = self.dir / "cost-ledger.json"
        self.autoreview_md = self.dir / "AUTOREVIEW_LOG.md"
        self.timeline_md = self.dir / "TIMELINE.md"
        self.state_json = self.dir / "campaign-state.json"
        self._ensure_headers()

    def _ensure_headers(self) -> None:
        if not self.cost_ledger_md.exists():
            self.cost_ledger_md.write_text(
                "# Cost ledger\n\n"
                "Every worker/agent/judge run appended as it completes. "
                "`real` = real `claude` tokens spent; `mock` = MockBackend (free).\n\n"
                "| when | feature | label | source | model | real? | cost_usd | turns | note |\n"
                "|------|---------|-------|--------|-------|-------|----------|-------|------|\n"
            )
        if not self.cost_ledger_json.exists():
            self.cost_ledger_json.write_text("[]")
        if not self.autoreview_md.exists():
            self.autoreview_md.write_text(
                "# Auto-review log\n\n"
                "Every gate decision by the synthetic reviewer: draft summary, rubric "
                "scores, rationale, and the action enacted through the TUI. Lets you "
                "judge whether the reviewer was reasonable and recalibrate it.\n"
            )
        if not self.timeline_md.exists():
            self.timeline_md.write_text(
                "# State-file transition timeline\n\n"
                "Disk-truth transitions the harness waited on, with real-time latency.\n\n"
                "| when | feature | transition | latency_s | detail |\n"
                "|------|---------|------------|-----------|--------|\n"
            )

    # ----- cost ledger ----- #
    def append_cost(self, *, feature: str, label: str, source: str, model: str,
                    real: bool, cost_usd: float, turns: int = 0, note: str = "",
                    run_id: str = "") -> None:
        entries = json.loads(self.cost_ledger_json.read_text() or "[]")
        if run_id and any(e.get("run_id") == run_id for e in entries):
            return  # idempotent for foreman runs reconciled from disk
        rec = {"when": _now(), "feature": feature, "label": label, "source": source,
               "model": model, "real": real, "cost_usd": round(cost_usd, 6),
               "turns": turns, "note": note, "run_id": run_id}
        entries.append(rec)
        self.cost_ledger_json.write_text(json.dumps(entries, indent=2))
        with self.cost_ledger_md.open("a") as f:
            f.write(f"| {rec['when']} | {feature} | {label} | {source} | {model} | "
                    f"{'real' if real else 'mock'} | {rec['cost_usd']:.4f} | {turns} | {note} |\n")

    def harness_spend(self) -> float:
        """Spend Foreman never sees (judges, baseline, probe) — for the guardrail."""
        entries = json.loads(self.cost_ledger_json.read_text() or "[]")
        return sum(float(e.get("cost_usd", 0.0)) for e in entries
                   if e.get("source") in HARNESS_SOURCES)

    def recorded_run_ids(self) -> set[str]:
        entries = json.loads(self.cost_ledger_json.read_text() or "[]")
        return {e.get("run_id") for e in entries if e.get("run_id")}

    # ----- auto-review log ----- #
    def log_autoreview(self, *, feature: str, gate: str, decision: str, draft_summary: str,
                       scores: dict, rationale: str, action_detail: str = "") -> None:
        with self.autoreview_md.open("a") as f:
            f.write(f"\n## {feature} · {gate} → **{decision}**  ({_now()})\n\n")
            f.write(f"- **Draft:** {draft_summary}\n")
            if scores:
                sc = ", ".join(f"{k}={v}" for k, v in scores.items())
                f.write(f"- **Rubric:** {sc}\n")
            f.write(f"- **Rationale:** {rationale}\n")
            if action_detail:
                f.write(f"- **Action enacted (TUI):** {action_detail}\n")

    # ----- timeline ----- #
    def record_transition(self, *, feature: str, transition: str, latency_s: float,
                          detail: str = "") -> None:
        with self.timeline_md.open("a") as f:
            f.write(f"| {_now()} | {feature} | {transition} | {latency_s:.1f} | {detail} |\n")

    # ----- resumable state ----- #
    def write_state(self, state: dict) -> None:
        tmp = self.state_json.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=2))
        tmp.replace(self.state_json)

    def read_state(self) -> dict:
        if self.state_json.exists():
            try:
                return json.loads(self.state_json.read_text())
            except ValueError:
                return {}
        return {}

    # ----- generic ----- #
    def append_section(self, filename: str, text: str) -> None:
        (self.dir / filename).open("a").write(text)
