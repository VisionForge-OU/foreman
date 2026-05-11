"""One place that shells out to the real ``claude`` CLI in ``--print`` JSON mode.

Reused by the LLM auto-reviewer (read-only judgment) and the C1 plain baseline
(an ordinary one-shot session, the yardstick the pipeline is measured against).
Mirrors how Foreman itself spawns workers (``asyncio.create_subprocess_exec``),
but uses ``--output-format json`` (single result object, easy to parse) rather
than the stream parser.
"""
from __future__ import annotations

import asyncio
import json
import shutil

_BIN = shutil.which("claude") or "/home/arash/.local/share/pnpm/claude"


async def run_print_json(prompt: str, *, model: str, cwd: str,
                         permission_mode: str = "plan", effort: str = "low",
                         max_cost_usd: float = 0.25, timeout_s: float = 240,
                         extra_args: tuple[str, ...] = ()) -> dict:
    """Run one headless ``claude -p`` and return a normalized result dict.

    Returns ``{ok, result, cost_usd, num_turns, terminal_reason, error}``. Never
    raises for an agent-level failure (timeout / non-zero exit) — surfaces it in
    ``ok``/``error`` so the caller can record a finding and continue.
    """
    argv = [
        _BIN, "-p", prompt,
        "--output-format", "json",
        "--model", model,
        "--effort", effort,
        "--permission-mode", permission_mode,
        "--max-budget-usd", f"{max_cost_usd}",
        *extra_args,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=cwd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError:
        return {"ok": False, "result": "", "cost_usd": 0.0, "num_turns": 0,
                "terminal_reason": "killed_timeout", "error": f"timeout after {timeout_s}s"}
    except Exception as e:  # spawn failure
        return {"ok": False, "result": "", "cost_usd": 0.0, "num_turns": 0,
                "terminal_reason": "error", "error": str(e)}
    text = out.decode("utf-8", "replace").strip()
    try:
        data = json.loads(text)
    except ValueError:
        return {"ok": False, "result": text[:500], "cost_usd": 0.0, "num_turns": 0,
                "terminal_reason": "error",
                "error": "non-JSON output: " + err.decode("utf-8", "replace")[:300]}
    cost = float(data.get("total_cost_usd", 0.0) or 0.0)
    is_err = bool(data.get("is_error", False))
    return {
        "ok": (proc.returncode == 0) and not is_err,
        "result": data.get("result", ""),
        "cost_usd": cost,
        "num_turns": int(data.get("num_turns", 0) or 0),
        "terminal_reason": data.get("terminal_reason", "completed"),
        "error": "" if not is_err else str(data.get("result", ""))[:300],
    }


def extract_json_block(text: str) -> dict | None:
    """Pull the first balanced ``{...}`` JSON object out of an LLM's prose reply."""
    if not text:
        return None
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except ValueError:
                        break
        start = text.find("{", start + 1)
    return None
