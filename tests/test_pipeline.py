import itertools

import pytest

from foreman.backend import MockBackend
from foreman.config import Config
from foreman.demo_scripts import demo_scripts
from foreman.installer import init_repo
from foreman.models import DocStatus, Phase
from foreman.pipeline import Pipeline, PipelineError
from foreman.state import FileStore


@pytest.fixture
def repo(tmp_path):
    init_repo(tmp_path)
    return tmp_path


def make_pipeline(repo):
    counter = itertools.count(1)
    store = FileStore(repo, clock=lambda: f"2026-01-01T00:00:{next(counter):02d}Z")
    cfg = Config()
    backend = MockBackend(demo_scripts())
    rcounter = itertools.count(1)
    return Pipeline(store, cfg, backend, run_id_clock=lambda: f"run{next(rcounter):04d}"), store


@pytest.mark.asyncio
async def test_planner_produces_plan_in_review(repo):
    pipe, store = make_pipeline(repo)
    slug = store.create_feature("todo done", "Add a done command")
    plan = await pipe.run_planner(slug)
    assert plan.status == DocStatus.IN_REVIEW
    assert "Implementation Plan" in plan.body
    assert store.load_feature(slug).phase == Phase.PLAN_REVIEW
    # Run record persisted (R4).
    runs = list(store.paths.runs_dir(slug).iterdir())
    assert runs


@pytest.mark.asyncio
async def test_grill_requires_approved_plan(repo):
    pipe, store = make_pipeline(repo)
    slug = store.create_feature("todo done", "Add a done command")
    await pipe.run_planner(slug)
    with pytest.raises(PipelineError):
        await pipe.run_grill(slug)  # plan not approved yet


@pytest.mark.asyncio
async def test_grill_open_questions_loop(repo):
    pipe, store = make_pipeline(repo)
    slug = store.create_feature("todo done", "Add a done command")
    await pipe.run_planner(slug)
    store.approve_doc(slug, "plan", "arash")

    adr, prd = await pipe.run_grill(slug)
    assert "Decisions made on your behalf" in adr.body
    assert "Decisions made on your behalf" in prd.body
    # PRD v1 has an open question -> cannot approve.
    assert prd.has_open_questions
    with pytest.raises(ValueError):
        store.approve_doc(slug, "prd", "arash")

    # Reviewer answers via request_changes; revision consumes it and resolves.
    store.request_changes(slug, "prd", "arash", "Re-completing should be a silent no-op.")
    adr2, prd2 = await pipe.run_grill(slug)
    assert not prd2.has_open_questions
    assert prd2.version == 2
    # Now approvable.
    store.approve_doc(slug, "prd", "arash")
    store.approve_doc(slug, "adr", "arash")
    assert store.load_feature(slug).doc("prd").status == DocStatus.APPROVED


@pytest.mark.asyncio
async def test_grill_revision_feeds_comments_for_adr_and_prd(repo):
    """H2: reviewer answers on both grill docs must reach the next grill pass."""
    from foreman.demo_scripts import _assistant, _init, _result

    counter = itertools.count(1)
    store = FileStore(repo, clock=lambda: f"2026-01-01T00:00:{next(counter):02d}Z")
    slug = store.create_feature("todo done", "Add a done command")
    store.write_doc(slug, "plan", "# Approved plan")
    store.approve_doc(slug, "plan", "arash")
    prompts = []

    async def grill_script(spec):
        prompts.append(spec.prompt)
        adr = store.paths.doc_draft_file(slug, "adr")
        prd = store.paths.doc_draft_file(slug, "prd")
        adr.parent.mkdir(parents=True, exist_ok=True)
        yield _init(spec)
        if len(prompts) == 1:
            adr.write_text(
                "# ADR\n\n"
                "## Open questions for reviewer\n\n"
                "- Which persistence tradeoff should we choose?\n\n"
                "## Decisions made on your behalf\n\n"
                "- Kept the API shape from the plan.\n"
            )
            prd.write_text(
                "# PRD\n\n"
                "## Open questions for reviewer\n\n"
                "- Should duplicate tags be rejected or deduplicated?\n\n"
                "## Decisions made on your behalf\n\n"
                "- Kept the feature scoped to tags only.\n"
            )
        else:
            adr.write_text(
                "# ADR\n\n"
                "## Open questions for reviewer\n\n"
                "_None - reviewer chose in-memory persistence._\n\n"
                "## Decisions made on your behalf\n\n"
                "- Kept the API shape from the plan.\n\n"
                "## Changelog\n\n"
                "- Resolved persistence tradeoff from reviewer comment.\n"
            )
            prd.write_text(
                "# PRD\n\n"
                "## Open questions for reviewer\n\n"
                "_None - reviewer chose deduplication._\n\n"
                "## Decisions made on your behalf\n\n"
                "- Kept the feature scoped to tags only.\n\n"
                "## Changelog\n\n"
                "- Resolved duplicate-tag behavior from reviewer comment.\n"
            )
        yield _assistant(text="wrote grill docs")
        yield _result()

    rc = itertools.count(1)
    pipe = Pipeline(store, Config(), MockBackend({"grill": grill_script}),
                    run_id_clock=lambda: f"r{next(rc):04d}")

    adr1, prd1 = await pipe.run_grill(slug)
    assert adr1.has_open_questions
    assert prd1.has_open_questions

    store.request_changes(slug, "adr", "arash", "Use in-memory persistence for this demo.")
    store.request_changes(slug, "prd", "arash", "Deduplicate duplicate tags silently.")
    adr2, prd2 = await pipe.run_grill(slug)

    assert "Use in-memory persistence for this demo." in prompts[-1]
    assert "Deduplicate duplicate tags silently." in prompts[-1]
    assert adr2.version == 2 and prd2.version == 2
    assert not adr2.has_open_questions
    assert not prd2.has_open_questions
    assert "## Changelog" in adr2.body
    assert "## Changelog" in prd2.body

    store.approve_doc(slug, "adr", "arash")
    store.approve_doc(slug, "prd", "arash")
    st = store.load_feature(slug)
    assert st.doc("adr").status == DocStatus.APPROVED
    assert st.doc("prd").status == DocStatus.APPROVED


