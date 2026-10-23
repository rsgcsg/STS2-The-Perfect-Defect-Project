"""Object-store-backed run events and immutable, conditional final-result selection."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from spireagent.artifact_contracts import Manifest
from spireagent.json_boundary import BoundaryError, decode_json, digest, json_bytes, object_fields
from spireagent.storage.blobs import BlobStore, StoreError
from spireagent.storage.store import ArtifactStore


class ObjectStoreRunReporter:
    def __init__(self, store: ArtifactStore, slots: BlobStore, *,
                 cache_events: bool = False, read_concurrency: int = 1) -> None:
        if type(read_concurrency) is not int or not 1 <= read_concurrency <= 16:
            raise BoundaryError("reporter", "invalid_read_concurrency")
        self.store = store
        self.slots = slots
        self._read_concurrency = read_concurrency
        # Only opt-in observers reuse verified immutable bytes. Auditors retain
        # the default fresh read behavior; the index is still listed each time.
        self._events_cache: dict[str, Manifest] | None = {} if cache_events else None

    def emit(self, event: Manifest) -> str:
        if (
            event.kind != "run_event"
            or event.parameters.value().get("schema") != "stpd/run-event-v1"
        ):
            raise BoundaryError("reporter", "unsupported_event")
        run_id = event.parent("run")
        key = f"run-events/{run_id}/{event.artifact_id}.json"
        if self._indexed(run_id):
            # The declaration makes this directory exhaustive, including a
            # publication interrupted before its final manifest is visible.
            self.slots.put_if_absent(key, event.to_bytes())
            self.store.publish(event)
        else:
            self.store.publish(event)
            self.slots.put_if_absent(key, event.to_bytes())
        return event.artifact_id

    def completed(self, run_id: str) -> Manifest | None:
        digest(run_id, "reporter.run_id")
        try:
            raw = self.slots.get(f"run-completions/{run_id}.json")
        except StoreError as error:
            if error.code == "object_not_found":
                return None
            raise
        marker = object_fields(
            decode_json(raw), {"schema", "run_id", "result_id"}, "run_completion"
        )
        if (
            marker["schema"] != "stpd/run-completion-v1"
            or marker["run_id"] != run_id
            or raw != json_bytes(marker)
        ):
            raise BoundaryError("reporter", "completion_marker_mismatch")
        result = self.store.get_manifest(digest(marker["result_id"], "reporter.result_id"))
        if (
            result.kind != "run_result"
            or result.parent("run") != run_id
            or result.parameters.value().get("schema") != "stpd/run-result-v1"
        ):
            raise BoundaryError("reporter", "completion_result_mismatch")
        for parent in result.parents:
            self.store.get_manifest(parent.artifact_id)
        return result

    def complete(self, result: Manifest) -> str:
        if (
            result.kind != "run_result"
            or result.parameters.value().get("schema") != "stpd/run-result-v1"
        ):
            raise BoundaryError("reporter", "unsupported_result")
        run_id = result.parent("run")
        self.store.publish(result)
        self.slots.put_if_absent(
            f"run-completions/{run_id}.json",
            json_bytes(
                {
                    "schema": "stpd/run-completion-v1",
                    "run_id": run_id,
                    "result_id": result.artifact_id,
                }
            ),
        )
        return result.artifact_id

    def _indexed(self, run_id: str) -> bool:
        run = self.store.get_manifest(run_id)
        declaration = run.parameters.value().get("event_index")
        if declaration is not None and declaration != "run-events-v1":
            raise BoundaryError("reporter", "unsupported_event_index")
        return declaration == "run-events-v1"

    def _indexed_events(self, run_id: str) -> list[Manifest]:
        prefix = f"run-events/{run_id}/"
        found, pending = [], []
        for key in self.slots.keys(prefix):
            identity = key.removeprefix(prefix).removesuffix(".json")
            if key != f"{prefix}{digest(identity, 'reporter.event_id')}.json":
                raise BoundaryError("reporter", "event_index_key_mismatch")
            if self._events_cache is not None and identity in self._events_cache:
                found.append(self._events_cache[identity])
                continue
            pending.append((identity, key))

        def read_one(row: tuple[str, str]) -> Manifest:
            identity, key = row
            event = Manifest.from_bytes(self.slots.get(key), identity)
            if (event.kind != "run_event" or event.parent("run") != run_id
                    or event.parameters.value().get("schema") != "stpd/run-event-v1"):
                raise BoundaryError("reporter", "event_index_binding_mismatch")
            try:
                published = self.store.get_manifest(identity)
            except StoreError as error:
                if error.code == "object_not_found":
                    # This is an incomplete publication, not an empty history or
                    # corrupt index. The caller retains its current attempt.
                    raise BoundaryError("reporter", "event_publication_pending") from error
                raise
            if published != event:
                raise BoundaryError("reporter", "event_index_manifest_mismatch")
            return event

        def retain(event: Manifest) -> None:
            if self._events_cache is not None:
                self._events_cache[event.artifact_id] = event
            found.append(event)

        if self._read_concurrency == 1 or len(pending) < 2:
            for row in pending:
                retain(read_one(row))
        else:
            # Independent immutable reads only. Retention remains on this caller
            # thread; each index/manifest pair keeps the same complete validation.
            with ThreadPoolExecutor(max_workers=self._read_concurrency,
                                    thread_name_prefix="verified-run-event") as pool:
                for event in pool.map(read_one, pending):
                    retain(event)
        return found

    def events(self, run_id: str) -> tuple[Manifest, ...]:
        digest(run_id, "reporter.run_id")
        if self._indexed(run_id):
            return tuple(sorted(self._indexed_events(run_id),
                                key=lambda e: (e.parameters.value().get("step", 0), e.artifact_id)))
        found = []
        for artifact_id in self.store.manifest_ids():
            manifest = self.store.get_manifest(artifact_id)
            if manifest.kind == "run_event" and any(
                p.role == "run" and p.artifact_id == run_id for p in manifest.parents
            ):
                if manifest.parameters.value().get("schema") != "stpd/run-event-v1":
                    raise BoundaryError("reporter", "unsupported_event")
                found.append(manifest)
        return tuple(
            sorted(found, key=lambda e: (e.parameters.value().get("step", 0), e.artifact_id))
        )
