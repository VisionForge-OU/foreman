import sys

import pytest

from foreman.backend import ClaudeBackend, RunSpec
from foreman.models import Budget
from foreman.stream_parser import AssistantMessage, ResultEvent


def _spec(d):
    return RunSpec(kind="evaluator", slug="s", repo_root=d, cwd=d, prompt="p",
                   model="m", effort="high", permission_mode="acceptEdits",
                   budget=Budget(), label="ISS-001-eval")


@pytest.mark.asyncio
async def test_claude_backend_handles_oversized_stream_line(tmp_path):
    """A single stream-json event bigger than asyncio's default 64 KiB line buffer
    must not crash the run with `ValueError: ... chunk is longer than limit`."""
    fake = tmp_path / "fakeclaude"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "big = 'x' * 200000\n"   # 200 KiB single line > the 64 KiB default
        "print(json.dumps({'type':'assistant','message':{'content':"
        "[{'type':'text','text':big}],'usage':{'input_tokens':1,'output_tokens':1}}}))\n"
        "print(json.dumps({'type':'result','subtype':'success','is_error':False,"
        "'total_cost_usd':0.0,'num_turns':1,'terminal_reason':'completed',"
        "'usage':{'input_tokens':1,'output_tokens':1}}))\n"
    )
    fake.chmod(0o755)
    backend = ClaudeBackend(executable=str(fake))
    events = [e async for e in backend.run(_spec(tmp_path))]
    assert any(isinstance(e, AssistantMessage) for e in events)
    assert any(isinstance(e, ResultEvent) for e in events)
