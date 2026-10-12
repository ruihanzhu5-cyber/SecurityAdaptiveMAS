"""Recreate the fixed five official JSON scenarios from pinned Gaia2 Parquet files."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from .config import ROOT


def restore(data_dir: Path, output_root: Path = ROOT) -> list[str]:
    import pyarrow.parquet as pq

    source = json.loads((ROOT / "configs" / "source_versions.json").read_text(encoding="utf-8"))
    manifest = json.loads((ROOT / "configs" / "five_manifest.json").read_text(encoding="utf-8"))
    categories = {row["category"] for row in manifest}
    tables = {}
    for category in categories:
        path = data_dir / f"{category}.parquet"
        expected = source["files"][path.name]
        raw = path.read_bytes()
        if len(raw) != expected["bytes"] or hashlib.sha256(raw).hexdigest() != expected["sha256"]:
            raise ValueError(f"Pinned Gaia2 Parquet checksum mismatch: {path}")
        tables[category] = pq.read_table(path, columns=["id", "scenario_id", "data"])

    restored = []
    for row in manifest:
        category = row["category"]
        selected = tables[category].slice(row["row_index_zero_based"], 1).to_pylist()[0]
        if selected["id"] != row["dataset_id"] or selected["scenario_id"] != row["scenario_id"]:
            raise ValueError(f"Pinned Gaia2 row identity mismatch: {row['scenario_id']}")
        raw = selected["data"].encode("utf-8")
        if hashlib.sha256(raw).hexdigest() != row["sha256"]:
            raise ValueError(f"Pinned Gaia2 scenario checksum mismatch: {row['scenario_id']}")
        target = output_root / row["path"]
        single = output_root / row["single_scenario_dir"] / category / "validation" / target.name
        for destination in (target, single):
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw)
        restored.append(row["scenario_id"])

    local_manifest = output_root / "work" / "selected" / "manifest.json"
    local_manifest.parent.mkdir(parents=True, exist_ok=True)
    local_manifest.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return restored


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "work" / "data")
    parser.add_argument("--output-root", type=Path, default=ROOT)
    args = parser.parse_args()
    print(json.dumps(restore(args.data_dir, args.output_root), ensure_ascii=False))


if __name__ == "__main__":
    main()
