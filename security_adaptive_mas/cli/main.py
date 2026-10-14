"""Unified CLI. Commands execute in this process, without a second runner."""
import argparse
import sys


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(prog="security-mas", description="ARE/Gaia2 research harness")
    parser.add_argument("command", nargs="?", choices=("doctor", "run", "experiment", "report"))
    if not argv or argv[0] in {"-h", "--help"}:
        parser.print_help()
        return 0
    args = parser.parse_args(argv[:1])
    if args.command == "run":
        from .run import main as command
    elif args.command == "doctor":
        from .doctor import main as command
    elif args.command == "experiment":
        from .experiment import main as command
    else:
        from .report import main as command
    command(argv[1:])
    return 0
