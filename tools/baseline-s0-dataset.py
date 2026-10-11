#!/usr/bin/env python3
"""Verify completed S0 teacher ledgers using the owning STPD verifier.

Archival CLI arguments and projected SOURCE_SCHEMA v1 bytes are unchanged.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

# Re-export the existing diagnostic API for archival import callers.
from stpd.fullrun.agent_source_verifier import *
from stpd.fullrun.agent_source_verifier import main

if __name__ == "__main__":
    raise SystemExit(main())
