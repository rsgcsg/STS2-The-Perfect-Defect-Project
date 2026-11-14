"""R7 mu archive, local only: node-cache snapshot pack, follower weights, ledger memory."""
from __future__ import annotations

import contextlib
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from test_fast_mu_follower import COMPILER as FOLLOWER_COMPILER
from test_fast_mu_follower import Card, Service
from test_rl_fast_assembly import Harness

from spireagent.json_boundary import BoundaryError
from stpd.p1.fast_mu_follower import FastMuFollower
from stpd.rl import fast_mu_pack
from stpd.rl.fast_assembly import MuHandoff
from stpd.rl.fast_handoff import HandoffError
from stpd.rl.fast_mu_pack import (
    FILES,
    local_mu_archive,
    node_cache_weights,
    pack,
    prune,
    read_snapshot,
    snapshot_digest,
    snapshot_of,
    unpack,
)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def snapshot(index: int) -> tuple[dict[str, Any], dict[str, bytes]]:
    bodies = {name: f"{name}-{index}".encode() for name in FILES}
    identity: dict[str, Any] = {
        "model_sha256": _sha(bodies["model.tensor-tree"]),
        "heads_sha256": _sha(bodies["heads.tensor-tree"]),
        "heads_artifact_id": f"heads-{index}", "precision": "fp32", "tf32": False,
        "projection": "p", "execution": "e", "adapter": "a", "deduplicate_text": True,
        "training_generation": "gen-1", "version": index, "updates": index,
        "compiler_identity": FOLLOWER_COMPILER}
    identity["snapshot_sha256"] = snapshot_digest(identity)
    return identity, bodies


def write_snapshot(cache_root: Path, index: int) -> dict[str, Any]:
    identity, bodies = snapshot(index)
    root = cache_root / identity["snapshot_sha256"]
    root.mkdir(parents=True)
    for name, body in bodies.items():
        (root / name).write_bytes(body)
    (root / "identity.json").write_text(json.dumps(identity))
    (root / "readers.lock").touch()
    (root / "COMPLETE").write_text(identity["snapshot_sha256"])
    return identity


class Cache:
    """`NodeMuCache.materialize`'s contract: the identity file equals the grant."""

    def __init__(self, root: Path) -> None:
        self.root, self.reads = root, 0

    @contextlib.contextmanager
    def materialize(self, identity: dict[str, Any]):
        root = self.root / identity["snapshot_sha256"]
        if json.loads((root / "identity.json").read_bytes()) != identity:
            raise ValueError("IDENTITY_MISMATCH")
        self.reads += 1
        yield root, None, (root / "heads.tensor-tree").read_bytes()


def handoff(identity: dict[str, Any], raw: bytes | None, generation: str = "gen-1") -> MuHandoff:
    return MuHandoff(identity["snapshot_sha256"], generation, identity["version"],
                     identity["updates"], weights=raw)


def test_pack_is_the_snapshot_and_restores_the_node_cache_layout(tmp_path):
    identity = write_snapshot(tmp_path / "cache", 1)
    raw = node_cache_weights(Cache(tmp_path / "cache"), identity)
    assert raw == read_snapshot(tmp_path / "cache" / identity["snapshot_sha256"])
    assert raw == pack(identity, snapshot(1)[1])  # deterministic
    assert snapshot_of(raw) == identity["snapshot_sha256"]
    restored = unpack(raw, tmp_path / "replay")
    source = tmp_path / "cache" / identity["snapshot_sha256"]
    for name in FILES:
        assert (restored / name).read_bytes() == (source / name).read_bytes()
    assert json.loads((restored / "identity.json").read_bytes()) == identity
    assert (restored / "COMPLETE").is_file() and (restored / "readers.lock").is_file()
    assert Cache(tmp_path / "replay").materialize(identity).__enter__()[0] == restored
    with pytest.raises(HandoffError, match="mu_pack_target_exists"):
        unpack(raw, tmp_path / "replay")


def test_a_snapshot_still_being_written_is_not_packed(tmp_path):
    identity = write_snapshot(tmp_path, 1)
    (tmp_path / identity["snapshot_sha256"] / "COMPLETE").unlink()
    with pytest.raises(HandoffError, match="mu_pack_snapshot_incomplete"):
        node_cache_weights(Cache(tmp_path), identity)


def _tamper(raw: bytes, old: bytes, new: bytes) -> bytes:
    assert raw.count(old) == 1 and len(old) == len(new)
    return raw.replace(old, new)


