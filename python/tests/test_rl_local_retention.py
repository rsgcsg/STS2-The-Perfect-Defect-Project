"""#558 learner-local retention: GPU-node copies of mu publications, mu-network and CPs."""
from __future__ import annotations

import contextlib
import threading
import tomllib
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace

import pytest

from spireagent.artifact_contracts import Manifest, Parent, Producer
from spireagent.json_boundary import FrozenObject
from stpd.p1.local_inference import SegmentInferencePool
from stpd.rl import local_retention
from stpd.rl.local_retention import LocalRetention, trim
from stpd.rl.segment_learner import SegmentLearner

ROOT = Path(__file__).resolve().parents[2]
PRODUCER = Producer("rsgcsg/STS2-The-Perfect-Defect", "a" * 40, "b" * 64)
ZERO = bytes(16)  # one chunk every payload shares (CHUNK_BYTES = 16 below)


class Flights:
    def hold(self, _key):
        return contextlib.nullcontext()


@pytest.fixture
def live(tmp_path, monkeypatch):
    """A learner-live dir: node-local ArchivedStore (archive = a peer dir), as in dataplane mode."""
    from spireagent.storage import store as store_module
    from spireagent.storage.config import open_peer

    monkeypatch.setattr(store_module, "CHUNK_BYTES", 16)
    monkeypatch.setattr(local_retention, "_REFERENCES", {})
    root = tmp_path / "learner-live"
    store = open_peer({"kind": "local", "directory": str(root / "store"),
                       "archive": {"kind": "local", "directory": str(tmp_path / "peer")}})
    pool = SimpleNamespace(snapshots={}, latest=None, node_caches={})
    learner = SimpleNamespace(condition=threading.Condition(), pool=pool, live_work=root,
                              mu_flights=Flights(), generation="g-test",
                              exchange=SimpleNamespace(store=store,
                                                       model=lambda *_a, **_k: {}))
    return SimpleNamespace(root=root, store=store, pool=pool, learner=learner,
                           identities=[])


def publish(live, version: int, *, weights: bytes | None = None) -> dict:
    """One mu publication as `_publish_snapshot` makes it, then the pool publish + retire."""
    store = live.store
    weights_payload = store.put_bytes(
        "weights", weights or ZERO + f"weights-v{version:07d}".encode())
    model = Manifest("model", PRODUCER, payloads=(weights_payload, store.put_bytes(
        "package_manifest", f"package-json-v{version:04d}".encode())))
    store.publish(model)
    heads = Manifest("checkpoint", PRODUCER, parents=(Parent("policy", model.artifact_id),),
                     payloads=(store.put_bytes(
                         "checkpoint", ZERO + f"heads-v{version:09d}".encode()),),
                     parameters=FrozenObject.of({"schema": "stpd/rl-behavior-heads-1"}))
    store.publish(heads)
    identity = {"version": version, "snapshot_sha256": f"{version:064x}",
                "model_artifact_id": model.artifact_id, "heads_artifact_id": heads.artifact_id}
    live.pool.snapshots[identity["snapshot_sha256"]] = identity
    live.pool.latest = identity["snapshot_sha256"]
    live.identities.append(identity)
    return identity


def serve(live, node: str, cached: list[str] | None = None) -> None:
    """One `get_mu` from a service on `node` (always the latest, as the client asks)."""
    SegmentLearner._model_files(live.learner, None, node, cached or [])


def retire(live, leases: set[str] = frozenset()) -> None:
    SegmentInferencePool.retire(live.pool, set(leases))


def drain(live) -> None:
    assert live.store.archive.drain(timeout=30, owned=True)["pending"] == 0


def unique_chunk(live, data: bytes) -> Path:
    import hashlib
    return live.root / "store" / "objects" / "sha256" / hashlib.sha256(data).hexdigest()


def run_pass(live, retention: LocalRetention) -> dict:
    job = retention.select(set(live.pool.snapshots))
    return retention.settle(trim(str(live.root), **job))


def test_default_keeps_every_local_copy(live):
    """Pinned: keep counts default 0 and 0 means no candidate, ever; profile values are 0."""
    retention = LocalRetention()
    assert (retention.keep_models, retention.keep_checkpoints) == (0, 0)
    for version in range(1, 6):
        retention.published(publish(live, version), str(live.root / "versions" / "x"))
        serve(live, "node-a")
        retention.checkpoint(f"cp{version}", version)
        retire(live)
    drain(live)
    passes = [run_pass(live, retention) for _ in range(3)]
    for fields in passes:
        assert fields["deleted_models"] == fields["deleted_checkpoints"] == 0
        assert fields["deleted_mu_dirs"] == 0 and fields["freed_bytes"] == 0
    assert len(list((live.root / "mu-network").iterdir())) == 5
    # The local-bytes walk runs once after a new CP, not on every pass.
    assert passes[0]["store_object_bytes"] > 0 and passes[0]["mu_network_bytes"] > 0
    assert "store_object_bytes" not in passes[1] and "store_object_bytes" not in passes[2]
    retention.checkpoint("cp6", 5)
    assert run_pass(live, retention)["mu_network_bytes"] > 0
    # The learner builds no LocalRetention at all when both counts are 0.
    source = (ROOT / "python/stpd/rl/segment_learner.py").read_text()
    assert "if any(keep) else None" in source
    profile = tomllib.loads((ROOT / "tools/friday/fastchain/rl5-fastchain.toml").read_text())
    for name in ("learner_local_keep_models", "learner_local_keep_checkpoints"):
        assert profile["knobs"][name]["value"] == profile["knobs"][name]["default"] == 0


