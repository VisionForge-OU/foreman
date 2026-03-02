import json

from foreman import stream_parser as sp


def test_parse_init():
    line = json.dumps({"type": "system", "subtype": "init", "session_id": "abc",
                       "model": "claude-fable-5", "permissionMode": "acceptEdits",
                       "tools": ["Bash"], "skills": ["foreman-tdd"]})
    ev = sp.parse_line(line)
    assert isinstance(ev, sp.SystemInit)
    assert ev.session_id == "abc"
    assert ev.permission_mode == "acceptEdits"


def test_parse_assistant_with_thinking_text_tooluse():
    line = json.dumps({"type": "assistant", "message": {
        "model": "m", "content": [
            {"type": "thinking", "thinking": "hmm"},
            {"type": "tool_use", "name": "Bash", "id": "t1", "input": {"command": "ls"}},
            {"type": "text", "text": "all good"},
        ], "usage": {"input_tokens": 3, "output_tokens": 7}}})
    ev = sp.parse_line(line)
    assert isinstance(ev, sp.AssistantMessage)
    assert ev.text == "all good"
    assert ev.thinking == "hmm"
    assert ev.tool_uses[0].name == "Bash"
    assert ev.usage.output_tokens == 7


def test_parse_result():
    line = json.dumps({"type": "result", "subtype": "success", "is_error": False,
                       "total_cost_usd": 0.123, "num_turns": 4, "result": "PONG",
                       "terminal_reason": "completed", "usage": {"input_tokens": 1}})
    ev = sp.parse_line(line)
    assert isinstance(ev, sp.ResultEvent)
    assert ev.total_cost_usd == 0.123
    assert ev.num_turns == 4
    assert ev.result_text == "PONG"


def test_unknown_type_does_not_raise():
    ev = sp.parse_line(json.dumps({"type": "brand_new_event_2027", "foo": 1}))
    assert isinstance(ev, sp.UnknownEvent)
    assert ev.type == "brand_new_event_2027"


def test_garbage_line_is_unknown_not_exception():
    ev = sp.parse_line("this is not json {")
    assert isinstance(ev, sp.UnknownEvent)
    assert ev.raw["_unparsed"].startswith("this is not")


def test_blank_line_returns_none():
    assert sp.parse_line("   \n") is None


def test_humanize_elides_thinking_and_oneliners_tools():
    a = sp.parse_line(json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Edit", "id": "x", "input": {"file_path": "a.py"}}]}}))
    line = sp.humanize(a)
    assert "Edit" in line and "a.py" in line


def test_real_captured_fixture_parses(tmp_path):
    # A line lifted verbatim from the live v2.1.174 capture (DECISIONS.md §0).
    real = ('{"type":"result","subtype":"success","is_error":false,'
            '"duration_ms":2358,"num_turns":1,"result":"PONG",'
            '"total_cost_usd":0.0179604,"terminal_reason":"completed",'
            '"usage":{"input_tokens":10,"output_tokens":48}}')
    ev = sp.parse_line(real)
    assert isinstance(ev, sp.ResultEvent)
    assert ev.result_text == "PONG"
    assert abs(ev.total_cost_usd - 0.0179604) < 1e-9
