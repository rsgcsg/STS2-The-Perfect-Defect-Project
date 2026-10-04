"""Real synthetic curation-to-prepared-PublicM2 fixture; no external data or weights."""

from __future__ import annotations

import hashlib
import tempfile
from dataclasses import dataclass
from pathlib import Path

from platform_bundle3_fixture import bundle3

from spireagent.artifact_contracts import Producer
from spireagent.json_boundary import decode_json
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from spireagent.storage.store import ManifestArtifactStore
from stpd.canonical import semantic_hash
from stpd.fullrun.decision_training import AllocationSpec, publish_allocation
from stpd.fullrun.features import load_model_view
from stpd.fullrun.public_bc import publish_public_bc_view
from stpd.fullrun.public_m2_input_storage import public_m2_source_binding_digest
from stpd.fullrun.public_m2_segments import (
    AcceptedMark,
    FrameRef,
    JournalMark,
    LedgerOccurrence,
    ProvedTransition,
    derive_public_m2_segments,
)
from stpd.fullrun.public_m2_sequences import (
    PublicM2EvidenceRow,
    compile_public_m2_input,
    project_public_m2_chains,
)
from stpd.models.token_core import ScratchShape
from stpd.workers.public_m2_engine import PublicM2EngineConfig
from stpd.workers.public_m2_run import prepare_public_m2_run


@dataclass(frozen=True)
class PreparedM2Campaign:
    owner: object
    store: ManifestArtifactStore
    reporter: ObjectStoreRunReporter
    producer: Producer
    project_config: Path
    dataset_id: str
    allocation: object
    view: object
    training_input: object
    config: PublicM2EngineConfig
    runtime: dict
    run: object
    training_admission: dict
    dev_admission: dict


def _source_archive(store: ManifestArtifactStore, dataset_id: str) -> tuple[str, bytes]:
    pending = [dataset_id]
    seen: set[str] = set()
    while pending:
        identity = pending.pop()
        if identity in seen:
            continue
        seen.add(identity)
        manifest = store.get_manifest(identity)
        if manifest.kind == "evidence":
            payload = manifest.payload("archive")
            return payload.sha256, b"".join(store.read_payload(payload))
        pending.extend(parent.artifact_id for parent in manifest.parents)
    raise AssertionError("curated dataset has no verified source archive")


def _lines(path: Path) -> tuple[dict, ...]:
    return tuple(decode_json(line) for line in path.read_bytes().splitlines() if line)


