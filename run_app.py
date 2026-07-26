from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from scpi_automation.app import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())

