"""Fresh CPU2/inter1 test child, using the public production Agent constructor."""

from __future__ import annotations

import torch

# Called only as a fresh process, never during an already-running Pytest suite.
torch.set_num_threads(2)
torch.set_num_interop_threads(1)
from stpd.policy.native_agent import main

raise SystemExit(main())
