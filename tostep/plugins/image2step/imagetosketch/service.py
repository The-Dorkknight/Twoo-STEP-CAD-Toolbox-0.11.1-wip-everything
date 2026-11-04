# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
# Copied from the ImageToSketch project by tools/sync_imagetosketch.py - edit it there.
"""One front door for programs that use the tracer without FreeCAD: the standalone app
(its own app) and other apps that embed this library (the Image -> STEP plugin of Twoo STEP CAD Toolbox).

They all do the same few things - turn a form's fields into Options, trace, show a preview, save
SVG / DXF / STEP, hand out the printable scale token - so those things live here once.

Settings arrive as a flat dict of plain values (strings or numbers, as a web form or a command
line gives them):

    scale            "calsheet" | "token" | "markers" | "dpi" | "image_width" | "mm_per_px" | "square"
    width_mm, mm_per_px, square_mm, check_line_mm
    stand_height     calsheet: the stand's top (with its label) above the paper, mm (50)
    sheet_across, sheet_along   calsheet: what the page-2 check lines measure (150, 200)
    empty_sheet      calsheet: path of a photo of the same sheet without the part (optional)
    token_kind       "paper" | "plastic"        token_side, token_thickness
    above            how much higher the traced face is than the token / the paper (mm)
    camera           camera to table (mm), 0 = from the photo's lens data (used only without lens data)
    straight_walls   true / false
    tone             "auto" | "dark" | "light"   line_art  "auto" | "on" | "off"
    enlarge          0 = automatic, 1 = never, or a factor
    picture_edge     "auto" | "edge" | "cut"
    find             "tone" (the part against its background) | "transitions" (EXPERIMENTAL: every
                     change of colour; each colour area gets its own outline)
    colour_step      transitions: smallest colour difference that counts as an edge, in CIE Lab
                     units; 0 = automatic
    shared_edges     transitions: "once" (an edge two areas share is drawn once) | "both"
    weld_edges       transitions: true (default) = neighbours use one and the same curve for a
                     shared edge, so the pieces fit exactly | false = each keeps its own fit
"""
import base64
import os
import tempfile

SCALES = ("calsheet", "token", "markers", "dpi", "image_width", "mm_per_px", "square")
SCALE_NAMES = {"calsheet": "calibration sheet", "token": "scale token", "markers": "marker sheet", "dpi": "the file's own resolution",
               "image_width": "picture width you gave", "mm_per_px": "value you gave",
               "square": "reference square in the picture"}
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp")
EXPORTS = ("svg", "dxf", "step")
FINDS = ("tone", "transitions")


def number(p, key, default=0.0):
    v = p.get(key)
    if v is None or str(v).strip() == "":
        return float(default)
    try:
        return float(v)
    except (TypeError, ValueError):
        raise ValueError("'%s' must be a number" % key)


def flag(p, key, default=True):
    v = p.get(key, default)
    if isinstance(v, str):
        return v.strip().lower() not in ("", "0", "false", "no", "off")
    return bool(v)


