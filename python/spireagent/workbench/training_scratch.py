"""Read-only, bounded scratch accounting for one fenced training operation.

Retained attempts remain charged until their owning files are removed explicitly.
This monitor is a detection threshold, not an OS/filesystem hard allocation quota.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any, cast

from spireagent.json_boundary import BoundaryError, digest
from spireagent.workbench.trusted_recipes import MAX_TOTAL_ATTEMPTS

MAX_SCRATCH_ENTRIES = 4096


def scratch_failure_snapshot(value: object) -> dict[str, Any]:
    """Project only scan-time enums and counters; never paths or OS messages."""
    if type(value) is not dict:
        return {}
    enums = {
        "scan_phase": {"attempts", "attempt_name", "lstat", "attributes", "scandir_open",
                       "scandir_iterate", "entry_type"},
        "reason": {"attempt_limit", "duplicate_attempt", "current_name", "entry_limit",
                   "link_or_reparse", "entry_type", "os_error"},
        "attempt_scope": {"current", "prior"},
        "entry_scope": {"root", "descendant"},
        "file_type": {"directory", "regular", "symlink", "other"},
    }
    result: dict[str, Any] = {
        key: item if type(item) is str and item in allowed else "unknown"
        for key, allowed in enums.items() for item in (value.get(key),)
    }
    result["active_attempt"] = (value.get("active_attempt")
                                if type(value.get("active_attempt")) is bool else None)
    for key in ("entries", "queued"):
        item = value.get(key)
        result[key] = item if type(item) is int and item >= 0 else None
    return result


def retained_scratch_bytes(
    path: Path, operation: dict[str, Any], *, active_attempt: bool = False
) -> int:
    """Count only exact operation attempt paths; never follow links or clean up.

    Caller retains the operation owner lock. Recovery also proves prior child
    ownership released before using this inventory to admit another attempt.
    A live child may remove temporary descendants during enumeration. Only that
    current attempt's descendants may disappear; attempt roots and retained
    attempts remain strict. This is a live size sample, not an atomic inventory.
    """
    phase, attempt_scope, entry_scope, file_type = "attempts", "unknown", "unknown", "unknown"
    entries = 0
    pending: list[Path] = []

    def invalid(reason: str) -> BoundaryError:
        error = BoundaryError("local_training", "scratch_inventory_invalid")
        # Preserve the original exception class/code and chained OS cause. These
        # facts describe the failing scan, not a later terminal re-enumeration.
        cast(Any, error).scratch_inventory_diagnostic = {
            "scan_phase": phase, "reason": reason, "active_attempt": active_attempt,
            "attempt_scope": attempt_scope, "entry_scope": entry_scope,
            "file_type": file_type, "entries": entries, "queued": len(pending),
        }
        return error

    attempts = [item["attempt_id"] for item in operation["attempts"]]
    attempts.append(operation["attempt_id"])
    if len(attempts) > MAX_TOTAL_ATTEMPTS or len(set(attempts)) != len(attempts):
        raise invalid("attempt_limit" if len(attempts) > MAX_TOTAL_ATTEMPTS
                      else "duplicate_attempt")
    roots = []
    for identity in attempts:
        digest(identity, "local_training.scratch_attempt", length=32)
        name = "local-training-"+identity+".scratch"
        phase, attempt_scope, entry_scope = "attempt_name", "current", "root"
        if identity == operation["attempt_id"] and operation.get("scratch_name") != name:
            raise invalid("current_name")
        roots.append(path.parent/name)
    active_root = roots[-1]
    size, entries = 0, 0
    pending = list(roots)
    try:
        while pending:
            item = pending.pop()
            attempt_scope = ("current" if item == active_root or active_root in item.parents
                             else "prior")
            entry_scope = "root" if item in roots else "descendant"
            phase, file_type = "lstat", "unknown"
            entries += 1
            if entries > MAX_SCRATCH_ENTRIES:
                raise invalid("entry_limit")
            try:
                info = item.lstat()
            except FileNotFoundError:
                if active_attempt and active_root in item.parents:
                    continue
                raise
            phase = "attributes"
            file_type = ("symlink" if stat.S_ISLNK(info.st_mode) else
                         "directory" if stat.S_ISDIR(info.st_mode) else
                         "regular" if stat.S_ISREG(info.st_mode) else "other")
            if (stat.S_ISLNK(info.st_mode)
                    or getattr(info, "st_file_attributes", 0) &
                    getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)):
                raise invalid("link_or_reparse")
            if stat.S_ISDIR(info.st_mode):
                try:
                    phase = "scandir_open"
                    with os.scandir(item) as children:
                        phase = "scandir_iterate"
                        for child in children:
                            # Bound discovery even when queued entries later disappear.
                            if entries+len(pending) >= MAX_SCRATCH_ENTRIES:
                                raise invalid("entry_limit")
                            pending.append(Path(child.path))
                except FileNotFoundError:
                    if not (active_attempt and active_root in item.parents):
                        raise
            elif stat.S_ISREG(info.st_mode) and item not in roots:
                size += info.st_size
            else:
                phase = "entry_type"
                raise invalid("entry_type")
    except OSError as error:
        raise invalid("os_error") from error
    return size
