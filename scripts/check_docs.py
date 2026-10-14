"""Check local Markdown file targets, including tracked and new non-ignored docs."""
from pathlib import Path
import re
import subprocess
from urllib.parse import unquote


def check(root):
    names = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "--", "*.md"], cwd=root
    ).decode().splitlines()
    files = set(subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=root
    ).decode().splitlines())
    issues, checked = [], 0
    for name in sorted(set(names)):
        source = root / name
        if not source.is_file():
            continue
        fenced = False
        for number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith(("```", "~~~")):
                fenced = not fenced
                continue
            if fenced:
                continue
            for match in re.finditer(r"!?\[[^\]\n]*\]\(([^\n]+?)\)", line):
                raw = match[1]
                dest = raw[1:raw.index(">")] if raw.startswith("<") else raw.split(' "', 1)[0]
                if dest.startswith("#") or re.match(r"^[a-zA-Z][\w+.-]*:", dest):
                    continue
                path = unquote(dest.split("#", 1)[0])
                target = root / path.lstrip("/") if path.startswith("/") else source.parent / path
                checked += 1
                if not target.exists():
                    issues.append(f"{name}:{number}: missing {dest}")
                else:
                    resolved = target.resolve()
                    if not resolved.is_relative_to(root.resolve()):
                        issues.append(f"{name}:{number}: target outside checkout {dest}")
                    else:
                        relative = resolved.relative_to(root.resolve()).as_posix()
                        if relative not in files and not any(f.startswith(relative.rstrip("/") + "/") for f in files):
                            issues.append(f"{name}:{number}: ignored/untracked target not available on GitHub {dest}")
    return checked, issues


if __name__ == "__main__":
    count, failures = check(Path(__file__).resolve().parents[1])
    print(f"Checked {count} local file/image targets; {len(failures)} missing. Heading anchors not checked.")
    for failure in failures:
        print(failure)
    raise SystemExit(bool(failures))
