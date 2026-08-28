"""Make the package root (containing backend/) importable regardless of how
pytest is invoked (bare `pytest`, `python -m pytest`, from any cwd)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
