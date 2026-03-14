"""Regression ratchet (WS1.4).

Maintains a per-feature baseline of passing test ids, snapshotted after each
merged issue. The merge gate requires that no test which was passing at the
baseline is now failing; a regression auto-bounces the work with the **specific
newly-failing tests** named.

Test ids come from parsing the test command's output. The parser is robust to:
1. an authoritative ``FOREMAN-TEST-RESULTS {json}`` trailer (emitted by the
   ``foreman-test`` wrapper) — precise passed+failed ids;
2. pytest verbose output (``path::test PASSED|FAILED|ERROR``);
3. pytest quiet output (``FAILED path::test`` lines — failures only).

When no per-test ids can be extracted the baseline stays empty and the ratchet
makes no claim — the overall pass/fail gate (``verify``) still applies. So the
ratchet only ever *adds* safety; it never blocks on ambiguity.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

_RESULTS_TRAILER = re.compile(r"FOREMAN-TEST-RESULTS\s+(\{.*\})")
_VERBOSE = re.compile(r"^(?P<id>[\w./\[\]:-]+::[\w\[\]:.-]+)\s+(?P<status>PASSED|FAILED|ERROR)\b")
_QUIET_FAIL = re.compile(r"^(?:FAILED|ERROR)\s+(?P<id>[\w./\[\]:-]+::[\w\[\]:.-]+)")


@dataclass
class TestResults:
    passed: set[str] = field(default_factory=set)
    failed: set[str] = field(default_factory=set)
    has_passed_ids: bool = False  # could we enumerate passing ids at all?


def parse_test_output(text: str) -> TestResults:
    text = text or ""
    # 1. Authoritative trailer (foreman-test wrapper).
    for m in _RESULTS_TRAILER.finditer(text):
        try:
            obj = json.loads(m.group(1))
        except (json.JSONDecodeError, ValueError):
            continue
        passed = set(map(str, obj.get("passed", []) or []))
        failed = set(map(str, obj.get("failed", []) or []))
        return TestResults(passed=passed, failed=failed, has_passed_ids=True)

    # 2. pytest verbose.
    passed: set[str] = set()
    failed: set[str] = set()
    saw_verbose = False
    for line in text.splitlines():
        m = _VERBOSE.match(line.strip())
        if m:
            saw_verbose = True
            (passed if m.group("status") == "PASSED" else failed).add(m.group("id"))
    if saw_verbose:
        return TestResults(passed=passed, failed=failed, has_passed_ids=True)

    # 3. pytest quiet — failures only.
    for line in text.splitlines():
        m = _QUIET_FAIL.match(line.strip())
        if m:
            failed.add(m.group("id"))
    return TestResults(passed=set(), failed=failed, has_passed_ids=False)


@dataclass
class RatchetResult:
    ok: bool
    regressed: list[str] = field(default_factory=list)
    reason: str = ""

    def report(self) -> str:
        if self.ok:
            return "regression ratchet: OK (no previously-passing test now fails)"
        return (
            "regression ratchet: BLOCKED — previously-passing test(s) now failing:\n"
            + "\n".join(f"  - {t}" for t in self.regressed)
        )


def read_baseline(path: Path | str) -> set[str]:
    path = Path(path)
    if not path.exists():
        return set()
    try:
        obj = json.loads(path.read_text())
    except (json.JSONDecodeError, ValueError, OSError):
        return set()
    return set(map(str, obj.get("passed", []) or []))


def write_baseline(path: Path | str, passed: set[str]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"passed": sorted(passed)}, indent=2) + "\n")


def update_baseline(path: Path | str, now: TestResults) -> set[str]:
    """After a merge, fold the now-passing ids into the baseline (monotone grow).

    We *add* newly-passing ids rather than replace, so a test that simply wasn't
    run in this issue's slice doesn't silently drop out of the protected set.
    Failing ids are removed from the baseline (they're no longer "passing").
    """
    if not now.has_passed_ids:
        return read_baseline(path)
    baseline = read_baseline(path)
    baseline |= now.passed
    baseline -= now.failed
    write_baseline(path, baseline)
    return baseline


def check(baseline: set[str], now: TestResults) -> RatchetResult:
    """Regression = a baseline-passing test that is now failing."""
    regressed = sorted(baseline & now.failed)
    if regressed:
        return RatchetResult(ok=False, regressed=regressed)
    return RatchetResult(ok=True)
