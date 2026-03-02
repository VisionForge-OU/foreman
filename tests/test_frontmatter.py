from foreman import frontmatter


def test_parse_no_frontmatter():
    doc = frontmatter.parse("# Hello\n\nbody text")
    assert doc.meta == {}
    assert doc.body == "# Hello\n\nbody text"


def test_parse_with_frontmatter():
    text = "---\nkind: plan\nversion: 2\n---\n\n# Title\n\nbody"
    doc = frontmatter.parse(text)
    assert doc.meta == {"kind": "plan", "version": 2}
    assert doc.body == "# Title\n\nbody"


def test_roundtrip():
    meta = {"id": "ISS-001", "depends_on": ["ISS-000"], "attempts": 0}
    body = "## Goal\n\nDo the thing.\n"
    text = frontmatter.serialize(meta, body)
    doc = frontmatter.parse(text)
    assert doc.meta == meta
    assert doc.body.rstrip() == body.rstrip()


def test_malformed_frontmatter_is_treated_as_body():
    # No closing delimiter -> whole text is body, never raises (crash safety).
    text = "---\nnot: closed\nstill going"
    doc = frontmatter.parse(text)
    assert doc.meta == {}
    assert doc.body == text


def test_non_mapping_frontmatter_is_body():
    text = "---\n- just\n- a\n- list\n---\nbody"
    doc = frontmatter.parse(text)
    assert doc.meta == {}


def test_empty_meta_serializes_to_body_only():
    assert frontmatter.serialize({}, "just body") == "just body"
