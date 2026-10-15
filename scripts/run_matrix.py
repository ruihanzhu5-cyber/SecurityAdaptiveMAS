"""Forward plan/run parameters to the shared experiment command."""
import sys
from security_adaptive_mas.cli.main import main

if __name__ == "__main__":
    raise SystemExit(main(["experiment", *sys.argv[1:]]))
