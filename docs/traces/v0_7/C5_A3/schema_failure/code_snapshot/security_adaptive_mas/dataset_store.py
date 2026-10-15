"""Pinned Gaia2 release download and read-only scenario selection."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess


from .config import ROOT
VERSIONS = ROOT / "configs" / "source_versions.json"


def source_versions(path: Path = VERSIONS) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def verify_file(path: Path, expected: dict) -> None:
    if path.stat().st_size != expected["bytes"]:
        raise ValueError(f"Gaia2 file size mismatch: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != expected["sha256"]:
        raise ValueError(f"Gaia2 checksum mismatch: {path}")


def fetch_release(data_dir: Path = ROOT / "work" / "data",
                  filesystem_root: Path = ROOT / "work" / "gaia2_filesystem") -> None:
    source = source_versions()
    data_dir.mkdir(parents=True, exist_ok=True)
    assets = dict(source["files"])
    archive = source["filesystem_archive"]
    assets[archive["name"]] = archive
    for name, expected in assets.items():
        target = data_dir / name
        try:
            verify_file(target, expected)
        except (FileNotFoundError, ValueError):
            subprocess.run([
                "gh", "release", "download", source["github_release_tag"],
                "--repo", source["github_repository"], "--pattern", name,
                "--dir", str(data_dir), "--clobber",
            ], check=True)
            verify_file(target, expected)
    if not (filesystem_root / "demo_filesystem").is_dir():
        filesystem_root.mkdir(parents=True, exist_ok=True)
        shutil.unpack_archive(str(data_dir / archive["name"]), str(filesystem_root))


def category_path(category: str, data_dir: Path, source: dict) -> Path:
    name = f"{category}.parquet"
    if name not in source["files"]:
        raise ValueError(f"Unknown Gaia2 category: {category}")
    path = data_dir / name
    verify_file(path, source["files"][name])
    return path


def list_scenarios(category: str, data_dir: Path = ROOT / "work" / "data") -> list[str]:
    import pyarrow.parquet as pq

    path = category_path(category, data_dir, source_versions())
    return pq.read_table(path, columns=["scenario_id"])["scenario_id"].to_pylist()


def export_scenario(category: str, scenario_id: str,
                    data_dir: Path = ROOT / "work" / "data",
                    output_dir: Path = ROOT / "work" / "full_scenarios") -> tuple[Path, str, int]:
    import pyarrow.parquet as pq

    path = category_path(category, data_dir, source_versions())
    row_index = 0
    for batch in pq.ParquetFile(path).iter_batches(batch_size=4, columns=["scenario_id", "data"]):
        for row in batch.to_pylist():
            if row["scenario_id"] == scenario_id:
                raw = row["data"].encode("utf-8")
                target = output_dir / category / f"{scenario_id}.json"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw)
                return target, hashlib.sha256(raw).hexdigest(), row_index
            row_index += 1
    raise ValueError(f"Scenario not found in Gaia2 {category}: {scenario_id}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("fetch", help="Download and verify all release assets")
    listing = commands.add_parser("list", help="List IDs in one pinned category")
    listing.add_argument("--category", required=True)
    listing.add_argument("--limit", type=int, default=20)
    export = commands.add_parser("export", help="Export an unchanged official scenario JSON")
    export.add_argument("--category", required=True)
    export.add_argument("--scenario", required=True)
    args = parser.parse_args()
    if args.command == "fetch":
        fetch_release()
        print("Verified all Gaia2 release assets and filesystem")
    elif args.command == "list":
        ids = list_scenarios(args.category)
        print(json.dumps({"category": args.category, "total": len(ids),
                          "scenario_ids": ids[:args.limit]}, ensure_ascii=False, indent=2))
    else:
        path, digest, index = export_scenario(args.category, args.scenario)
        print(json.dumps({"category": args.category, "scenario_id": args.scenario,
                          "row_index_zero_based": index, "path": str(path),
                          "sha256": digest}, indent=2))


if __name__ == "__main__":
    main()