def test_slow_service_keeps_version_n_minus_k(live):
    """A service on node `slow` pulled v2 and still reports it cached: v2 stays servable
    (pool.snapshots via node_caches) and keeps its local copies while v1, v3-v5 go."""
    retention = LocalRetention(keep_models=1)
    for version in range(1, 7):
        identity = publish(live, version)
        serve(live, "fast")  # fast node: always the latest, nothing older cached
        if version == 2:
            serve(live, "slow")  # then silent: node_caches["slow"] stays {v2}
        retire(live)
        retention.published(identity, str(live.root / "versions" / "g" / f"{version:06d}"))
    drain(live)
    v2 = f"{2:064x}"
    assert set(live.pool.snapshots) == {v2, f"{6:064x}"}
    first = run_pass(live, retention)  # grace: nothing was unservable at the pass before
    assert first["deleted_models"] == first["deleted_mu_dirs"] == 0
    second = run_pass(live, retention)
    assert (second["deleted_models"], second["deleted_mu_dirs"]) == (4, 4)
    assert second["failures"] == [] and second["freed_bytes"] > 0
    assert not unique_chunk(live, b"weights-v0000001").exists()
    assert unique_chunk(live, b"weights-v0000002").exists()
    assert unique_chunk(live, ZERO).exists()  # shared by live payloads
    assert sorted(p.name for p in (live.root / "mu-network").iterdir()) == [v2, f"{6:064x}"]
    # The slow service asks again for v2: still served from the local copy.
    SegmentLearner._model_files(live.learner, v2, "slow", [v2])
    # v1 is no longer servable: refused exactly as before this change.
    with pytest.raises(KeyError):
        SegmentLearner._model_files(live.learner, f"{1:064x}", "slow", [])
    # Lineage stays and the bytes come back from the peer on demand.
    model = live.store.get_manifest(live.identities[0]["model_artifact_id"])
    assert b"".join(live.store.read_payload(model.payload("weights"))).endswith(b"v0000001")
    # The slow node moves on: v2 is retired, then deleted one pass later (grace).
    serve(live, "slow")
    retire(live)
    assert run_pass(live, retention)["deleted_models"] == 0
    assert run_pass(live, retention)["deleted_models"] == 1
    assert not unique_chunk(live, b"weights-v0000002").exists()


def test_lease_and_newest_checkpoint_version_are_kept(live):
    retention = LocalRetention(keep_models=1)
    for version in range(1, 5):
        retention.published(publish(live, version), str(live.root / "v"))
        serve(live, "fast")
        retire(live, leases={f"{1:064x}"})  # a lease still bound to v1
    retention.checkpoint("cp-a", 2)
    retention.checkpoint("cp-b", 3)  # the newest local CP names v3
    drain(live)
    run_pass(live, retention)
    fields = run_pass(live, retention)
    assert fields["deleted_models"] == 1  # v2 only: v1 leased, v3 named, v4 newest
    assert not unique_chunk(live, b"weights-v0000002").exists()
    for version in (1, 3, 4):
        assert unique_chunk(live, f"weights-v{version:07d}".encode()).exists()


def test_unarchived_copy_is_kept_until_the_receipt_exists(live):
    import hashlib

    from spireagent.json_boundary import json_bytes

    retention = LocalRetention(keep_models=1)
    for version in range(1, 4):
        retention.published(publish(live, version), str(live.root / "v"))
        serve(live, "fast")
        retire(live)
    drain(live)
    key = f"manifests/{live.identities[0]['heads_artifact_id']}.json"
    raw = (live.root / "store" / key).read_bytes()
    identity = hashlib.sha256(json_bytes([key, hashlib.sha256(raw).hexdigest()])).hexdigest()
    receipt = live.root / "store" / ".archive" / "completed" / f"{identity}.json"
    body = receipt.read_bytes()
    receipt.unlink()  # as if the archive had not copied v1's heads yet
    run_pass(live, retention)
    fields = run_pass(live, retention)
    assert (fields["deleted_models"], fields["kept_unarchived"]) == (1, 1)
    assert unique_chunk(live, b"weights-v0000001").exists()
    assert (live.root / "mu-network" / f"{1:064x}").is_dir()
    receipt.write_bytes(body)
    assert run_pass(live, retention)["deleted_models"] == 1
    assert not unique_chunk(live, b"weights-v0000001").exists()


