"""The hash-seal — the single owner of the approval invariant (R3, DECISIONS §4)."""

from foreman import seal
from foreman.hashing import body_hash


def test_fingerprint_matches_body_hash():
    assert seal.fingerprint("# Plan\nbody\n") == body_hash("# Plan\nbody\n")


def test_intact_true_when_unchanged():
    body = "# Plan\nbody\n"
    assert seal.intact(seal.fingerprint(body), body) is True


def test_intact_false_when_body_edited():
    body = "# Plan\nbody\n"
    fp = seal.fingerprint(body)
    assert seal.intact(fp, body + "edited\n") is False


def test_intact_false_when_no_seal():
    assert seal.intact(None, "anything") is False
    assert seal.intact("", "anything") is False


def test_intact_ignores_trailing_whitespace_and_crlf():
    # body_hash normalizes CRLF + trailing whitespace; seal inherits that.
    assert seal.intact(seal.fingerprint("a\nb\n"), "a\r\nb\n   ") is True
