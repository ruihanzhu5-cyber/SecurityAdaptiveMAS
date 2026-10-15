"""Small shared entry points; ARE execution lives only in run_framework."""
import json
from pathlib import Path

from .orchestration.configuration import load_design


def load_configuration(path):
    return load_design(Path(path))


def run(argv, *, design=None):
    from .run_framework import main
    return main(argv, design_override=design)


def read_result(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_new_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
