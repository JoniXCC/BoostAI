"""Convenience launcher: ``python run.py`` (same as ``python -m boostai``)."""

import sys

from boostai.main import main

if __name__ == "__main__":
    sys.exit(main())