def _evidence_rows(
    store: ManifestArtifactStore, dataset_id: str, records: tuple, samples: tuple,
) -> tuple[PublicM2EvidenceRow, ...]:
    """Join selected public rows back to exact source events and verified records."""
    from stpd.fullrun.platform_bundle3 import _extract

    archive_sha, archive = _source_archive(store, dataset_id)
    by_transition = {record.transition_id: record for record in records}
    if len(by_transition) != len(records):
        raise AssertionError("verified dataset transition ids must be unique")
    sample_by_id = {sample.transition_id: sample for sample in samples}
    if set(sample_by_id) - set(by_transition):
        raise AssertionError("public view contains a transition outside its dataset")

    with tempfile.TemporaryDirectory(prefix="m2-campaign-source-") as directory:
        root = Path(directory)
        _extract(archive, root)
        trace = _lines(root / "raw/semantic-boundary-trace.jsonl")
        journal_values = _lines(root / "raw/run-journal.jsonl")

        journal = tuple(JournalMark(
            row["sequence"], semantic_hash(row), row["kind"], row["session_id"],
            row.get("run_id"), row["recorded_at"],
        ) for row in journal_values)
        accepted_events = [event for event in trace if event.get("kind") == "action_accepted"]
        accepted_by_key = {
            (event["run_id"], event["action"]["record_id"]): event
            for event in accepted_events
        }
        if len(accepted_by_key) != len(accepted_events):
            raise AssertionError("synthetic accepted events must be uniquely keyed")

        ledger = []
        for record in records:
            evidence = record.source_evidence.value()
            action = evidence["commit"]["evidence"]["action"]
            accepted = accepted_by_key[(record.run_id.rsplit("/", 1)[-1], action["record_id"])]
            accepted_ref = semantic_hash(accepted)
            ledger.append(LedgerOccurrence(
                accepted["sequence"], accepted_ref, record.run_id.rsplit("/", 1)[-1],
                action["action_witness_id"], evidence["action_sequence"],
                "transition_proved", record.provenance.record_id,
            ))

        accepted_marks = tuple(AcceptedMark(
            event["sequence"], semantic_hash(event), event["session_id"], event["run_id"],
            event["action"]["action_witness_id"], event["action"]["action_sequence"],
            event["observed_at"],
        ) for event in accepted_events)
        proofs = []
        proof_events = {
            semantic_hash(event): event for event in trace
            if event.get("kind") == "transition_proved"
        }
        for record in records:
            evidence = record.source_evidence.value()
            event = proof_events.get(evidence["proof_ref"])
            if event is None:
                continue
            pre, successor = event["execution_pre_ref"], event["successor_ref"]
            accepted_event = accepted_by_key[(record.run_id.rsplit("/", 1)[-1],
                                               event["action"]["record_id"])]
            proofs.append(ProvedTransition(
                semantic_hash(accepted_event),
                record.provenance.record_id,
                event["action"]["action_witness_id"],
                evidence["action_sequence"],
                FrameRef(pre["object_ref"], pre["content_sha256"], pre["snapshot_id"]),
                FrameRef(successor["object_ref"], successor["content_sha256"],
                         successor["snapshot_id"]),
            ))

        session_id = journal_values[0]["session_id"]
        row_to_recording_segment: dict[str, str] = {}
        for run_id in sorted({record.run_id.rsplit("/", 1)[-1] for record in records}):
            run_accepted = tuple(mark for mark in accepted_marks if mark.run_id == run_id)
            run_ledger = tuple(item for item in ledger if item.run_id == run_id)
            run_refs = {item.accepted_ref for item in run_ledger}
            run_proofs = tuple(proof for proof in proofs
                               if proof.accepted_ref in run_refs)
            result = derive_public_m2_segments(
                source_archive_sha256=archive_sha, session_id=session_id, run_id=run_id,
                journal=journal, accepted=run_accepted, ledger=run_ledger, proved=run_proofs,
            )
            row_to_recording_segment.update({
                item.accepted_ref: item.recording_segment_id
                for item in result.placements
                if item.status == "known-qualified" and item.recording_segment_id is not None
            })

        output = []
        for sample in samples:
            record = by_transition[sample.transition_id]
            evidence = record.source_evidence.value()
            action = evidence["commit"]["evidence"]["action"]
            accepted = accepted_by_key[(record.run_id.rsplit("/", 1)[-1], action["record_id"])]
            accepted_ref = semantic_hash(accepted)
            segment = row_to_recording_segment.get(accepted_ref)
            if segment is None:
                raise AssertionError("verified source could not qualify a selected transition")
            output.append(PublicM2EvidenceRow(
                sample.transition_id, archive_sha, evidence["session_id"],
                evidence["native_run_id"], evidence["action_sequence"],
                evidence["pre_frame_sha256"], evidence["successor_frame_sha256"],
                record.provenance.commit_ref, evidence["proof_ref"], segment,
                semantic_hash(sample.to_dict()),
            ))
        return tuple(output)


