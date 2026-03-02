import asyncio

import pytest

from foreman.backend import MockBackend, RunSpec
from foreman.cost import CostModel, Price
from foreman.models import Budget
from foreman.runner import (
    AgentRunner, COMPLETED, KILLED_TURNS, KILLED_COST, KILLED_TIMEOUT, KILLED_USER,
)
from foreman.stream_parser import StreamEvent

from conftest import assistant_event, init_event, result_event, make_script


def make_spec(budget: Budget, kind="tdd", label="ISS-001", repo=None):
    repo = repo or "/tmp/x"
    from pathlib import Path
    return RunSpec(
        kind=kind, slug="feat", repo_root=Path(repo), cwd=Path(repo),
        prompt="do it", model="claude-fable-5", effort="high",
        permission_mode="acceptEdits", budget=budget, label=label,
    )


async def run_with(events, budget, **kw):
    backend = MockBackend({"tdd": make_script(events)})
    runner = AgentRunner(backend, **kw.pop("runner_kw", {}))
    spec = make_spec(budget)
    return await runner.run(spec, run_id="r1", **kw)


@pytest.mark.asyncio
async def test_happy_path_captures_session_and_cost(tmp_path):
    events = [init_event(session_id="sess-abc"),
              assistant_event(text="working"),
              result_event(cost=0.25, num_turns=3, result="done")]
    res = await run_with(events, Budget(max_turns=80, max_cost_usd=5))
    assert res.ok
    assert res.record.terminal_reason == COMPLETED
    assert res.record.session_id == "sess-abc"
    assert res.record.cost_usd == 0.25
    assert res.escalation_reason is None


@pytest.mark.asyncio
async def test_turn_budget_kills(tmp_path):
    # 5 assistant turns but max_turns=2 -> killed at turn 3.
    events = [init_event()] + [assistant_event(text=f"t{i}") for i in range(5)] + [result_event()]
    res = await run_with(events, Budget(max_turns=2, max_cost_usd=99))
    assert res.record.terminal_reason == KILLED_TURNS
    assert res.killed
    assert "turn budget" in res.escalation_reason


@pytest.mark.asyncio
async def test_cost_budget_kills_via_foreman_estimate(tmp_path):
    # Pricey model: each assistant message ~ $0.50; budget 0.6 -> killed on 2nd.
    pricey = CostModel({"claude-fable-5": Price(0, 1_000_000.0, 0, 0)})
    big = {"input_tokens": 0, "output_tokens": 500_000, "cache_creation_input_tokens": 0,
           "cache_read_input_tokens": 0}
    events = [init_event(),
              assistant_event(text="a", usage=big),
              assistant_event(text="b", usage=big),
              result_event()]
    res = await run_with(events, Budget(max_turns=80, max_cost_usd=0.6),
                         runner_kw={"cost_model": pricey})
    assert res.record.terminal_reason == KILLED_COST
    assert "cost budget" in res.escalation_reason


@pytest.mark.asyncio
async def test_cancel_event_kills(tmp_path):
    async def slow_script(spec):
        yield init_event()
        await asyncio.sleep(0.3)
        yield assistant_event(text="late")
    backend = MockBackend({"tdd": slow_script})
    runner = AgentRunner(backend)
    cancel = asyncio.Event()
    spec = make_spec(Budget())

    async def trigger():
        await asyncio.sleep(0.05)
        cancel.set()

    res, _ = await asyncio.gather(
        runner.run(spec, run_id="r1", cancel_event=cancel), trigger()
    )
    assert res.record.terminal_reason == KILLED_USER


@pytest.mark.asyncio
async def test_timeout_kills(tmp_path):
    async def slow_script(spec):
        yield init_event()
        await asyncio.sleep(0.5)
        yield result_event()
    backend = MockBackend({"tdd": slow_script})
    runner = AgentRunner(backend)
    spec = make_spec(Budget())
    res = await runner.run(spec, run_id="r1", timeout_s=0.1)
    assert res.record.terminal_reason == KILLED_TIMEOUT


@pytest.mark.asyncio
async def test_transcript_written(tmp_path):
    events = [init_event(), assistant_event(text="x"), result_event()]
    tpath = tmp_path / "transcript.jsonl"
    await run_with(events, Budget(), transcript_path=tpath)
    lines = tpath.read_text().strip().splitlines()
    assert len(lines) == 3


@pytest.mark.asyncio
async def test_final_text_not_duplicated_by_result_event(tmp_path):
    # Real CLI repeats the final assistant text in result.result; we must not
    # double it (regression for the 'PONG\nPONG' bug found in real-backend smoke).
    events = [init_event(), assistant_event(text="working"),
              result_event(result="working")]
    res = await run_with(events, Budget())
    assert res.final_text == "working"


@pytest.mark.asyncio
async def test_result_text_used_as_fallback_when_no_assistant_text(tmp_path):
    events = [init_event(), result_event(result="only-final")]
    res = await run_with(events, Budget())
    assert res.final_text == "only-final"


@pytest.mark.asyncio
async def test_summary_extracted_from_final_text(tmp_path):
    summary_text = ('```json\n{"schema":"foreman-summary/v1","issue_id":"ISS-001",'
                    '"commands":{"test":{"ran":true,"passed":true}}}\n```')
    events = [init_event(), assistant_event(text=summary_text), result_event(result="")]
    res = await run_with(events, Budget())
    assert res.summary is not None
    assert res.summary.claims_pass is True
