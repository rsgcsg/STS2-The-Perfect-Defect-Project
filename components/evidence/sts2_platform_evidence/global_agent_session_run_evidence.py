"""GLOBAL original archive integrity plus trusted shared-code semantic replay.

Archive bytes never select executable code. A trusted application supplies its
installed Runtime directory; its exact compiled digest must match the producer.
This verifies recorded facts, not native coverage, Human origin or research use.
"""
from __future__ import annotations
import hashlib
import json
import re
import subprocess
from collections import OrderedDict
from pathlib import Path
from threading import Lock
from typing import Any, Mapping
from .agent_run_evidence import _canonical_json, _file_entry, _read_checksums, _autonomy_config_limits, _recorded_budget_limits, _verify_autonomy_budget
from .agent_session_run_evidence import AgentSessionRunEvidence, _object, _json, _require, _sha, _digest, _time, _integer

BASE = {"manifest.json", "agent-manifest.json", "adapter-attestation.json", "events.jsonl"}
EXTRA = {"checksums.sha256", "evidence-manifest.json"}
ARTIFACT = re.compile(r"global-([a-f0-9]{64})\.bin(?:\.gz)?\Z")

# A measured 33-floor original needs ~58s after decoded-payload retention is
# bounded. This is a finite verification budget, not a gameplay or validity rule.
GLOBAL_REPLAY_TIMEOUT_SECONDS = 120
_SEMANTIC_REPLAY_CACHE: OrderedDict[tuple[str, str], dict[str, Any]] = OrderedDict()
_SEMANTIC_REPLAY_CACHE_LOCK = Lock()


def _verify_global_budget_facts(events: bytes, requested: Mapping[str, object], version: int) -> None:
    """Bind recorded budget facts after trusted shared semantic replay; no live eligibility claim."""
    if "schema" in requested:
        _require(requested["schema"] == f"sts2.policy-runtime/agent-session-startup-{version}", "global_expected_event_version")
    expected_limits = _autonomy_config_limits(requested["autonomy_budget"], version) if "autonomy_budget" in requested else None
    authorization_limits: tuple[object, ...] | None = None
    for line in events.splitlines():
        event = json.loads(line)
        if "autonomy_budget" in event["payload"] and (version == 2 or expected_limits is not None):
            budget = _verify_autonomy_budget(event["payload"]["autonomy_budget"], version)
            limits = _recorded_budget_limits(budget, version)
            _require(expected_limits is None or expected_limits == limits, "global_expected_budget_drift")
            if version == 2:
                _require(authorization_limits is None or authorization_limits == limits, "global_budget_config_changed")
                authorization_limits = limits
    if version == 2 and expected_limits is not None:
        _require(authorization_limits is not None, "global_expected_budget_absent")


