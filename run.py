#!/usr/bin/env python3
"""Development launcher -- runs the app straight from source.

The registry commands written by shell_integration point at this file when the
app has not been frozen, so file associations can be tested without building.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from torrentapp.__main__ import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
