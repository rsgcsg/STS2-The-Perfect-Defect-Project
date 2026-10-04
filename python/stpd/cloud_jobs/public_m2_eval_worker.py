"""Single-attempt opaque stdin/stdout entry for public M2 evaluation bytes."""

from __future__ import annotations

import hashlib
import os
import re
import sys
from pathlib import Path

from spireagent.artifact_contracts import Producer
from spireagent.json_boundary import BoundaryError, decode_json, digest, json_bytes
from spireagent.source import source_identity

from .public_m2_modal import (
    MAX_PUBLIC_M2_REQUEST_BYTES,
    MAX_PUBLIC_M2_RESULT_BYTES,
    PublicM2ModalBinding,
    PublicM2ModalResources,
    encode_modal_response,
)

_IMAGE_ROOT = Path("/opt/stpd/python")


def _runtime_source_identity() -> Producer:
    root = Path(__file__).resolve().parents[2]
    if root != _IMAGE_ROOT.resolve():
        raise BoundaryError("public_m2_eval_worker", "fixed_image_checkout_required")
    return source_identity(root)


def _execute_once(request: bytes, *, request_sha256: str,
                  attempt_id: str, producer: Producer,
                  resources: PublicM2ModalResources) -> bytes:
    from stpd.workers.public_m2_eval_remote import (
        MAX_REQUEST_BYTES,
        MAX_RESULT_BYTES,
        _request_parts,
        execute_public_m2_eval_request,
    )

    if (not isinstance(request, bytes) or not 1 <= len(request) <= MAX_PUBLIC_M2_REQUEST_BYTES
            or len(request) > MAX_REQUEST_BYTES
            or hashlib.sha256(request).hexdigest() != request_sha256):
        raise BoundaryError("public_m2_eval_worker", "request_identity_or_size_mismatch")
    if _runtime_source_identity() != producer:
        raise BoundaryError("public_m2_eval_worker", "image_source_identity_mismatch")
    header, _ = _request_parts(request)
    if (header["attempt_id"] != attempt_id
            or header["evaluation_producer"] != producer.to_dict()
            or header["resources"] != resources.to_dict()):
        raise BoundaryError("public_m2_eval_worker", "request_target_binding_mismatch")
    result = execute_public_m2_eval_request(request, request_sha256=request_sha256)
    if not 1 <= len(result) <= min(MAX_RESULT_BYTES, MAX_PUBLIC_M2_RESULT_BYTES):
        raise BoundaryError("public_m2_eval_worker", "result_size_limit")
    return result


def _worker_main() -> int:
    try:
        request_sha = digest(os.environ.get("STPD_PUBLIC_M2_REQUEST_SHA256", ""),
                             "public_m2_eval_worker.request_sha256")
        attempt_id = os.environ.get("STPD_PUBLIC_M2_ATTEMPT_ID", "")
        if (re.fullmatch(r"[0-9a-f]{32}", attempt_id) is None
                or os.environ.get("STPD_PUBLIC_M2_APP_NAME")
                != "stpd-public-m2-" + attempt_id):
            raise BoundaryError("public_m2_eval_worker", "attempt_identity_mismatch")
        plan_raw = os.environ.get("STPD_PUBLIC_M2_RESOURCE_PLAN", "").encode("utf-8")
        resources = PublicM2ModalResources.from_bytes(plan_raw)
        if resources.plan_sha256 != os.environ.get("STPD_PUBLIC_M2_RESOURCE_PLAN_SHA256"):
            raise BoundaryError("public_m2_eval_worker", "resource_plan_mismatch")
        producer_raw = os.environ.get("STPD_PUBLIC_M2_PRODUCER", "").encode("utf-8")
        if not 1 <= len(producer_raw) <= 4096:
            raise BoundaryError("public_m2_eval_worker", "producer_size_limit")
        producer = Producer.decode(decode_json(producer_raw))
        if json_bytes(producer.to_dict()) != producer_raw:
            raise BoundaryError("public_m2_eval_worker", "noncanonical_producer")
        request = sys.stdin.buffer.read(MAX_PUBLIC_M2_REQUEST_BYTES + 1)
        from stpd.workers.public_m2_eval_remote import _request_parts
        request_header, _ = _request_parts(request)
        runtime_contract_raw = os.environ.get(
            "STPD_PUBLIC_M2_RUNTIME_RECEIPT", "",
        ).encode("utf-8")
        runtime_sha = hashlib.sha256(runtime_contract_raw).hexdigest()
        if runtime_sha != os.environ.get("STPD_PUBLIC_M2_RUNTIME_RECEIPT_SHA256"):
            raise BoundaryError("public_m2_eval_worker", "runtime_contract_digest_mismatch")
        if decode_json(runtime_contract_raw) != request_header["runtime_requirement"]:
            raise BoundaryError("public_m2_eval_worker", "runtime_contract_binding_mismatch")
        result = _execute_once(request, request_sha256=request_sha,
                               attempt_id=attempt_id, producer=producer,
                               resources=resources)
        # Use the caller's inference contract digest as an opaque execution pin;
        # actual GPU/runtime facts are included in the typed eval result itself.
        from stpd.workers.public_m2_eval_remote import InferenceRuntimeRequirement
        requirement = InferenceRuntimeRequirement.from_value(decode_json(runtime_contract_raw))
        image_id = os.environ.get("STPD_PUBLIC_M2_IMAGE_ID", "")
        if re.fullmatch(r"im-[A-Za-z0-9_-]+", image_id) is None:
            raise BoundaryError("public_m2_eval_worker", "image_identity_mismatch")
        binding = PublicM2ModalBinding(
            producer, request_sha, image_id,
            runtime_sha, resources,
        )
        if not requirement.gpu_name_contains:
            raise BoundaryError("public_m2_eval_worker", "runtime_contract_invalid")
        sys.stdout.buffer.write(encode_modal_response(result, binding))
        sys.stdout.buffer.flush()
    except Exception:
        print("public_m2_eval_worker_failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_worker_main())
