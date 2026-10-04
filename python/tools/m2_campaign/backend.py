"""Actual configured-owner and typed public-M2 backend over a frozen worker checkout.

The CLI selects the checkout before importing this module. No data or model state is
created here: each run was already prepared through the research owner.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

from spireagent.artifact_contracts import Producer
from spireagent.json_boundary import BoundaryError, json_bytes
from spireagent.source import source_identity
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from spireagent.storage.store import ManifestArtifactStore
from spireagent.workbench.developer import ProjectConfig
from spireagent.workbench.developer_server import instance_lock
from spireagent.workbench.inplace_curation import configured_owner
from stpd.fullrun.selection_session import verified_dataset_sources
from stpd.fullrun.view_session import verified_model_views
from stpd.workers.public_m2_preflight import validate_public_m2_checkpoint
from stpd.workers.public_m2_remote import (
    _remaining_windows,
    _run_events,
    accept_public_m2_remote_result,
    build_public_m2_remote_request,
)
from stpd.workers.public_m2_run import MAX_CHECKPOINT_BYTES, _load_run, _read

from .config import RunBinding, Settings, canonical, sha
from .core import Accepted, NextSlice
from .journal import CampaignJournal, JournalError, _mkdir, _write


class PublicM2Backend:
    def __init__(self, settings: Settings, *, journal: CampaignJournal | None = None):
        self.settings = settings
        self.journal = journal
        value = settings.value
        self.producer = Producer.decode(value["producer"])
        self.worker_root = Path(value["worker_python_root"])
        self.project_config = Path(value["project_config"])
        self.store_root = Path(value["store_root"])
        self.lock_path = Path(value["store_lock"])
        self.campaign_lock_path = self.store_root.parent / ".public-m2-campaign.lock"
        self.runtime = dict(value["runtime"])
        self.bindings = {row.run_id: row for row in settings.runs}
        self._open = False
        self.store = ManifestArtifactStore(LocalBlobStore(self.store_root, create=False))
        self.reporter = ObjectStoreRunReporter(self.store, self.store.blobs)
        self.owner = configured_owner(ProjectConfig.load(self.project_config))
        if self.owner.store_dir.resolve() != self.store_root:
            raise ValueError("configured_owner_store_mismatch")
        self._source()
        self.reuse_stats: Any = None

    def _source(self) -> None:
        if source_identity(self.worker_root) != self.producer:
            raise ValueError("frozen_worker_source_mismatch")
        self.preparations = self.settings.preparations()

    @contextmanager
    def session(self) -> Iterator[None]:
        if self._open:
            raise ValueError("backend_session_already_open")
        self._open = True
        try:
            with (
                instance_lock(self.campaign_lock_path),
                verified_dataset_sources(self.store) as sources,
                verified_model_views(self.store),
            ):
                self._bind_budget_scope()
                self.reuse_stats = sources
                yield
        finally:
            self._open = False

    def _bind_budget_scope(self) -> None:
        directory = self.store_root.parent / ".public-m2-budget-scopes"
        if directory.is_symlink():
            raise ValueError("budget_scope_directory_unsafe")
        _mkdir(directory)
        location = directory / (self.settings.value["budget_scope_id"] + ".json")
        origin = self.settings.identity
        if self.journal is not None:
            if self.journal.current_settings_bytes() != self.settings.raw:
                raise ValueError("execution_settings_not_current")
            origin = self.journal.origin_settings_sha256
        raw = canonical({"schema": "stpd/m2-campaign-budget-scope-v1",
                         "config_sha256": origin,
                         "journal_root": self.settings.value["journal_root"]})
        if location.is_symlink():
            raise ValueError("budget_scope_unsafe")
        try:
            _write(location, raw, immutable=True)
        except JournalError as error:
            if str(error) == "immutable_conflict":
                raise ValueError("budget_scope_already_bound_to_other_campaign") from None
            raise

    def _binding(self, run_id: str) -> RunBinding:
        if not self._open or run_id not in self.bindings:
            raise ValueError("run_outside_open_campaign")
        return self.bindings[run_id]

    def _admit(self, binding: RunBinding) -> None:
        # Reconstruct the configured owner every time. Immutable input caches never
        # stand in for current owner identity, ledger integrity or use permission.
        owner = configured_owner(ProjectConfig.load(self.project_config))
        if owner.identity != self.owner.identity or owner.store_dir.resolve() != self.store_root:
            raise ValueError("configured_owner_changed")
        training = owner.require_training_datasets(
            self.store, (binding.dataset_id,), binding.operation_id,
        )
        dev = owner._allocation_dev_use(
            self.store, allocation_id=binding.allocation_id,
            training_operation_id=binding.operation_id,
            evaluation_operation_id=binding.evaluation_operation_id, record_use=False,
        )
        if (sha(json_bytes(training)) != binding.training_admission_sha256
                or sha(json_bytes(dev)) != binding.dev_admission_sha256):
            raise ValueError("current_owner_admission_changed")

    def admit(self, run_id: str) -> None:
        binding = self._binding(run_id)
        with instance_lock(self.lock_path):
            self._admit(binding)

    def _profile(self, binding: RunBinding) -> tuple[Any, Any, Any]:
        run, training, value, config, profile = _load_run(
            self.store, binding.run_id, self.producer, preflight_only=True,
        )
        info = run.parameters.value()
        receipt = self.preparations[binding.preparation_sha256]
        declared = receipt.get("runs")
        if (receipt.get("schema") not in {"stpd/m2-prepared-run-evidence-v1",
                                          "private-public-m2-memory-three-prepared-v1"}
                or not isinstance(declared, dict)):
            raise ValueError("prepared_run_inventory_required")
        matches = [row for row in declared.values() if isinstance(row, dict)
                   and row.get("run_id") == binding.run_id]
        if len(matches) != 1 or receipt.get("producer") != self.producer.to_dict():
            raise ValueError("prepared_run_evidence_mismatch")
        row = matches[0]
        if (any(row.get(key) != getattr(binding, key) for key in (
                "dataset_id", "allocation_id", "source_view_id", "operation_id",
                "evaluation_operation_id", "input_identity"))
                or row.get("config") != info["config"]
                or row.get("training_input_payload_sha256")
                != training.payload("training_input").sha256
                or row.get("state_tokenizer_sha256")
                != training.payload("state_tokenizer").sha256
                or row.get("receipt_sha256") not in self.preparations
                or receipt.get("intake_receipt_sha256") != row.get("receipt_sha256")):
            raise ValueError("prepared_run_intake_binding_mismatch")
        intake = self.preparations[row["receipt_sha256"]]
        products = intake.get("products", [])
        matches = [product for product in products if isinstance(product, dict)
                   and product.get("input_identity") == binding.input_identity]
        if (intake.get("schema") not in {"stpd/public-m2-intake-evidence-v1",
                                         "private-public-m2-intake-receipt-v1"}
                or intake.get("status") != "prepared_not_trained"
                or intake.get("producer") != row.get("intake_producer")
                or any(intake.get(key) != getattr(binding, key)
                       for key in ("dataset_id", "allocation_id", "source_view_id"))
                or intake.get("tokenizer_sha256") != training.payload("state_tokenizer").sha256
                or type(intake.get("codec_fit_calls")) is not int
                or intake["codec_fit_calls"] != 1
                or type(intake.get("official_source_projection_calls")) is not int
                or intake["official_source_projection_calls"] < 1
                or len(matches) != 1):
            raise ValueError("source_intake_evidence_mismatch")
        product = matches[0]
        train_counts = [product.get("train_labels") for product in products
                        if isinstance(product, dict)]
        if any(type(count) is not int or count < 1 for count in train_counts):
            raise ValueError("source_intake_product_counts_invalid")
        if (intake.get("codec_fit_labels") != min(cast(list[int], train_counts))
                or product.get("sha256") != training.payload("training_input").sha256
                or product.get("bytes") != training.payload("training_input").size
                or product.get("train_labels") != binding.train_decisions
                or product.get("dev_labels") != binding.dev_decisions):
            raise ValueError("source_intake_product_mismatch")
        if (info["operation_id"] != binding.operation_id
                or info["allocation_id"] != binding.allocation_id
                or info["source_view_id"] != binding.source_view_id
                or info["input_identity"] != binding.input_identity
                or sha(json_bytes(info["config"])) != binding.config_sha256
                or self.store.get_manifest(binding.source_view_id).parent("dataset")
                != binding.dataset_id
                or config.epochs != self.settings.value["goal_epochs"]
                or sum(len(chain.steps) for chain in value.chains if chain.split == "train")
                != binding.train_decisions
                or sum(len(chain.steps) for chain in value.chains if chain.split == "dev")
                != binding.dev_decisions):
            raise ValueError("prepared_run_configuration_mismatch")
        window_limit = self.settings.value["max_windows"]
        epoch_windows = _remaining_windows(profile, None)
        nominal_requests = ((epoch_windows + window_limit - 1) // window_limit
                            * self.settings.value["goal_epochs"])
        if binding.max_requests < nominal_requests:
            raise ValueError("request_limit_cannot_complete_configured_run")
        return run, training, profile

    def _progress(self, run: Any, training: Any, profile: Any) -> tuple[str | None, Any]:
        events = _run_events(self.store, run.artifact_id)
        checkpoints = [event for event in events
                       if event.parameters.value().get("kind") == "checkpoint"]
        if not checkpoints:
            if events:
                raise ValueError("run_history_without_accepted_checkpoint")
            return None, None
        latest = max(event.parameters.value()["optimizer_updates"] for event in checkpoints)
        identities = {event.parameters.value()["details"]["checkpoint_id"]
                      for event in checkpoints
                      if event.parameters.value()["optimizer_updates"] == latest}
        if len(identities) != 1:
            raise ValueError("ambiguous_latest_checkpoint")
        identity = identities.pop()
        item = self.store.get_manifest(identity)
        if (item.kind != "checkpoint" or item.producer != self.producer
                or item.parent("run") != run.artifact_id
                or item.parent("training_input") != training.artifact_id):
            raise ValueError("checkpoint_lineage_mismatch")
        progress = validate_public_m2_checkpoint(
            _read(self.store, item.payload("checkpoint"), MAX_CHECKPOINT_BYTES),
            profile, expected_runtime=self.runtime,
        )
        if any(item.parameters.value().get(key) != progress[key] for key in (
            "completed_epochs", "chain_index", "window_cursor", "optimizer_updates", "label_count",
        )):
            raise ValueError("checkpoint_progress_mismatch")
        return identity, progress

    def plan_next(self, run_id: str) -> NextSlice | None:
        binding = self._binding(run_id)
        with instance_lock(self.lock_path):
            self._admit(binding)
            run, training, profile = self._profile(binding)
            checkpoint_id, progress = self._progress(run, training, profile)
            completed = self.reporter.completed(run_id)
            if completed is not None:
                if (progress is None or progress["completed_epochs"] != 5
                        or completed.producer != self.producer
                        or completed.parent("run") != run_id
                        or completed.parent("checkpoint") != checkpoint_id):
                    raise ValueError("run_completion_mismatch")
                return None
            remaining = _remaining_windows(profile, progress)
            if remaining < 1:
                raise ValueError("terminal_checkpoint_requires_completion_reconciliation")
            windows = min(self.settings.value["max_windows"], remaining)
            epoch = 1 if progress is None else progress["completed_epochs"] + 1
            return NextSlice(checkpoint_id, windows, epoch, windows == remaining)

    def build(self, run_id: str, attempt_id: str, planned: NextSlice) -> bytes:
        binding = self._binding(run_id)
        self._source()
        with instance_lock(self.lock_path):
            self._admit(binding)
            self._profile(binding)
            self.owner._allocation_dev_use(
                self.store, allocation_id=binding.allocation_id,
                training_operation_id=binding.operation_id,
                evaluation_operation_id=binding.evaluation_operation_id, record_use=True,
            )
            return build_public_m2_remote_request(
                self.store, self.reporter, run_id, self.producer, attempt_id=attempt_id,
                expected_runtime=self.runtime, resume=planned.resume_id,
                max_windows=planned.max_windows,
            )

    def accept(self, run_id: str, request: bytes, result: bytes) -> Accepted:
        binding = self._binding(run_id)
        self._source()
        with instance_lock(self.lock_path):
            self._admit(binding)
            outcome = accept_public_m2_remote_result(
                self.store, self.reporter, request, result, request_sha256=sha(request),
                expected_runtime=self.runtime, select_completion=False,
            )
            if outcome.run_id != run_id or outcome.checkpoint_id is None:
                raise ValueError("typed_outcome_run_mismatch")
            if outcome.state == "completed":
                if outcome.result_id is None:
                    raise ValueError("terminal_result_identity_missing")
                # The acceptor has validated and published the full typed delta.
                # Select only its terminal result; partial slices remain resumable.
                self.reporter.complete(self.store.get_manifest(outcome.result_id))
            run, training, profile = self._profile(binding)
            checkpoint_id, progress = self._progress(run, training, profile)
            if checkpoint_id is None or checkpoint_id != outcome.checkpoint_id:
                raise ValueError("accepted_checkpoint_not_latest")
            summaries = self.stage_summaries(run_id)
            return Accepted(
                checkpoint_id, progress["completed_epochs"], progress["optimizer_updates"],
                outcome.state == "completed", tuple(item["epoch"] for item in summaries),
            )

    def stage_summaries(self, run_id: str) -> list[dict[str, Any]]:
        binding = self._binding(run_id)
        found: dict[int, dict[str, Any]] = {}
        for event in _run_events(self.store, run_id):
            row = event.parameters.value()
            if row.get("kind") != "epoch_stage":
                continue
            stage = self.store.get_manifest(row["details"]["stage_id"])
            model = self.store.get_manifest(stage.parent("model"))
            evaluation = self.store.get_manifest(stage.parent("offline_evaluation"))
            metrics = evaluation.parameters.value()
            epoch = stage.parameters.value()["epoch"]
            if (stage.parent("run") != run_id or model.parent("run") != run_id
                    or evaluation.parent("run") != run_id
                    or evaluation.parent("model") != model.artifact_id
                    or metrics["label_count"] != binding.dev_decisions
                    or epoch not in (1, 3, 5)):
                raise BoundaryError("m2_campaign", "stage_summary_binding_mismatch")
            summary = {"epoch": epoch, "stage_id": stage.artifact_id,
                       "checkpoint_id": stage.parent("checkpoint"),
                       "model_id": model.artifact_id,
                       "weights_sha256": model.payload("weights").sha256,
                       "evaluation_id": evaluation.artifact_id, "metrics": metrics}
            if epoch in found and found[epoch] != summary:
                raise ValueError("conflicting_stage_summary")
            found[epoch] = summary
        return [found[key] for key in sorted(found)]
