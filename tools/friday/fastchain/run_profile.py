#!/usr/bin/env python3
"""One run profile for the fast chain: every knob one line (value, default, why, measured).

The chain launcher (`launch.py`), the job scripts (through `job-env.sh` and
`effective-profile.json` in the run directory) and offline benches read this same
file. A bench reads it with `run_profile.py show --profile P --format env|json`.

Profile file (TOML):

    schema = "spireagent/friday-fastchain-profile-1"
    [knobs]
    source_gb = { value = 36, default = 10, why = "...", measured = "c3c31, c2d-c3s2" }

Every knob in `KNOBS` must be present, typed and documented; unknown knobs are refused.
A site profile (cluster paths, partitions, node names) is a separate file whose real
values stay out of the public repository (`site.example.toml` documents the keys).

    run_profile.py show --profile P [--set name=value ...] [--format json|env]
    run_profile.py check --profile P [--site S]
"""
from __future__ import annotations

import argparse
import json
import math
import shlex
import sys
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

SCHEMA = "spireagent/friday-fastchain-profile-1"
SITE_SCHEMA = "spireagent/friday-fastchain-site-1"
FIELDS = ("value", "default", "why", "measured")


def _int(low: int = 0, high: int | None = None) -> Callable[[Any], str | None]:
    def check(value: Any) -> str | None:
        if type(value) is not int or value < low or (high is not None and value > high):
            return f"int in [{low}, {high if high is not None else 'inf'}]"
        return None
    return check


def _num(low: float = 0.0, high: float | None = None) -> Callable[[Any], str | None]:
    def check(value: Any) -> str | None:
        if (type(value) not in (int, float) or not math.isfinite(value) or value < low
                or (high is not None and value > high)):
            return f"finite number in [{low}, {high if high is not None else 'inf'}]"
        return None
    return check


def _bool(value: Any) -> str | None:
    return None if type(value) is bool else "bool"


def _str(value: Any) -> str | None:
    return None if isinstance(value, str) else "string"


def _choice(*options: str) -> Callable[[Any], str | None]:
    def check(value: Any) -> str | None:
        return None if value in options else f"one of {options}"
    return check


def _strs(value: Any) -> str | None:
    ok = isinstance(value, list) and all(isinstance(item, str) for item in value)
    return None if ok else "list of strings"


def _table_of(check: Callable[[Any], str | None], keys: frozenset[str] | None = None,
              what: str = "values") -> Callable[[Any], str | None]:
    """A table whose every value passes `check` (and, with `keys`, only those keys)."""
    def inner(value: Any) -> str | None:
        if not isinstance(value, dict):
            return f"table of {what}"
        bad = sorted(set(value) - keys) if keys is not None else []
        if bad:
            return f"table with keys from {sorted(keys)} (unknown: {bad})"
        for key, item in value.items():
            if check(item) is not None:
                return f"table of {what} ({key}: {check(item)})"
        return None
    return inner


def _any_of(*checks: Callable[[Any], str | None]) -> Callable[[Any], str | None]:
    def check(value: Any) -> str | None:
        errors = [c(value) for c in checks]
        return None if None in errors else " or ".join(e for e in errors if e)
    return check


def _zero(value: Any) -> str | None:
    return None if type(value) in (int, float) and value == 0 else "0 (unset)"


def _service_kwarg(value: Any) -> str | None:
    """FastInferenceService keyword values are scalars (bool/int/finite float/str)."""
    if type(value) in (bool, str, int):
        return None
    return None if type(value) is float and math.isfinite(value) else "bool/int/finite/str"


# FastInferenceService keywords a profile may pass (the rest are set by roles.py), plus
# `segment_format`, which roles.py hands to the FastSegmentAssembler instead.
SERVICE_KWARGS = frozenset(("segment_format",
    "in_flight", "job_parts", "join_workers", "arena_row_bytes", "device_threads", "bf16",
    "part_rows", "device_rows", "trace", "audit_fraction", "kernels", "exit_grace_seconds"))
# env_driver.mjs memo layers: int = check interval, bool = layer on.
ENV_MEMO_KEYS = frozenset(("projection", "receipt", "reduce", "deck", "hud"))


