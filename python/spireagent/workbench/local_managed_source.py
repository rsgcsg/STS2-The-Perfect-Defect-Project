"""Explicit local purpose admission for an archived Managed control-input stream.

The report archive is a Workbench-owned record of public observations, not a
Human witness or independent attestation of the actor or game process.
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import closing
from pathlib import Path
from typing import Any

from spireagent.json_boundary import BoundaryError, digest
from spireagent.source import source_identity
from spireagent.storage.registry import SQLiteRegistry, sync_registry
from spireagent.workbench.developer import ROOT, ProjectConfig
from spireagent.workbench.local_dataset import LocalDatasetService
from spireagent.workbench.local_environment import LocalEnvironmentService
from stpd.fullrun.managed_text_menu_import import (
    SOURCE_SCHEMA,
    ManagedImportExpectation,
    import_managed_text_menu_report,
    load_managed_text_menu_source,
)

BINDING_SCHEMA = "stpd/local-managed-source-binding-v1"
IMPORT_SCHEMA = "stpd/local-managed-source-import-v1"


def _expectation(report: dict[str, Any]) -> ManagedImportExpectation:
    """Bind to exact facts in the existing immutable app-owned report archive."""
    episode = report.get("episode_identity")
    if not isinstance(episode, dict):
        raise BoundaryError("local_managed_source", "archived_identity_unavailable")
    provenance = episode.get("episode_provenance")
    if not isinstance(provenance, dict):
        raise BoundaryError("local_managed_source", "archived_identity_unavailable")
    try:
        return ManagedImportExpectation.from_dict({
            "session_id": report["session_id"],
            "scenario_id": report["scenario_id"],
            "seed": report["seed"],
            "host_package_pin": report["host_package_pin"],
            "candidate_build": episode["candidate_build"],
            "environment_fingerprint": episode["environment_fingerprint"],
            "runtime_instance_id": provenance["runtime_instance_id"],
        })
    except (KeyError, TypeError, ValueError) as error:
        raise BoundaryError("local_managed_source", "archived_identity_unavailable") from error


class LocalManagedSourceService:
    def __init__(self, config: ProjectConfig, environment: LocalEnvironmentService) -> None:
        self.config = config
        self.environment = environment
        self.selection = LocalDatasetService(config)
        self.lock = threading.RLock()

    def _selected(self) -> tuple[Any, Any, Path]:
        return self.selection._selected()

    def binding(self, source_id: object) -> dict[str, Any]:
        """Read typed source and shared purpose claim; never reserve or write use."""
        identity = digest(source_id, "local_managed_source.source_id")
        owner, store, registry_path = self._selected()
        source = load_managed_text_menu_source(store, identity)
        claim = owner.ledger.dataset(identity)
        purpose = claim[0] if claim == ("training", {source.split_run_id}) or claim == (
            "test", {source.split_run_id}) else None
        with closing(sqlite3.connect(owner.path.resolve().as_uri() + "?mode=ro",
                                     uri=True)) as db:
            pending = db.execute("SELECT 1 FROM local_source_pending WHERE artifact=?",
                                 (identity,)).fetchone() is not None
        indexed = owner.ledger.exact_source_ready(identity)
        try:
            registered = (SQLiteRegistry(registry_path, readonly=True).get(identity)
                          == source.manifest)
        except BoundaryError as error:
            if error.code != "not_indexed":
                raise
            registered = False
        return {
            "schema": BINDING_SCHEMA, "artifact_id": identity,
            "report_artifact_id": source.report_id,
            "sample_type": "managed_control_input_stream",
            "scope": "engineering_control", "actor": "unverified",
            "status": "admitted" if purpose and indexed and registered and not pending
            else "recovery_required",
            "curation_purpose": purpose,
            "split_run_id": source.split_run_id,
            "event_count": len(source.inputs),
        }

    def import_report(self, report_id: object, purpose: object) -> dict[str, Any]:
        identity = digest(report_id, "local_managed_source.report_id")
        if not isinstance(purpose, str) or purpose not in {"training", "test"}:
            raise BoundaryError("local_managed_source", "managed_purpose_invalid")
        with self.lock:
            archive = self.environment.report_archive(identity)
            expected = _expectation(self.environment.report(identity))
            owner, store, registry_path = self._selected()
            existing = []
            for artifact_id in store.manifest_ids():
                manifest = store.get_manifest(artifact_id)
                if (manifest.kind == "dataset"
                        and manifest.parameters.value().get("schema") == SOURCE_SCHEMA
                        and any(parent.role == "managed_report"
                                and parent.artifact_id == identity
                                for parent in manifest.parents)):
                    existing.append(artifact_id)
            if len(existing) > 1:
                raise BoundaryError("local_managed_source", "source_identity_ambiguous")
            if existing:
                claim = owner.ledger.dataset(existing[0])
                if claim is not None and claim[0] != purpose:
                    raise BoundaryError("local_managed_source", "reservation_identity_conflict")
            owner.begin_source(identity)
            if existing:
                source = load_managed_text_menu_source(store, existing[0], expected=expected)
            else:
                source = import_managed_text_menu_report(
                    archive, store, identity, source_identity(ROOT), expected=expected,
                )
            source_id = source.manifest.artifact_id
            owner.published_source(identity, source_id)
            owner.ledger.reserve_managed_observed_source(
                store, source_id, purpose, expected=expected,
            )
            registry = SQLiteRegistry(registry_path, readonly=False)
            cached = frozenset(item.artifact_id for item in registry.manifests()
                               if registry.is_cached(item.artifact_id))
            sync_registry(store, registry, cached)
            owner.complete_index(identity, source_id)
            return {
                "schema": IMPORT_SCHEMA, "artifact_id": source_id,
                "report_artifact_id": identity,
                "sample_type": "managed_control_input_stream",
                "scope": "engineering_control", "actor": "unverified",
                "status": "admitted",
                "curation_purpose": purpose,
                "split_run_id": source.split_run_id,
                "event_count": len(source.inputs),
            }
