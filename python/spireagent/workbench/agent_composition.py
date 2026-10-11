"""Code-owned effective Agent interpretation of existing registered originals."""

from __future__ import annotations

import hashlib
from typing import Any, cast

from spireagent.encoding import canonical_json
from spireagent.json_boundary import BoundaryError

SCHEMA = "spireagent/agent-composition-v1"
TEACHER_ADAPTER = "stpd-native-public-program-teacher"


def describe(
    entry: dict[str, Any],
    manifest: dict[str, Any],
    config: dict[str, Any],
    original: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """No inference/weight reads; exact originals stay the installation authority."""
    from spireagent.workbench.trusted_agents import TRUSTED_AGENT_ADAPTERS

    adapter = entry["adapter"]
    if adapter not in TRUSTED_AGENT_ADAPTERS:
        raise BoundaryError("agent_composition", "unsupported_trusted_adapter")
    native = manifest.get("schema") == "sts2.policy-runtime/agent-manifest-1"
    teacher = adapter == TEACHER_ADAPTER
    artifact = dict(manifest.get("artifact", {}))
    original_input = dict(manifest.get("input" if native else "representation", {}))
    state: dict[str, Any]
    if teacher:
        if (
            not native
            or original_input.get("state_recovery", {}).get("model_bindings") != []
            or manifest.get("adapter", {}).get("id") != TEACHER_ADAPTER
        ):
            raise BoundaryError("agent_composition", "teacher_requires_declared_empty_models")
        models: list[dict[str, Any]] = []
        state = {"kind": "ACK_committed_script", "declaration": original_input}
    elif native:
        bindings = original_input.get("state_recovery", {}).get("model_bindings")
        if isinstance(bindings, list) and len(bindings) > 1:
            raise BoundaryError("agent_composition", "multi_model_execution_not_supported")
        # Recovery bindings are not the complete model dependency declaration.
        models = [
            {
                "model_id": config.get("package_model_id", artifact.get("id")),
                "weights_sha256": config.get("weights_sha256"),
                "binding": "original_native_model_package",
            }
        ]
        state = {"kind": "declared_native_state", "declaration": original_input}
    else:
        models = [
            {
                "model_artifact_id": artifact.get("id"),
                "package_sha256": artifact.get("sha256"),
                "binding": "legacy_bound_package",
            }
        ]
        state = {
            "kind": "qualified_public_W"
            if adapter == "stpd-public-m2-decision-adapter"
            else "stateless_current"
            if adapter == "stpd-public-m0-decision-adapter"
            else "legacy_bound_state",
            "declaration": (
                {
                    "runtime_profile": manifest.get("adapter_config", {}).get(
                        "public_stateful_profile"
                    ),
                    "adapter_profile": manifest.get("adapter_config", {}).get("stpd_public_m2"),
                    "reset_binding": "original_model_config_and_explicit_live_segment",
                    "cache_binding": "exact_continuity_namespace_and_qualified_ordinal",
                }
                if adapter == "stpd-public-m2-decision-adapter"
                else config.get("memory")
            ),
        }
    if adapter == "stpd-public-m0-decision-adapter":
        task = manifest.get("adapter_config", {}).get("public_m0", {}).get("task_spec")
        program = manifest.get("adapter_config", {}).get("public_m0", {}).get("program_spec")
    elif adapter == "stpd-public-m2-decision-adapter":
        task = manifest.get("adapter_config", {}).get("public_m2_installation", {}).get("task_spec")
        program = None
    else:
        task = (original or config).get("agent_spec", {}).get("task_spec") if native else None
        program = (original or {}).get("agent_spec") if teacher else None
    definition = {
        "binding": "legacy_manifest_bound",
        "agent": dict(manifest.get("agent" if native else "policy", {})),
        "models": models,
        "program": program,
        "input": original_input,
        "state": state,
        "task_binding": {
            "original_spec": task,
            "origin": "manifest" if not native else "original_artifact",
        },
        "environment": {
            "profile": entry.get("runtime_profile"),
            "support": manifest.get("support"),
            "requirements": manifest.get("requirements"),
        },
        "driver": {
            "protocol": manifest.get("adapter", {}).get("protocol"),
            "capabilities": "AgentSession"
            if native
            else "Decision4"
            if adapter == "stpd-public-m2-decision-adapter"
            else "legacy_decision",
        },
    }
    implementation = {
        "adapter": dict(manifest.get("adapter", {})),
        # Durable output identity; private launch paths remain in the hash-bound
        # original manifest/config and are never copied into public composition.
        "runtime_artifact": {"id": artifact.get("id"), "sha256": artifact.get("sha256")},
        "original_manifest_id": manifest.get("manifest_id"),
        "manifest_sha256": hashlib.sha256(canonical_json(manifest).encode()).hexdigest(),
        "config_sha256": hashlib.sha256(canonical_json(config).encode()).hexdigest(),
        "requirements": manifest.get("requirements"),
    }
    value = {
        "schema": SCHEMA,
        "selection_id": entry["id"],
        "semantic_definition": definition,
        "implementation": implementation,
    }
    value["composition_id"] = hashlib.sha256(canonical_json(value).encode()).hexdigest()
    return value


def launch_arguments(entry: dict[str, Any]) -> list[str]:
    """Only this closed code-owned table selects a module; originals cannot."""
    from spireagent.workbench.trusted_agents import trusted_agent_support

    adapter = entry["adapter"]
    modules = {
        "stpd-public-m2-decision-adapter": ("stpd.policy.public_m2_cli", ["serve"]),
        "stpd-public-m0-decision-adapter": ("stpd.policy.public_m0_port", []),
        "token-v1": ("stpd.policy.token_port", []),
        "stpd-m2-decision-adapter": ("stpd.policy.memory_port", []),
    }
    if adapter in modules:
        module, prefix = modules[adapter]
        return [
            "-m",
            module,
            *prefix,
            "--config",
            entry["config"],
            "--manifest",
            entry["manifest"],
            "--binding-root",
            entry["binding_root"],
        ]
    return cast(list[str], trusted_agent_support(adapter).arguments(entry))