def _per_cards(check: Callable[[Any], str | None]) -> Callable[[Any], str | None]:
    """A table keyed by card count ("1", "2", ...)."""
    def inner(value: Any) -> str | None:
        if not isinstance(value, dict) or not value:
            return "table keyed by card count"
        for key, item in value.items():
            if not key.isdigit() or int(key) < 1 or check(item) is not None:
                return f"table keyed by card count of {check(item) or 'valid values'}"
        return None
    return inner


def _prep_groups(value: Any) -> str | None:
    need = {"type": str, "nodes": int, "cpus": int, "mem_g": int, "workers": int}
    if not isinstance(value, list):
        return "list of prep host groups"
    for group in value:
        if (not isinstance(group, dict) or set(group) != set(need)
                or any(type(group[key]) is not kind for key, kind in need.items())
                or group["nodes"] < 0 or group["cpus"] < 1 or group["workers"] < 1):
            return f"each group exactly {sorted(need)}"
    return None


def _capacity(value: Any) -> str | None:
    if not isinstance(value, dict):
        return "table card type -> {inference, training} rows/h"
    for item in value.values():
        if (not isinstance(item, dict) or set(item) != {"inference", "training"}
                or any(type(v) is not int or v <= 0 for v in item.values())):
            return "table card type -> {inference, training} rows/h"
    return None


# name -> validator. Grouped by role; the profile file carries the why and the run.
KNOBS: dict[str, Callable[[Any], str | None]] = {
    # learner data plane
    "source_gb": _int(1, 512),
    "source_slots": _int(1),
    "prepared_batches": _int(1, 256),
    "prepared_gib": _num(1, 512),
    "preparation_workers": _int(1, 128),
    "assembly_workers": _int(1, 128),
    "dispatch_workers": _int(1, 64),
    "prepare_slots": _int(1),
    "capacity_rows": _int(1),
    "wal_max_gib": _int(1, 512),
    "lanes": _int(1, 64),
    "sgd_start_seconds": _num(1),
    "checkpoint_seconds": _num(0),
    "shards": _int(0, 64),
    "sgd_threads_single": _int(1, 64),
    "sgd_threads_per_rank": _int(1, 64),
    "sgd_cpus": _int(1, 256),
    "data_parallel_timeout_seconds": _int(10, 3600),
    "levers": _bool,
    "prepared_kernels": _str,
    "prepared_bucket_rows": _int(1),
    "learner_alloc_conf": _str,
    "prefetch_referenced_only": _bool,
    "fast_batched_apply": _bool,
    "fast_verify_workers": _int(0, 64),
    "lock_timing": _bool,
    "persistent_pools": _bool,
    "ahead_while_waiting": _bool,
    "sgd_stage_thread": _bool,
    "fast_verify_arena_slots": _int(1, 4096),
    "fast_verify_slot_bytes": _int(4096),
    "sgd_stage_stream_reuse": _bool,
    "sgd_stage_lean_tree": _bool,
    "sgd_verify_split": _bool,
    "sgd_verify_threads": _int(1, 32),
    "sgd_switch_interval_seconds": _any_of(_zero, _num(1e-5, 0.1)),
    "diagnostics_every": _int(1),
    "shards_adopt_unsharded_checkpoint": _bool,
    "producer_identity": _choice("checkout", "placeholder"),
    # remote prepare
    "remote_prepare": _bool,
    "remote_prepare_connections": _int(1, 1024),
    "remote_prepare_codec": _choice("lz4", "none"),
    "remote_prepare_client_processes": _int(0, 64),
    "remote_prepare_timeout_seconds": _num(1),
    "remote_prepare_wait_seconds": _num(1),
    "prep_groups": _prep_groups,
    "prep_host_inflight_gb": _num(0),
    "prep_scratch_min_free": _bool,
    "prep_start_timeout_seconds": _num(0),
    # inference services
    "services": _int(1, 64),
    "service_kwargs": _table_of(_service_kwarg, SERVICE_KWARGS, "service keywords"),
    "service_window_ms": _num(0),
    "service_compile": _bool,
    "service_prepare_workers": _int(1, 64),
    "service_max_rows": _int(1),
    "service_max_job_rows": _int(1),
    "mu_poll_seconds": _num(0.1),
    "handoff_write_workers": _int(1, 64),
    "drain_seconds": _num(0),
    "env_drain_seconds": _num(0),
    "stall_dump_seconds": _num(1),
    # GPU job resources and guards
    "service_cores": _int(1, 512),
    "rank_cores": _int(1, 512),
    "job_mem_g": _per_cards(_int(1)),
    "mem_guard_gb": _int(1),
    "mem_hard_gb": _int(1),
    "core_cap": _per_cards(_int(1)),
    "wall_seconds": _int(60),
    "smoke_wall_seconds": _int(60),
    # environment nodes
    "env_node_cores": _int(1, 512),
    "env_node_mem_g": _int(1),
    "env_machine_types": _strs,
    "env_slow_types": _strs,
    "env_slow_type_nodes": _int(0),
    "env_core_budget": _per_cards(_int(1)),
    "env_wait_seconds": _int(0),
    "env_min_join_seconds": _int(0),
    "env_procs_fraction": _num(0.05, 2),
    "env_slots": _int(1, 16),  # MAX_SLOTS = 16 in the fast env (V3-shen)
    "env_aggregators": _int(1, 64),
    "env_encoders": _int(1, 64),
    "aggregator_window_ms": _num(0),
    "env_pkg_build": _str,
    "env_candidate_artifact_sha256": _str,
    "env_memo": _table_of(_any_of(_bool, _int(1)), ENV_MEMO_KEYS, "memo layers"),
    "driver_inference_timeout_ms": _int(100),
    "driver_reopen_backoff_start_ms": _int(0),
    "driver_reopen_backoff_cap_ms": _int(0),
    "driver_reopen_backoff_jitter": _num(0, 1),
    "driver_cold_start": _int(0),
    "driver_warm_seconds": _int(0),
    "driver_steady_seconds": _int(1),
    "driver_status_seconds": _int(1),
    "driver_single_threaded_gc": _bool,
    "driver_reduce_light_interval": _int(0),
    "native_nice": _int(0, 19),
    # layout and readout
    "default_layouts": _per_cards(_str),
    "targets_rows_h": _per_cards(_int(1)),
    "ready_rows_h": _per_cards(_int(1)),
    "card_capacity_rows_h": _capacity,
    "allow_switch_off": _strs,
}

