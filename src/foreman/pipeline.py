"""Phase A — the gated pipeline (plan -> ADR/PRD -> issues), §6.

The pipeline spawns the planner, grill and slicer agents and lands their output
as repo-managed files via :class:`FileStore`. It enforces the gate ordering
(a phase cannot run until the upstream document is approved) and the rule that
the pipeline cannot start at all if a required vendored skill is missing (R2/§12).

Document-producing agents write a plain markdown *body* to a path Foreman dictates;
the pipeline then stamps canonical version/status frontmatter through FileStore, so
all approval/versioning logic stays in Foreman (R3/R4) and the agent never fights
the frontmatter.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from . import frontmatter, vendored
from .backend import AgentBackend, RunSpec
from .config import Config
from .models import DocStatus, GatedDoc, Phase
from .runner import AgentRunner, RunResult
from .skill_invocation import SkillInvocation
from .state import FileStore


class PipelineError(RuntimeError):
    pass


@dataclass
class SpawnContext:
    """Resolved inputs for one agent spawn."""

    kind: str
    label: str
    prompt: str
    cwd: Path
    model: str
    extra_dirs: list[Path]


class Pipeline:
    def __init__(
        self,
        store: FileStore,
        config: Config,
        backend: AgentBackend,
        runner: Optional[AgentRunner] = None,
        *,
        run_id_clock: Optional[Callable[[], str]] = None,
        event_sink: Optional[Callable] = None,
    ):
        self.store = store
        self.config = config
        self.backend = backend
        self.runner = runner or AgentRunner(backend)
        self.event_sink = event_sink
        if run_id_clock is None:
            counter = itertools.count(1)
            run_id_clock = lambda: f"run{next(counter):04d}"  # noqa: E731
        self._run_id_clock = run_id_clock

    # ------------------------------------------------------------------ #
    # Guards
    # ------------------------------------------------------------------ #
    def ensure_skills_installed(self) -> None:
        """Refuse to run any pipeline phase if a required skill is missing (§12)."""
        missing = vendored.missing_required(
            self.store.paths.root, self.config.required_skills
        )
        if missing:
            raise PipelineError(
                "required vendored skill(s) missing: "
                + ", ".join(missing)
                + " — run `foreman init` to install them."
            )

    # ------------------------------------------------------------------ #
    # Internal spawn
    # ------------------------------------------------------------------ #
    async def _spawn(self, slug: str, ctx: SpawnContext, budget=None) -> RunResult:
        budget = budget or self.config.run_budget
        run_id = f"{self._run_id_clock()}-{ctx.label}"
        spec = RunSpec(
            kind=ctx.kind,
            slug=slug,
            repo_root=self.store.paths.root,
            cwd=ctx.cwd,
            prompt=ctx.prompt,
            model=ctx.model,
            effort=self.config.effort,
            permission_mode=self.config.permission_mode,
            budget=budget,
            label=ctx.label,
            extra_dirs=ctx.extra_dirs,
        )
        transcript = self.store.paths.run_transcript(slug, run_id)
        result = await self.runner.run(
            spec, run_id=run_id, transcript_path=transcript, on_event=self.event_sink
        )
        # Persist run metadata + summary (R4).
        self.store.write_run_record(slug, result.record)
        if result.final_text:
            self.store.write_run_summary(slug, run_id, result.final_text)
        return result

    @staticmethod
    def _run_note(result: RunResult) -> str:
        """A human-readable note about how a run ended, for error messages."""
        reason = result.record.terminal_reason or "unknown"
        note = f"run ended: {reason}"
        if result.escalation_reason:
            note += f" — {result.escalation_reason}"
        if reason != "completed":
            note += " (raise the phase budget in config.run_budget if this was a budget kill)"
        return note

    def _read_agent_body(self, path: Path, fallback: str) -> str:
        """Read the body an agent wrote to ``path``; fall back to its final text."""
        if path.exists():
            body = frontmatter.parse(path.read_text()).body.strip()
            if body:
                return body
        return fallback.strip()

    # ------------------------------------------------------------------ #
    # Phase 1: planner
    # ------------------------------------------------------------------ #
    async def run_planner(self, slug: str) -> GatedDoc:
        self.ensure_skills_installed()
        state = self.store.load_feature(slug)
        existing = state.doc("plan")
        target_version = (existing.version + 1) if existing else 1

        # The agent writes to a Foreman-owned DRAFT path, never the canonical plan.md,
        # so the version-of-record can't be corrupted or read mid-write (it stays at
        # the prior approved/in-review version until Foreman re-stamps below).
        draft_path = self.store.paths.doc_draft_file(slug, "plan")
        draft_path.parent.mkdir(parents=True, exist_ok=True)
        draft_path.unlink(missing_ok=True)  # never read a previous run's draft
        prompt = SkillInvocation.planner(state.request, slug, draft_path)
        ctx = SpawnContext(
            kind="planner", label="planner", prompt=prompt,
            cwd=self.store.paths.root, model=self.config.model_planner, extra_dirs=[],
        )
        result = await self._spawn(slug, ctx)
        body = self._read_agent_body(draft_path, result.final_text)
        if not body:
            raise PipelineError(f"planner produced no plan content ({self._run_note(result)})")
        return self.store.write_doc(
            slug, "plan", body, version=target_version, status=DocStatus.IN_REVIEW
        )

    # ------------------------------------------------------------------ #
    # Phase 2: grill -> adr + prd
    # ------------------------------------------------------------------ #
    async def run_grill(self, slug: str) -> tuple[GatedDoc, GatedDoc]:
        self.ensure_skills_installed()
        state = self.store.load_feature(slug)
        plan = state.doc("plan")
        if plan is None or plan.status != DocStatus.APPROVED:
            raise PipelineError("cannot grill: plan is not approved")

        # Agents write to Foreman-owned DRAFT paths, never the canonical adr.md/prd.md,
        # so the version-of-record is never corrupted or read mid-write.
        adr_path = self.store.paths.doc_draft_file(slug, "adr")
        prd_path = self.store.paths.doc_draft_file(slug, "prd")
        adr_path.parent.mkdir(parents=True, exist_ok=True)
        adr_path.unlink(missing_ok=True)
        prd_path.unlink(missing_ok=True)

        # Gather reviewer comments + previous bodies for a revision pass.
        review_comments: dict[str, str] = {}
        prev_bodies: dict[str, str] = {}
        adr_v = prd_v = None
        for kind, path in (("adr", adr_path), ("prd", prd_path)):
            existing = state.doc(kind)
            if existing is not None:
                prev_bodies[kind] = existing.body
                review = self.store.latest_review(slug, kind, existing.version)
                if review and review.action == "request_changes":
                    review_comments[kind] = review.comments
            if kind == "adr":
                adr_v = (existing.version + 1) if existing else 1
            else:
                prd_v = (existing.version + 1) if existing else 1

        prompt = SkillInvocation.grill(
            slug, plan.body, adr_path, prd_path,
            review_comments=review_comments or None,
            prev_bodies=prev_bodies or None,
        )
        ctx = SpawnContext(
            kind="grill", label="grill", prompt=prompt,
            cwd=self.store.paths.root, model=self.config.model_planner, extra_dirs=[],
        )
        result = await self._spawn(slug, ctx)

        adr_body = self._read_agent_body(adr_path, "")
        prd_body = self._read_agent_body(prd_path, "")
        if not adr_body or not prd_body:
            got = [k for k, v in (("adr", adr_body), ("prd", prd_body)) if v]
            raise PipelineError(
                "grill did not produce both ADR and PRD drafts "
                f"(wrote: {', '.join(got) or 'neither'}; {self._run_note(result)})"
            )
        adr = self.store.write_doc(slug, "adr", adr_body, version=adr_v, status=DocStatus.IN_REVIEW)
        prd = self.store.write_doc(slug, "prd", prd_body, version=prd_v, status=DocStatus.IN_REVIEW)
        return adr, prd

    # ------------------------------------------------------------------ #
    # Phase 3: slicer -> issues
    # ------------------------------------------------------------------ #
    async def run_slicer(self, slug: str) -> list:
        self.ensure_skills_installed()
        state = self.store.load_feature(slug)
        prd = state.doc("prd")
        if prd is None or prd.status != DocStatus.APPROVED:
            raise PipelineError("cannot slice: PRD is not approved")

        issues_dir = self.store.paths.issues_dir(slug)
        issues_dir.mkdir(parents=True, exist_ok=True)
        prompt = SkillInvocation.slicer(slug, prd.body, issues_dir)
        ctx = SpawnContext(
            kind="slicer", label="slicer", prompt=prompt,
            cwd=self.store.paths.root, model=self.config.model_planner, extra_dirs=[],
        )
        await self._spawn(slug, ctx)
        # Re-load issues from disk (FileStore validates the schema).
        return self.store.load_feature(slug).issues
