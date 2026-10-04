"""Run prepared PublicM2 experiments with explicit source and operator bindings."""

import sys
from pathlib import Path

# -I deliberately omits the script directory. Load only this entry's sibling
# application package; the application later selects the frozen worker root.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from m2_campaign.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
