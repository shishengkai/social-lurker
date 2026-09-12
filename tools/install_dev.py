#!/usr/bin/env python3
"""Developer convenience wrapper around the resumable bootstrap."""

import importlib.util
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "social_lurker_bootstrap", Path(__file__).resolve().parents[1] / "install.py"
)
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)

if __name__ == "__main__":
    raise SystemExit(bootstrap.main(["--channel", "preview", "--allow-working-tree", *sys.argv[1:]]))
