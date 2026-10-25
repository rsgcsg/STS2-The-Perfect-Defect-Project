"""Operator API/CLI for real-TRAIN performance preparation; no live job control."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from spireagent.json_boundary import BoundaryError
from spireagent.source import source_identity
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.store import ArtifactStore, ManifestArtifactStore
from spireagent.workbench.developer import ROOT, ProjectConfig
from spireagent.workbench.p1_training_data import record_p1_training_use


def prepare_calibration(
    config: ProjectConfig, run_id: str, checkpoint_id: str, operation_id: str, *,
    updates: int = 4,
) -> dict:
    from stpd.p1.calibration import prepare

    if config.research_workspace is not None:
        from spireagent.workbench.inplace_curation import configured_owner

        owner = configured_owner(config)
    else:
        from spireagent.workbench.managed_local_workspace import inspect_managed_workspace

        managed_owner = inspect_managed_workspace(config.state_dir).get("curation_owner")
        if managed_owner is None:
            raise BoundaryError("p1_calibration", "original_curation_owner_required")
        owner = managed_owner
    store = ManifestArtifactStore(LocalBlobStore(owner.store_dir, create=False))
    run = store.get_manifest(run_id)
    training = store.get_manifest(run.parent("training_input"))
    snapshot_id = training.parent("source")
    producer = source_identity(ROOT)
    # Same allocation/use owner as formal training. Never obtain calibration
    # permission from possession of a readable checkpoint or copied data alone.
    record_p1_training_use(owner, store, snapshot_id, operation_id)
    artifact = prepare(store, run_id, checkpoint_id, producer, operation_id, updates=updates)
    return {"state": "prepared", "calibration_input_id": artifact.artifact_id,
            "producer": producer.to_dict(), "source_run_id": run_id,
            "checkpoint_id": checkpoint_id, "store": str(owner.store_dir),
            "sample_count": updates * 64, "gpu_submitted": False, "formal_run_changed": False}


def authorize_qualification(
    config: ProjectConfig, input_id: str, operation_id: str,
) -> dict:
    """Verify the capsule's prior Curation use; project only on remote compute.

    The new operation is receipt provenance, not a newly recorded ledger use.
    This bounded entry cannot authorize another Snapshot or a formal Run.
    """
    from spireagent import p1_curation
    from spireagent.json_boundary import digest
    from stpd.p1.calibration import INPUT_SCHEMA

    digest(operation_id, "p1_qualification.operation_id", length=32)

    if config.research_workspace is not None:
        from spireagent.workbench.inplace_curation import configured_owner

        owner = configured_owner(config)
    else:
        from spireagent.workbench.managed_local_workspace import inspect_managed_workspace

        managed_owner = inspect_managed_workspace(config.state_dir).get("curation_owner")
        if managed_owner is None:
            raise BoundaryError("p1_calibration", "original_curation_owner_required")
        owner = managed_owner
    store = ManifestArtifactStore(LocalBlobStore(owner.store_dir, create=False))
    capsule = store.get_manifest(input_id)
    if capsule.parameters.value().get("schema") != INPUT_SCHEMA:
        raise BoundaryError("p1_calibration", "calibration_input_required")
    run = store.get_manifest(capsule.parent("run"))
    checkpoint = store.get_manifest(capsule.parent("checkpoint"))
    training = store.get_manifest(run.parent("training_input"))
    snapshot_id = training.parent("source")
    if (snapshot_id != capsule.parent("snapshot") or checkpoint.parent("run") != run.artifact_id
            or checkpoint.producer != run.producer):
        raise BoundaryError("p1_calibration", "source_parent_binding_mismatch")
    producer = source_identity(ROOT)
    original_operation = capsule.parameters.value()["operation_id"]
    p1_curation.require_recorded_p1_training_use(owner.ledger, store, snapshot_id,
                                               original_operation)
    return {"calibration_input_id": input_id, "operation_id": operation_id,
            "source_run_id": run.artifact_id, "checkpoint_id": checkpoint.artifact_id,
            "snapshot_id": snapshot_id, "training_use_recorded": False,
            "original_training_use_verified": True, "training_use_reference": original_operation,
            "producer": producer.to_dict(), "formal_run_changed": False}


def publish_calibration(source: ArtifactStore, peer: ArtifactStore, identity: str) -> dict:
    """Publish the bounded compute read set; original provenance stays in the same peer.

    Parent existence and identity are checked, not granted a new recursive audit.
    All own payloads are streamed and checked through the normal Store API.
    """
    import tempfile

    from stpd.p1.calibration import INPUT_SCHEMA

    manifest = source.get_manifest(identity)
    if manifest.parameters.value().get("schema") != INPUT_SCHEMA:
        raise BoundaryError("p1_calibration", "calibration_input_required")
    for parent in manifest.parents:
        if peer.get_manifest(parent.artifact_id) != source.get_manifest(parent.artifact_id):
            raise BoundaryError("p1_calibration", "source_parent_not_durable_in_selected_peer")
    for payload in manifest.payloads:
        with tempfile.TemporaryFile("w+b") as stream:
            for block in source.read_payload(payload):
                stream.write(block)
            stream.seek(0)
            if peer.put_payload(payload.role, stream, payload.media_type) != payload:
                raise BoundaryError("p1_calibration", "calibration_transfer_mismatch")
    peer.publish(manifest)
    return {"state": "published", "calibration_input_id": identity,
            "own_payload_bytes": sum(payload.size for payload in manifest.payloads),
            "parent_manifests_verified": len(manifest.parents), "recursive_reaudit": False,
            "formal_run_changed": False}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "push"))
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--run")
    parser.add_argument("--checkpoint")
    parser.add_argument("--operation-id")
    parser.add_argument("--input")
    parser.add_argument("--updates", type=int, default=4)
    args = parser.parse_args(argv)
    try:
        config = ProjectConfig.load(args.config)
        if args.action == "prepare":
            if any(value is None for value in (args.run, args.checkpoint, args.operation_id)):
                raise BoundaryError("p1_calibration", "exact_run_checkpoint_operation_required")
            result = prepare_calibration(config, args.run, args.checkpoint,
                                         args.operation_id, updates=args.updates)
        else:
            import os

            from spireagent.storage.config import open_peer
            from spireagent.workbench.service_process import cloud_environment

            if config.remote_compute is None or args.input is None:
                raise BoundaryError("p1_calibration", "selected_compute_peer_and_input_required")
            os.environ.update(cloud_environment(config.cloud_environment_file))
            if config.research_workspace is not None:
                from spireagent.workbench.inplace_curation import configured_owner

                owner = configured_owner(config)
            else:
                from spireagent.workbench.managed_local_workspace import inspect_managed_workspace

                managed_owner = inspect_managed_workspace(config.state_dir).get("curation_owner")
                if managed_owner is None:
                    raise BoundaryError("p1_calibration", "original_curation_owner_required")
                owner = managed_owner
            source = ManifestArtifactStore(LocalBlobStore(owner.store_dir, create=False))
            result = publish_calibration(source, open_peer(config.remote_compute.compute_store),
                                         args.input)
    except Exception as error:
        print(json.dumps({"state": "failed", "error_code": getattr(error, "code",
                                                                    type(error).__name__)}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