@pytest.mark.asyncio
async def test_slicer_requires_both_adr_and_prd_approved(repo):
    pipe, store = make_pipeline(repo)
    slug = store.create_feature("todo done", "Add a done command")
    await pipe.run_planner(slug)
    store.approve_doc(slug, "plan", "arash")
    await pipe.run_grill(slug)
    store.request_changes(slug, "prd", "arash", "no-op")
    await pipe.run_grill(slug)
    store.approve_doc(slug, "prd", "arash")

    assert store.load_feature(slug).phase == Phase.DOC_REVIEW
    with pytest.raises(PipelineError, match="ADR is not approved"):
        await pipe.run_slicer(slug)

    store.approve_doc(slug, "adr", "arash")
    assert store.load_feature(slug).phase == Phase.SLICING


@pytest.mark.asyncio
async def test_slicer_emits_schema_valid_issues(repo):
    pipe, store = make_pipeline(repo)
    slug = store.create_feature("todo done", "Add a done command")
    await pipe.run_planner(slug)
    store.approve_doc(slug, "plan", "arash")
    await pipe.run_grill(slug)
    # Resolve open q and approve prd.
    store.request_changes(slug, "prd", "arash", "no-op")
    await pipe.run_grill(slug)
    store.approve_doc(slug, "adr", "arash")
    store.approve_doc(slug, "prd", "arash")

    issues = await pipe.run_slicer(slug)
    assert [i.id for i in issues] == ["ISS-001", "ISS-002"]
    assert issues[1].depends_on == ["ISS-001"]
    for i in issues:
        assert i.prd_refs, f"{i.id} missing prd_refs traceability"
    # Queue not yet confirmed -> phase is the final gate.
    assert store.load_feature(slug).phase == Phase.QUEUE_REVIEW


@pytest.mark.asyncio
async def test_pipeline_blocked_when_skill_missing(repo):
    pipe, store = make_pipeline(repo)
    # Remove a required skill.
    import shutil
    shutil.rmtree(store.paths.skills_install_dir / "foreman-grill-docs")
    slug = store.create_feature("x", "y")
    with pytest.raises(PipelineError):
        await pipe.run_planner(slug)


@pytest.mark.asyncio
async def test_planner_revision_never_corrupts_canonical_doc(repo):
    """Regression ('reverted to v1'): a doc agent writes its OWN frontmatter; it must
    land on a Foreman draft path, never the canonical plan.md, so the version-of-record
    is never reverted or read mid-write."""
    from foreman.demo_scripts import _init, _result

    counter = itertools.count(1)
    store = FileStore(repo, clock=lambda: f"2026-01-01T00:00:{next(counter):02d}Z")
    slug = store.create_feature("Add tagging", "tags on notes")
    # Prior state: Foreman-owned canonical plan at v3, in review.
    store.write_doc(slug, "plan", "# old plan v3", version=3, status=DocStatus.IN_REVIEW)
    canonical = store.paths.doc_file(slug, "plan")
    seen = {}

    async def planner_script(spec):
        yield _init(spec)
        # Agent writes its OWN frontmatter (version 1 / draft) to whatever path it was
        # handed; that must be the Foreman draft path, not the canonical doc.
        draft = store.paths.doc_draft_file(slug, "plan")
        draft.write_text("---\nkind: plan\nversion: 1\nstatus: draft\n---\n# revised plan\n")
        # Mid-run the canonical doc must be UNCHANGED (still v3) — not reverted to v1.
        d = store.load_feature(slug).doc("plan")
        seen["mid"] = (d.version, d.status.value)
        seen["canonical_corrupted"] = "status: draft" in canonical.read_text()
        yield _result()

    rc = itertools.count(1)
    pipe = Pipeline(store, Config(), MockBackend({"planner": planner_script}),
                    run_id_clock=lambda: f"r{next(rc):04d}")
    plan = await pipe.run_planner(slug)

    # Mid-run: canonical stayed v3/in_review; the agent's v1/draft never showed.
    assert seen["mid"] == (3, "in_review")
    assert seen["canonical_corrupted"] is False
    # After: Foreman re-stamped the canonical to v4 and owns the frontmatter.
    assert plan.version == 4 and plan.status == DocStatus.IN_REVIEW
    assert plan.body.strip() == "# revised plan"   # agent frontmatter stripped


