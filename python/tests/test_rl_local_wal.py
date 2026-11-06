"""Real SQLite crash/receipt counterexamples; no native World or SGD claims."""

from __future__ import annotations

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from spireagent.rl_local_wal import LocalControlWAL, WalUncertain


def original(index=0):
    return {"run": "r", "training_generation": "g", "operation": "begin", "index": index}


def test_full_commit_and_original_response_survive_process_death_before_apply_or_ack(tmp_path):
    path = tmp_path / "control.sqlite"
    script = """import os,sys
from pathlib import Path
from spireagent.rl_local_wal import LocalControlWAL
w=LocalControlWAL(Path(sys.argv[1]),run="r",generation="g")
w.append("k",{"run":"r","training_generation":"g","operation":"begin","index":0},
         {"lease":"a/0","mu":{"full":"identity"}})
os._exit(17)
"""
    result = subprocess.run([sys.executable, "-c", script, str(path)], check=False)
    assert result.returncode == 17
    owner = LocalControlWAL(path, run="r", generation="g")
    try:
        receipt = owner.receipt("k", original())
        assert receipt["status"] == "FOUND" and receipt["response"]["lease"] == "a/0"
        assert receipt["durable_position"] == 1
        assert owner.receipt("missing", original())["status"] == "UNKNOWN"
        with pytest.raises(WalUncertain):
            owner.append("next", original(1), {})
    finally:
        owner.close()


def test_live_absence_is_bounded_by_exact_owner_and_pending_request(tmp_path):
    owner = LocalControlWAL(tmp_path / "wal", run="r", generation="g")
    try:
        assert owner.receipt("k", original())["status"] == "NOT_ACCEPTED"
        assert (
            owner.receipt("k", {**original(), "training_generation": "old"})["status"] == "UNKNOWN"
        )
        owner.pending_keys.add("pending")
        assert owner.receipt("pending", original())["status"] == "UNKNOWN"
        owner.append("k", original(), {"accepted": True})
        with pytest.raises(ValueError, match="original_request_changed"):
            owner.receipt("k", original(2))
    finally:
        owner.close()


def test_same_key_different_body_fences_without_replacing_original(tmp_path):
    owner = LocalControlWAL(tmp_path / "wal", run="r", generation="g")
    try:
        owner.append("k", original(), {"accepted": True})
        with pytest.raises(WalUncertain):
            owner.append("k", original(2), {"accepted": True})
        assert owner.receipt("k", original())["response"] == {"accepted": True}
        assert owner.receipt("other", original())["status"] == "UNKNOWN"
    finally:
        owner.close()


def test_64_concurrent_records_are_unique_contiguous_and_original_duplicates_are_noop(tmp_path):
    owner = LocalControlWAL(tmp_path / "wal", run="r", generation="g")
    try:
        with ThreadPoolExecutor(max_workers=16) as pool:
            records = list(
                pool.map(lambda i: owner.append(str(i), original(i), {"i": i}), range(64))
            )
        assert sorted(r["sequence"] for r in records) == list(range(1, 65))
        for record in records:
            assert owner.append(record["key"], record["request"], record["response"]) == record
        assert owner.sequence == 64
        assert owner.db.execute("PRAGMA synchronous").fetchone()[0] == 2
        assert owner.db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        owner.close()


@pytest.mark.parametrize("committed", [False, True])
def test_sync_error_is_uncertain_whether_transaction_reached_disk(tmp_path, committed):
    path = tmp_path / "wal"
    owner = LocalControlWAL(path, run="r", generation="g")
    real = owner.db

    class FailedCommit:
        def __getattr__(self, key):
            return getattr(real, key)

        def execute(self, sql, *args):
            if sql == "COMMIT":
                if committed:
                    real.execute(sql, *args)
                raise OSError("injected sync failure")
            return real.execute(sql, *args)

    owner.db = FailedCommit()
    try:
        with pytest.raises(WalUncertain):
            owner.append("k", original(), {"lease": "a/0"})
        assert owner.status()["input_fenced"]
        assert owner.receipt("new", original())["status"] == "UNKNOWN"
    finally:
        owner.close()
    reopened = LocalControlWAL(path, run="r", generation="g")
    try:
        assert reopened.receipt("k", original())["status"] == ("FOUND" if committed else "UNKNOWN")
    finally:
        reopened.close()


def test_replica_prefix_never_certifies_missing_suffix_or_new_generation(tmp_path):
    owner = LocalControlWAL(tmp_path / "local", run="r", generation="g")
    import sqlite3

    replica_path = tmp_path / "replica"
    try:
        owner.append("k1", original(1), {"lease": "a/1"})
        with sqlite3.connect(replica_path) as replica:
            owner.db.backup(replica)
        owner.append("k2", original(2), {"lease": "a/2"})
        prefix = LocalControlWAL(replica_path, run="r", generation="g", authoritative=False)
        try:
            assert prefix.receipt("k1", original(1))["status"] == "FOUND"
            assert prefix.receipt("k2", original(2))["status"] == "UNKNOWN"
        finally:
            prefix.close()
    finally:
        owner.close()
    with pytest.raises(ValueError, match="owner_identity_changed"):
        LocalControlWAL(replica_path, run="r", generation="other")


