"""WS4.2 — crash-safe task locks + heartbeat reclaim."""

from foreman import locks


def test_acquire_then_foreign_acquire_blocked(tmp_path):
    assert locks.acquire(tmp_path, "ISS-001", run_id="run-A", now=1000.0)
    # A different worker cannot take a live lock.
    assert not locks.acquire(tmp_path, "ISS-001", run_id="run-B", now=1000.0)
    # The same worker may re-acquire (idempotent).
    assert locks.acquire(tmp_path, "ISS-001", run_id="run-A", now=1001.0)


def test_stale_lock_can_be_taken_by_another(tmp_path):
    locks.acquire(tmp_path, "ISS-001", run_id="run-A", now=1000.0, ttl_s=100)
    # 200s later with a 100s TTL → the original is stale → reclaimable.
    assert locks.acquire(tmp_path, "ISS-001", run_id="run-B", now=1200.0, ttl_s=100)
    assert locks.read_lock(tmp_path, "ISS-001").run_id == "run-B"


def test_heartbeat_keeps_lock_live(tmp_path):
    locks.acquire(tmp_path, "ISS-001", run_id="run-A", now=1000.0, ttl_s=100)
    locks.heartbeat(tmp_path, "ISS-001", run_id="run-A", now=1180.0)
    # Now at 1200 the lock is only 20s old → still live → foreign acquire fails.
    assert not locks.acquire(tmp_path, "ISS-001", run_id="run-B", now=1200.0, ttl_s=100)


def test_reclaim_stale_removes_only_dead(tmp_path):
    locks.acquire(tmp_path, "ISS-001", run_id="A", now=100.0, ttl_s=50)   # will be stale
    locks.acquire(tmp_path, "ISS-002", run_id="B", now=1000.0, ttl_s=50)  # fresh
    reclaimed = locks.reclaim_stale(tmp_path, now=1010.0, ttl_s=50)
    assert reclaimed == ["ISS-001"]
    assert set(locks.active(tmp_path)) == {"ISS-002"}


def test_release(tmp_path):
    locks.acquire(tmp_path, "ISS-001", run_id="A", now=1.0)
    locks.release(tmp_path, "ISS-001")
    assert locks.read_lock(tmp_path, "ISS-001") is None
    assert locks.active(tmp_path) == {}
