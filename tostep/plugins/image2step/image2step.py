# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""
image2step — a photo, scan or drawing of a part  ->  its outline as a STEP file.

The tracing is done by ImageToSketch (same author): the parts of it that need no FreeCAD are
copied into app/imagetosketch by tools/sync_imagetosketch.py.

  picture -> outlines (lines, arcs, circles, ellipses, splines; holes by nesting)
          -> scaled to millimetres (the scale token - printed on paper or in plastic -, the
             marker sheet, a scan's own resolution, or a number you type)
          -> STEP: a solid of the thickness you ask for, or with thickness 0 the flat shape
             as faces. A STEP file does not have to hold a solid.

Two ways of tracing: the part against its background (the default), or - EXPERIMENTAL -
every change of colour, where each colour area of an icon, logo or painted part gets its own
outline and becomes its own face or solid.

This module is the glue for the web page, the worker and the command line. Everything the
tracer itself offers is reached through its `service` front door (the library's own door for
programs that embed it), so this tab and the standalone ImageToSketch app behave the same.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}
PICTURE_EDGES = ("auto", "edge", "cut")
FINDS = ("tone", "transitions")          # "transitions" is EXPERIMENTAL: an outline wherever the colour changes


def tracer():
    """The picture tracer: the ImageToSketch library built into this plugin, reached through its
    `service` front door (the library's own door for programs that embed it)."""
    from tostep.plugins.image2step import imagetosketch
    from tostep.plugins.image2step.imagetosketch import service, step

    from types import SimpleNamespace
    return SimpleNamespace(service=service, describe_step=step.describe,
                           version=getattr(imagetosketch, "__release__", imagetosketch.__version__),
                           source="built in")


def _service():
    return tracer().service


def _clean(p):
    """The page and the command line may send empty or unknown choices: fall back to the
    automatic setting for those (numbers are still checked and reported)."""
    q = dict(p or {})
    for key, allowed in (("tone", ("auto", "dark", "light")), ("line_art", ("auto", "on", "off")),
                         ("picture_edge", PICTURE_EDGES), ("find", FINDS), ("shared_edges", ("once", "both"))):
        if q.get(key) not in allowed:
            q[key] = allowed[0]
    if str(q.get("colour_step") or "").strip() == "":
        q["colour_step"] = 0
    if q.get("token_kind") == "":
        q["token_kind"] = None
    return q


def options(p):
    """Web / command-line parameters -> imagetosketch Options."""
    return _service().options(_clean(p))


@dataclass
class ImageReport:
    ok: bool = False
    is_solid: bool = False
    outlines: int = 0
    holes: int = 0
    borderline: int = 0
    borderline_used: bool = False
    mm_per_px: float = 0.0
    scale_from: str = ""
    step: dict = field(default_factory=dict)
    areas: int = 0                                   # colour areas traced ("every colour change" only)
    extras: list = field(default_factory=list)       # other files written beside the STEP (SVG, DXF)
    trace_line: str = ""
    seconds: float = 0.0

    def summary(self):
        describe = tracer().describe_step
        left = "" if not self.borderline else (f"; {self.borderline} borderline included" if self.borderline_used
                                               else f"; {self.borderline} borderline left out")
        lines = [f"Outlines traced: {self.outlines} ({self.holes} of them holes)"
                 + (f", {self.areas} colour areas" if self.areas else "") + left,
                 f"Scale: {self.mm_per_px:.5f} mm per pixel of the picture ({self.scale_from})",
                 "STEP: " + describe(self.step)]
        if self.step.get("kind") == "solid":
            lines.append("Closed solid: " + ("yes" if self.is_solid else "NO"))
        else:
            lines.append("Flat shape, no thickness: the file holds a surface, not a solid.")
        for path in self.extras:
            lines.append("Also saved: " + os.path.basename(path))
        lines.append(f"Time: {self.seconds:.1f} s")
        return "\n".join(lines)


def _trace(src, p, log):
    svc = _service()
    res = svc.trace_file(src, _clean(p))
    log(res.summary())
    for m in res.info["messages"]:
        log("  " + m)
    return res