def verify_global_session(directory: Path, expected: Mapping[str, object] | None) -> AgentSessionRunEvidence:
    entries = list(directory.iterdir())
    _require(all(p.is_file() and not p.is_symlink() for p in entries), "global_archive_ordinary_files")
    names = {p.name for p in entries}
    artifacts = names - BASE - EXTRA
    _require(BASE | EXTRA <= names and len(names) <= 65542
             and all(ARTIFACT.fullmatch(n) for n in artifacts), "global_archive_inventory")
    _require(sum(p.stat().st_size for p in entries) <= 768 * 1024**2
             and all(0 < (directory / n).stat().st_size <= 96 * 1024**2 for n in artifacts), "global_archive_capacity")
    checksums = _read_checksums(directory / "checksums.sha256")
    _require(set(checksums) == names - {"checksums.sha256"}, "global_checksum_inventory")
    for name, value in checksums.items():
        _require(_sha((directory / name).read_bytes()) == value, "global_original_checksum")
    run = _object(_json((directory / "manifest.json").read_bytes(), "manifest.json"), {
        "schema", "run_id", "manifest_id", "agent_manifest_sha256", "agent_id", "agent_version", "agent_artifact_sha256",
        "runtime_version", "runtime_code_sha256", "started_at", "ended_at", "status", "mode", "tainted", "append_only"})
    _require(run["schema"] == "sts2.policy-runtime/agent-session-run-1" and run["status"] in {"completed", "stopped", "tainted"}
             and run["append_only"] is True and type(run["tainted"]) is bool, "global_closed_run_required")
    _time(run["started_at"]); _time(run["ended_at"]); _digest(run["runtime_code_sha256"])
    agent = _json((directory / "agent-manifest.json").read_bytes(), "agent-manifest.json")
    _require(agent.get("input", {}).get("profile") == "p1-global-r1"
             and _sha(_canonical_json(agent).encode()) == run["agent_manifest_sha256"], "global_agent_manifest_binding")
    immutable = _object(_json((directory / "evidence-manifest.json").read_bytes(), "evidence-manifest.json"), {
        "schema", "run_id", "complete", "append_only", "files", "manifest_sha256"})
    files = [_file_entry(directory / n, n) for n in sorted(BASE | artifacts)]
    _require(immutable["schema"] == "sts2.policy-runtime/immutable-evidence-manifest-1" and immutable["run_id"] == run["run_id"]
             and immutable["complete"] is True and immutable["append_only"] is True and immutable["files"] == files
             and immutable["manifest_sha256"] == _sha(_canonical_json({"run_id": run["run_id"], "files": files}).encode()), "global_immutable_inventory")
    # An archive cannot supply executable paths. This is an application-owned installed package pin.
    requested = dict(expected or {})
    trusted = requested.pop("global_runtime_directory", None)
    _require(isinstance(trusted, str) and bool(trusted), "global_trusted_shared_replay_required")
    runtime = Path(trusted)
    _require(runtime.is_absolute() and runtime.is_dir() and runtime.resolve() == runtime
             and all(not p.is_symlink() for p in [runtime, *runtime.parents]), "global_runtime_directory_unsafe")
    modules = sorted(runtime.glob("*.js"))
    _require(modules and all(p.is_file() and not p.is_symlink() for p in modules), "global_runtime_code_required")
    digest = hashlib.sha256()
    for module in modules:
        digest.update(module.name.encode()); digest.update(b"\0"); digest.update(module.read_bytes()); digest.update(b"\0")
    _require(digest.hexdigest() == run["runtime_code_sha256"], "global_trusted_runtime_code_drift")
    _require(requested.get("schema", "sts2.policy-runtime/agent-session-startup-1") in {"sts2.policy-runtime/agent-session-startup-1", "sts2.policy-runtime/agent-session-startup-2"}, "global_expected_namespace")
    aliases = {"agent_manifest_id": "manifest_id"}
    for key in ("run_id", "agent_manifest_id", "agent_id", "agent_version", "agent_manifest_sha256", "agent_artifact_sha256", "runtime_version", "runtime_code_sha256"):
        if key in requested:
            _require(run[aliases.get(key, key)] == requested[key], "global_expected_identity")
    if "adapter" in requested:
        _require(requested["adapter"] == agent["adapter"], "global_expected_adapter")
    # Every call still verifies the complete physical inventory, checksums,
    # immutable seal, trusted installed code and requested context above. The
    # content ID covers ALL files, including the seal/checksum files. Only the
    # pure semantic proof for those exact bytes/code can be reused in-process.
    identity = {"schema": "sts2.evidence/store-directory-1", "files": [_file_entry(directory / n, n) for n in sorted(names)]}
    content_id = _sha(_canonical_json(identity).encode())
    identity_script = "import{pathToFileURL}from'node:url';const m=await import(pathToFileURL(process.argv[1]));console.log(JSON.stringify(typeof m.globalReplaySemanticIdentity==='function'?await m.globalReplaySemanticIdentity():null));"
    try:
        checked = subprocess.run(["node", "--input-type=module", "-e", identity_script,
                                  str(runtime / "global-replay.js")], capture_output=True,
                                 text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        _require(False, "global_shared_replay_identity_unavailable")
    _require(checked.returncode == 0 and len(checked.stdout) <= 65536,
             "global_shared_replay_identity_failed")
    parsed_identity = json.loads(checked.stdout)
    semantic_identity = _object(parsed_identity) if parsed_identity is not None else None
    replay_key = None
    report = None
    # Old trusted packages without this capability still execute their complete
    # original replay. They are never memoized from an unbound dependency set.
    if semantic_identity is not None:
        _require(semantic_identity.get("schema") == "sts2.policy-runtime/global-replay-code-1"
                 and semantic_identity.get("runtime_code_sha256") == run["runtime_code_sha256"]
                 and _object(semantic_identity.get("core")).get("module_digest")
                 == agent["input"]["global"]["core_sha256"], "global_shared_replay_identity")
        dependencies = semantic_identity.get("semantic_dependencies_sha256")
        _digest(dependencies)
        replay_key = (content_id, dependencies)
        with _SEMANTIC_REPLAY_CACHE_LOCK:
            report = _SEMANTIC_REPLAY_CACHE.get(replay_key)
    if report is None:
        script = "import{pathToFileURL}from'node:url';const m=await import(pathToFileURL(process.argv[1]));console.log(JSON.stringify(await m.replayGlobalAgentRun(process.argv[2])));"
        try:
            result = subprocess.run(["node", "--input-type=module", "-e", script, str(runtime / "global-replay.js"), str(directory)],
                                    capture_output=True, text=True, timeout=GLOBAL_REPLAY_TIMEOUT_SECONDS, check=False)
        except (OSError, subprocess.SubprocessError):
            _require(False, "global_shared_replay_unavailable")
        _require(result.returncode == 0 and len(result.stdout) <= 65536, "global_shared_replay_failed")
        report = _object(json.loads(result.stdout))
    _require(report.get("schema") == "sts2.policy-runtime/global-replay-1" and report.get("run_id") == run["run_id"]
             and report.get("verdict") == "verified" and report.get("native_qualification") is False
             and report.get("human_origin") is False and report.get("research_admission") is False, "global_shared_replay_report")
    if replay_key is not None:
        with _SEMANTIC_REPLAY_CACHE_LOCK:
            _SEMANTIC_REPLAY_CACHE[replay_key] = report
            _SEMANTIC_REPLAY_CACHE.move_to_end(replay_key)
            while len(_SEMANTIC_REPLAY_CACHE) > 16:
                _SEMANTIC_REPLAY_CACHE.popitem(last=False)
    events = (directory / "events.jsonl").read_bytes()
    first = json.loads(events.splitlines()[0])
    version = 2 if first.get("schema") == "sts2.policy-runtime/agent-session-event-2" else 1
    _verify_global_budget_facts(events, requested, version)
    _require(events.endswith(b"\n"), "global_journal_truncated")
    count = len(events.splitlines()); _integer(count, positive=True, maximum=65536)
    return AgentSessionRunEvidence(directory, run, agent, immutable, count, content_id)
