#!/usr/bin/env python3
"""Re-freeze a Managed evaluation execution template on the current source, with a record.

The Managed executor refuses an execution whose `agent_adapter` is not this source's
`stpd.p1.export.adapter_identity()` (`frozen_agent_implementation_changed`). That check
stays exact. When the adapter code closure changed, the template is re-frozen here, the
same rule as `release_swap.build_execution`: only `agent_adapter` and `producer` are
recomputed and every other key is copied unchanged (refused otherwise), so a baseline
round on the same Host and Runtime pins stays pairable. The pairing is not silent:
fast_eval pairs a baseline round under another adapter only through this record, and
writes both fingerprints into summary.json, eval-record.json and the report.

    eval_template.py refreeze --template OLD.json --output NEW.json \
        --previous-revision REV --reason TEXT
    eval_template.py find-revision --template OLD.json [--search-from REV]

Run it with the evaluation interpreter from the clean checkout the evaluation will run
(`PYTHONPATH=<checkout>/python:<checkout>/components/evidence`), like fast_eval.py.
Refused: an existing NEW.json (never overwritten); a template that is itself a re-freeze
(re-freeze the original, so one record links the baseline's adapter to this one); a REV
(the commit OLD was frozen on, e.g. its `producer.source_revision`) that does not
reproduce OLD's pinned adapter; a checkout whose HEAD files are not the source the
interpreter imports (dirty tree, or another checkout on PYTHONPATH). NEW.json carries
`refrozen_from` (OLD's sha256, the record's file name, the old adapter, the reason) so it
cannot pass for an original freeze; beside it `NEW.json.refreeze.json` records both
templates' sha256, the old and new adapter and producer, the unchanged keys, the reason,
and the closure files that differ from REV to this source.

Which REV: OLD's own `producer.source_revision` is the HEAD of the checkout that froze it,
which need not be a commit whose files compute the pinned adapter (#598: the RL5-A40
template named f9fa3367, whose tree lacks a closure file and is on neither side of
411106ad, which reproduced the pin). `find-revision` walks the first-parent ancestors of
--search-from (default HEAD: the checkout doing the re-freeze), newest first, and prints
the newest commit whose adapter closure files compute OLD's pinned `code_sha256`, with how
many commits and distinct closure states it checked (about 1 s on the integration branch);
none found exits 2. Pass that commit as --previous-revision. The refreeze check itself
stays exact.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

FRIDAY = Path(__file__).resolve().parent
ROOT = FRIDAY.parents[1]
SCHEMA = "spireagent/evaluation-template-refreeze-1"
# The only keys a re-freeze writes; every other key must come through unchanged.
RECOMPUTED = ("agent_adapter", "producer", "refrozen_from")
# The files `stpd.p1.export._adapter_identity` hashes, keyed as it keys them. A test
# rebuilds the identity from this list and compares it with export.adapter_identity().
_P1 = ("agent.py", "agent_driver.py", "local_inference.py", "local_mu.py", "export.py",
       "input.py", "model.py", "input_encoding.py", "frame_compiler.py", "tensor_input.py",
       "text_cache.py", "data.py", "execution.py", "spec.py", "input_order.py", "sampling.py",
       "vocabulary.py", "public_vocabulary_v1.json", "input_identity.py",
       "inference_service.py")
ADAPTER_CLOSURE = {
    **{name: f"python/stpd/p1/{name}" for name in _P1},
    "checkpoint_codec.py": "python/stpd/workers/checkpoint_codec.py",
    "rl/heads.py": "python/stpd/rl/heads.py",
    "value_head.py": "python/stpd/p1/value_head.py",
    **{f"spireagent/{name}": f"python/spireagent/{name}"
       for name in ("policy_files.py", "json_boundary.py", "encoding.py")},
}


def closure_code_sha256(read: Any) -> str:
    """export._adapter_identity's code_sha256 from `read(repo_path) -> bytes`."""
    closure = {key: hashlib.sha256(read(path)).hexdigest()
               for key, path in ADAPTER_CLOSURE.items()}
    return hashlib.sha256(json.dumps(closure, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def git(*args: str) -> bytes:
    return subprocess.run(["git", "-C", str(ROOT), *args], check=True,
                          capture_output=True).stdout


def at_revision(revision: str) -> dict[str, Any]:
    """The old template's commit: its adapter code_sha256 and the closure files changed since."""
    sha = git("rev-parse", "--verify", revision + "^{commit}").decode().strip()
    changed = git("diff", "--name-only", sha, "HEAD", "--",
                  *ADAPTER_CLOSURE.values()).decode().split()
    return {"revision": sha,
            "code_sha256": closure_code_sha256(lambda path: git("show", f"{sha}:{path}")),
            "closure_files_changed_since": changed}


def find_revision(code_sha256: str, search_from: str = "HEAD",
                  run: Any = git) -> dict[str, Any]:
    """The newest first-parent ancestor of `search_from` (itself included) whose closure
    files compute `code_sha256`. Commits with the same closure blobs share one hash, so
    only distinct closure states are hashed; a state missing a closure file never matches."""
    paths = list(ADAPTER_CLOSURE.values())
    commits = run("rev-list", "--first-parent", search_from).decode().split()
    seen: dict[bytes, str | None] = {}
    for checked, commit in enumerate(commits, 1):
        blobs = run("ls-tree", "-r", commit, "--", *paths)
        if blobs not in seen:
            present = {line.split("\t", 1)[1] for line in blobs.decode().splitlines()}
            seen[blobs] = (closure_code_sha256(lambda path, c=commit: run(
                "show", f"{c}:{path}")) if present == set(paths) else None)
        if seen[blobs] == code_sha256:
            return {"revision": commit, "code_sha256": code_sha256,
                    "commits_checked": checked, "closure_states_hashed": len(seen)}
    raise ValueError(f"no_first_parent_ancestor_reproduces_the_pin: {code_sha256} in "
                     f"{len(commits)} commits from {search_from} "
                     f"({len(seen)} closure states)")


def current_identities() -> dict[str, Any]:
    from spireagent.source import source_identity
    from spireagent.workbench import managed_evaluation
    from stpd.p1 import export

    return {"agent_adapter": export.adapter_identity(),
            "producer": source_identity(managed_evaluation.ROOT).to_dict()}


def refreeze(template: Path, output: Path, *, previous_revision: str, reason: str,
             identities: dict[str, Any] | None = None,
             revision: Any = at_revision) -> dict[str, Any]:
    from release_swap import build_execution

    if not reason.strip():
        raise ValueError("refreeze_reason_required")
    record_path = Path(str(output) + ".refreeze.json")
    if output.exists() or record_path.exists():
        raise ValueError("refreeze_output_exists_never_overwritten")
    template_raw = template.read_bytes()
    old = json.loads(template_raw)
    if old.get("schema") != "spireagent/managed-evaluation-execution-v1":
        raise ValueError("managed_evaluation_execution_template_required")
    if "refrozen_from" in old:
        raise ValueError("refreeze_from_an_original_template_required")
    new_ids = identities or current_identities()
    if old.get("agent_adapter") == new_ids["agent_adapter"]:
        raise ValueError("template_adapter_already_current")
    old_code = (old.get("agent_adapter") or {}).get("code_sha256")
    previous = revision(previous_revision)
    if previous["code_sha256"] != old_code:
        raise ValueError(f"previous_revision_does_not_reproduce_the_old_pin: "
                         f"{previous['revision']} computes {previous['code_sha256']}, "
                         f"the template pins {old_code} (eval_template.py find-revision "
                         "names the newest commit that does)")
    head = revision("HEAD")
    if head["code_sha256"] != new_ids["agent_adapter"]["code_sha256"]:
        raise ValueError(f"checkout_head_is_not_the_imported_source: HEAD files compute "
                         f"{head['code_sha256']}, the imported adapter is "
                         f"{new_ids['agent_adapter']['code_sha256']} (dirty tree or "
                         "another checkout on PYTHONPATH)")
    new = {**build_execution(old, new_ids), "refrozen_from": {
        "template_sha256": hashlib.sha256(template_raw).hexdigest(),
        "record": record_path.name, "agent_adapter_code_sha256": old_code,
        "reason": reason}}
    changed = sorted(k for k in old.keys() | new.keys()
                     if k not in RECOMPUTED and old.get(k) != new.get(k))
    if changed:
        raise ValueError("refreeze_changed_other_keys: " + ", ".join(changed))
    raw = (json.dumps(new, indent=1, sort_keys=True) + "\n").encode()
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "xb") as stream:
        stream.write(raw)
    output.chmod(0o600)
    record = {
        "schema": SCHEMA, "written_at": time.time(), "reason": reason,
        "template_in": {"path": str(template),
                        "sha256": hashlib.sha256(template_raw).hexdigest()},
        "template_out": {"path": str(output), "sha256": hashlib.sha256(raw).hexdigest()},
        "agent_adapter": {"old": old.get("agent_adapter"), "new": new_ids["agent_adapter"]},
        "producer": {"old": old.get("producer"), "new": new_ids["producer"]},
        "unchanged": sorted(k for k in old if k not in RECOMPUTED),
        "previous": {**previous, "reproduces_old_pin": True},
        "head": head["revision"],
        "rule": "release_swap.build_execution: only agent_adapter and producer recomputed",
    }
    with open(record_path, "x", encoding="utf-8") as stream:
        json.dump(record, stream, indent=1, sort_keys=True)
        stream.write("\n")
    record_path.chmod(0o600)
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("refreeze")
    command.add_argument("--template", type=Path, required=True)
    command.add_argument("--output", type=Path, required=True)
    command.add_argument("--previous-revision", required=True,
                         help="commit the old template was frozen on (git, in this checkout)")
    command.add_argument("--reason", required=True,
                         help="why play is unchanged, e.g. the PR or review that showed it")
    finder = commands.add_parser("find-revision", help="the --previous-revision to pass")
    finder.add_argument("--template", type=Path, required=True)
    finder.add_argument("--search-from", default="HEAD",
                        help="walk this commit's first-parent ancestors (default HEAD)")
    args = parser.parse_args()
    sys.path.insert(0, str(FRIDAY))
    if args.command == "find-revision":
        try:
            pinned = str((json.loads(args.template.read_bytes()).get("agent_adapter")
                          or {}).get("code_sha256") or "")
            if len(pinned) != 64:
                raise ValueError("template_has_no_agent_adapter_code_sha256")
            found = find_revision(pinned, args.search_from)
        except (ValueError, OSError, subprocess.CalledProcessError) as error:
            print(f"eval_template refused: {type(error).__name__}: {error}", file=sys.stderr)
            raise SystemExit(2) from None
        print(json.dumps(found, sort_keys=True))
        return
    try:
        record = refreeze(args.template.resolve(), args.output.resolve(),
                          previous_revision=args.previous_revision, reason=args.reason)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f"eval_template refused: {type(error).__name__}: {error}", file=sys.stderr)
        raise SystemExit(2) from None
    print(json.dumps({k: record[k] for k in ("template_out", "agent_adapter", "previous")},
                     sort_keys=True))


if __name__ == "__main__":
    main()
