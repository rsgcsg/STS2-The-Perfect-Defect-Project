"""Strict configuration for a prepared-run campaign, independent of private arm names."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True, allow_nan=False) + "\n").encode()


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def digest(value: object, length: int = 64) -> str:
    if not isinstance(value, str) or re.fullmatch(f"[0-9a-f]{{{length}}}", value) is None:
        raise ValueError("invalid_digest")
    return value


def fields(value: object, names: set[str]) -> dict[str, Any]:
    if type(value) is not dict or set(value) != names:
        raise ValueError("configuration_fieldset_mismatch")
    return value


def path(value: object) -> Path:
    if type(value) is not str:
        raise ValueError("absolute_path_required")
    result = Path(value)
    if not result.is_absolute() or result.resolve(strict=False) != result:
        raise ValueError("canonical_absolute_path_required")
    return result


def bounded_file(location: Path, maximum: int) -> bytes:
    if location.is_symlink() or not location.is_file():
        raise ValueError("regular_file_required")
    with location.open("rb") as stream:
        raw = stream.read(maximum + 1)
    if not 0 < len(raw) <= maximum:
        raise ValueError("file_size_limit")
    return raw


@dataclass(frozen=True)
class RunBinding:
    run_id: str
    max_requests: int
    dataset_id: str
    allocation_id: str
    source_view_id: str
    operation_id: str
    evaluation_operation_id: str
    input_identity: str
    preparation_sha256: str
    config_sha256: str
    training_admission_sha256: str
    dev_admission_sha256: str
    train_decisions: int
    dev_decisions: int

    @classmethod
    def decode(cls, value: object) -> RunBinding:
        row = fields(value, set(cls.__dataclass_fields__))
        for name in set(row) - {"max_requests", "train_decisions", "dev_decisions"}:
            digest(row[name], 32 if name.endswith("operation_id") else 64)
        if any(type(row[key]) is not int or row[key] < 1
               for key in ("max_requests", "train_decisions", "dev_decisions")):
            raise ValueError("invalid_run_limits")
        if row["max_requests"] > 1000:
            raise ValueError("request_limit_too_large")
        return cls(**row)


@dataclass(frozen=True)
class Settings:
    raw: bytes
    value: dict[str, Any]
    runs: tuple[RunBinding, ...]

    @property
    def identity(self) -> str:
        return sha(self.raw)

    @classmethod
    def load(cls, location: Path) -> Settings:
        raw = bounded_file(location, 1024 * 1024)
        value = fields(json.loads(raw), {
            "schema", "worker_python_root", "producer", "project_config", "store_root",
            "store_lock", "journal_root", "approval_dir", "provider_adapter", "runtime",
            "runs", "max_windows", "goal_epochs", "limits",
            "preparation_evidence", "budget_scope_id",
        })
        if value["schema"] != "stpd/m2-campaign-config-v1":
            raise ValueError("unsupported_campaign_config")
        digest(value["budget_scope_id"], 32)
        for name in ("worker_python_root", "project_config", "store_root", "store_lock",
                     "journal_root", "approval_dir"):
            path(value[name])
        producer = fields(value["producer"], {"repository", "source_revision", "uv_lock_sha256"})
        if type(producer["repository"]) is not str or not producer["repository"]:
            raise ValueError("repository_required")
        digest(producer["source_revision"], 40)
        digest(producer["uv_lock_sha256"])
        adapter = fields(value["provider_adapter"], {"path", "sha256"})
        path(adapter["path"])
        digest(adapter["sha256"])
        evidence = value["preparation_evidence"]
        if type(evidence) is not list or not 1 <= len(evidence) <= 16:
            raise ValueError("preparation_evidence_required")
        evidence_ids = set()
        for reference in evidence:
            reference = fields(reference, {"path", "sha256"})
            path(reference["path"])
            evidence_ids.add(digest(reference["sha256"]))
        runtime = fields(value["runtime"], {
            "torch", "python", "platform", "default_dtype", "cpu_threads",
            "implementation_sha256",
        })
        digest(runtime["implementation_sha256"])
        if (runtime["default_dtype"] != "torch.float32"
                or type(runtime["cpu_threads"]) is not int or runtime["cpu_threads"] < 1
                or any(type(runtime[k]) is not str or not runtime[k]
                       for k in ("torch", "python", "platform"))):
            raise ValueError("invalid_runtime")
        if (type(value["max_windows"]) is not int or not 1 <= value["max_windows"] <= 512
                or type(value["goal_epochs"]) is not int or value["goal_epochs"] != 5):
            raise ValueError("unsupported_campaign_limits")
        limits = fields(value["limits"], {
            "command_seconds", "approval_wait_seconds", "poll_seconds", "max_rss_bytes",
            "minimum_free_bytes", "batch_raw_usd", "workspace_cap_usd",
            "external_exposure_usd",
        })
        for key in ("command_seconds", "approval_wait_seconds", "poll_seconds",
                    "max_rss_bytes", "minimum_free_bytes"):
            if type(limits[key]) is not int or limits[key] < 1:
                raise ValueError("positive_resource_limit_required")
        if not isinstance(value["runs"], list) or not 1 <= len(value["runs"]) <= 32:
            raise ValueError("bounded_run_inventory_required")
        runs = tuple(RunBinding.decode(row) for row in value["runs"])
        if len({row.run_id for row in runs}) != len(runs):
            raise ValueError("duplicate_run")
        if any(row.preparation_sha256 not in evidence_ids for row in runs):
            raise ValueError("run_preparation_evidence_missing")
        return cls(raw, value, runs)

    def preparations(self) -> dict[str, Any]:
        result = {}
        for reference in self.value["preparation_evidence"]:
            raw = bounded_file(Path(reference["path"]), 4 * 1024 * 1024)
            if sha(raw) != reference["sha256"]:
                raise ValueError("preparation_evidence_digest_mismatch")
            result[reference["sha256"]] = json.loads(raw)
        return result
