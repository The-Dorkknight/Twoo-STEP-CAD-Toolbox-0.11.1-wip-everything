# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""tostep hardpoints part.stl [more.stl ...]  — what a hardpoint STL contains."""
from __future__ import annotations

import argparse

CLI_HELP = "Show what a hardpoint STL contains."


def main(argv):
    ap = argparse.ArgumentParser(prog="tostep hardpoints", description="Show what a hardpoint STL contains.")
    ap.add_argument("input", nargs="+")
    a = ap.parse_args(argv)
    from tostep.core import hstl
    for p in a.input:
        print(hstl.describe(p))
    return 0