def test_snapshot_of_refuses_any_pack_that_is_not_its_snapshot():
    identity, bodies = snapshot(1)
    raw = pack(identity, bodies)
    length = 8 + int.from_bytes(raw[:8], "big")
    cases = {
        "mu_pack_truncated": [b"", raw[:4], raw[:length - 1], raw[:-1]],
        "mu_pack_trailing_bytes": [raw + b"x"],
        "mu_pack_file_digest": [raw[:length] + _tamper(raw[length:], b"model.json-1",
                                                       b"model.json-2")],
    }
    for error, packs in cases.items():
        for candidate in packs:
            with pytest.raises(HandoffError, match=error):
                snapshot_of(candidate)
    # A consistent pack whose identity disagrees with its own bytes.
    lying = dict(identity, model_sha256=_sha(b"other"))
    with pytest.raises(HandoffError, match="mu_pack_payload_digest"):
        snapshot_of(pack(lying, bodies))
    wrong = dict(identity, deduplicate_text=False)  # numerical settings changed
    with pytest.raises(HandoffError, match="mu_pack_identity"):
        snapshot_of(pack(wrong, bodies))
    loose = json.dumps({"schema": fast_mu_pack.PACK_SCHEMA, "identity": identity,
                        "files": []}).encode()
    with pytest.raises(HandoffError, match="mu_pack_header"):
        snapshot_of(len(loose).to_bytes(8, "big") + loose)
    with pytest.raises(HandoffError, match="mu_pack_files"):
        pack(identity, {"model.json": b""})


def test_local_archive_keeps_only_real_snapshots_and_prunes_with_checkpoints(tmp_path):
    archive = local_mu_archive(tmp_path / "r7-mu")
    first, second = snapshot(1), snapshot(2)
    raw1, raw2 = pack(*first), pack(*second)
    archive(handoff(first[0], raw1))
    archive(handoff(first[0], raw1, "gen-2"))  # CP restore: same weights, new generation
    archive(handoff(second[0], raw2))
    with pytest.raises(HandoffError, match="mu_archive_weights_not_snapshot"):
        archive(handoff(first[0], raw2))
    assert archive.read(first[0]["snapshot_sha256"], "gen-1")[1] == raw1
    # A weights file with no manifest yet (archiving in progress) is never pruned.
    pending = snapshot(3)
    (archive.root / (pending[0]["snapshot_sha256"] + ".weights")).write_bytes(pack(*pending))
    removed = prune(archive, lambda mu: mu["training_generation"] == "gen-2")
    one, two = first[0]["snapshot_sha256"], second[0]["snapshot_sha256"]
    assert sorted(removed) == sorted([
        archive._manifest_path(one, "gen-1").name, archive._manifest_path(two, "gen-1").name,
        two + ".weights"])
    assert archive.read(one, "gen-2")[1] == raw1  # weights still bound by gen-2
    assert (archive.root / (pending[0]["snapshot_sha256"] + ".weights")).exists()
    assert prune(archive, lambda mu: True) == []
    (archive.root / "broken.json").write_text("{")
    with pytest.raises(HandoffError, match="mu_archive_unreadable_manifest"):
        prune(archive, lambda mu: False)
    assert archive.read(one, "gen-2")[1] == raw1


class Source:
    def __init__(self, identity: dict[str, Any]) -> None:
        self.current, self.loads = identity, []

    def latest(self) -> dict[str, Any]:
        return dict(self.current)

    def load(self, identity: dict[str, Any]) -> Any:
        self.loads.append(identity["snapshot_sha256"])
        return "model"


def test_follower_attaches_the_pack_and_keeps_it_for_a_reoffer(tmp_path):
    cache = Cache(tmp_path)
    identity = write_snapshot(tmp_path, 1)
    card = Card([True, False])
    follower = FastMuFollower(Source(identity), card,
                              weights=lambda i: node_cache_weights(cache, i))
    assert follower.step() is True and follower.behind == [1]
    raw = card.published[0][1].weights
    assert raw is not None and snapshot_of(raw) == identity["snapshot_sha256"]
    card.answers[1] = True
    assert follower.step() is True and card.calls[-1] == [1]
    assert card.published[1][1].weights == raw and cache.reads == 1  # not read again
    assert follower.behind == [] and follower.weights_bytes is None
    # Default: no weights, as before.
    plain = Service()
    assert FastMuFollower(Source(identity), plain).step() is True
    assert plain.published[0][1].weights is None


def test_follower_refuses_weights_of_another_type():
    identity = snapshot(1)[0]
    with pytest.raises(ValueError, match="invalid_fast_mu_follower_settings"):
        FastMuFollower(Source(identity), Service(), weights="not callable")  # type: ignore[arg-type]
    follower = FastMuFollower(Source(identity), Service(), weights=lambda i: "text")  # type: ignore[arg-type,return-value]
    with pytest.raises(BoundaryError, match="MU_WEIGHTS_NOT_BYTES"):
        follower.step()


