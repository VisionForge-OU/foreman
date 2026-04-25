"""FileStore owns the .foreman/ run-artifact + escalation layout (deepening 4).

Orchestrators used to write verdict/audit/escalation/report files and glob
usage.json themselves; now FileStore owns that I/O behind intent methods.
"""

import itertools
import json

from foreman.installer import init_repo
from foreman.models import RunRecord
from foreman.sample import create_sample_repo
from foreman.state import FileStore


def _store(tmp_path):
    repo = create_sample_repo(tmp_path / "repo")
    init_repo(repo)
    c = itertools.count(1)
    store = FileStore(repo, clock=lambda: f"2026-01-01T00:00:{next(c):02d}Z")
    slug = store.create_feature("feat", "desc")
    return store, slug


def test_write_run_verdict_roundtrip(tmp_path):
    store, slug = _store(tmp_path)
    store.write_run_verdict(slug, "r1", {"schema": "foreman-verdict/v1", "verdict": "pass"})
    data = json.loads(store.paths.run_verdict(slug, "r1").read_text())
    assert data["verdict"] == "pass"


def test_escalation_append_and_read(tmp_path):
    store, slug = _store(tmp_path)
    assert store.read_escalation(slug, "ISS-001") == ""   # absent → empty
    store.append_escalation(slug, "ISS-001", "first\n")
    store.append_escalation(slug, "ISS-001", "second\n")
    assert store.read_escalation(slug, "ISS-001") == "first\nsecond\n"


def test_read_run_progress_absent_is_empty(tmp_path):
    store, slug = _store(tmp_path)
    assert store.read_run_progress(slug, "r1") == ""


def test_usage_records_sorted_dicts(tmp_path):
    store, slug = _store(tmp_path)
    for rid, cost in (("b", 1.0), ("a", 2.0)):
        rec = RunRecord(run_id=rid, label="x", started="t")
        rec.cost_usd = cost
        store.write_run_record(slug, rec)
    recs = store.usage_records(slug)
    assert [r["run_id"] for r in recs] == ["a", "b"]  # sorted by run dir
    assert round(sum(r["cost_usd"] for r in recs), 2) == 3.0


def test_audit_payloads_newest_first(tmp_path):
    store, slug = _store(tmp_path)
    store.write_run_audit(slug, "r1", {"status": "a"})
    store.write_run_audit(slug, "r2", {"status": "b"})
    assert [p["status"] for p in store.audit_payloads(slug)] == ["b", "a"]


def test_write_report(tmp_path):
    store, slug = _store(tmp_path)
    store.write_report(slug, "# Report\nbody\n")
    assert store.paths.report_file(slug).read_text() == "# Report\nbody\n"


def test_review_snapshot_roundtrip(tmp_path):
    store, slug = _store(tmp_path)
    assert store.read_review_snapshot(slug, "prd", 1) is None  # absent
    store.write_review_snapshot(slug, "prd", 1, "# PRD v1 body\n")
    assert store.read_review_snapshot(slug, "prd", 1) == "# PRD v1 body\n"
