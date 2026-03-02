"""Shared test helpers: build StreamEvents and mock backend scripts."""

from __future__ import annotations

from typing import AsyncIterator

from foreman.backend import RunSpec
from foreman.stream_parser import StreamEvent, parse_event


def init_event(session_id="sess-1234abcd", model="claude-fable-5", permission="acceptEdits"):
    return parse_event({
        "type": "system", "subtype": "init",
        "session_id": session_id, "model": model, "cwd": "/repo",
        "tools": ["Bash", "Edit"], "skills": ["foreman-tdd"],
        "permissionMode": permission,
    })


def assistant_event(text="", thinking="", tool_uses=None, usage=None, model="claude-fable-5"):
    content = []
    if thinking:
        content.append({"type": "thinking", "thinking": thinking})
    for tu in (tool_uses or []):
        content.append({"type": "tool_use", "name": tu[0], "id": tu[1] if len(tu) > 1 else "t1",
                        "input": tu[2] if len(tu) > 2 else {}})
    if text:
        content.append({"type": "text", "text": text})
    return parse_event({
        "type": "assistant",
        "message": {"model": model, "content": content,
                    "usage": usage or {"input_tokens": 10, "output_tokens": 5}},
    })


def result_event(cost=0.05, num_turns=3, result="done", is_error=False,
                 terminal_reason="completed", subtype="success"):
    return parse_event({
        "type": "result", "subtype": subtype, "is_error": is_error,
        "total_cost_usd": cost, "num_turns": num_turns, "result": result,
        "terminal_reason": terminal_reason,
        "usage": {"input_tokens": 100, "output_tokens": 50},
    })


def make_script(events: list[StreamEvent], *, side_effect=None):
    """Wrap a list of pre-built events into a MockBackend script."""
    async def script(spec: RunSpec) -> AsyncIterator[StreamEvent]:
        if side_effect is not None:
            side_effect(spec)
        for ev in events:
            yield ev
    return script
