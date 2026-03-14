"""WS5 — review-screen-v2 helpers: diff, read-time, deltas, digest, comment, badges."""

from foreman import review


# --- diff_since --- #

def test_diff_since_identical_is_empty():
    body = "line one\nline two\n"
    assert review.diff_since(body, body) == ""


def test_diff_since_shows_markers():
    old = "alpha\nbeta\ngamma\n"
    new = "alpha\nBETA\ngamma\n"
    d = review.diff_since(old, new)
    assert d != ""
    assert "-beta" in d
    assert "+BETA" in d
    assert "@@" in d  # unified diff hunk header


# --- read_time_minutes --- #

def test_read_time_monotonic():
    short = "word " * 10
    long = "word " * 1000
    assert review.read_time_minutes(short) < review.read_time_minutes(long)


def test_read_time_empty_is_zero():
    assert review.read_time_minutes("") == 0.0


def test_read_time_respects_wpm():
    text = "word " * 220
    assert review.read_time_minutes(text, wpm=220) == 1.0
    assert review.read_time_minutes(text, wpm=440) == 0.5
    # Degenerate wpm falls back to the default rather than dividing by zero.
    assert review.read_time_minutes(text, wpm=0) == 1.0


# --- word_delta --- #

def test_word_delta_sign():
    assert review.word_delta("a b c", "a b c d e") == 2     # grew
    assert review.word_delta("a b c d e", "a b") == -3      # shrank
    assert review.word_delta("a b c", "x y z") == 0         # same count


# --- decisions_digest --- #

def test_decisions_digest_extracts_bullets():
    body = """\
# PRD

## Decisions made on your behalf

- Chose optimistic locking over a mutex.
- Defaulted retention to 30 days.
- [x] already-resolved, ignored
- ~~struck out~~

## Problem Statement

- this bullet is in another section
"""
    digest = review.decisions_digest(body)
    assert digest == [
        "Chose optimistic locking over a mutex.",
        "Defaulted retention to 30 days.",
    ]


def test_decisions_digest_absent_returns_empty():
    assert review.decisions_digest("# PRD\n\n## Problem\n\n- x\n") == []
    assert review.decisions_digest("") == []


# --- compose_review_comment --- #

def test_compose_review_comment_skips_blanks():
    answers = {
        "Should we shard?": "Yes, by tenant id.",
        "Unanswered?": "   ",
        "Retention?": "30 days.",
    }
    out = review.compose_review_comment(answers)
    assert "Yes, by tenant id." in out
    assert "30 days." in out
    assert "Should we shard?" in out
    assert "Unanswered?" not in out


def test_compose_review_comment_all_blank_is_empty():
    assert review.compose_review_comment({"q": "", "r": "  "}) == ""
    assert review.compose_review_comment({}) == ""


# --- badges --- #

def test_badges_shape():
    old = "word " * 100
    new = "word " * 150
    b = review.badges(old, new)
    assert set(b) == {"read_min", "word_delta", "changed"}
    assert isinstance(b["read_min"], float)
    assert b["word_delta"] == 50
    assert b["changed"] is True


def test_badges_unchanged():
    body = "same text here\n"
    b = review.badges(body, body)
    assert b["changed"] is False
    assert b["word_delta"] == 0
