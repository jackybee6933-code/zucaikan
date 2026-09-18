from __future__ import annotations

import sys
from pathlib import Path

from football_analysis.main import main


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    raise SystemExit(main())