def prepared_m2_campaign(
    tmp_path: Path, monkeypatch, *, producer: Producer | None = None,
) -> PreparedM2Campaign:
    """Build a curation-owned, source-bound prepared run from six synthetic rows."""
    import test_decision_store
    from test_light_action_m0_canonical_cli import _synthetic_workspace

    monkeypatch.setattr(test_decision_store, "bundle3", lambda path: bundle3(
        path, public_bindings=True,
    ))
    config_path, store_dir, dataset_id, owner = _synthetic_workspace(tmp_path)
    store = ManifestArtifactStore(LocalBlobStore(store_dir, create=False))
    producer = producer or store.get_manifest(dataset_id).producer
    training_operation_id = "a" * 32
    evaluation_operation_id = "b" * 32
    training_admission = owner.reserve_training_datasets(
        store, (dataset_id,), training_operation_id,
    )
    allocation = publish_allocation(
        store, dataset_id, AllocationSpec(max_train=8, max_dev=4), producer,
    )
    dev_admission = owner._allocation_dev_use(
        store, allocation_id=allocation.artifact_id,
        training_operation_id=training_operation_id,
        evaluation_operation_id=evaluation_operation_id,
        record_use=True,
    )
    view = publish_public_bc_view(store, allocation.artifact_id, producer)
    _, samples = load_model_view(store, view.artifact_id)
    from stpd.fullrun.decision_training import load_allocation

    _, dataset, _ = load_allocation(store, allocation.artifact_id)
    records = tuple(dataset.records)
    evidence = _evidence_rows(store, dataset_id, records, samples)
    chains = project_public_m2_chains(samples, evidence)
    train_chains = tuple(chain for chain in chains if chain.split == "train")
    training_input = compile_public_m2_input(
        chains, train_chains,
        source_binding_digest=public_m2_source_binding_digest(
            view.artifact_id, allocation.artifact_id,
        ),
        max_state_tokens=512,
        max_action_bytes=512,
    )
    prepared_train_chains = tuple(chain for chain in training_input.chains
                                  if chain.split == "train")
    if sum((len(chain.steps) + 3) // 4 for chain in prepared_train_chains) < 2:
        raise AssertionError("fixture must prepare at least two training windows per epoch")
    from tokenizers import Tokenizer

    vocab_size = Tokenizer.from_str(training_input.state_tokenizer.decode()).get_vocab_size()
    config = PublicM2EngineConfig(
        source_digest=training_input.identity,
        state_tokenizer_sha256=hashlib.sha256(training_input.state_tokenizer).hexdigest(),
        shape=ScratchShape(vocab_size, 8, 1, 2, 16, 0.0, 1024),
        max_action_bytes=training_input.max_action_bytes,
        max_actions_per_step=max(len(step.action_ids) for chain in training_input.chains
                                 for step in chain.steps),
        max_chain_steps=16,
        max_total_steps=sum(len(chain.steps) for chain in training_input.chains),
        max_total_input_tokens=16_384,
        slots=8,
        epochs=5,
        window_steps=4,
        device="cpu",
    )
    import torch

    from stpd.workers.public_m2_engine import PublicM2Engine, PublicM2EngineChain
    from stpd.workers.public_m2_preflight import preflight_public_m2_engine

    previous_threads = torch.get_num_threads()
    torch.set_num_threads(1)
    try:
        engine_chains = tuple(PublicM2EngineChain(chain.chain_id, chain.steps)
                              for chain in training_input.chains)
        preflight = preflight_public_m2_engine(
            tuple(chain for chain in engine_chains
                  if next(c.split for c in training_input.chains
                          if c.chain_id == chain.chain_id) == "train"),
            tuple(chain for chain in engine_chains
                  if next(c.split for c in training_input.chains
                          if c.chain_id == chain.chain_id) == "dev"),
            config,
        )
        runtime = dict(PublicM2Engine(
            preflight.train_chains, preflight.dev_chains, config,
        ).runtime)
        run = prepare_public_m2_run(
            store, training_input, config, producer,
            source_view_id=view.artifact_id, allocation_id=allocation.artifact_id,
            operation_id=training_operation_id,
        )
    finally:
        torch.set_num_threads(previous_threads)
    reporter = ObjectStoreRunReporter(store, store.blobs)
    return PreparedM2Campaign(
        owner, store, reporter, producer, config_path, dataset_id, allocation, view,
        training_input, config, runtime, run, training_admission, dev_admission,
    )
