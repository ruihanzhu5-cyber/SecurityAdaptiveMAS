"""Fetch exact upstream archives for local review; never execute upstream setup."""
import hashlib
import io
import json
from pathlib import Path
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SOURCES = {
    "aorchestra": ("FoundationAgents/AOrchestra", "14a1a2051d6b03c479b706f8f555a60b8419e3b5"),
    "atm": ("sidikbro/jiuwen_atm", "951e9aa8c8421e8b8d59302ee4727cc855f84714"),
}

if __name__ == "__main__":
    for name, (repo, revision) in SOURCES.items():
        target = ROOT / "work/upstream" / name
        target.mkdir(parents=True, exist_ok=True)
        if (target / "archive_metadata.json").exists():
            print(name, "already fetched")
            continue
        url = f"https://codeload.github.com/{repo}/zip/{revision}"
        data = urllib.request.urlopen(url, timeout=60).read()
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for entry in archive.infolist():
                parts = Path(entry.filename).parts[1:]
                if not parts or entry.is_dir():
                    continue
                dest = (target.joinpath(*parts)).resolve()
                if not dest.is_relative_to(target.resolve()):
                    raise ValueError("archive_path_escape")
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(archive.read(entry))
        (target / "archive_metadata.json").write_text(json.dumps({
            "repository":repo, "revision":revision,"url":url,
            "archive_sha256":hashlib.sha256(data).hexdigest()
        },indent=2)+"\n",encoding="utf-8")
        print(name, revision, "fetched")
