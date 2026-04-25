"""AgentRunner — supervise one agent run and enforce its guardrails (R5/§9).

The runner consumes an :class:`AgentBackend` event stream and:
- counts assistant turns and kills the run if ``max_turns`` is exceeded;
- tracks a running cost estimate and kills if ``max_cost_usd`` is exceeded
  (the native ``--max-budget-usd`` flag is a second line of defence);
- enforces a wall-clock timeout;
- is cancellable (pause/kill from the TUI);
- persists the raw transcript, a usage/cost record, and the final summary to disk;
- extracts the worker's FOREMAN-SUMMARY block.

All enforcement happens here, in Foreman — never by trusting the agent.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, Optional

from .backend import AgentBackend, RunSpec
from .cost import CostModel
from .models import RunRecord
from .stream_parser import (
    AssistantMessage,
    ResultEvent,
    StreamEvent,
    SystemInit,
    humanize,
)
from .summary import WorkerSummary, extract as extract_summary

# Terminal reasons.
COMPLETED = "completed"
KILLED_TURNS = "killed_turns"
KILLED_COST = "killed_cost"
KILLED_TIMEOUT = "killed_timeout"
KILLED_USER = "killed_user"
KILLED_STUCK = "killed_stuck"
ERROR = "error"

def should_extend(
    terminal_reason: str,
    *,
    has_session: bool,
    extensions: int,
    max_extensions: int,
    auto_extend: bool,
    requested_more: bool = False,
) -> bool:
    """Decide 'resume the SAME session with more turns' vs 'give up' (R5/§9, WS3.3).

    The single owner of the extend-vs-escalate policy, shared by the worker loop,
    the non-worker agent loop (evaluator/e2e/auditor), and the Phase-A pipeline.

    A turn cut-off (``KILLED_TURNS``) — or an explicit worker request via
    ``requested_more`` — extends; cost/timeout/stuck/error kills never do. An
    extension is only possible when auto-extend is enabled, a resumable session
    exists, and the per-run extension cap has not yet been reached.
    """
    if not auto_extend or not has_session or extensions >= max_extensions:
        return False
    return requested_more or terminal_reason == KILLED_TURNS


EventCallback = Callable[[StreamEvent], None]


async def _drain(task: "asyncio.Future") -> None:
    """Cancel a pending ``__anext__`` task and wait for it to settle.

    Must complete before the async generator can be ``aclose()``d, otherwise the
    generator is still 'running' and aclose raises.
    """
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, StopAsyncIteration, Exception):
        pass


@dataclass
class RunResult:
    record: RunRecord
    final_text: str
    summary: Optional[WorkerSummary]
    transcript_path: Optional[Path]

    @property
    def ok(self) -> bool:
        return self.record.terminal_reason == COMPLETED

    @property
    def killed(self) -> bool:
        return self.record.terminal_reason in (
            KILLED_TURNS, KILLED_COST, KILLED_TIMEOUT, KILLED_USER,
        )

    @property
    def escalation_reason(self) -> Optional[str]:
        """A human-facing reason this run needs attention, if any (§7)."""
        if self.summary and self.summary.escalate:
            return self.summary.escalation_question or "agent requested escalation"
        if self.record.terminal_reason == KILLED_TURNS:
            return f"turn budget exhausted ({self.record.num_turns} turns)"
        if self.record.terminal_reason == KILLED_COST:
            return f"cost budget exhausted (${self.record.cost_usd:.2f})"
        if self.record.terminal_reason == KILLED_TIMEOUT:
            return "wall-clock timeout"
        if self.record.terminal_reason == KILLED_STUCK:
            return "stuck: no file/test progress"
        if self.record.terminal_reason == ERROR:
            return "agent run errored"
        return None


class AgentRunner:
    def __init__(
        self,
        backend: AgentBackend,
        cost_model: Optional[CostModel] = None,
        *,
        clock: Callable[[], str] = lambda: datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
    ):
        self.backend = backend
        self.cost_model = cost_model or CostModel()
        self._clock = clock

    async def run(
        self,
        spec: RunSpec,
        *,
        run_id: str,
        transcript_path: Optional[Path] = None,
        on_event: Optional[EventCallback] = None,
        cancel_event: Optional[asyncio.Event] = None,
        timeout_s: Optional[float] = None,
        stuck_turns: Optional[int] = None,
    ) -> RunResult:
        loop = asyncio.get_event_loop()
        if timeout_s is None:
            timeout_s = spec.budget.timeout_min * 60 if spec.budget.timeout_min else None
        deadline = (loop.time() + timeout_s) if timeout_s else None

        record = RunRecord(run_id=run_id, label=spec.label, started=self._clock())
        final_text_parts: list[str] = []
        est_cost = 0.0
        turns = 0
        idle_turns = 0
        terminal = COMPLETED

        transcript = None
        if transcript_path is not None:
            transcript_path.parent.mkdir(parents=True, exist_ok=True)
            transcript = transcript_path.open("w")

        agen = self.backend.run(spec).__aiter__()
        next_task: Optional["asyncio.Future"] = None
        cancel_task: Optional["asyncio.Future"] = None
        try:
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    terminal = KILLED_USER
                    break

                next_task = asyncio.ensure_future(agen.__anext__())
                waits = {next_task}
                cancel_task = None
                if cancel_event is not None:
                    cancel_task = asyncio.ensure_future(cancel_event.wait())
                    waits.add(cancel_task)
                remaining = (deadline - loop.time()) if deadline is not None else None
                done, _pending = await asyncio.wait(
                    waits, timeout=remaining, return_when=asyncio.FIRST_COMPLETED
                )

                if not done:  # timed out
                    terminal = KILLED_TIMEOUT
                    await _drain(next_task)
                    if cancel_task:
                        cancel_task.cancel()
                    break
                if cancel_task is not None and cancel_task in done:
                    terminal = KILLED_USER
                    await _drain(next_task)
                    break
                if cancel_task is not None:
                    cancel_task.cancel()

                try:
                    event = next_task.result()
                except StopAsyncIteration:
                    break

                # persist + surface
                if transcript is not None:
                    transcript.write(json.dumps(event.raw) + "\n")
                if on_event is not None:
                    on_event(event)

                # accounting + enforcement
                if isinstance(event, SystemInit):
                    record.session_id = event.session_id or record.session_id
                elif isinstance(event, AssistantMessage):
                    turns += 1
                    record.num_turns = turns
                    record.input_tokens += event.usage.input_tokens
                    record.output_tokens += event.usage.output_tokens
                    est_cost += self.cost_model.estimate(event.usage, event.model or spec.model)
                    record.cost_usd = max(record.cost_usd, est_cost)
                    if event.text.strip():
                        final_text_parts.append(event.text)
                    if turns > spec.budget.max_turns:
                        terminal = KILLED_TURNS
                        break
                    if spec.budget.max_cost_usd and est_cost > spec.budget.max_cost_usd:
                        terminal = KILLED_COST
                        break
                    # Stuck detection: consecutive turns with no progress tool use.
                    if stuck_turns:
                        idle_turns = 0 if event.made_progress else idle_turns + 1
                        if idle_turns >= stuck_turns:
                            terminal = KILLED_STUCK
                            break
                elif isinstance(event, ResultEvent):
                    # Authoritative cost reconciliation.
                    record.cost_usd = event.total_cost_usd or record.cost_usd
                    if event.num_turns:
                        record.num_turns = event.num_turns
                    # `result.result` duplicates the final assistant message text;
                    # only use it when nothing was streamed (fallback), to avoid
                    # doubling the captured output.
                    if event.result_text and not final_text_parts:
                        final_text_parts.append(event.result_text)
                    if event.is_error and terminal == COMPLETED:
                        terminal = ERROR
                    record.terminal_reason = event.terminal_reason or terminal
                    break
        finally:
            # If we're unwinding while a step is still in flight (e.g. the run was
            # cancelled mid-`__anext__` during app shutdown), drain it first —
            # otherwise aclose() raises "asynchronous generator is already running".
            if next_task is not None and not next_task.done():
                await _drain(next_task)
            if cancel_task is not None and not cancel_task.done():
                cancel_task.cancel()
            try:
                await agen.aclose()
            except RuntimeError:
                pass  # generator already finishing — nothing left to close
            if transcript is not None:
                transcript.close()

        record.finished = self._clock()
        if not record.terminal_reason or terminal != COMPLETED:
            record.terminal_reason = terminal

        final_text = "\n".join(final_text_parts)
        summary = extract_summary(final_text)
        return RunResult(
            record=record,
            final_text=final_text,
            summary=summary,
            transcript_path=transcript_path,
        )
