#!/usr/bin/env python3
"""Source-checkout entry point; also works when iCloud hides editable-install .pth files."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from social_lurker.cli import main

raise SystemExit(main())
