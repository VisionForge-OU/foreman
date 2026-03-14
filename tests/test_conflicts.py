"""WS4.1 — conflict graph + conflict-aware dispatch."""

from foreman import conflicts
from foreman.models import Issue


def _iss(id, touches):
    return Issue(id=id, title=id, touches=touches)


def test_footprint_overlap_path_containment():
    assert conflicts.footprints_overlap(["src/"], ["src/a.py"])
    assert conflicts.footprints_overlap(["a/b.py"], ["a/b.py"])
    assert not conflicts.footprints_overlap(["a/"], ["b/"])
    assert not conflicts.footprints_overlap(["src/a.py"], ["src/b.py"])


def test_unknown_footprint_conflicts_with_everything():
    assert conflicts.footprints_overlap([], ["anything"])
    assert conflicts.footprints_overlap(["x"], [])


def test_conflict_graph():
    a = _iss("ISS-001", ["src/a.py"])
    b = _iss("ISS-002", ["src/a.py", "src/b.py"])  # overlaps a
    c = _iss("ISS-003", ["docs/"])                  # disjoint
    g = conflicts.conflict_graph([a, b, c])
    assert g["ISS-001"] == {"ISS-002"}
    assert g["ISS-002"] == {"ISS-001"}
    assert g["ISS-003"] == set()


def test_pick_dispatch_runs_disjoint_in_parallel():
    a = _iss("ISS-001", ["src/a.py"])
    b = _iss("ISS-002", ["src/b.py"])
    chosen = conflicts.pick_dispatch([a, b], running=[], max_new=2)
    assert {i.id for i in chosen} == {"ISS-001", "ISS-002"}


def test_pick_dispatch_serializes_overlapping():
    a = _iss("ISS-001", ["src/a.py"])
    b = _iss("ISS-002", ["src/a.py"])  # conflicts with a
    chosen = conflicts.pick_dispatch([a, b], running=[], max_new=2)
    assert len(chosen) == 1


def test_pick_dispatch_respects_running_footprints():
    running = [_iss("ISS-001", ["src/a.py"])]
    ready = [_iss("ISS-002", ["src/a.py"]), _iss("ISS-003", ["docs/"])]
    chosen = conflicts.pick_dispatch(ready, running, max_new=2)
    assert {i.id for i in chosen} == {"ISS-003"}  # ISS-002 conflicts with running


def test_unknown_footprint_running_blocks_all():
    running = [_iss("ISS-001", [])]  # unknown footprint
    ready = [_iss("ISS-002", ["docs/"])]
    assert conflicts.pick_dispatch(ready, running, max_new=2) == []


def test_unknown_footprint_ready_runs_alone():
    ready = [_iss("ISS-001", []), _iss("ISS-002", ["docs/"])]
    chosen = conflicts.pick_dispatch(ready, running=[], max_new=2)
    # The unknown-footprint issue can only run alone; the disjoint one is preferred.
    assert len(chosen) == 1


def test_pick_dispatch_respects_max_new():
    ready = [_iss("ISS-001", ["a/"]), _iss("ISS-002", ["b/"]), _iss("ISS-003", ["c/"])]
    chosen = conflicts.pick_dispatch(ready, running=[], max_new=2)
    assert len(chosen) == 2
