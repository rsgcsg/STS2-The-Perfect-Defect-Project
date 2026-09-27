"""Synthetic local evidence keeps its archive identity while admitting typed input labels."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from platform_bundle3_fixture import bundle3, load, seal, write
from sts2_platform_evidence import DirectoryTransferManifest, verify_human_session_bundle
from test_artifact_store_v1 import store
from test_local_recording_preview import PRODUCER
from test_text_menu_human_import import _declared_bundle

from spireagent.artifact_contracts import Manifest
from spireagent.json_boundary import BoundaryError, FrozenObject, json_bytes
from spireagent.workbench.local_recording_import import _archive_verified_bundle
from stpd.fullrun.text_menu_human_import import (
    load_human_text_source,
    load_verified_human_text_bundle,
    publish_human_text_bc_view,
    publish_human_text_source,
)


def _local_evidence(target, bundle: Path, output: Path) -> Manifest:
    output.mkdir(parents=True, exist_ok=True)
    identity = load(bundle / "session-bundle-manifest.json")
    transfer = DirectoryTransferManifest.from_directory(
        bundle, content_id=identity["bundle_content_id"],
        artifact_type="human-session-bundle",
    )
    archive = output / "bundle.tar.gz"
    _archive_verified_bundle(bundle, archive, transfer)
    raw = bundle / "raw"
    payloads = (
        target.put_bytes("archive", archive.read_bytes(), "application/gzip"),
        target.put_bytes("transfer", json_bytes(transfer.to_dict())),
    )
    evidence = Manifest("evidence", PRODUCER, payloads=payloads,
                        parameters=FrozenObject.of({
                            "schema": "stpd/local-verified-bundle-v1",
                            "candidate_id": hashlib.sha256(
                                identity["session_id"].encode()).hexdigest(),
                            "session_id": identity["session_id"],
                            "timeline_id": identity["timeline_id"],
                            "manifest_sha256": hashlib.sha256(
                                (raw / "recording-manifest.json").read_bytes()).hexdigest(),
                            "close_sha256": hashlib.sha256(
                                (raw / "session-close-receipt.json").read_bytes()).hexdigest(),
                            "content_id": transfer.content_id,
                            "transfer_manifest_sha256": transfer.manifest_sha256,
                            "tool_release_id": "d" * 64,
                            "worker_id": identity["worker_id"],
                            "campaign_id": identity["campaign_id"],
                            "human_origin_attested": True,
                            "disposition": "locally_verified",
                            "research_admission": "not_evaluated",
                            "hub_receipt": None,
                        }))
    target.publish(evidence)
    return evidence


def _changed(target, source: Manifest, **parameters: object) -> Manifest:
    changed = Manifest("evidence", PRODUCER, payloads=source.payloads,
                       parameters=FrozenObject.of({**source.parameters.value(), **parameters}))
    target.publish(changed)
    return changed


def test_local_verified_human_stream_reuses_exact_archive_and_parents(tmp_path: Path) -> None:
    target = store(tmp_path / "store")
    evidence = []
    originals = []
    for name in ("session-a", "session-b"):
        output = tmp_path / name
        bundle, row, _ = _declared_bundle(
            output, "begin_card_play_exact_factory_return", "begin_card_play",
            session_id=name,
        )
        assert verify_human_session_bundle(bundle).passed
        item = _local_evidence(target, bundle, output)
        evidence.append(item)
        originals.append(b"".join(target.read_payload(item.payload("archive"))))
        selected, typed, rows = load_verified_human_text_bundle(target, item.artifact_id)
        assert selected == item and typed.text_input_schema_version == 1
        assert rows[0]["record_id"] == row["record_id"]
    source = publish_human_text_source(
        target, tuple(item.artifact_id for item in evidence), PRODUCER)
    view = publish_human_text_bc_view(target, source.artifact_id, PRODUCER)
    _, rows = load_human_text_source(target, source.artifact_id)
    assert len(rows) == 2
    assert tuple(parent.artifact_id for parent in source.parents) == tuple(
        item.artifact_id for item in evidence)
    assert view.parent("dataset") == source.artifact_id
    assert [b"".join(target.read_payload(item.payload("archive")))
            for item in evidence] == originals
    assert all(target.get_manifest(item.artifact_id) == item for item in evidence)
    assert sum(target.get_manifest(key).kind == "evidence" for key in target.manifest_ids()) == 2


@pytest.mark.parametrize("change", [
    {"transfer_manifest_sha256": "0" * 64},
    {"human_origin_attested": False},
    {"schema": "stpd/received-bundle-v1"},
])
def test_local_evidence_metadata_forgery_is_rejected(tmp_path: Path, change: dict) -> None:
    target = store(tmp_path / "store")
    bundle, _, _ = _declared_bundle(
        tmp_path / "session", "begin_card_play_exact_factory_return", "begin_card_play")
    source = _local_evidence(target, bundle, tmp_path / "session")
    forged = _changed(target, source, **change)
    with pytest.raises(BoundaryError):
        load_verified_human_text_bundle(target, forged.artifact_id)


def test_local_transfer_and_side_stream_hash_fail_closed(tmp_path: Path) -> None:
    target = store(tmp_path / "store")
    bundle, _, _ = _declared_bundle(
        tmp_path / "session", "begin_card_play_exact_factory_return", "begin_card_play")
    source = _local_evidence(target, bundle, tmp_path / "session")
    wrong_transfer = target.put_bytes("transfer", b"{}", "application/json")
    forged = Manifest("evidence", PRODUCER,
                      payloads=(source.payload("archive"), wrong_transfer),
                      parameters=source.parameters)
    target.publish(forged)
    with pytest.raises(BoundaryError):
        load_verified_human_text_bundle(target, forged.artifact_id)

    receipt = load(bundle / "raw/session-close-receipt.json")
    receipt["human_text_inputs_sha256"] = "0" * 64
    write(bundle / "raw/session-close-receipt.json", receipt)
    seal(bundle)
    corrupted = _local_evidence(target, bundle, tmp_path / "session/corrupted")
    with pytest.raises(BoundaryError):
        load_verified_human_text_bundle(target, corrupted.artifact_id)


def test_local_text_parse_remains_bound_to_verified_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = store(tmp_path / "store")
    bundle, _, _ = _declared_bundle(
        tmp_path / "session", "begin_card_play_exact_factory_return", "begin_card_play")
    source = _local_evidence(target, bundle, tmp_path / "session")
    import stpd.fullrun.text_menu_human_import as importer

    original = importer._verified

    def altered_after_parse(directory: Path):
        result = original(directory)
        path = directory / "raw/recording-manifest.json"
        path.write_bytes(path.read_bytes() + b" ")
        return result

    monkeypatch.setattr(importer, "_verified", altered_after_parse)
    with pytest.raises(BoundaryError, match="bundle_identity_mismatch"):
        load_verified_human_text_bundle(target, source.artifact_id)


def test_local_bundle_without_declared_human_stream_is_not_a_human_source(
    tmp_path: Path,
) -> None:
    target = store(tmp_path / "store")
    output = tmp_path / "plain"
    bundle = bundle3(output / "fixture", runs=1)
    raw = bundle / "raw"
    recording = load(raw / "recording-manifest.json")
    recording["close_schema_version"] = 1
    write(raw / "recording-manifest.json", recording)
    write(raw / "session-close-receipt.json", {
        "schema": "sts2.human-annotator/session-close-1",
        "session_id": recording["session_id"], "timeline_id": recording["timeline_id"],
        "status": "closed", "closed_at": "2026-09-26T00:00:02Z",
    })
    seal(bundle)
    assert verify_human_session_bundle(bundle).passed
    source = _local_evidence(target, bundle, output)
    with pytest.raises(BoundaryError, match="declared_human_text_stream_required"):
        load_verified_human_text_bundle(target, source.artifact_id)
