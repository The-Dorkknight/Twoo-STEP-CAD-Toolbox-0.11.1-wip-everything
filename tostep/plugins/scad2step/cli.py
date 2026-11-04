# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""tostep scad model.scad [-o out.step] [-D name=value] [--watch]  — OpenSCAD (.scad/.csg) -> solid STEP.

This command takes over the whole command line (it has several inputs, a live link and its own exit
codes), so it defines main(argv) instead of the smaller hooks."""
from __future__ import annotations

import argparse
import os

from tostep.core import cliutil
from tostep.core.job import scale_for

CLI_HELP = "Convert OpenSCAD (.scad/.csg) to solid STEP."


def main(argv):
    ap = argparse.ArgumentParser(prog="tostep scad", description="Convert OpenSCAD (.scad/.csg) to solid STEP.")
    ap.add_argument("input", nargs="+", help=".scad or .csg file(s)")
    ap.add_argument("-o", "--output", help="output .step (single input only; default: next to the input)")
    ap.add_argument("-D", dest="defines", action="append", metavar="NAME=VALUE",
                    help="set a variable, like OpenSCAD's -D (strings need quotes: -D 'txt=\"Hi\"')")
    ap.add_argument("--units", choices=["mm", "in", "inches", "cm", "m"], default="mm",
                    help="what one OpenSCAD unit means (STEP is written in mm)")
    ap.add_argument("--faceted-fn", type=int, default=8,
                    help="circles with an explicit $fn at or below this stay polygons (default 8: hexagon nut "
                         "traps stay hexagons); a higher $fn becomes truly round")
    ap.add_argument("--no-verify", action="store_true", help="skip the comparison with OpenSCAD's render")
    ap.add_argument("--openscad", default="", help="path to the OpenSCAD program (default: find it)")
    ap.add_argument("--watch", action="store_true",
                    help="keep running; re-export every time the file (or a library it uses) is saved")
    ap.add_argument("--pause", action="store_true", help="wait for Enter before closing (for file-manager use)")
    ap.add_argument("--machine", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    from . import scad2step_core as sc
    defines = {}
    for it in a.defines or []:
        if "=" not in it:
            ap.error(f"-D needs name=value, got {it!r}")
        k, v = it.split("=", 1)
        defines[k.strip()] = v.strip()
    prm = sc.ScadParams(defines=defines, faceted_fn=a.faceted_fn, scale=scale_for(a.units),
                        verify=not a.no_verify, openscad=a.openscad)
    if a.openscad:
        sc.save_settings(openscad=a.openscad)
    if a.output and len(a.input) > 1:
        ap.error("-o/--output works with a single input file")
    code = 0
    try:
        if a.watch:
            if len(a.input) != 1:
                ap.error("--watch works with a single input file")
            out = a.output or os.path.splitext(a.input[0])[0] + ".step"
            try:
                sc.watch(a.input[0], out, prm, log=print)
            except KeyboardInterrupt:
                print("\nStopped watching.")
            return 0
        for inp in a.input:
            out = a.output or os.path.splitext(inp)[0] + ".step"
            if len(a.input) > 1:
                print(f"=== {inp}")

            def go(inp=inp, out=out):
                rep = sc.convert(inp, out, prm, log=lambda s: print(s, flush=True),
                                 progress=cliutil.machine_progress(a.machine))
                print(f"\nSaved {out}\n", flush=True)
                if not rep.is_solid:
                    raise RuntimeError("the result is not a closed solid")
                return out
            try:
                if cliutil.run_machine_safe(a.machine, go):
                    code = 1
            except sc.ScadError as e:
                print(f"\nERROR: {e}\n")
                code = 1
            except RuntimeError as e:
                print(f"\nWARNING: {e}\n")
                code = 2
    finally:
        if a.pause:
            cliutil.pause()
    return code
