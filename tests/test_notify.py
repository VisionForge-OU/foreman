"""WS5 — notify_command runner: no-op, payload delivery, never-raises."""

from foreman import notify


async def test_notify_none_is_noop():
    assert await notify.notify(None, event="e", feature="f", ref="r", reason="x") is False
    assert await notify.notify("", event="e", feature="f", ref="r", reason="x") is False
    assert await notify.notify("   ", event="e", feature="f", ref="r", reason="x") is False


async def test_notify_delivers_env_payload(tmp_path):
    out = tmp_path / "payload.txt"
    cmd = (
        f"sh -c 'printf \"%s|%s|%s|%s\" "
        f'"$FOREMAN_EVENT" "$FOREMAN_FEATURE" "$FOREMAN_REF" "$FOREMAN_REASON" '
        f"> {out}'"
    )
    ok = await notify.notify(
        cmd, event="review_needed", feature="login", ref="prd", reason="diverged"
    )
    assert ok is True
    assert out.read_text() == "review_needed|login|prd|diverged"


async def test_notify_delivers_args_payload(tmp_path):
    out = tmp_path / "args.txt"
    # $1..$4 are the appended positional args.
    cmd = f"sh -c 'printf \"%s-%s-%s-%s\" \"$1\" \"$2\" \"$3\" \"$4\" > {out}' _"
    ok = await notify.notify(
        cmd, event="escalation", feature="pay", ref="ISS-007", reason="stuck"
    )
    assert ok is True
    assert out.read_text() == "escalation-pay-ISS-007-stuck"


async def test_notify_bad_command_never_raises():
    # A command that exits non-zero → False, no exception.
    assert await notify.notify(
        "sh -c 'exit 3'", event="e", feature="f", ref="r", reason="x"
    ) is False
    # A command that does not exist → False, no exception.
    assert await notify.notify(
        "this-binary-does-not-exist-foreman", event="e", feature="f", ref="r", reason="x"
    ) is False


def test_fire_sync_outside_loop(tmp_path):
    out = tmp_path / "fire.txt"
    cmd = f"sh -c 'printf done > {out}'"
    ok = notify.fire(cmd, event="e", feature="f", ref="r", reason="x")
    assert ok is True
    assert out.read_text() == "done"


def test_fire_sync_no_command():
    assert notify.fire(None, event="e", feature="f", ref="r", reason="x") is False
