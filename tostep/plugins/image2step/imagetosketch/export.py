# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
# Copied from the ImageToSketch project by tools/sync_imagetosketch.py - edit it there.
"""Write a trace Result as SVG or DXF (millimetres), so it is usable without FreeCAD too.

Both files import into FreeCAD, Fusion 360, Inkscape, LibreCAD and most CAM tools.
Accepted outlines go on layer / group "OUTLINE", borderline ones on "MAYBE".
"""
import math

from .geom import arc_from_3pts, cubic_spline


def _runs(s):
    """What to draw of a shape: [(primitives, closed)]. One closed run unless pieces of the
    outline are left to a neighbouring colour area that draws the same edge (transition mode)."""
    return s.drawn() if hasattr(s, "drawn") else [(s.prims, True)]


def _fmt(v):
    return ("%.4f" % v).rstrip("0").rstrip(".")


def to_svg(res, include_maybe=True):
    x0, y0, x1, y1 = res.image_extent_mm()
    w, h = x1 - x0, y1 - y0

    def X(p):                                                  # sketch mm (y up) -> SVG (y down)
        return p[0] - x0, y1 - p[1]

    def path(prims, closed=True):
        d = []
        for i, p in enumerate(prims):
            if p[0] == "circle":
                cx, cy = X((p[1], p[2]))
                r = p[3]
                return ("M %s %s A %s %s 0 1 0 %s %s A %s %s 0 1 0 %s %s Z" % tuple(
                    _fmt(v) for v in (cx - r, cy, r, r, cx + r, cy, r, r, cx - r, cy)))
            if p[0] == "ellipse":
                cx, cy = X((p[1], p[2]))
                a, b, th = p[3], p[4], -p[5]                   # y flip reverses the angle
                dx, dy = a * math.cos(th), a * math.sin(th)
                rot = math.degrees(th)
                return ("M %s %s A %s %s %s 1 0 %s %s A %s %s %s 1 0 %s %s Z" % tuple(
                    _fmt(v) for v in (cx - dx, cy - dy, a, b, rot, cx + dx, cy + dy, a, b, rot, cx - dx, cy - dy)))
            start = X(p[1] if p[0] != "spline" else p[1][0])
            if i == 0:
                d.append("M %s %s" % (_fmt(start[0]), _fmt(start[1])))
            if p[0] == "line":
                e = X(p[2])
                d.append("L %s %s" % (_fmt(e[0]), _fmt(e[1])))
            elif p[0] == "arc":
                g = arc_from_3pts(p[1], p[2], p[3])
                e = X(p[3])
                if g is None:
                    d.append("L %s %s" % (_fmt(e[0]), _fmt(e[1])))
                    continue
                _, _, r, a0, a1, ccw = g
                sweep = (a1 - a0) % (2 * math.pi) if ccw else (a0 - a1) % (2 * math.pi)
                large = 1 if sweep > math.pi else 0
                flag = 0 if ccw else 1                         # y flip reverses the turn direction
                d.append("A %s %s 0 %d %d %s %s" % (_fmt(r), _fmt(r), large, flag, _fmt(e[0]), _fmt(e[1])))
            else:
                S = cubic_spline(p[1], p[2], per_seg=8)
                for q in S[1:]:
                    e = X(q)
                    d.append("L %s %s" % (_fmt(e[0]), _fmt(e[1])))
        return " ".join(d) + (" Z" if closed else "")

    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<svg xmlns="http://www.w3.org/2000/svg" width="%smm" height="%smm" viewBox="0 0 %s %s">' % (
               _fmt(w), _fmt(h), _fmt(w), _fmt(h)),
           '<!-- ImageToSketch: units are millimetres -->']
    groups = [("OUTLINE", "#000000", [s for s in res.shapes if s.status == "accept"])]
    if include_maybe:
        groups.append(("MAYBE", "#ff9600", [s for s in res.shapes if s.status == "maybe"]))
    for name, col, shapes in groups:
        if not shapes:
            continue
        out.append('<g id="%s" fill="none" stroke="%s" stroke-width="0.2">' % (name, col))
        out.extend('<path d="%s"/>' % path(prims, closed) for s in shapes for prims, closed in _runs(s))
        out.append("</g>")
    out.append("</svg>")
    return "\n".join(out) + "\n"


def to_dxf(res, include_maybe=True):
    """Minimal R12 ASCII DXF: LINE, ARC, CIRCLE; splines as closed/open POLYLINEs."""
    e = []

    def add(*pairs):
        for code, val in pairs:
            e.append(str(code))
            e.append(_fmt(val) if isinstance(val, float) else str(val))

    def emit(prims, layer):
        for p in prims:
            if p[0] == "circle":
                add((0, "CIRCLE"), (8, layer), (10, float(p[1])), (20, float(p[2])), (40, float(p[3])))
            elif p[0] == "line":
                add((0, "LINE"), (8, layer), (10, float(p[1][0])), (20, float(p[1][1])),
                    (11, float(p[2][0])), (21, float(p[2][1])))
            elif p[0] == "arc":
                g = arc_from_3pts(p[1], p[2], p[3])
                if g is None:
                    add((0, "LINE"), (8, layer), (10, float(p[1][0])), (20, float(p[1][1])),
                        (11, float(p[3][0])), (21, float(p[3][1])))
                    continue
                cx, cy, r, a0, a1, ccw = g
                if not ccw:                                    # DXF arcs always run counter-clockwise
                    a0, a1 = a1, a0
                add((0, "ARC"), (8, layer), (10, float(cx)), (20, float(cy)), (40, float(r)),
                    (50, math.degrees(a0) % 360.0), (51, math.degrees(a1) % 360.0))
            else:
                if p[0] == "ellipse":                          # R12 has no ELLIPSE entity
                    from .geom import sample_prims
                    S, closed = sample_prims([p], max(p[4] / 40.0, 1e-3)), True
                else:
                    S, closed = cubic_spline(p[1], p[2], per_seg=8), p[2]
                add((0, "POLYLINE"), (8, layer), (66, 1), (70, 1 if closed else 0))
                for q in S:
                    add((0, "VERTEX"), (8, layer), (10, float(q[0])), (20, float(q[1])))
                add((0, "SEQEND"), (8, layer))

    add((0, "SECTION"), (2, "HEADER"), (9, "$INSUNITS"), (70, 4), (0, "ENDSEC"))
    add((0, "SECTION"), (2, "ENTITIES"))
    for s in res.shapes:
        for prims, _ in _runs(s):
            if s.status == "accept":
                emit(prims, "OUTLINE")
            elif include_maybe:
                emit(prims, "MAYBE")
    add((0, "ENDSEC"), (0, "EOF"))
    return "\n".join(e) + "\n"
