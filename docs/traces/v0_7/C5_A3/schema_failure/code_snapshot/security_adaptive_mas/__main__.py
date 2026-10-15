"""The module command uses the same CLI as the console script."""
from .cli.main import main

if __name__ == "__main__":
    raise SystemExit(main())