def test_only_the_latest_mu_keeps_its_pack_in_memory_and_r7_still_archives(tmp_path):
    archive = local_mu_archive(tmp_path / "r7-mu")
    h = Harness(mu_archive=archive)
    first, second = snapshot(1), snapshot(2)
    h.assembler.offer_mu(handoff(first[0], pack(*first)))
    h.open("r7", audit_retain_mu=True)
    plans = [h.step("r7")[0]]  # the first segment binds (and archives) the first mu
    h.assembler.offer_mu(handoff(second[0], pack(*second)))  # archived when bound
    ledger = h.assembler.ledger
    assert ledger.resident[first[0]["snapshot_sha256"]].weights is None
    assert ledger.latest is not None and ledger.latest.weights == pack(*second)
    plans += [h.step("r7")[0] for _ in range(70)]  # into a second segment
    assert {plan.mu.snapshot_sha256 for plan in plans} == {first[0]["snapshot_sha256"],
                                                           second[0]["snapshot_sha256"]}
    assert all(plan.mu.weights is None for plan in plans)
    game = h.assembler.games["r7"]
    assert game.segments and all(segment.mu.weights is None for segment in game.segments)
    h.close("r7", "stop")
    assert h.sealed
    for item in (first, second):
        assert archive.read(item[0]["snapshot_sha256"], "gen-1")[1] == pack(*item)


def test_local_archive_byte_bound_refuses_new_weights_loudly(tmp_path):
    first, second = snapshot(1), snapshot(2)
    size = len(pack(*first))
    archive = local_mu_archive(tmp_path / "r7-mu", max_bytes=size + 1)
    archive(handoff(first[0], pack(*first)))
    with pytest.raises(HandoffError, match="mu_archive_full"):
        archive(handoff(second[0], pack(*second)))
    assert archive.status() == {"weights_files": 1, "manifests": 1, "bytes": size,
                                "max_bytes": size + 1}
    # The same weights under a new generation (CP restore) are never refused.
    archive(handoff(first[0], pack(*first), generation="gen-2"))
    assert archive.status()["manifests"] == 2
    with pytest.raises(HandoffError, match="mu_archive_invalid_bound"):
        local_mu_archive(tmp_path / "other", max_bytes=0)


_HOLD = '''
import sys, time
sys.path[:0] = sys.argv[3:]
from pathlib import Path
from spireagent.instance_lock import instance_lock
with instance_lock(Path(sys.argv[1]), blocking=True):
    print("held", flush=True)
    while not Path(sys.argv[2]).exists():
        time.sleep(0.01)
'''


def test_prune_and_a_re_archive_in_another_process_are_serialized(tmp_path):
    """Review E: a CP restore re-archives a snapshot under a new generation while
    another process prunes the old generation; the weights must survive."""
    import subprocess
    import sys
    import threading

    archive = local_mu_archive(tmp_path / "r7-mu")
    first = snapshot(1)
    raw = pack(*first)
    snap = first[0]["snapshot_sha256"]
    archive(handoff(first[0], raw))  # gen-1, about to be pruned
    release = tmp_path / "release"
    holder = subprocess.Popen(
        [sys.executable, "-c", _HOLD, str(archive.root / fast_mu_pack.LOCK), str(release),
         *sys.path], stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout is not None and holder.stdout.readline().strip() == "held"
        done: list[list[str]] = []
        pruner = threading.Thread(target=lambda: done.append(
            prune(archive, lambda mu: mu["training_generation"] != "gen-1")))
        pruner.start()
        writer = threading.Thread(target=archive, args=(handoff(first[0], raw, "gen-2"),))
        writer.start()
        pruner.join(0.3)
        writer.join(0.3)
        assert pruner.is_alive() and writer.is_alive()  # both wait for the other process
        assert len(list(archive.root.glob("*.json"))) == 1
        release.touch()
        holder.wait(10)
        pruner.join(10)
        writer.join(10)
    finally:
        release.touch()
        holder.kill()
        holder.wait()
    assert not pruner.is_alive() and not writer.is_alive()
    # Whichever ran first, gen-2 is complete and its weights are kept.
    assert archive.read(snap, "gen-2")[1] == raw
    assert archive._manifest_path(snap, "gen-1").name in done[0]
    assert not archive._manifest_path(snap, "gen-1").exists()


def test_node_cache_weights_fails_loud_when_the_snapshot_was_trimmed(tmp_path):
    identity = write_snapshot(tmp_path, 1)
    root = tmp_path / identity["snapshot_sha256"]
    for path in root.iterdir():
        path.unlink()
    root.rmdir()
    with pytest.raises(OSError):
        node_cache_weights(Cache(tmp_path), identity)
    follower = FastMuFollower(Source(identity), Service(),
                              weights=lambda i: node_cache_weights(Cache(tmp_path), i))
    with pytest.raises(OSError):
        follower.step()
