# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
# Copied from the ImageToSketch project by tools/sync_imagetosketch.py - edit it there.
"""Write a trace as a STEP file: flat, or extruded to a thickness.

    from tostep.plugins.image2step.imagetosketch import trace, Options
    from tostep.plugins.image2step.imagetosketch.step import to_step
    res = trace("photo.jpg", Options(scale_mode="token"))
    to_step(res, "part.step", depth=5.0)       # a 5 mm thick solid
    to_step(res, "outline.step")               # depth 0: the flat shape, as faces

What goes into the file
  depth > 0   one solid per outline, holes cut through, standing on z = 0.
  depth = 0   one flat FACE per outline (with its holes). A STEP file does not have to hold a
              solid: a face is a surface body, and every CAD program reads those.
  wires=True  only the curves, no face. Legal STEP, but several programs (Fusion among them)
              read faces and solids from STEP and silently skip bare curves - so this is
              offered, not the default.

Nesting decides what is a hole: an outline inside another is a hole in it; an outline inside a
hole is a separate piece again (the star inside a ring).

A trace made in transition mode has one outline per colour area, and neighbouring areas share
edges. There each area is its own face (or solid), with only its own holes: the pieces lie side
by side like a jigsaw, not nested.

The file is written by a real CAD kernel, not by hand: OpenCascade through `cadquery-ocp`
(pip install cadquery-ocp), or FreeCAD's own Part module when running inside FreeCAD. Curves
are the same lines, arcs, circles, ellipses and interpolated splines the FreeCAD sketch gets.
"""
import math

import numpy as np

from .geom import sample_prims, poly_area


# ----------------------------------------------------------------- nesting --
def _tree(shapes):
    """For each shape: index of the smallest shape that contains it (or None), and its depth."""
    polys = [np.asarray(sample_prims(s.prims, 0.25), float) for s in shapes]
    areas = [poly_area(p) if len(p) >= 3 else 0.0 for p in polys]
    order = sorted(range(len(shapes)), key=lambda i: -areas[i])
    parent = [None] * len(shapes)

    def inside(pt, poly):
        x, y = pt
        px, py = poly[:, 0], poly[:, 1]
        qx, qy = np.roll(px, -1), np.roll(py, -1)
        hit = ((py > y) != (qy > y)) & (x < (qx - px) * (y - py) / np.where(qy == py, 1e-300, qy - py) + px)
        return bool(hit.sum() % 2)

    for a, i in enumerate(order):
        if len(polys[i]) < 3:
            continue
        probe = polys[i][0]
        for j in reversed(order[:a]):                          # smallest larger shape first
            if len(polys[j]) >= 3 and inside(probe, polys[j]):
                parent[i] = j
                break
    depth = [0] * len(shapes)
    for i in order:
        depth[i] = 0 if parent[i] is None else depth[parent[i]] + 1
    return parent, depth


def groups(shapes):
    """[(outer shape, [hole shapes])] by nesting. Outlines that belong to different colour areas
    (transition mode: shape.group) are never nested in one another: each area is its own piece."""
    by_area = {}
    for s in shapes:
        by_area.setdefault(getattr(s, "group", -1), []).append(s)
    if len(by_area) > 1:
        out = []
        for g in sorted(by_area):
            out += groups_nested(by_area[g])
        return out
    return groups_nested(shapes)


def groups_nested(shapes):
    parent, depth = _tree(shapes)
    out = []
    for i, s in enumerate(shapes):
        if depth[i] % 2 == 0:
            out.append((s, [shapes[k] for k in range(len(shapes)) if parent[k] == i and depth[k] % 2 == 1]))
    return out


# -------------------------------------------------------------- OpenCascade --
def _S(cls, name):
    """OCP exposes static methods as Name_s in some versions and Name in others."""
    return getattr(cls, name + "_s", None) or getattr(cls, name)


def _quiet():
    """Stop OpenCascade printing its transfer statistics to the terminal."""
    try:
        from OCP.Message import Message, Message_Gravity
        for p in _S(Message, "DefaultMessenger")().Printers():
            p.SetTraceLevel(Message_Gravity.Message_Fail)
    except Exception:
        pass


def _ocp():
    try:
        import OCP  # noqa: F401
        return True
    except Exception:
        return False


def _freecad_part():
    """FreeCAD's Part module, if we are inside FreeCAD (and not looking at a test stand-in)."""
    try:
        import Part
    except Exception:
        return None
    return Part if all(hasattr(Part, a) for a in ("Wire", "Face", "makeCompound")) else None


