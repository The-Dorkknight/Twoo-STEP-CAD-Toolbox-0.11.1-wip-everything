# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""jetwash's command line:  tostep wash old.step [clean.step] --threshold 0.05 --coarseness 2"""
from __future__ import annotations

import os

from tostep.core import cliutil

CLI_HELP = "Jetwash: clean artifacts out of a STEP (or STL) file."


def add_arguments(ap):
    ap.add_argument("--threshold", type=float, default=0.05, help="mm; junk thinner/smaller than this is removed")
    ap.add_argument("--coarseness", type=int, choices=[1, 2, 3], default=2,
                    help="1 gentle, 2 normal, 3 aggressive (also small holes/fillets/chamfers)")
    ap.add_argument("--units", choices=["mm", "in", "inches", "cm", "m"], default="mm",
                    help="units of an STL input")
    cliutil.add_performance_args(ap)


def params_from_args(a, output):
    return dict(threshold=a.threshold, coarseness=str(a.coarseness),
                units="inches" if a.units == "in" else a.units)


def check(rep):
    if not getattr(rep, "ok", True):
        raise RuntimeError("cleaning failed")
