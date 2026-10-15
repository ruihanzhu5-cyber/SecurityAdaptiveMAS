"""Read-only reporting through the shared CLI."""
import sys
from security_adaptive_mas.cli.main import main

if __name__ == "__main__":
    raise SystemExit(main(["report", *sys.argv[1:]]))
