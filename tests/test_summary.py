from foreman.summary import extract


GOOD = """
Here is my work.

```json
{
  "schema": "foreman-summary/v1",
  "issue_id": "ISS-001",
  "files_touched": ["src/a.py", "tests/test_a.py"],
  "tests_added": ["adds two numbers"],
  "commands": {
    "test": {"ran": true, "passed": true, "output_tail": "1 passed"},
    "lint": {"ran": true, "passed": true, "output_tail": "ok"},
    "typecheck": {"ran": false, "passed": null, "output_tail": "not configured"}
  },
  "open_concerns": [],
  "escalate": false,
  "escalation_question": ""
}
```
"""


def test_extract_basic():
    s = extract(GOOD)
    assert s is not None
    assert s.issue_id == "ISS-001"
    assert s.files_touched == ["src/a.py", "tests/test_a.py"]
    assert s.commands["test"].passed is True
    assert s.commands["typecheck"].ran is False
    assert s.claims_pass is True


def test_escalation_flag():
    text = """```json
{"schema":"foreman-summary/v1","issue_id":"ISS-002","escalate":true,
 "escalation_question":"Which auth provider?","commands":{}}
```"""
    s = extract(text)
    assert s.escalate is True
    assert "auth provider" in s.escalation_question


def test_claims_pass_false_when_a_command_failed():
    text = """```json
{"schema":"foreman-summary/v1","issue_id":"ISS-003",
 "commands":{"test":{"ran":true,"passed":false,"output_tail":"1 failed"}}}
```"""
    assert extract(text).claims_pass is False


def test_no_summary_returns_none():
    assert extract("no block here") is None


def test_ignores_non_foreman_json_blocks_and_takes_last_valid():
    text = """```json
{"schema":"something-else","x":1}
```
```json
{"schema":"foreman-summary/v1","issue_id":"LAST","commands":{}}
```"""
    assert extract(text).issue_id == "LAST"


def test_request_more_turns_parsed_tolerantly():
    base = '{"schema":"foreman-summary/v1","issue_id":"ISS-001"%s}'
    assert extract("```json\n" + base % ',"request_more_turns":15' + "\n```").request_more_turns == 15
    assert extract("```json\n" + base % "" + "\n```").request_more_turns == 0      # absent
    assert extract("```json\n" + base % ',"request_more_turns":null' + "\n```").request_more_turns == 0
    assert extract("```json\n" + base % ',"request_more_turns":-3' + "\n```").request_more_turns == 0  # clamp ≥0
