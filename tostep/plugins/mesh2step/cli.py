# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""
mesh2step's command line:  tostep convert part.stl [out.step] [options]   (or just: tostep part.stl)

The core builds the command from these hooks (docs/PLUGIN_CONTRACT.md, "command line"):
  add_arguments(ap)          this plugin's own options
  defaults_for(argv)         option defaults chosen by an earlier option (--preset), or None
  params_from_args(ns)       the typed-parameter dictionary the plugin's run() takes
  default_output(ns)         where the result goes when the user names no file
  check(report)              raise RuntimeError when the engine says it failed
  after_run(ns, job, report, lines)   extra lines printed when the job is done
"""
from __future__ import annotations

import os

from tostep.core import cliutil

CLI_HELP = "Convert STL/OBJ/PLY meshes to solid STEP."


def add_arguments(ap):
    ap.add_argument("--preset", choices=["draft", "quick", "standard", "fine"], default=None,
                    help="quality preset of the STL → STEP tab: draft (fastest), quick, standard (the defaults), "
                         "fine (slowest); any other option given still overrides it")
    ap.add_argument("--mode", choices=["surface", "faceted", "extrude", "loft"], default="surface",
                    help="surface/faceted rebuild the mesh; extrude/loft (EXPERIMENTAL) redraw it from its 2D outline")
    ap.add_argument("--engine", choices=["classic", "staged", "ladder", "v2", "best", "quad", "auto"],
                    default="classic",
                    help="surface/faceted: classic (default); quad = EXPERIMENTAL quad patches for smooth organic "
                         "parts (a QuadriFlow / Instant Meshes quad layout, one Coons patch per quad; other parts go "
                         "to Classic); staged = EXPERIMENTAL roughing first (lathe shapes, cylinders, cones and "
                         "spheres as exact faces before patch fitting); v2 = EXPERIMENTAL fitter; best = "
                         "EXPERIMENTAL second pass (run both, keep the better)")
    cliutil.add_performance_args(ap)
    ap.add_argument("--axis", choices=["x", "y", "z"], default="z", help="extrude/loft (EXPERIMENTAL): direction")
    ap.add_argument("--source", choices=["silhouette", "slice"], default="silhouette",
                    help="extrude: whole-part outline, or an exact cross-section")
    ap.add_argument("--slice-at", type=float, default=None, help="extrude slice: mm along the axis")
    ap.add_argument("--height", type=float, default=0.0, help="extrude: mm (0 = the part's own height)")
    ap.add_argument("--simplify", type=float, default=0.0, help="extrude/loft: outline tolerance mm (0 = auto)")
    ap.add_argument("--smooth-sketch", action="store_true",
                    help="extrude (EXPERIMENTAL): redraw each outline loop as lines, arcs, circles, ellipses "
                         "and splines instead of many straight segments")
    ap.add_argument("--symmetric", action="store_true",
                    help="extrude: split the height both ways around the cross-section (or the part's middle)")
    ap.add_argument("--heights", default="", metavar="H1,H2,..",
                    help="loft: use these slice heights (mm along the axis) instead of picking them")
    ap.add_argument("--max-slices", type=int, default=15, help="loft: most cross-sections used")
    ap.add_argument("--sensitivity", type=float, default=0.02, help="loft: lower = more slices")
    ap.add_argument("--units", choices=["mm", "in", "inches", "cm", "m"], default="mm",
                    help="units the mesh was modelled in (STEP is written in mm)")
    ap.add_argument("--tol", type=float, default=0.0, help="fit tolerance in mm (0 = auto)")
    ap.add_argument("--patch-size", type=float, default=0.30)
    ap.add_argument("--max-bend", type=float, default=60.0)
    ap.add_argument("--plane-angle", type=float, default=3.0)
    ap.add_argument("--crease-angle", type=float, default=30.0)
    ap.add_argument("--decimate", type=int, default=0,
                    help="reduce to N triangles; flat faces, holes and sharp edges are found first "
                         "and kept exact, only region interiors are simplified")
    ap.add_argument("--smooth", type=int, default=0)
    ap.add_argument("--no-primitives", action="store_true")
    ap.add_argument("--guess", nargs="?", type=float, const=0.3, default=0.0, metavar="MM",
                    help="guesstimate organic areas: freeform surfaces may be up to MM off (default 0.3); "
                         "flat faces, round holes and edges keep the normal tolerance. Much faster on "
                         "dense organic meshes. Classic fitter only")
    ap.add_argument("--no-labels", action="store_true",
                    help="hardpoint STL input: ignore the face labels in the file and find the faces from scratch")
    ap.add_argument("--save-labelled", nargs="?", const="auto", default="", metavar="FILE",
                    help="also write the input mesh as a hardpoint STL carrying the faces found "
                         "(default name: <output>_hardpoints.stl)")
    ap.add_argument("--route", choices=["auto", "prismatic", "lathe", "organic", "mixed"], default="auto",
                    help="ladder engine: force the route instead of reading it off the mesh")
    ap.add_argument("--no-ladder-guess", action="store_true",
                    help="ladder engine: never guesstimate problem areas (fit them exactly or keep facets)")
    ap.add_argument("--no-ladder-rough", action="store_true",
                    help="ladder engine, prismatic parts: plain fitter only (no extruded-outline roughing first)")
    ap.add_argument("--budget", type=float, default=0.0, metavar="S",
                    help="seconds; when passed, patches still outside tolerance become facets at once")
    ap.add_argument("--max-fills", type=int, default=0,
                    help="most slow plate fills per conversion (0 = the profile's allowance)")
    ap.add_argument("--gap-fill", choices=["plate", "coons+plate", "coons", "off"], default="plate",
                    help="a freeform patch the normal fit misses: plate filler (default), or EXPERIMENTAL "
                         "Coons patch on its outline (coons), Coons then plate (coons+plate), or neither (off)")
    ap.add_argument("--no-ceiling", action="store_true",
                    help="no time ceiling and no plate-fill allowance: refine for as long as it takes")
    ap.add_argument("--plane-rule", choices=["classic", "length"], default="classic",
                    help="flat-face border test: by edge count (classic) or edge length (keeps coarse cylinder "
                         "facets from passing as flats; can drop tiny real flats)")
    ap.add_argument("--no-memory-guard", action="store_true",
                    help="don't auto-reduce meshes that are too big for this computer's RAM")


def defaults_for(argv):
    """--preset NAME sets the defaults of the options it covers; any option given still wins."""
    import argparse
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--preset", default=None)
    ns, _ = pre.parse_known_args(argv)
    if not ns.preset:
        return None
    from . import presets
    try:
        return presets.cli_defaults(ns.preset)
    except ValueError:
        return None          # the real parser names the valid choices


def params_from_args(a, output):
    units = "inches" if a.units == "in" else a.units
    save = (os.path.splitext(output)[0] + "_hardpoints.stl") if a.save_labelled == "auto" else a.save_labelled
    return dict(
        units=units, mode=a.mode, engine=a.engine, tol=a.tol, plane_angle=a.plane_angle,
        crease_angle=a.crease_angle, patch_size=a.patch_size * 100.0, max_bend=a.max_bend,
        decimate=a.decimate, smooth=a.smooth, primitives=not a.no_primitives,
        memory_guard=not a.no_memory_guard, guess=bool(a.guess and a.guess > 0),
        organic_tol=(a.guess if a.guess and a.guess > 0 else 0.3), use_labels=not a.no_labels,
        save_labelled=save, route=a.route, ladder_guess=not a.no_ladder_guess,
        ladder_rough=not a.no_ladder_rough, budget_s=max(0.0, a.budget), max_fills=max(0, a.max_fills),
        plane_rule=a.plane_rule, no_ceiling=bool(a.no_ceiling), gap_fill=a.gap_fill,
        axis=a.axis, source=a.source, slice_at=a.slice_at, height=a.height, simplify=a.simplify,
        smooth_sketch=a.smooth_sketch, symmetric=a.symmetric,
        heights=",".join(repr(v) for v in (cliutil.heights_arg(a.heights) or ())),
        max_slices=a.max_slices, sensitivity=a.sensitivity)


def check(rep):
    if not getattr(rep, "ok", True):
        raise RuntimeError("conversion failed")


def after_run(a, job, rep, lines, machine):
    """The same diagnostic code the app's preview window shows as a QR, printed under the result."""
    if machine or a.mode in ("extrude", "loft"):
        return
    try:
        from tostep.core import diagcode
        from tostep.version import __version__
        p = dict(mode=a.mode, engine=a.engine, units=job.params["units"], tol=a.tol, guess=bool(a.guess),
                 organic_tol=a.guess, plane_angle=a.plane_angle, crease_angle=a.crease_angle,
                 patch_size=a.patch_size * 100.0, max_bend=a.max_bend, decimate=a.decimate, smooth=a.smooth,
                 primitives=not a.no_primitives, memory_guard=not a.no_memory_guard, route=a.route,
                 ladder_guess=not a.no_ladder_guess, use_labels=not a.no_labels,
                 budget_s=a.budget or float(os.environ.get("TOSTEP_BUDGET") or 0),   # (as engines.py: yours first)
                 max_fills=float(os.environ.get("TOSTEP_MAX_FILLS") or 0) or a.max_fills)
        prof = dict(name=a.profile or "auto", threads=float(os.environ.get("OMP_NUM_THREADS") or 0))
        print("Diagnostic code (read it back with: python run_tostep.py diag CODE):\n  "
              + diagcode.build("convert", p, "done", diagcode.facts(rep), lines, version=__version__,
                               job_id="%04x" % (os.getpid() & 0xFFFF), profile=prof), flush=True)
    except Exception:                  # an extra, never a reason to fail
        pass
