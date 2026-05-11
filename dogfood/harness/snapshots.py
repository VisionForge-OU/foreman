"""TUI snapshot capture for the report.

Two kinds of snapshot per gate:
  * a *text* snapshot — screen class, current slug, derived phase (disk truth),
    and the visible text of the key widgets — which is also the basis for the
    state-vs-display mismatch checks the goal asks for; and
  * a best-effort *SVG* screenshot via Textual's ``export_screenshot`` (visual
    record; skipped silently if the API isn't available in this Textual build).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

# Widget ids worth capturing, by screen.
_SCREEN_WIDGETS = {
    "DashboardScreen": ["#hint", "#board", "#skills", "#statusbar", "#glogbody"],
    "ReviewScreen": ["#title", "#digest", "#oq", "#md", "#comments"],
    "AttentionScreen": ["#detail", "#answer"],
    "RetroScreen": ["#pbody"],
    "MetricsScreen": ["#metrics"],
    "WorkerScreen": ["#logbody"],
    "SettingsScreen": ["#cfg"],
}


def _widget_text(screen, wid: str) -> str:
    try:
        w = screen.query_one(wid)
    except Exception:
        return "<absent>"
    for attr in ("renderable", "text"):
        val = getattr(w, attr, None)
        if val is not None:
            return str(val)
    return str(getattr(w, "_content", ""))


def capture(app, out_dir: Path, label: str) -> dict:
    """Capture a text snapshot (+ best-effort SVG) and return the text dict."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    screen = app.screen
    sname = type(screen).__name__
    ctrl = app.controller
    widgets = {wid: _widget_text(screen, wid) for wid in _SCREEN_WIDGETS.get(sname, [])}
    snap = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "label": label,
        "screen": sname,
        "current_slug": getattr(app, "current_slug", None),
        "status_line": ctrl.status_line(),
        "activity": (ctrl.activity.kind if ctrl.activity else None),
        "widgets": widgets,
    }
    safe = label.replace("/", "_").replace(" ", "_")
    (out_dir / f"{safe}.json").write_text(json.dumps(snap, indent=2))
    # Best-effort SVG.
    try:
        svg = app.export_screenshot()
        (out_dir / f"{safe}.svg").write_text(svg)
        snap["svg"] = f"{safe}.svg"
    except Exception:
        pass
    return snap


def render_text(snap: dict) -> str:
    """A compact human-readable rendering of a text snapshot for a report."""
    lines = [f"### {snap['label']}  ({snap['screen']})  @ {snap['ts']}",
             f"- slug: `{snap['current_slug']}`  ·  status_line: `{snap['status_line']}`"]
    for wid, txt in snap.get("widgets", {}).items():
        one = " ".join(str(txt).split())
        lines.append(f"- `{wid}`: {one[:200]}")
    return "\n".join(lines)