def options(p):
    """Form fields -> Options. Raises ValueError with a plain message for a bad field."""
    from .pipeline import Options
    scale = p.get("scale") or "token"
    if scale not in SCALES:
        raise ValueError("Unknown way of setting the scale")
    value = {"image_width": number(p, "width_mm", 100.0), "mm_per_px": number(p, "mm_per_px", 0.1),
             "square": number(p, "square_mm", 50.0)}.get(scale, 1.0)
    tone = p.get("tone") or "auto"
    line = p.get("line_art") or "auto"
    edge = p.get("picture_edge") or "auto"
    find = p.get("find") or "tone"
    shared = p.get("shared_edges") or "once"
    for name, v, allowed in (("part tone", tone, ("auto", "dark", "light")),
                             ("line drawing", line, ("auto", "on", "off")),
                             ("picture edge", edge, ("auto", "edge", "cut")),
                             ("what to trace", find, FINDS),
                             ("shared edges", shared, ("once", "both"))):
        if v not in allowed:
            raise ValueError("The %s setting must be one of: %s" % (name, ", ".join(allowed)))
    kind = p.get("token_kind") or None                         # "paper" | "plastic" | not given
    if kind not in (None, "paper", "plastic"):
        raise ValueError("The token is either 'paper' or 'plastic'")
    tthick = 0.0 if kind == "paper" else number(p, "token_thickness", 1.2 if kind == "plastic" else 0.0)
    if tthick < 0:
        raise ValueError("The token thickness must be zero or more")
    return Options(
        scale_mode=scale, scale_value=value, check_line_mm=number(p, "check_line_mm", 100.0),
        polarity=tone, line_art=line, enlarge=number(p, "enlarge", 0.0), picture_edge=edge,
        find=find, colour_step=number(p, "colour_step", 0.0), shared_edges=shared,
        weld_edges=flag(p, "weld_edges", True),
        token_side_mm=number(p, "token_side", 40.0), token_thickness_mm=tthick,
        part_above_token_mm=number(p, "above", 0.0), camera_distance_mm=number(p, "camera", 0.0),
        focal_35mm=number(p, "focal_35mm", 0.0), straight_walls=flag(p, "straight_walls", True),
        stand_height_mm=number(p, "stand_height", 50.0), sheet_across_mm=number(p, "sheet_across", 150.0),
        sheet_along_mm=number(p, "sheet_along", 200.0), empty_sheet=_empty(p))


def _empty(p):
    v = p.get("empty_sheet")
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    if isinstance(v, str) and not os.path.isfile(v):
        raise ValueError("The empty-sheet photo is not there any more: choose it again")
    return v


def trace_file(src, p):
    """Trace a picture file with form settings. Returns the pipeline's Result."""
    try:
        import cv2  # noqa: F401
    except ImportError:
        raise ValueError("The tracer needs OpenCV, which is not installed. Start the app with its "
                         "start file: it installs what is missing.")
    from .pipeline import trace
    return trace(src, options(p))


def describe(res, p=None):
    """What the trace found, as lines of plain text (summary first, then the messages)."""
    lines = [res.summary()] + list(res.info["messages"])
    if p is not None:
        lines.append("Scale: %.5f mm per pixel of the picture (%s)." % (
            float(res.info.get("mm_per_source_px") or res.mm_per_px), SCALE_NAMES.get(p.get("scale") or "token", "")))
    return lines


def preview_png(res, max_side=1100):
    """The verdicts drawn on the picture, as PNG bytes: green = traced, amber = borderline,
    red = rejected."""
    import cv2
    ok, buf = cv2.imencode(".png", res.overlay(max_side))
    if not ok:
        raise ValueError("Could not draw the preview")
    return buf.tobytes()


def data_url(png):
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def guess(src):
    """Which scale source the picture obviously carries: {"scale": ..., "note": ...} or {}."""
    try:
        import cv2
        from . import imageio, pattern, scaletoken
        from .detect import composite
        if imageio.read_dpi(src):
            return {"scale": "dpi", "note": "This file records its own resolution (a scan)."}
        img, _ = imageio.load_image(src)
        img, _ = composite(img)
        f = min(1.0, 1600.0 / max(img.shape[:2]))
        small = cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_AREA) if f < 1 else img
        try:
            from . import calsheet
            if calsheet.have_aruco():
                found = calsheet.detect(small)
                n = sum(1 for i in found if calsheet.paper_of(i))
                if n >= 4:
                    stand = calsheet.STAND_ID in found
                    return {"scale": "calsheet", "note": "Calibration sheet found in the picture (%d frame markers)%s."
                            % (n, ", with the stand: it measures the camera distance" if stand else
                               "; no stand in view" + ("" if imageio.read_lens(src).get("focal_35mm") else
                                                       " and no lens data: for a part taller than a few mm, "
                                                       "stand the stand on the sheet or type the camera distance"))}
        except Exception:
            pass
        try:
            pattern.find_sheet(small)
            return {"scale": "markers", "note": "Marker sheet found in the picture."}
        except Exception:
            pass
        try:
            scaletoken.find_token(small)
            lens = imageio.read_lens(src)
            return {"scale": "token", "note": "Scale token found in the picture."
                    + ("" if lens.get("focal_35mm") else " The file has no lens data: if the part is taller "
                       "than the token, type the camera distance (or a phone's main camera without zoom is "
                       "assumed).")}
        except Exception:
            pass
    except Exception:
        pass
    return {}