def test_checkpoints_keep_newest_and_shared_chunks(live):
    retention = LocalRetention(keep_checkpoints=1)
    publish(live, 1, weights=ZERO + b"shared-with-cp-1")
    ids = []
    for sequence in range(1, 4):
        unique = b"shared-with-cp-1" if sequence == 1 else f"cp-unique-{sequence:06d}".encode()
        manifest = Manifest("checkpoint", PRODUCER, payloads=(live.store.put_bytes(
            "checkpoint", ZERO + unique),), parameters=FrozenObject.of({"sequence": sequence}))
        live.store.publish(manifest)
        retention.checkpoint(manifest.artifact_id, 1)
        ids.append(manifest.artifact_id)
    drain(live)
    fields = run_pass(live, retention)
    assert fields["deleted_checkpoints"] == 2 and fields["tracked_checkpoints"] == 1
    assert unique_chunk(live, b"shared-with-cp-1").exists()  # the live model uses it
    assert not unique_chunk(live, b"cp-unique-000002").exists()
    assert unique_chunk(live, b"cp-unique-000003").exists()


def test_failed_delete_is_counted_and_never_raised(live, monkeypatch):
    retention = LocalRetention(keep_models=1)
    for version in range(1, 4):
        retention.published(publish(live, version), str(live.root / "v"))
        serve(live, "fast")
        retire(live)
    drain(live)
    blocked = unique_chunk(live, b"weights-v0000001")
    original = Path.unlink

    def unlink(self, *args, **kwargs):
        if self == blocked:
            raise PermissionError("read-only")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink)
    run_pass(live, retention)
    fields = run_pass(live, retention)
    assert fields["deleted_models"] == 2 and len(fields["failures"]) == 1
    assert fields["totals"]["failed_deletes"] == 1 and blocked.exists()
    assert not unique_chunk(live, b"weights-v0000002").exists()


def test_learner_pass_reports_a_failed_pass_and_goes_on(live, capsys):
    """`_local_retention_pass`: a pass that raised is an event + stderr line, not an error;
    the next pass is queued on the publisher."""
    events, submitted = [], []

    def submit(function, *args, **kwargs):
        submitted.append((function, args, kwargs))
        return Future()

    broken: Future = Future()
    broken.set_exception(OSError("store vanished"))
    retention = LocalRetention(keep_models=1)
    retention.future = broken
    learner = SimpleNamespace(local_retention=retention, condition=threading.Condition(),
                              pool=live.pool, live_work=live.root,
                              publisher=SimpleNamespace(submit=submit),
                              _event=lambda kind, **fields: events.append((kind, fields)))
    SegmentLearner._local_retention_pass(learner)
    assert events[0][0] == "learner_local_retention"
    assert events[0][1]["totals"]["pass_failures"] == 1
    assert "store vanished" in capsys.readouterr().err
    assert submitted[0][0] is trim and submitted[0][1] == (str(live.root),)
    # A pass still running: nothing new is queued.
    SegmentLearner._local_retention_pass(learner)
    assert len(submitted) == 1 and len(events) == 1


def test_checkpoint_without_receipt_is_kept(live):
    retention = LocalRetention(keep_checkpoints=1)
    ids = []
    for sequence in range(1, 4):
        manifest = Manifest("checkpoint", PRODUCER, payloads=(live.store.put_bytes(
            "checkpoint", ZERO + f"cp-unique-{sequence:06d}".encode()),),
            parameters=FrozenObject.of({"sequence": sequence}))
        live.store.publish(manifest)
        retention.checkpoint(manifest.artifact_id, 1)
        ids.append(manifest.artifact_id)
    drain(live)
    for receipt in (live.root / "store" / ".archive" / "completed").iterdir():
        if receipt.read_text().find(ids[0]) >= 0:
            receipt.unlink()  # cp1 not yet copied to the peer
    fields = run_pass(live, retention)
    assert (fields["deleted_checkpoints"], fields["kept_unarchived"]) == (1, 1)
    assert unique_chunk(live, b"cp-unique-000001").exists()
    assert not unique_chunk(live, b"cp-unique-000002").exists()


def test_chunk_of_a_payload_without_manifest_yet_survives(live):
    """A payload whose index exists but whose manifest is not written yet (mid-publication)
    keeps every chunk it names, even one only a dropped version otherwise names."""
    retention = LocalRetention(keep_models=1)
    for version in range(1, 4):
        retention.published(publish(live, version), str(live.root / "v"))
        serve(live, "fast")
        retire(live)
    drain(live)
    live.store.put_bytes("checkpoint", b"weights-v0000001" + b"in-flight-cp-xxx")  # no manifest
    run_pass(live, retention)
    assert run_pass(live, retention)["deleted_models"] == 2
    assert unique_chunk(live, b"weights-v0000001").exists()
    assert not unique_chunk(live, b"weights-v0000002").exists()