@pytest.mark.asyncio
async def test_planner_budget_is_model_floored(repo):
    """Issue #1 wiring: a small-tier planner model receives the tier-floored turn
    budget at the real spawn site, even when run_budget.max_turns is set lower."""
    from foreman.demo_scripts import _init, _result, _assistant

    counter = itertools.count(1)
    store = FileStore(repo, clock=lambda: f"2026-01-01T00:00:{next(counter):02d}Z")
    cfg = Config()
    cfg.model_planner = "claude-haiku-4-5"   # small tier (floor 60)
    cfg.run_budget.max_turns = 30            # below the floor → should be raised
    slug = store.create_feature("Add tagging", "tags on notes")
    seen_turns = []

    async def planner_script(spec):
        seen_turns.append(spec.budget.max_turns)
        yield _init(spec)
        draft = store.paths.doc_draft_file(slug, "plan")
        draft.parent.mkdir(parents=True, exist_ok=True)
        draft.write_text("# Implementation Plan\n\nThe plan body.")
        yield _assistant(text="wrote the plan draft")
        yield _result()

    rc = itertools.count(1)
    pipe = Pipeline(store, cfg, MockBackend({"planner": planner_script}),
                    run_id_clock=lambda: f"r{next(rc):04d}")
    await pipe.run_planner(slug)
    assert seen_turns == [60]                 # 30 floored up to the small-tier floor


@pytest.mark.asyncio
async def test_planner_resumes_on_turn_kill(repo):
    """Phase-A: a planner cut off by the turn budget is resumed (same session) to
    finish, rather than handing back a half-written draft (the planner-kill problem)."""
    from foreman.demo_scripts import _init, _result, _assistant
    from foreman.stream_parser import parse_event

    counter = itertools.count(1)
    store = FileStore(repo, clock=lambda: f"2026-01-01T00:00:{next(counter):02d}Z")
    cfg = Config()
    cfg.run_budget.max_turns = 2          # tiny → first run is cut off
    # This test targets the extension loop; keep the tiny budget by disabling the
    # model-aware turn floor (issue #1) so it actually reaches the runner.
    cfg.turn_tiers = {"small": 1, "large": 1}
    slug = store.create_feature("Add tagging", "tags on notes")
    sessions = []

    async def planner_script(spec):
        sessions.append(spec.session_id)
        yield _init(spec)
        if len(sessions) == 1:
            for i in range(4):            # 4 > max_turns 2 → KILLED_TURNS
                yield parse_event({"type": "assistant", "message": {
                    "content": [{"type": "text", "text": f"exploring {i}"}],
                    "usage": {"input_tokens": 1}}})
            yield _result()
        else:                             # resumed: write the draft + finish
            draft = store.paths.doc_draft_file(slug, "plan")
            draft.parent.mkdir(parents=True, exist_ok=True)
            draft.write_text("# Implementation Plan\n\nThe plan body.")
            yield _assistant(text="wrote the plan draft")
            yield _result()

    rc = itertools.count(1)
    pipe = Pipeline(store, cfg, MockBackend({"planner": planner_script}),
                    run_id_clock=lambda: f"r{next(rc):04d}")
    plan = await pipe.run_planner(slug)
    assert "Implementation Plan" in plan.body
    assert len(sessions) == 2             # initial + one resume
    assert sessions[1] == "demo-planner"  # resumed the SAME session


@pytest.mark.asyncio
async def test_planner_revision_feeds_reviewer_comment_and_prior_plan(repo):
    """The plan revise loop must FEED the reviewer's comment + the prior plan to the
    planner — not rely on it incidentally discovering .foreman/reviews/ while exploring."""
    from foreman.demo_scripts import _init, _result, _assistant
    counter = itertools.count(1)
    store = FileStore(repo, clock=lambda: f"2026-01-01T00:00:{next(counter):02d}Z")
    slug = store.create_feature("Add tagging", "Notes can carry tags via the API.")
    captured = {}

    async def planner_script(spec):
        captured["prompt"] = spec.prompt
        draft = store.paths.doc_draft_file(slug, "plan")
        draft.parent.mkdir(parents=True, exist_ok=True)
        draft.write_text("# Plan\n\nORIGINAL PLAN BODY")
        yield _init(spec)
        yield _assistant(text="wrote plan")
        yield _result()

    rc = itertools.count(1)
    pipe = Pipeline(store, Config(), MockBackend({"planner": planner_script}),
                    run_id_clock=lambda: f"r{next(rc):04d}")
    await pipe.run_planner(slug)                                    # v1
    store.request_changes(slug, "plan", reviewer="rev",
                          comments="add a color field to each tag")
    await pipe.run_planner(slug)                                    # v2 revision

    assert "add a color field to each tag" in captured["prompt"]   # comment fed
    assert "ORIGINAL PLAN BODY" in captured["prompt"]              # prior plan fed
