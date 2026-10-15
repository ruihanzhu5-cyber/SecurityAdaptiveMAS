"""Forward directly to the single existing ARE runner."""
from ..api import run


def main(argv=None):
    return run(argv)
