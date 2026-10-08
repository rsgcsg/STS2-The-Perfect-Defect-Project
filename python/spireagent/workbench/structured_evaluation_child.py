"""Fixed code-owned private child for the existing local evaluation operation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from spireagent.json_boundary import BoundaryError, json_bytes
from spireagent.source import source_identity
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.store import ManifestArtifactStore
from spireagent.workbench.local_curation import OWNER_NAME, LocalCurationOwner
from spireagent.workbench.local_memory_evaluation import LocalMemoryEvaluationService


def execute(operation_file: Path, ledger: Path, operation_id: str) -> dict[str, str]:
    # Current journal and owner identity fence every domain publication boundary.
    initial = json.loads(operation_file.read_bytes())
    identity = tuple(initial["_owner"])
    operation = LocalMemoryEvaluationService._read(operation_file, identity)
    store_dir = Path(identity[3])
    marker = json.loads((store_dir / OWNER_NAME).read_bytes())
    owner = LocalCurationOwner(
        ledger,
        store_dir,
        *identity[:3],
        legacy_guard=operation["legacy_guard"],
        owner_schema=marker["schema"],
    )
    store = ManifestArtifactStore(LocalBlobStore(store_dir, create=False))

    class Fence:
        def assert_current(self) -> None:
            current = LocalMemoryEvaluationService._read(operation_file, owner.identity)
            if (
                current.get("operation_id") != operation_id
                or current.get("status") != "pending"
                or current.get("model_type") != "structured"
                or any(
                    current.get(key) != operation.get(key)
                    for key in ("model_id", "source_id", "partition")
                )
            ):
                raise BoundaryError("local_memory_evaluation", "operation_superseded")

    fence = Fence()
    fence.assert_current()
    owner.record_verified_protocol_evaluation_use(
        store, operation["source_id"], operation["model_id"], operation_id
    )
    # Delayed import preserves collector dependency and parent RNG boundaries.
    from stpd.workers.structured_evaluation import (
        StructuredEvaluationRequest,
        prepare_structured_evaluation,
        run_structured_evaluation,
    )

    producer = source_identity(Path(__file__).resolve().parents[2])
    request = StructuredEvaluationRequest(
        operation["source_id"], operation["model_id"], operation_id, operation["partition"]
    )
    prepared = prepare_structured_evaluation(store, request, producer, authority=fence)
    result = run_structured_evaluation(store, prepared.artifact_id, producer, authority=fence)
    return {"evaluation_id": result.artifact_id, "evaluation_input_id": prepared.artifact_id}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operation-file", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--operation", required=True)
    args = parser.parse_args()
    print(json_bytes(execute(args.operation_file, args.ledger, args.operation)).decode())


if __name__ == "__main__":
    main()