# The speed switches the lead set as defaults (PLAN-TABLE §9): a run with one of
# them off is refused unless the profile names it in `allow_switch_off`.
REQUIRED_ON = ("levers", "remote_prepare")
REQUIRED_VALUES = {"remote_prepare_codec": "lz4"}

SITE_KEYS: dict[str, Callable[[Any], str | None]] = {
    "work_root": _str, "src_dir": _str, "python": _str, "module_load": _str,
    "lib_path": _str, "lz4_path": _str, "node_release": _str, "node_bin": _str,
    "dotnet_dir": _str, "inference_auth_file": _str, "prepare_auth_file": _str,
    "gate_dir": _str, "warmup_file": _str, "start_package": _str, "peer_dir": _str,
    "inductor_cache": _str, "seeds_file": _str, "input_spec_file": _str,
    "initial_progress_file": _str, "env_pkg": _str, "env_candidate": _str,
    "gpu_local_tmp": _str, "env_tmp_root": _str, "shared_fs_prefixes": _strs,
    "env_partition": _str, "prep_partition": _str, "excluded_prep_hosts": _strs,
    "prep_scratch": _table_of(_str, what="paths"), "run_name": _str,
    "gpu_types": _table_of(_table_of(lambda v: None), what="gpu types"),
    "partition_max_minutes": _table_of(_int(1), what="minutes (int)"),
}
GPU_TYPE_KEYS = {"gres": str, "partition": str, "nodes": list}


class ProfileError(ValueError):
    """A profile that is incomplete, mistyped or carries unknown knobs."""


def _parse_value(text: str) -> Any:
    try:
        return tomllib.loads(f"v = {text}")["v"]
    except tomllib.TOMLDecodeError:
        return text  # a bare word is a string


