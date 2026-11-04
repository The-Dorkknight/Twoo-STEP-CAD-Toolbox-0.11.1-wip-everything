# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""
job — the one object a plugin's run() receives, and the one line format a worker prints.

    def run(job):                      # the whole plugin contract, code side
        job.log("starting")
        job.progress(0.5)              # 0..1
        ... write job.out ...
        return report                  # anything with .summary() -> str; .ok is honoured when present

A worker process runs exactly one job (so the app can cancel it by killing the process). It prints one
JSON object per line, each prefixed with "@@":
    {"p": 0.42}  progress     {"l": "text"}  log line     {"s": "summary"}  result summary
    {"e": "message"}  error   {"d": {...}}   the result as plain numbers (for the diagnostic code)
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from typing import Callable

UNITS = {"mm": 1.0, "inches": 25.4, "in": 25.4, "cm": 10.0, "m": 1000.0}


def scale_for(units):
    """Millimetres per unit of the input file. Unknown names mean millimetres."""
    return UNITS.get(str(units or "mm"), 1.0)


@dataclass
class Job:
    plugin: str
    src: str
    out: str
    params: dict = field(default_factory=dict)
    kind: str = ""
    scale: float = 1.0
    log: Callable = lambda s: None
    progress: Callable = lambda f: None


class UserError(Exception):
    """A problem the user can fix (a missing program, a bad file): shown as a sentence, no traceback."""


def emit(**kw):
    """One protocol line on stdout (see the module docstring)."""
    sys.stdout.write("@@" + json.dumps(kw) + "\n")
    sys.stdout.flush()