def export(res, kind, path, thickness=0.0, curves_only=False, include_borderline=False, name=None):
    """Write the traced outlines to a file. kind: "svg" | "dxf" | "step". Returns a dict:
    kind, path, text (one plain line saying what was written), and for STEP the report of
    step.to_step (pieces, volume / area, ...). Raises ValueError if there is nothing to write."""
    if kind not in EXPORTS:
        raise ValueError("Unknown file type '%s'" % kind)
    if not res.accepted and not (include_borderline and res.borderline):
        raise ValueError("Nothing in the picture passed as a real outline. The preview shows what was "
                         "found: amber shapes are borderline, red ones rejected. A sharper, higher-contrast "
                         "picture, or the other part tone, usually helps.")
    acc = res.accepted
    n, holes = len(acc), sum(1 for s in acc if s.hole)
    if kind == "step":
        from .step import to_step, describe as describe_step
        if thickness < 0:
            raise ValueError("The thickness must be zero or more")
        rep = to_step(res, path, depth=float(thickness), wires=bool(curves_only),
                      include_borderline=bool(include_borderline),
                      name=name or os.path.splitext(os.path.basename(path))[0] or "image")
        return dict(rep, kind="step", shape=rep["kind"], path=path, text="STEP: " + describe_step(rep))
    from .export import to_svg, to_dxf
    text = (to_svg if kind == "svg" else to_dxf)(res, include_maybe=bool(include_borderline))
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    extra = len(res.borderline) if include_borderline else 0
    return dict(kind=kind, path=path, outlines=n, holes=holes,
                text="%s: %d outline%s (%d of them holes), in millimetres%s." % (
                    kind.upper(), n, "" if n == 1 else "s", holes,
                    ("; %d borderline on their own layer" % extra) if extra else ""))


def _made(make, suffix):
    fd, tmp = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    try:
        make(tmp)
        with open(tmp, "rb") as f:
            return f.read()
    finally:
        os.remove(tmp)


def token_pdf(paper="A4", side=40.0):
    """The token sheet to print on paper (a token in two opposite corners), as PDF bytes."""
    from . import scaletoken
    return _made(lambda t: scaletoken.make_pdf(t, paper, float(side)), ".pdf")


def token_stl(side=40.0, thickness=1.2):
    """The token for 3D printing, as STL bytes."""
    from . import scaletoken
    return _made(lambda t: scaletoken.make_stl(t, float(side), float(thickness)), ".stl")


def sheet_pdf(paper="A4"):
    """The printable marker sheet, as PDF bytes."""
    from . import pattern
    if paper not in pattern.SHEETS:
        raise ValueError("Paper must be A4 or Letter")
    return _made(lambda t: pattern.make_sheet(t, paper), ".pdf")


def calsheet_pdf(paper="A4"):
    """The calibration sheet (page 1: the marker frame; page 2: stand labels and check lines),
    as PDF bytes."""
    from . import calsheet
    return _made(lambda t: calsheet.make_pdf(t, paper), ".pdf")


def stand_stl(height=50.0):
    """The 50 mm stand for the calibration sheet, as STL bytes (prints top face down)."""
    from . import calsheet
    return _made(lambda t: calsheet.make_stand_stl(t, float(height)), ".stl")
