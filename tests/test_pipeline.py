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
async def test_slicer_emits_schema_valid_issues(repo):
    pipe, store = make_pipeline(repo)
    slug = store.create_feature("todo done", "Add a done command")
    await pipe.run_planner(slug)
    store.approve_doc(slug, "plan", "arash")
    await pipe.run_grill(slug)
    # Resolve open q and approve prd.
    store.request_changes(slug, "prd", "arash", "no-op")
    await pipe.run_grill(slug)
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
