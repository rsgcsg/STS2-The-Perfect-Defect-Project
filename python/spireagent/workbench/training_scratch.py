"""Read-only, bounded scratch accounting for one fenced training operation.

Retained attempts remain charged until their owning files are removed explicitly.
This monitor is a detection threshold, not an OS/filesystem hard allocation quota.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

from spireagent.json_boundary import BoundaryError, digest
from spireagent.workbench.trusted_recipes import MAX_TOTAL_ATTEMPTS

MAX_SCRATCH_ENTRIES = 4096


def retained_scratch_bytes(path: Path, operation: dict[str, Any]) -> int:
    """Count only exact operation attempt paths; never follow links or clean up.

    Caller retains the operation owner lock. Recovery also proves prior child
    ownership released before using this inventory to admit another attempt.
    """
    attempts = [item["attempt_id"] for item in operation["attempts"]]
    attempts.append(operation["attempt_id"])
    if len(attempts) > MAX_TOTAL_ATTEMPTS or len(set(attempts)) != len(attempts):
        raise BoundaryError("local_training", "scratch_inventory_invalid")
    roots = []
    for identity in attempts:
        digest(identity, "local_training.scratch_attempt", length=32)
        name = "local-training-"+identity+".scratch"
        if identity == operation["attempt_id"] and operation.get("scratch_name") != name:
            raise BoundaryError("local_training", "scratch_inventory_invalid")
        roots.append(path.parent/name)
    size, entries = 0, 0
    pending = list(roots)
    try:
        while pending:
            item = pending.pop()
            info = item.lstat()
            entries += 1
            if (entries > MAX_SCRATCH_ENTRIES or stat.S_ISLNK(info.st_mode)
                    or getattr(info, "st_file_attributes", 0) &
                    getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)):
                raise BoundaryError("local_training", "scratch_inventory_invalid")
            if stat.S_ISDIR(info.st_mode):
                with os.scandir(item) as children:
                    for child in children:
                        # Bound discovery as well as processing; a huge directory
                        # must not allocate an unbounded pending inventory.
                        if entries+len(pending) >= MAX_SCRATCH_ENTRIES:
                            raise BoundaryError("local_training", "scratch_inventory_invalid")
                        pending.append(Path(child.path))
            elif stat.S_ISREG(info.st_mode) and item not in roots:
                size += info.st_size
            else:
                raise BoundaryError("local_training", "scratch_inventory_invalid")
    except OSError as error:
        raise BoundaryError("local_training", "scratch_inventory_invalid") from error
    return size
