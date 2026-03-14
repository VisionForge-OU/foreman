"""Review-screen-v2 helpers — low-fatigue review DX (P2.3 WS5).

Pure, side-effect-free functions backing the v2 review experience: default to the
diff since the version I last acted on, surface a ``decisions made on your behalf``
digest the grill skill emits, compose a single review comment from inline answers
to the open questions, and compute read-time / word-delta triage badges. Kept as
plain functions so the TUI stays a thin renderer and every behaviour is unit-tested
above the UI seam.
"""

from __future__ import annotations

import difflib
import re

_DECISIONS_HEADING = "decisions made on your behalf"
_BULLET_PREFIXES = ("- ", "* ", "+ ")
_WORD_RE = re.compile(r"\S+")


def diff_since(old_body: str, new_body: str) -> str:
    """A readable unified line diff old → new. '' if the bodies are identical."""
    if old_body == new_body:
        return ""
    diff = difflib.unified_diff(
        old_body.splitlines(),
        new_body.splitlines(),
        fromfile="last-reviewed",
        tofile="current",
        lineterm="",
    )
    return "\n".join(diff)


def _word_count(text: str) -> int:
    return len(_WORD_RE.findall(text or ""))


def read_time_minutes(text: str, wpm: int = 220) -> float:
    """Estimated read time in minutes for ``text`` at ``wpm`` words/minute."""
    if wpm <= 0:
        wpm = 220
    return round(_word_count(text) / wpm, 2)


def word_delta(old: str, new: str) -> int:
    """Signed word-count change (new - old). Positive = grew, negative = shrank."""
    return _word_count(new) - _word_count(old)


def decisions_digest(body: str) -> list[str]:
    """Extract the bullets under a ``## Decisions made on your behalf`` heading.

    Mirrors ``models._extract_open_questions``: stops at the next heading; ignores
    ``- [x]`` / struck (``~~...~~``) bullets. Returns [] if the section is absent.
    """
    out: list[str] = []
    in_section = False
    for line in (body or "").splitlines():
        stripped = line.strip()
        low = stripped.lower()
        if low.startswith("#"):
            if _DECISIONS_HEADING in low:
                in_section = True
                continue
            if in_section:
                break  # a new heading ends the section
        if not in_section:
            continue
        if stripped.startswith(_BULLET_PREFIXES):
            content = stripped[2:].strip()
            if content.startswith(("[x]", "[X]")):
                continue
            if content.startswith("~~") and content.endswith("~~"):
                continue
            if content:
                out.append(content)
    return out


def compose_review_comment(answers: dict[str, str]) -> str:
    """Compose a single markdown review comment from {question: answer}.

    Backs the "answer all open questions then submit" flow. Blank answers are
    skipped (an unanswered question contributes nothing). '' if nothing answered.
    """
    blocks: list[str] = []
    for question, answer in (answers or {}).items():
        q = str(question).strip()
        a = str(answer).strip()
        if not a:
            continue
        if q:
            blocks.append(f"> {q}\n\n{a}")
        else:
            blocks.append(a)
    return "\n\n".join(blocks)


def badges(old_body: str, new_body: str) -> dict:
    """Dashboard triage badges for a doc draft vs. its last-reviewed version."""
    return {
        "read_min": read_time_minutes(new_body),
        "word_delta": word_delta(old_body, new_body),
        "changed": old_body != new_body,
    }