def test_events_have_durable_cursor_and_hash_chain_integrity(tmp_path):
    path = tmp_path / "wal"
    owner = LocalControlWAL(path, run="r", generation="g")
    try:
        owner.append("open", original(), {})
        owner.append("event", {"kind": "actor_world_offered"}, {}, kind="event")
        owner.append("close", original(2), {})
        facts = owner.events_since(0)
        assert len(facts) == 1 and facts[0]["sequence"] == 2
        assert owner.events_since(2) == []
    finally:
        owner.close()
    import sqlite3

    with sqlite3.connect(path) as db:
        db.execute("UPDATE facts SET response=? WHERE key='open'", (json.dumps({"corrupt": True}),))
    with pytest.raises(ValueError, match="chain_integrity"):
        LocalControlWAL(path, run="r", generation="g")


def test_same_local_journal_cannot_have_two_active_writers(tmp_path):
    owner = LocalControlWAL(tmp_path / "wal", run="r", generation="g")
    try:
        with pytest.raises(ValueError, match="already_running"):
            LocalControlWAL(tmp_path / "wal", run="r", generation="g")
        assert owner.append("k", original(), {})["sequence"] == 1
    finally:
        owner.close()


def test_local_wal_hard_limit_fences_but_does_not_replace_existing_receipts(tmp_path):
    owner = LocalControlWAL(tmp_path / "wal", run="r", generation="g")
    try:
        owner.append("k", original(), {"accepted": True})
        owner.max_bytes = 1
        with pytest.raises(WalUncertain, match="disk_byte_limit"):
            owner.append("next", original(1), {})
        assert owner.receipt("k", original())["response"] == {"accepted": True}
        assert owner.receipt("next", original(1))["status"] == "UNKNOWN"
    finally:
        owner.close()


def test_async_replication_failure_keeps_local_ack_and_copies_lifecycle_facts(tmp_path):
    import threading
    import time

    from spireagent.rl_local_wal import WalReplication, read_replica_block
    from spireagent.storage.local import LocalBlobStore

    owner = LocalControlWAL(tmp_path / "wal", run="r", generation="g")
    store = LocalBlobStore(tmp_path / "archive")
    available = threading.Event()

    class Offline:
        def put_if_absent(self, key, raw):
            if not available.is_set():
                raise OSError("offline")
            return store.put_if_absent(key, raw)

        def get(self, key):
            return store.get(key)

    replica = WalReplication(owner, Offline(), interval=0.01)
    try:
        owner.append("k", original(), {"accepted": True})
        owner.append(
            "event", {"kind": "actor_world_offered", "intent": "original"}, {}, kind="event"
        )
        until = time.monotonic() + 2
        while replica.status()["replication_error"] is None and time.monotonic() < until:
            threading.Event().wait(0.01)
        assert replica.status()["replication_error"]["type"] == "OSError"
        assert replica.status()["replicated_durable_position"] == 0
        assert owner.receipt("k", original())["status"] == "FOUND"
        available.set()
        until = time.monotonic() + 2
        while replica.status()["replicated_durable_position"] < 2 and time.monotonic() < until:
            threading.Event().wait(0.01)
        assert replica.status()["replicated_durable_position"] == 2
        blocks = [read_replica_block(store.get(key)) for key in store.keys("exchange/")]
        assert any(record["kind"] == "event" for block in blocks for record in block["records"])
    finally:
        replica.close()
        owner.close()


def test_blocked_FULL_commit_keeps_receipts_status_and_enqueue_serviceable(tmp_path):
    import threading

    owner = LocalControlWAL(tmp_path / "wal", run="r", generation="g")
    real = owner.db
    entered, release = threading.Event(), threading.Event()

    class SlowCommit:
        def __getattr__(self, key):
            return getattr(real, key)

        def execute(self, sql, *args):
            if sql == "COMMIT":
                entered.set()
                assert release.wait(5)
            return real.execute(sql, *args)

    owner.db = SlowCommit()
    try:
        first = owner.append_async("first", original(), {"lease": "a/0"})
        assert entered.wait(3)
        assert first.cancel() is False
        with ThreadPoolExecutor(max_workers=1) as reader:
            result = reader.submit(lambda: (owner.status(), owner.receipt("first", original()),
                owner.receipt("missing", original()), owner.append_async("next", original(1), {})))
            status, receipt, missing, second = result.result(timeout=1)
        assert not first.done() and not second.done()
        assert status["local_durable_position"] == 0
        assert receipt["status"] == "UNKNOWN" and missing["status"] == "NOT_ACCEPTED"
        release.set()
        assert first.result(timeout=3)["sequence"] == 1
        assert second.result(timeout=3)["sequence"] == 2
        assert owner.receipt("first", original())["response"] == {"lease": "a/0"}
    finally:
        release.set()
        owner.close()
