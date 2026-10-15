"""One development run through the same CLI; pass --dry-run for no model calls."""
import sys
from security_adaptive_mas.cli.main import main

if __name__ == "__main__":
    raise SystemExit(main(["run", *sys.argv[1:]]))