def _occ_edges(prims):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
    from OCP.GC import GC_MakeArcOfCircle
    from OCP.GeomAPI import GeomAPI_Interpolate
    from OCP.gp import gp_Ax2, gp_Circ, gp_Dir, gp_Elips, gp_Pnt
    try:                                                       # OCCT 7.x
        from OCP.TColgp import TColgp_HArray1OfPnt as PntArray
    except ImportError:                                        # OCCT 8
        from OCP.collections import HArray1_gp_Pnt as PntArray

    def P(q):
        return gp_Pnt(float(q[0]), float(q[1]), 0.0)

    Z = gp_Dir(0, 0, 1)
    edges = []
    for p in prims:
        k = p[0]
        if k == "line":
            if math.hypot(p[2][0] - p[1][0], p[2][1] - p[1][1]) < 1e-9:
                continue
            edges.append(BRepBuilderAPI_MakeEdge(P(p[1]), P(p[2])).Edge())
        elif k == "arc":
            mk = GC_MakeArcOfCircle(P(p[1]), P(p[2]), P(p[3]))
            edges.append(BRepBuilderAPI_MakeEdge(mk.Value()).Edge() if mk.IsDone()
                         else BRepBuilderAPI_MakeEdge(P(p[1]), P(p[3])).Edge())
        elif k == "circle":
            edges.append(BRepBuilderAPI_MakeEdge(gp_Circ(gp_Ax2(P((p[1], p[2])), Z), float(p[3]))).Edge())
        elif k == "ellipse":
            cx, cy, a, b, th = p[1:6]
            if b > a:
                a, b, th = b, a, th + math.pi / 2.0
            ax = gp_Ax2(P((cx, cy)), Z, gp_Dir(math.cos(th), math.sin(th), 0.0))
            edges.append(BRepBuilderAPI_MakeEdge(gp_Elips(ax, float(a), float(b))).Edge())
        else:                                                  # spline through the points
            pts = []
            for q in p[1]:
                if not pts or math.hypot(q[0] - pts[-1][0], q[1] - pts[-1][1]) > 1e-7:
                    pts.append(q)
            periodic = bool(p[2])
            if periodic and len(pts) > 2 and math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1]) < 1e-7:
                pts.pop()
            if len(pts) < 2:
                continue
            if len(pts) == 2:
                edges.append(BRepBuilderAPI_MakeEdge(P(pts[0]), P(pts[1])).Edge())
                continue
            arr = PntArray(1, len(pts))
            for i, q in enumerate(pts, start=1):
                arr.SetValue(i, P(q))
            it = GeomAPI_Interpolate(arr, periodic, 1e-7)
            it.Perform()
            if it.IsDone():
                edges.append(BRepBuilderAPI_MakeEdge(it.Curve()).Edge())
            else:                                              # kernel refused: straight pieces instead
                ring = pts + pts[:1] if periodic else pts
                edges += [BRepBuilderAPI_MakeEdge(P(a_), P(b_)).Edge() for a_, b_ in zip(ring[:-1], ring[1:])]
    return edges


def _occ_wire(prims):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeWire
    mw = BRepBuilderAPI_MakeWire()
    for e in _occ_edges(prims):
        mw.Add(e)
    if not mw.IsDone():
        raise ValueError("an outline's pieces do not join end to end")
    w = mw.Wire()
    if not w.Closed():
        raise ValueError("an outline is not closed")
    return w


def _occ_build(grouped, depth, wires):
    """One face or solid per outline (with its holes). An outline the kernel cannot make
    anything valid of is left out and counted, never fatal."""
    items, measure, skipped = [], 0.0, 0
    for outer, holes in grouped:
        try:
            ow = _occ_wire(outer.prims)
            hw = []
            for h in holes:
                try:
                    hw.append(_occ_wire(h.prims))
                except Exception:                              # ValueError, or the kernel's own StdFail
                    skipped += 1
        except Exception:
            skipped += 1
            continue
        if wires:
            items += [ow] + hw
            continue
        try:
            shape, mass = _occ_face_or_solid(ow, hw, depth)
        except Exception:
            skipped += 1
            continue
        if shape is None:
            skipped += 1
            continue
        measure += abs(mass)
        items.append(shape)
    return _occ_collect(items, measure, skipped)