def load_profile(path: Path, overrides: list[str] | None = None) -> dict[str, Any]:
    """Validated profile: {name: {value, default, why, measured[, overridden_from]}}."""
    data = tomllib.loads(Path(path).read_text())
    if data.get("schema") != SCHEMA:
        raise ProfileError(f"profile schema must be {SCHEMA}")
    knobs = data.get("knobs")
    if not isinstance(knobs, dict):
        raise ProfileError("profile needs a [knobs] table")
    problems = []
    for name in sorted(set(knobs) - set(KNOBS)):
        problems.append(f"{name}: unknown knob")
    for name in sorted(set(KNOBS) - set(knobs)):
        problems.append(f"{name}: missing")
    result: dict[str, Any] = {}
    for name, knob in knobs.items():
        if name not in KNOBS:
            continue
        if not isinstance(knob, dict) or set(knob) != set(FIELDS):
            problems.append(f"{name}: needs exactly {FIELDS}")
            continue
        for field in ("why", "measured"):
            if not isinstance(knob[field], str) or not knob[field].strip():
                problems.append(f"{name}: {field} must be a non-empty string")
        for field in ("value", "default"):
            error = KNOBS[name](knob[field])
            if error:
                problems.append(f"{name}.{field}: expected {error}, got {knob[field]!r}")
        result[name] = dict(knob)
    for item in overrides or []:
        name, sep, text = item.partition("=")
        if not sep or name not in KNOBS:
            problems.append(f"--set {item}: unknown knob or missing '='")
            continue
        value = _parse_value(text)
        error = KNOBS[name](value)
        if error:
            problems.append(f"--set {name}: expected {error}, got {value!r}")
            continue
        if name in result:  # a repeated --set keeps the profile value as the origin
            origin = result[name].get("overridden_from", result[name]["value"])
            result[name] = {**result[name], "overridden_from": origin, "value": value}
    if problems:
        raise ProfileError("profile refused:\n  " + "\n  ".join(problems))
    return result


def values(profile: dict[str, Any]) -> dict[str, Any]:
    return {name: knob["value"] for name, knob in profile.items()}


def load_site(path: Path) -> dict[str, Any]:
    data = tomllib.loads(Path(path).read_text())
    if data.get("schema") != SITE_SCHEMA:
        raise ProfileError(f"site schema must be {SITE_SCHEMA}")
    site = data.get("site")
    if not isinstance(site, dict):
        raise ProfileError("site profile needs a [site] table")
    problems = [f"{key}: missing" for key in sorted(set(SITE_KEYS) - set(site))]
    problems += [f"{key}: unknown site key" for key in sorted(set(site) - set(SITE_KEYS))]
    for key, check in SITE_KEYS.items():
        if key in site and check(site[key]):
            problems.append(f"{key}: expected {check(site[key])}")
    for name, spec in (site.get("gpu_types") or {}).items():
        if (not isinstance(spec, dict) or set(spec) != set(GPU_TYPE_KEYS)
                or any(not isinstance(spec[k], t) for k, t in GPU_TYPE_KEYS.items())):
            problems.append(f"gpu_types.{name}: needs exactly {sorted(GPU_TYPE_KEYS)}")
    if problems:
        raise ProfileError("site profile refused:\n  " + "\n  ".join(problems))
    return site


def env_lines(knobs: dict[str, Any]) -> list[str]:
    """`FC_<NAME>=value` shell lines (tables and lists as JSON)."""
    out = []
    for name, value in sorted(knobs.items()):
        if isinstance(value, bool):
            text = "1" if value else "0"
        elif isinstance(value, (dict, list)):
            text = json.dumps(value, separators=(",", ":"), sort_keys=True)
        else:
            text = str(value)
        out.append(f"export FC_{name.upper()}={shlex.quote(text)}")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("command", choices=("show", "check"))
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--site", type=Path)
    parser.add_argument("--set", action="append", default=[], dest="overrides")
    parser.add_argument("--format", choices=("json", "env"), default="json")
    args = parser.parse_args(argv)
    try:
        profile = load_profile(args.profile, args.overrides)
        if args.site:
            load_site(args.site)
    except ProfileError as error:
        print(error, file=sys.stderr)
        return 2
    if args.command == "check":
        print(f"profile ok: {len(profile)} knobs")
        return 0
    if args.format == "env":
        print("\n".join(env_lines(values(profile))))
    else:
        print(json.dumps(profile, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
