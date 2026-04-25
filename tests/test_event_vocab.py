"""StreamEvent vocabulary keeps CLI specifics behind the seam (deepening 8).

Consumers above the backend seam use is_assistant / is_result / made_progress
instead of importing concrete event classes or hard-coding tool names.
"""

from foreman.stream_parser import (
    AssistantMessage,
    ResultEvent,
    StreamEvent,
    SystemInit,
    ToolUse,
)


def test_is_assistant_only_for_assistant_message():
    assert AssistantMessage().is_assistant is True
    assert ResultEvent().is_assistant is False
    assert SystemInit().is_assistant is False
    assert StreamEvent().is_assistant is False


def test_is_result_only_for_result_event():
    assert ResultEvent().is_result is True
    assert AssistantMessage().is_result is False
    assert StreamEvent().is_result is False


def test_made_progress_detects_file_command_and_mcp_tools():
    assert AssistantMessage(tool_uses=[ToolUse("Edit", "1")]).made_progress is True
    assert AssistantMessage(tool_uses=[ToolUse("Bash", "1")]).made_progress is True
    assert AssistantMessage(
        tool_uses=[ToolUse("mcp__lean-ctx__ctx_edit", "1")]
    ).made_progress is True


def test_made_progress_false_for_readonly_or_empty():
    assert AssistantMessage(tool_uses=[]).made_progress is False
    assert AssistantMessage(tool_uses=[ToolUse("Read", "1")]).made_progress is False
    assert StreamEvent().made_progress is False
