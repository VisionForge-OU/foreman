"""Tolerant parser for Claude Code ``--output-format stream-json`` (R1, §3).

Converts newline-delimited JSON event lines into typed events. The schema was
captured empirically from ``claude`` v2.1.174 (see DECISIONS.md §0 and the
``fixtures/`` directory). Robustness is the priority: any line that is not valid
JSON, or whose ``type`` we don't recognise, becomes an :class:`UnknownEvent` and
never raises — the TUI must survive schema drift.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional


# --------------------------------------------------------------------------- #
# Event types
# --------------------------------------------------------------------------- #
# Native tool names that count as the agent making real progress (file/command
# activity). MCP tools count too (see ``AssistantMessage.made_progress``): a worker
# that edits/runs via MCP equivalents (e.g. lean-ctx ctx_edit / ctx_shell instead of
# Edit / Bash) is still actively working, not stuck. This CLI-specific knowledge
# lives here, behind the event vocabulary, so consumers never hard-code tool names.
_PROGRESS_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit", "Bash", "Skill"}


@dataclass
class StreamEvent:
    """Base class for all parsed events. ``raw`` is the original JSON dict.

    The ``is_*`` / ``made_progress`` predicates let consumers above the backend
    seam (runner stuck-detection, the TUI) react to events without importing the
    concrete CLI event classes — so a stream-schema change stays contained here.
    """

    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def is_assistant(self) -> bool:
        """True only for an assistant message (one model 'turn')."""
        return False

    @property
    def is_result(self) -> bool:
        """True only for the terminal result event (carries the authoritative cost)."""
        return False

    @property
    def made_progress(self) -> bool:
        """True if this event shows the agent doing real file/command work."""
        return False


@dataclass
class SystemInit(StreamEvent):
    session_id: str = ""
    model: str = ""
    cwd: str = ""
    tools: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    permission_mode: str = ""


@dataclass
class HookEvent(StreamEvent):
    subtype: str = ""
    hook_name: str = ""


@dataclass
class ThinkingTokens(StreamEvent):
    estimated_tokens: int = 0
    delta: int = 0


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    @classmethod
    def from_dict(cls, d: Optional[dict[str, Any]]) -> "Usage":
        d = d or {}
        return cls(
            input_tokens=int(d.get("input_tokens", 0) or 0),
            output_tokens=int(d.get("output_tokens", 0) or 0),
            cache_creation_input_tokens=int(d.get("cache_creation_input_tokens", 0) or 0),
            cache_read_input_tokens=int(d.get("cache_read_input_tokens", 0) or 0),
        )


@dataclass
class ToolUse:
    name: str
    tool_id: str
    tool_input: dict[str, Any] = field(default_factory=dict)


@dataclass
class AssistantMessage(StreamEvent):
    text: str = ""
    thinking: str = ""
    tool_uses: list[ToolUse] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    model: str = ""

    @property
    def is_assistant(self) -> bool:
        return True

    @property
    def made_progress(self) -> bool:
        # Progress = a native file/command tool OR any MCP tool. Pure rumination
        # and native read-only browsing still count as idle, so a genuinely
        # spinning worker is caught while an MCP-driven one is not killed.
        return any(
            tu.name in _PROGRESS_TOOLS or tu.name.startswith("mcp__")
            for tu in self.tool_uses
        )


@dataclass
class UserMessage(StreamEvent):
    """Tool results fed back to the model during an agentic loop."""

    text: str = ""


@dataclass
class RateLimitEvent(StreamEvent):
    status: str = ""


@dataclass
class ResultEvent(StreamEvent):
    subtype: str = ""
    is_error: bool = False
    total_cost_usd: float = 0.0
    num_turns: int = 0
    result_text: str = ""
    usage: Usage = field(default_factory=Usage)
    terminal_reason: str = ""

    @property
    def is_result(self) -> bool:
        return True


@dataclass
class UnknownEvent(StreamEvent):
    type: str = ""
    subtype: str = ""


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
def _parse_content_blocks(message: dict[str, Any]) -> tuple[str, str, list[ToolUse]]:
    text_parts: list[str] = []
    thinking_parts: list[str] = []
    tool_uses: list[ToolUse] = []
    for block in message.get("content", []) or []:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            text_parts.append(str(block.get("text", "")))
        elif btype == "thinking":
            thinking_parts.append(str(block.get("thinking", "")))
        elif btype == "tool_use":
            tool_uses.append(
                ToolUse(
                    name=str(block.get("name", "")),
                    tool_id=str(block.get("id", "")),
                    tool_input=block.get("input", {}) or {},
                )
            )
    return "\n".join(text_parts), "\n".join(thinking_parts), tool_uses


def parse_event(obj: dict[str, Any]) -> StreamEvent:
    """Turn one decoded JSON dict into a typed event. Never raises."""
    etype = obj.get("type")

    if etype == "system":
        subtype = obj.get("subtype", "")
        if subtype == "init":
            return SystemInit(
                raw=obj,
                session_id=str(obj.get("session_id", "")),
                model=str(obj.get("model", "")),
                cwd=str(obj.get("cwd", "")),
                tools=list(obj.get("tools", []) or []),
                skills=list(obj.get("skills", []) or []),
                permission_mode=str(obj.get("permissionMode", "")),
            )
        if subtype == "thinking_tokens":
            return ThinkingTokens(
                raw=obj,
                estimated_tokens=int(obj.get("estimated_tokens", 0) or 0),
                delta=int(obj.get("estimated_tokens_delta", 0) or 0),
            )
        if subtype in ("hook_started", "hook_response"):
            return HookEvent(raw=obj, subtype=subtype, hook_name=str(obj.get("hook_name", "")))
        return UnknownEvent(raw=obj, type="system", subtype=str(subtype))

    if etype == "assistant":
        message = obj.get("message", {}) or {}
        text, thinking, tool_uses = _parse_content_blocks(message)
        return AssistantMessage(
            raw=obj,
            text=text,
            thinking=thinking,
            tool_uses=tool_uses,
            usage=Usage.from_dict(message.get("usage")),
            model=str(message.get("model", "")),
        )

    if etype == "user":
        message = obj.get("message", {}) or {}
        text, _, _ = _parse_content_blocks(message)
        return UserMessage(raw=obj, text=text)

    if etype == "rate_limit_event":
        info = obj.get("rate_limit_info", {}) or {}
        return RateLimitEvent(raw=obj, status=str(info.get("status", "")))

    if etype == "result":
        return ResultEvent(
            raw=obj,
            subtype=str(obj.get("subtype", "")),
            is_error=bool(obj.get("is_error", False)),
            total_cost_usd=float(obj.get("total_cost_usd", 0.0) or 0.0),
            num_turns=int(obj.get("num_turns", 0) or 0),
            result_text=str(obj.get("result", "")),
            usage=Usage.from_dict(obj.get("usage")),
            terminal_reason=str(obj.get("terminal_reason", "")),
        )

    return UnknownEvent(raw=obj, type=str(etype), subtype=str(obj.get("subtype", "")))


def parse_line(line: str) -> Optional[StreamEvent]:
    """Parse a single NDJSON line. Returns None for blank lines.

    A non-JSON line is wrapped as an UnknownEvent carrying the raw text rather
    than raising (e.g. a stray log line on stderr-merged streams).
    """
    line = line.strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return UnknownEvent(raw={"_unparsed": line}, type="_unparsed")
    if not isinstance(obj, dict):
        return UnknownEvent(raw={"_nonobject": obj}, type="_nonobject")
    return parse_event(obj)


def humanize(event: StreamEvent) -> Optional[str]:
    """One-line, human-readable rendering for the worker log pane (§8.3).

    Thinking is elided; tool calls are one-lined; final text is shown. Returns
    None for events not worth showing in the log.
    """
    if isinstance(event, SystemInit):
        return f"▶ session {event.session_id[:8]} · model {event.model} · {event.permission_mode}"
    if isinstance(event, ThinkingTokens):
        return None  # elide thinking
    if isinstance(event, AssistantMessage):
        lines = []
        for tu in event.tool_uses:
            lines.append(f"  ⚙ {tu.name}({_summarize_input(tu.tool_input)})")
        if event.text.strip():
            lines.append(f"  {event.text.strip().splitlines()[0][:200]}")
        return "\n".join(lines) if lines else None
    if isinstance(event, UserMessage):
        if event.text.strip():
            first = event.text.strip().splitlines()[0]
            return f"  ↩ {first[:160]}"
        return None
    if isinstance(event, ResultEvent):
        flag = "✓" if not event.is_error else "✗"
        return (
            f"{flag} done · {event.num_turns} turns · "
            f"${event.total_cost_usd:.4f} · {event.terminal_reason or event.subtype}"
        )
    if isinstance(event, HookEvent):
        return None
    if isinstance(event, RateLimitEvent):
        return None
    return None


def _summarize_input(d: dict[str, Any]) -> str:
    for key in ("command", "file_path", "path", "pattern", "description", "skill"):
        if key in d:
            return f"{key}={str(d[key])[:60]}"
    if not d:
        return ""
    k = next(iter(d))
    return f"{k}=…"