def _occ_face_or_solid(ow, hw, depth):
    """A face with holes, or that face extruded: (shape, area or volume), or (None, 0) if the
    kernel makes nothing valid of it."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.BRepGProp import BRepGProp
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.GProp import GProp_GProps
    from OCP.ShapeFix import ShapeFix_Face
    from OCP.gp import gp_Vec
    mf = BRepBuilderAPI_MakeFace(ow, True)
    for w in hw:
        mf.Add(w)
    fix = ShapeFix_Face(mf.Face())
    fix.Perform()
    fix.FixOrientation()
    face = fix.Face()
    props = GProp_GProps()
    if depth > 0:
        shape = BRepPrimAPI_MakePrism(face, gp_Vec(0.0, 0.0, float(depth))).Shape()
        _S(BRepGProp, "VolumeProperties")(shape, props)
    else:
        shape = face
        _S(BRepGProp, "SurfaceProperties")(shape, props, 1e-6)
    if not BRepCheck_Analyzer(shape).IsValid() or abs(props.Mass()) < 1e-9:
        return None, 0.0
    return shape, props.Mass()


def _occ_collect(items, measure, skipped):
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound
    if not items:
        raise ValueError("Nothing to write: no outline could be built into geometry.")
    if len(items) == 1:
        return items[0], len(items), measure, skipped
    comp = TopoDS_Compound()
    bb = BRep_Builder()
    bb.MakeCompound(comp)
    for it in items:
        bb.Add(comp, it)
    return comp, len(items), measure, skipped


def _occ_write(shape, path, name):
    import os
    import shutil
    import tempfile
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.Interface import Interface_Static
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer
    _quiet()
    for key, val in (("write.step.schema", "AP214IS"), ("write.step.unit", "MM"),
                     ("write.step.product.name", name)):
        try:
            _S(Interface_Static, "SetCVal")(key, val)
        except Exception:
            pass
    wr = STEPControl_Writer()
    if wr.Transfer(shape, STEPControl_AsIs) != IFSelect_RetDone:
        raise ValueError("The CAD kernel could not translate the shape for STEP.")
    target, tmp = str(path), None
    try:
        target.encode("ascii")
    except UnicodeError:                                       # OpenCascade's file output can fail on
        fd, tmp = tempfile.mkstemp(suffix=".step")             # non-ASCII paths (notably on Windows)
        os.close(fd)
    try:
        if wr.Write(tmp or target) != IFSelect_RetDone:
            raise ValueError("Could not write %s" % path)
        if tmp:
            shutil.copyfile(tmp, target)
    finally:
        if tmp and os.path.exists(tmp):
            os.remove(tmp)


# ------------------------------------------------------------ FreeCAD Part --
def _fc_build(Part, grouped, depth, wires):
    """The same with FreeCAD's Part module (used inside FreeCAD, where OCP is not available)."""
    from FreeCAD import Vector
    from .fc_sketch import _geo_safe

    def wire(prims):
        edges = [g.toShape() for p in prims for g in _geo_safe(p)]
        w = Part.Wire(edges)
        if not w.isClosed():
            raise ValueError("an outline is not closed")
        return w

    def make_face(ow, hw):
        """The outline with its holes cut out. Part.Face([outer, hole, ...]) needs every hole to
        run the opposite way round to the outline, and a hole that is one circle never does (a
        circle edge always runs anticlockwise): a plate with a round hole came out invalid and was
        left out (found in FreeCAD 1.0.0, v0.8). So: the face maker FreeCAD's own Pad uses, which
        sorts that out; failing that, cut the holes out one by one. The area is checked either way."""
        solid = Part.Face(ow)
        if not hw:
            return solid
        want = solid.Area - sum(Part.Face(h).Area for h in hw)
        tries = []
        try:
            tries.append(Part.makeFace([ow] + hw, "Part::FaceMakerBullseye"))
        except Exception:
            pass
        try:
            cut = solid
            for h in hw:
                cut = cut.cut(Part.Face(h))
            tries.append(cut)
        except Exception:
            pass
        for t in tries:
            faces = t.Faces
            if len(faces) == 1 and faces[0].isValid() and abs(faces[0].Area - want) <= 1e-6 * max(1.0, solid.Area):
                return faces[0]
        raise ValueError("the holes could not be cut out of an outline")

    items, measure, skipped = [], 0.0, 0
    for outer, holes in grouped:
        try:
            ow = wire(outer.prims)
        except Exception:
            skipped += 1
            continue
        hw = []
        for h in holes:
            try:
                hw.append(wire(h.prims))
            except Exception:
                skipped += 1
        if wires:
            items += [ow] + hw
            continue
        try:
            face = make_face(ow, hw)
            shape = face.extrude(Vector(0, 0, float(depth))) if depth > 0 else face
            if not shape.isValid():
                raise ValueError("invalid")
            measure += abs(shape.Volume if depth > 0 else shape.Area)
            items.append(shape)
        except Exception:
            skipped += 1
    if not items:
        raise ValueError("Nothing to write: no outline could be built into geometry.")
    return (items[0] if len(items) == 1 else Part.makeCompound(items)), len(items), measure, skipped


