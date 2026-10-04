"""Synthetic command-scoped reuse; no real corpus or admission is changed."""

from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest
from test_local_recording_preview import PRODUCER, _fixture
from test_local_user_declaration import _make_curated, _prepared

from spireagent.json_boundary import BoundaryError, FrozenObject
from spireagent.storage.blobs import StoreError
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.store import ManifestArtifactStore, copy_artifact
from stpd.fullrun import curated_dataset, selection_session
from stpd.fullrun.curated_dataset import curate, load_selection, publish_selection
from stpd.fullrun.decision_dataset import SelectionRules
from stpd.fullrun.decision_spool import SpoolSelection
from stpd.fullrun.decision_store import preview
from stpd.fullrun.selection_session import verified_dataset_sources


def _curated(tmp_path):
    _, source_id, store = _fixture(tmp_path, canonical=True)
    source = store.get_manifest(source_id)
    rules = SelectionRules()
    base = preview(store, (source,), rules)
    selected = curate(base, "training", {"revision": 0, "items": {}})
    manifest = publish_selection(
        store, (source,), rules, PRODUCER, selected,
        merging=False, expected=selected.logical_id, paired_training=None,
    )
    base.records.owner.close()
    selected.records.owner.close()
    return store, source, manifest


def _close(dataset):
    assert isinstance(dataset.records, SpoolSelection)
    dataset.records.owner.close()


def _facts(dataset):
    return (dataset.logical_id, dataset.report, tuple(dataset.records.canonical_rows()),
            tuple(dataset.fingerprints()), dataset.run_ids)


def test_repeated_load_reuses_exact_selection_and_callers_own_rows(tmp_path, monkeypatch):
    store, _, manifest = _curated(tmp_path)
    cold = load_selection(store, manifest, cache=None)
    expected = _facts(cold)
    _close(cold)
    with verified_dataset_sources(store) as stats:
        first = load_selection(store, manifest, cache=None)
        assert _facts(first) == expected
        first_directory = first.records.owner.directory
        _close(first)
        assert not first_directory.exists()
        monkeypatch.setattr(curated_dataset, "_load_selection",
                            lambda *a, **k: pytest.fail("repeated semantic reprojection"))
        memo = {}
        repeated = load_selection(store, manifest, cache=None, memo=memo)
        assert _facts(repeated) == expected
        assert memo == {manifest.artifact_id: repeated}
        _close(repeated)
        again = load_selection(store, manifest, cache=None)
        assert _facts(again) == expected
        _close(again)
        assert (stats.misses, stats.hits, stats.bypassed) == (1, 2, 0)


def test_caller_memo_cannot_seed_private_verified_selection(tmp_path):
    store, _, manifest = _curated(tmp_path)
    original = load_selection(store, manifest, cache=None)
    expected = _facts(original)
    fake = replace(original, report=FrozenObject.of({"unverified": True}))
    memo = {manifest.artifact_id: fake}
    with verified_dataset_sources(store):
        actual = load_selection(store, manifest, cache=None, memo=memo)
        assert _facts(actual) == expected
        assert memo[manifest.artifact_id] is actual
        _close(actual)
    _close(original)


@pytest.mark.parametrize("role", ["archive", "transfer", "selection"])
def test_hit_rehashes_original_payloads_and_discards_failed_retention(tmp_path, role):
    store, source, manifest = _curated(tmp_path)
    payload = (manifest if role == "selection" else source).payload(role)
    index = json.loads(store.blobs.get(f"payload-indexes/v1/{payload.sha256}.json"))
    chunk = index["chunks"][0]["sha256"]
    path = store.blobs.root / "objects" / "sha256" / chunk
    with verified_dataset_sources(store) as stats:
        _close(load_selection(store, manifest, cache=None))
        directory = selection_session._CURRENT.get().selection.records.owner.directory
        path.write_bytes(b"corrupt original")
        with pytest.raises(StoreError, match="integrity"):
            load_selection(store, manifest, cache=None)
        assert not directory.exists()
        assert stats.hits == 0
        assert selection_session._CURRENT.get().selection is None
        # Catching an error inside the command cannot rehabilitate known-bad bytes.
        with pytest.raises(StoreError, match="integrity"):
            load_selection(store, manifest, cache=None)