def convert(src, out, p, log=None, progress=None):
    """Trace the picture and write the STEP. p: parameters as the web page sends them, plus
    thickness (mm, 0 = flat faces), curves_only, borderline (include the amber shapes),
    also_svg / also_dxf (write the outline in those formats too, beside the STEP or into
    extras_dir). Returns an ImageReport."""
    import time
    t = tracer()
    svc = t.service
    log = log or (lambda s: None)
    progress = progress or (lambda f: None)
    t0 = time.time()
    progress(0.05)
    log("Tracing the picture with ImageToSketch %s (%s)…" % (t.version, getattr(t, "source", "built in")))
    res = _trace(src, p, log)
    progress(0.6)
    thick = svc.number(p, "thickness", 0.0)
    if thick < 0:
        raise ValueError("The thickness must be zero or more")
    use_border = svc.flag(p, "borderline", False)
    name = os.path.splitext(os.path.basename(str(src)))[0] or "image"
    rep = svc.export(res, "step", out, thickness=thick, curves_only=svc.flag(p, "curves_only", False),
                     include_borderline=use_border, name=name)
    progress(0.85)
    acc = res.accepted
    r = ImageReport(ok=True, outlines=len(acc), holes=sum(1 for s in acc if s.hole), borderline=len(res.borderline),
                    borderline_used=use_border and bool(res.borderline),
                    mm_per_px=float(res.info.get("mm_per_source_px") or res.mm_per_px),
                    scale_from=svc.SCALE_NAMES.get(p.get("scale") or "token", ""),
                    step=dict(rep, kind=rep["shape"]), trace_line=res.summary(),
                    areas=len({s.group for s in acc if s.group >= 0}))
    if rep["shape"] == "solid":
        r.is_solid = _closed_solids(out) == rep["pieces"]
    log("Wrote " + ("a solid" if rep["shape"] == "solid" else "the flat shape" if rep["shape"] == "face"
                    else "the curves") + ".")
    # the same outline as a drawing, for laser cutters and 2D CAD
    folder = str(p.get("extras_dir") or "") or os.path.dirname(os.path.abspath(str(out)))
    stem = str(p.get("extras_stem") or "") or os.path.splitext(os.path.basename(str(out)))[0]
    for kind in ("svg", "dxf"):
        if svc.flag(p, "also_" + kind, False):
            path = os.path.join(folder, f"{stem}.{kind}")
            try:
                got = svc.export(res, kind, path, include_borderline=use_border, name=name)
                r.extras.append(path)
                log("  " + got["text"])
            except (ValueError, OSError) as e:
                log(f"  {kind.upper()} not written: {e}")
    r.seconds = time.time() - t0
    progress(1.0)
    return r


def _closed_solids(path):
    """How many valid closed solids the written file holds when read back."""
    # OCP only: the mesh converter (and trimesh, SciPy) is not needed for this (0.7.2)
    import shutil
    import tempfile
    tmp = None
    try:
        from OCP.BRepCheck import BRepCheck_Analyzer
        from OCP.IFSelect import IFSelect_RetDone
        from OCP.STEPControl import STEPControl_Reader
        from OCP.TopAbs import TopAbs_SOLID
        from OCP.TopExp import TopExp_Explorer
        src = str(path)
        if not src.isascii():                   # the STEP reader wants a plain path
            fd, tmp = tempfile.mkstemp(suffix=".step"); os.close(fd)
            shutil.copyfile(src, tmp)
        r = STEPControl_Reader()
        if r.ReadFile(tmp or src) != IFSelect_RetDone:
            return 0
        r.TransferRoots()
        shape = r.OneShape()
        if not BRepCheck_Analyzer(shape).IsValid():
            return 0
        ex, n = TopExp_Explorer(shape, TopAbs_SOLID), 0
        while ex.More():
            n += 1
            ex.Next()
        return n
    except Exception:
        return 0
    finally:
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                pass


def preview(src, p):
    """(PNG data URL of the verdicts, text). Green = traced, amber = borderline, red = rejected."""
    svc = _service()
    lines = []
    res = _trace(src, p, lines.append)
    lines.append("Green = becomes geometry · amber = borderline (left out of the STEP unless you tick "
                 "'Include borderline shapes') · red = rejected")
    return svc.data_url(svc.preview_png(res, 1100)), "\n".join(lines)


def guess(src):
    """Which scale source the picture obviously carries: {"scale": ..., "note": ...} or {}."""
    return _service().guess(src)


def token_pdf(paper, side):
    """The token sheet to print on paper (a token in two opposite corners) as PDF bytes."""
    return _service().token_pdf(paper, side)


def token_stl(side, thickness):
    """The token for 3D printing as STL bytes."""
    return _service().token_stl(side, thickness)


def sheet_pdf(paper):
    """The printable marker sheet as PDF bytes."""
    return _service().sheet_pdf(paper)


def tracer_version():
    try:
        return str(tracer().version)
    except Exception:
        return "?"


def _sheet_service(name):
    """The tracer's service if it has `name`, else the built-in library's: a drop-in tracer made
    before ImageToSketch 0.10 has no calibration sheet, and the downloads should still work."""
    s = _service()
    if hasattr(s, name):
        return s
    from tostep.plugins.image2step.imagetosketch import service
    return service


def calsheet_pdf(paper):
    """The calibration sheet (page 1: marker frame; page 2: stand labels, check lines) as PDF bytes.
    From ImageToSketch 0.10.0 (added with its module's tools/add_calibration_sheet.py)."""
    return _sheet_service("calsheet_pdf").calsheet_pdf(paper)


def stand_stl():
    """The calibration sheet's 50 mm stand as STL bytes (prints top face down)."""
    return _sheet_service("stand_stl").stand_stl()
