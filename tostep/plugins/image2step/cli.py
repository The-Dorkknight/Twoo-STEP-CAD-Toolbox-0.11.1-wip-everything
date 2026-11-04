# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""tostep image photo.jpg [out.step] [options]  — trace a photo, scan or drawing of a flat part into STEP.

This command takes over the whole command line (the scale can come from seven places, and it can also
write the printable sheets), so it defines main(argv) instead of the smaller hooks."""
from __future__ import annotations

import argparse
import os
import sys

from tostep.core import cliutil

CLI_HELP = "Trace a photo, scan or drawing of a flat part into STEP."


def main(argv):
    """Photo / scan / drawing -> outline -> STEP (flat, or a solid of a given thickness)."""
    ap = argparse.ArgumentParser(
        prog="tostep image",
        description="Trace a photo, scan or drawing of a flat part and write its outline as STEP: a solid "
                    "of the thickness you give, or (thickness 0) the flat shape as faces.")
    ap.add_argument("input", nargs="?", help="picture: PNG, JPG, BMP, TIFF or WebP")
    ap.add_argument("output", nargs="?", help="output .step (default: next to the picture)")
    ap.add_argument("--thickness", type=float, default=0.0, metavar="MM",
                    help="thickness of the solid; 0 (default) writes the flat shape, no extrusion needed")
    ap.add_argument("--curves-only", action="store_true",
                    help="write only the curves, no faces (some CAD programs skip bare curves in STEP)")
    sc = ap.add_mutually_exclusive_group()
    sc.add_argument("--token", action="store_true", help="scale from the printed token in the photo (default)")
    sc.add_argument("--markers", action="store_true", help="scale and perspective from the printed marker sheet")
    sc.add_argument("--dpi", action="store_true", help="scale from the resolution stored in the file (scans)")
    sc.add_argument("--width-mm", type=float, metavar="MM", help="real width of the whole picture")
    sc.add_argument("--mm-per-px", type=float, metavar="MM", help="known scale")
    sc.add_argument("--square-mm", type=float, metavar="MM", help="side of a plain reference square in the picture")
    ap.add_argument("--token-side", type=float, default=40.0, metavar="MM", help="measured side of the token")
    ap.add_argument("--token-thickness", type=float, default=0.0, metavar="MM",
                    help="0 = token printed on paper (default); 1.2 for the 3D-printed token")
    ap.add_argument("--part-above-token", type=float, default=0.0, metavar="MM",
                    help="how much higher the traced face is than the token's top (paper token: the part's "
                         "height; plastic token on the same table: part height minus token thickness)")
    ap.add_argument("--camera-distance", type=float, default=0.0, metavar="MM",
                    help="camera to table; only needed when the picture carries no lens data")
    ap.add_argument("--sloped-walls", action="store_true",
                    help="token: do not assume the part has straight vertical sides")
    ap.add_argument("--check-line-mm", type=float, default=100.0, help="marker sheet: measured length of its 100 mm line")
    ap.add_argument("--part", choices=["auto", "dark", "light"], default="auto", help="tone of the part")
    ap.add_argument("--line-art", choices=["auto", "on", "off"], default="auto",
                    help="trace thin drawn lines along their middle")
    ap.add_argument("--enlarge", type=float, default=0.0, help="small pictures: 0 = automatic, 1 = never, or a factor")
    ap.add_argument("--picture-edge", choices=["auto", "edge", "cut"], default="auto",
                    help="a shape that fills the picture to its edge: auto = trace it on drawn graphics, leave "
                         "photos alone; edge = always trace it; cut = always leave it out")
    ap.add_argument("--find", choices=["tone", "transitions"], default="tone",
                    help="what counts as an edge: tone = the part against its background (default); transitions = "
                         "EXPERIMENTAL, every sharp change of colour, each colour area its own face or solid")
    ap.add_argument("--colour-step", type=float, default=0.0, metavar="LAB",
                    help="--find transitions: smallest colour difference that counts as an edge (CIE Lab units, "
                         "about 2.3 is just noticeable); 0 = automatic")
    ap.add_argument("--shared-edges", choices=["once", "both"], default="once",
                    help="--find transitions, for --also-svg / --also-dxf: an edge two areas share is drawn once, "
                         "or by both so every outline is closed")
    ap.add_argument("--no-weld", action="store_true",
                    help="--find transitions: let each colour area keep its own fit of an edge it shares, "
                         "instead of both using one curve (by default neighbours meet exactly)")
    ap.add_argument("--include-borderline", action="store_true",
                    help="also write the shapes the tracer marked as borderline (amber in the preview)")
    ap.add_argument("--also-svg", action="store_true", help="also save the outline as SVG next to the STEP")
    ap.add_argument("--also-dxf", action="store_true", help="also save the outline as DXF next to the STEP")
    ap.add_argument("--make-token-sheet", metavar="PDF", help="write the scale token as a sheet to print on paper and exit")
    ap.add_argument("--make-token", metavar="STL", help="write the scale token for 3D printing and exit")
    ap.add_argument("--make-sheet", metavar="PDF", help="write the printable marker sheet and exit")
    ap.add_argument("--paper", choices=["A4", "Letter"], default="A4", help="paper for --make-token-sheet / --make-sheet")
    ap.add_argument("--pause", action="store_true", help="wait for Enter before closing (for file-manager use)")
    ap.add_argument("--machine", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    from . import image2step
    if a.make_token_sheet:
        try:
            data = image2step.token_pdf(a.paper, a.token_side)
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2
        with open(a.make_token_sheet, "wb") as f:
            f.write(data)
        print(f"Saved {a.make_token_sheet}. Print it at 100 % (actual size), then measure a black square with a ruler.")
        return 0
    if a.make_token:
        with open(a.make_token, "wb") as f:
            f.write(image2step.token_stl(a.token_side, a.token_thickness or 1.2))
        print(f"Saved {a.make_token}. Print it in matt black, flat on the bed, then measure its side with calipers.")
        return 0
    if a.make_sheet:
        with open(a.make_sheet, "wb") as f:
            f.write(image2step.sheet_pdf(a.paper))
        print(f"Saved {a.make_sheet}. Print it at 100 % (actual size).")
        return 0
    if not a.input:
        ap.error("a picture is required")
    if a.pause:
        import atexit
        atexit.register(cliutil.pause)
    scale = ("markers" if a.markers else "dpi" if a.dpi else "image_width" if a.width_mm else
             "mm_per_px" if a.mm_per_px else "square" if a.square_mm else "token")
    p = dict(scale=scale, width_mm=a.width_mm, mm_per_px=a.mm_per_px, square_mm=a.square_mm,
             token_side=a.token_side, token_thickness=a.token_thickness, above=a.part_above_token,
             camera=a.camera_distance, straight_walls=not a.sloped_walls, check_line_mm=a.check_line_mm,
             tone=a.part, line_art=a.line_art, enlarge=a.enlarge, thickness=a.thickness, curves_only=a.curves_only,
             picture_edge=a.picture_edge, borderline=a.include_borderline, also_svg=a.also_svg, also_dxf=a.also_dxf,
             find=a.find, colour_step=a.colour_step, shared_edges=a.shared_edges, weld_edges=not a.no_weld)
    out = a.output or os.path.splitext(a.input)[0] + ".step"

    def go():
        try:
            rep = image2step.convert(a.input, out, p, log=lambda s: print(s, flush=True),
                                     progress=cliutil.machine_progress(a.machine))
        except ValueError as e:
            raise RuntimeError(str(e))
        print("\n" + rep.summary() + f"\n\nSaved {out}", flush=True)
        return out
    try:
        return cliutil.run_machine_safe(a.machine, go)
    except RuntimeError as e:
        print(f"\nERROR: {e}\n")
        return 1
