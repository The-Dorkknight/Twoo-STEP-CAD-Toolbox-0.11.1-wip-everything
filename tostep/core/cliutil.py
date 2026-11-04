# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""
cliutil — small pieces every plugin's command line shares, so each plugin's cli.py stays short.
"""
from __future__ import annotations

import sys


def add_performance_args(ap):
    ap.add_argument("--profile", choices=["auto", "potato", "moderate", "extreme"], default=None,
                    help="performance profile: threads, priority, GPU, parallel Second pass")
    ap.add_argument("--no-gpu", action="store_true",
                    help="don't use the graphics card even if the GPU pack is installed")


def heights_arg(text):
    """--heights 2,10,30 -> (2.0, 10.0, 30.0). A bad list stops the command with a sentence."""
    try:
        vals = tuple(float(t) for t in text.replace(";", ",").split(",") if t.strip())
    except ValueError:
        sys.exit("--heights needs numbers separated by commas, e.g. --heights 2,10,30")
    return vals or None


def machine_progress(enabled):
    """--machine: progress as '@@PROGRESS n' lines that CAD plug-ins parse."""
    if not enabled:
        return None
    last = [-1]

    def progress(f):
        p = int(max(0.0, min(1.0, float(f))) * 100)
        if p != last[0]:
            last[0] = p
            print(f"@@PROGRESS {p}", flush=True)
    return progress


def run_machine_safe(machine, fn):
    """Run fn() -> output path. With --machine: '@@DONE path' or '@@ERROR message' (and exit code 1)."""
    try:
        out = fn()
        if machine:
            print(f"@@DONE {out}", flush=True)
        return 0
    except Exception as e:  # noqa
        if machine:
            print(f"@@ERROR {e or e.__class__.__name__}", flush=True)
            return 1
        raise


def pause():
    try:
        input("Press Enter to close…")
    except EOFError:
        pass
