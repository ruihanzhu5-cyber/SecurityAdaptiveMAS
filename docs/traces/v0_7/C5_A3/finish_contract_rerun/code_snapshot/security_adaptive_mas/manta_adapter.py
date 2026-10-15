"""Load MANTA's small topology kernel without its eager LangGraph package imports."""
from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path


def load_manta_spec(root: Path):
    root = root.resolve()
    if not (root / "MAS" / "self_evolved" / "spec.py").is_file():
        raise FileNotFoundError(f"MANTA spec.py missing under {root}")
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    for name, directory in (
        ("MAS", root / "MAS"),
        ("MAS.self_evolved", root / "MAS" / "self_evolved"),
    ):
        existing = sys.modules.get(name)
        if existing is not None and str(directory) not in getattr(existing, "__path__", []):
            raise RuntimeError(f"Conflicting loaded package: {name}")
        if existing is None:
            module = types.ModuleType(name)
            module.__path__ = [str(directory)]
            module.__package__ = name
            sys.modules[name] = module
    return importlib.import_module("MAS.self_evolved.spec")