# ------------------------------------------------------------------- public --
def have_kernel():
    """Which CAD kernel can write the file here: "freecad", "ocp" or None."""
    if _freecad_part() is not None:
        return "freecad"
    return "ocp" if _ocp() else None


def to_step(res, path, depth=0.0, wires=False, include_borderline=False, name="ImageToSketch"):
    """Write the traced outlines of `res` (a pipeline Result) to a STEP file in millimetres.

    depth   thickness of the extrusion in mm; 0 writes flat faces
    wires   write only the curves (ignores depth)
    Returns a report dict: kind ("solid" | "face" | "wire"), pieces, volume_mm3 or area_mm2,
    skipped (outlines that could not be built), kernel.
    Raises ValueError with a plain message if there is nothing to write or no kernel."""
    if depth < 0 or not math.isfinite(depth):
        raise ValueError("The thickness must be zero or more.")
    shapes = [s for s in res.shapes if s.status == "accept" or include_borderline]
    shapes = [s for s in shapes if s.prims]
    if not shapes:
        raise ValueError("Nothing to write: no outline was accepted as real geometry.")
    kernel = have_kernel()
    if kernel is None:
        raise ValueError("Writing STEP needs a CAD kernel: run this inside FreeCAD, or install "
                         "OpenCascade for Python with:  pip install cadquery-ocp")
    grouped = groups(shapes)
    try:
        if kernel == "freecad":
            shape, n, measure, skipped = _fc_build(_freecad_part(), grouped, depth, wires)
        else:
            shape, n, measure, skipped = _occ_build(grouped, depth, wires)
    except ValueError as e:
        if not str(e).startswith("Nothing to write"):
            raise
        raise ValueError(_none_built(res, len(shapes))) from None
    if kernel == "freecad":
        shape.exportStep(str(path))
    else:
        _occ_write(shape, path, name)
    kind = "wire" if wires else ("solid" if depth > 0 else "face")
    rep = dict(kind=kind, pieces=n, skipped=skipped, kernel=kernel, depth=float(depth), path=str(path))
    if kind == "solid":
        rep["volume_mm3"] = measure
    elif kind == "face":
        rep["area_mm2"] = measure
    return rep


def _none_built(res, n):
    """Why there is nothing to write although outlines were accepted, as far as can be told."""
    t = ("Nothing to write: the %d accepted outline%s could not be built into geometry (an outline "
         "that crosses itself or does not close cannot become a face)." % (n, "" if n == 1 else "s"))
    tk = (getattr(res, "info", None) or {}).get("token") or {}
    if tk.get("walls_corrected_mm"):
        t += (" The side-wall correction moved parts of the outline: if the part's height or the camera "
              "distance is not right, that can tangle it. Check both, or untick 'Part has straight "
              "vertical sides', and look at the preview.")
    else:
        t += " Look at the preview: a green outline that runs into a shadow or doubles back is the usual cause."
    return t


def describe(rep):
    """One plain sentence about what to_step wrote."""
    n = rep["pieces"]
    if rep["kind"] == "solid":
        t = "%d solid%s, %.2f mm thick, %.1f mm3" % (n, "" if n == 1 else "s", rep["depth"], rep["volume_mm3"])
    elif rep["kind"] == "face":
        t = "%d flat face%s, %.1f mm2 (no thickness)" % (n, "" if n == 1 else "s", rep["area_mm2"])
    else:
        t = "%d closed curve%s (no faces)" % (n, "" if n == 1 else "s")
    if rep["skipped"]:
        t += "; %d outline%s could not be built and %s left out" % (
            rep["skipped"], "" if rep["skipped"] == 1 else "s", "was" if rep["skipped"] == 1 else "were")
    return t