def test_hashes_bytes_even_when_driver_does_not_check_them(tmp_path, monkeypatch):
    store, source, manifest = _curated(tmp_path)
    with verified_dataset_sources(store):
        _close(load_selection(store, manifest, cache=None))
        original = store.read_payload

        def unchecked(payload):
            if payload == source.payload("archive"):
                return iter([b"invalid bytes"])
            return original(payload)

        monkeypatch.setattr(store, "read_payload", unchecked)
        with pytest.raises(BoundaryError, match="payload_identity_mismatch"):
            load_selection(store, manifest, cache=None)


def test_same_physical_store_wrapper_reuses_but_copy_and_code_identity_do_not(
    tmp_path, monkeypatch,
):
    store, _, manifest = _curated(tmp_path)
    original = curated_dataset._load_selection
    calls = []

    def counted(*args, **kwargs):
        calls.append(args[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(curated_dataset, "_load_selection", counted)
    implementation = ["implementation-a"]
    monkeypatch.setattr(selection_session, "_implementation_identity", lambda: implementation[0])
    with verified_dataset_sources(store) as stats:
        _close(load_selection(store, manifest, cache=None))
        wrapper = ManifestArtifactStore(LocalBlobStore(store.blobs.root, create=False))
        _close(load_selection(wrapper, manifest, cache=None))
        assert calls == [store]
        # Byte-identical content at another physical root cannot borrow verification.
        other = ManifestArtifactStore(LocalBlobStore(tmp_path / "other-store"))
        copy_artifact(store, other, manifest.artifact_id)
        _close(load_selection(other, manifest, cache=None))
        assert calls == [store, other]
        implementation[0] = "implementation-b"
        _close(load_selection(store, manifest, cache=None))
        assert calls == [store, other, store]
        assert (stats.misses, stats.hits) == (2, 1)


def test_replaced_root_does_not_borrow_previous_verification(tmp_path, monkeypatch):
    store, _, manifest = _curated(tmp_path)
    original = curated_dataset._load_selection
    calls = []

    def counted(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(curated_dataset, "_load_selection", counted)
    with verified_dataset_sources(store) as stats:
        _close(load_selection(store, manifest, cache=None))
        root = store.blobs.root
        saved = root.with_name("original-store")
        root.rename(saved)
        shutil.copytree(saved, root)
        _close(load_selection(store, manifest, cache=None))
        assert calls == [True, True]
        assert stats.hits == 0


@pytest.mark.parametrize("owner", ["research", "verifier", "local_verification"])
def test_implementation_identity_rehashes_actual_owner_source(monkeypatch, owner):
    import sts2_platform_evidence

    expected = selection_session._implementation_identity()
    target = {
        "research": Path(curated_dataset.__file__),
        "verifier": Path(sts2_platform_evidence.__file__),
        "local_verification": Path(selection_session.__file__).parents[2]
        / "spireagent/local_verified_bundle.py",
    }[owner]
    original = Path.read_bytes

    def changed_bytes(path):
        raw = original(path)
        return raw + b"\n# changed owner implementation\n" if path == target else raw

    # Simulate the bytes read from source; do not mutate installed dependencies.
    monkeypatch.setattr(Path, "read_bytes", changed_bytes)
    assert selection_session._implementation_identity() != expected


def test_unsupported_store_is_cold_and_cannot_inject_a_projection(tmp_path, monkeypatch):
    store, _, manifest = _curated(tmp_path)

    class OtherStore:
        def __getattr__(self, name):
            return getattr(store, name)

    other = OtherStore()
    original = curated_dataset._load_selection
    calls = []

    def counted(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(curated_dataset, "_load_selection", counted)
    with verified_dataset_sources(other) as stats:
        first = load_selection(other, manifest, cache=None)
        expected = _facts(first)
        _close(first)
        second = load_selection(other, manifest, cache=None)
        assert _facts(second) == expected
        _close(second)
        assert calls == [True, True]
        assert (stats.hits, stats.misses) == (0, 0)


def test_forged_source_and_changed_exclusion_snapshot_cannot_use_old_result(tmp_path):
    store, source, manifest = _curated(tmp_path)
    rules = SelectionRules()
    with verified_dataset_sources(store) as stats:
        first = load_selection(store, manifest, cache=None)
        rows = tuple(first.records.summaries())
        expected = _facts(first)
        base = preview(store, (source,), rules)
        changed = curate(base, "training", {"revision": 1, "items": {
            rows[-1]["id"]: {"sequence": 1, "action": "exclude", "reason": "synthetic"},
        }})
        _close(base)
        next_manifest = publish_selection(
            store, (source,), rules, PRODUCER, changed,
            merging=False, expected=changed.logical_id, paired_training=None,
        )
        changed_facts = _facts(changed)
        _close(changed)
        _close(first)
        repeated = load_selection(store, manifest, cache=None)
        assert _facts(repeated) == expected
        _close(repeated)
        next_selection = load_selection(store, next_manifest, cache=None)
        assert _facts(next_selection) == changed_facts
        assert changed_facts != expected
        assert len(next_selection.records) == len(rows) - 1
        _close(next_selection)
        forged_source = replace(source, parameters=FrozenObject.of({
            **source.parameters.value(), "human_origin_attested": False,
        }))
        store.publish(forged_source)
        forged = replace(next_manifest, parents=(replace(
            next_manifest.parents[0], role="source_" + forged_source.artifact_id,
            artifact_id=forged_source.artifact_id,
        ),))
        store.publish(forged)
        with pytest.raises(BoundaryError, match="not_local_verified_bundle"):
            load_selection(store, forged, cache=None)
        assert stats.hits == 1


def test_nested_and_error_cleanup_closes_only_retained_rows(tmp_path):
    store, _, manifest = _curated(tmp_path)
    assert selection_session._CURRENT.get() is None
    with verified_dataset_sources(store) as outer:
        returned = load_selection(store, manifest, cache=None)
        parent_session = selection_session._CURRENT.get()
        directory = parent_session.selection.records.owner.directory
        with pytest.raises(RuntimeError), verified_dataset_sources(store):
            _close(load_selection(store, manifest, cache=None))
            nested_directory = selection_session._CURRENT.get().selection.records.owner.directory
            raise RuntimeError("interrupted preparation")
        assert not nested_directory.exists()
        assert directory.exists()
        assert selection_session._CURRENT.get() is parent_session
        _close(load_selection(store, manifest, cache=None))
        assert outer.hits == 1
    assert not directory.exists()
    assert selection_session._CURRENT.get() is None
    # A consumer's independently owned rows survive the command's cache cleanup.
    assert returned.logical_id == manifest.parameters.value()["logical_id"]
    _close(returned)


def test_retention_bound_bypasses_without_changing_dataset(tmp_path, monkeypatch):
    store, _, manifest = _curated(tmp_path)
    monkeypatch.setattr(selection_session, "MAX_RETAINED_ROW_BYTES", 0)
    with verified_dataset_sources(store) as stats:
        first = load_selection(store, manifest, cache=None)
        expected = _facts(first)
        _close(first)
        second = load_selection(store, manifest, cache=None)
        assert _facts(second) == expected
        _close(second)
        assert selection_session._CURRENT.get().selection is None
        assert (stats.misses, stats.hits, stats.bypassed) == (2, 0, 2)


@pytest.mark.parametrize("damage,code", [
    ("gold", "held_out_data_cannot_train"),
    ("test", "held_out_data_cannot_train"),
    ("claim", "training_claim_mismatch"),
    ("source_index", "source_index_incomplete"),
    ("source_identity", "source_identity_conflict"),
    ("use", "training_source_use_missing"),
])
def test_training_admission_and_use_ledger_remain_fresh_on_hit(tmp_path, monkeypatch, damage, code):
    _, _, owner, store, source_id, canonical, _, rules = _prepared(tmp_path, monkeypatch)
    dataset_id, operation = _make_curated(owner, store, canonical, rules)
    with verified_dataset_sources(store) as stats:
        expected = owner.require_training_datasets(store, (dataset_id,), operation)
        monkeypatch.setattr(curated_dataset, "_load_selection",
                            lambda *a, **k: pytest.fail("repeated semantic reprojection"))
        assert owner.require_training_datasets(store, (dataset_id,), operation) == expected
        with owner.transaction() as db:
            if damage in {"gold", "test"}:
                db.execute("INSERT INTO curation_claims VALUES('new-heldout',?,NULL,0)", (damage,))
                db.executemany("INSERT INTO curation_claim_runs VALUES('new-heldout',?)",
                               ((run,) for run in expected["datasets"][0]["qualified_run_ids"]))
            elif damage == "claim":
                db.execute("DELETE FROM curation_claim_runs WHERE claim=?", (operation,))
            elif damage == "source_index":
                db.execute("DELETE FROM curation_exact_source_index WHERE source=?", (source_id,))
            elif damage == "source_identity":
                db.execute("UPDATE curation_sources SET archive=? WHERE id=?",
                           ("0" * 64, source_id))
            else:
                db.execute("DELETE FROM curation_source_uses WHERE reference=?", (operation,))
        with pytest.raises(BoundaryError, match=code):
            owner.require_training_datasets(store, (dataset_id,), operation)
        assert stats.hits == 2
