#!/usr/bin/env python3
"""SLA-breach responder - the capstone reference solution, Python. Local mode CLI (standard library only).

    python3 capstone.py --help
"""
import sys

from responder.cli import main

if __name__ == "__main__":
    sys.exit(main())
