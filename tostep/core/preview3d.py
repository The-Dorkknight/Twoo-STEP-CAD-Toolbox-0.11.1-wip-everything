# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""
preview3d — triangles + colours for the app's 3D preview of a result STEP (and the
original mesh, for comparison). Colours: blue = flat, green = cylinder/sphere/cone,
light grey = smooth NURBS surface, dark = kept as exact facets. Each face gets a slightly
different shade so the face borders show.
"""
from __future__ import annotations

import base64

import numpy as np

COL = {"plane": (0.36, 0.55, 0.95), "round": (0.33, 0.78, 0.45), "nurbs": (0.82, 0.83, 0.86),
       "facet": (0.20, 0.21, 0.24)}


def _b64(a, dtype):
    return base64.b64encode(np.ascontiguousarray(a, dtype=dtype).tobytes()).decode("ascii")


def step_preview(path, max_tris=250_000):
    from tostep.plugins.mesh2step import classic as core
    from OCP.BRep import BRep_Tool
    from OCP.TopLoc import TopLoc_Location
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.BRepAdaptor import BRepAdaptor_Surface, BRepAdaptor_Curve
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    shape = core.read_step(path)
    box = Bnd_Box(); core._S(BRepBndLib, "Add")(shape, box)
    diag = box.CornerMin().Distance(box.CornerMax()) if not box.IsVoid() else 1.0
    BRepMesh_IncrementalMesh(shape, max(diag * 1.5e-3, 1e-3), False, 0.35, True)
    P, C = [], []
    counts = {k: 0 for k in COL}
    rng = np.random.default_rng(7)
    for f in core._explore(shape, core.TopAbs_FACE):
        face = core._cast("Face", f)
        loc = TopLoc_Location()
        tri = core._S(BRep_Tool, "Triangulation")(face, loc)
        if tri is None or tri.NbTriangles() == 0:
            continue
        t = BRepAdaptor_Surface(face).GetType().name
        if t.endswith("Plane"):
            # a facet is a flat face with three straight edges (a disc cut by three arcs is not)
            es = [core._cast("Edge", e) for e in core._explore(face, TopAbs_EDGE)]
            straight = len(es) == 3 and all(BRepAdaptor_Curve(e).GetType().name.endswith("Line") for e in es)
            kind = "facet" if straight else "plane"
        elif t.endswith(("Cylinder", "Sphere", "Cone", "Torus", "Revolution", "Extrusion")):
            kind = "round"      # (lathe and swept surfaces are exact shapes too)
        else:
            kind = "nurbs"
        counts[kind] += 1
        trsf = loc.Transformation()
        V = np.array([[q.X(), q.Y(), q.Z()] for q in (tri.Node(i).Transformed(trsf) for i in range(1, tri.NbNodes() + 1))])
        T = np.array([tri.Triangle(i).Get() for i in range(1, tri.NbTriangles() + 1)]) - 1
        if face.Orientation().name.endswith("REVERSED"):
            T = T[:, ::-1]
        P.append(V[T].reshape(-1, 3))
        shade = 1.0 if kind == "facet" else rng.uniform(0.86, 1.0)
        C.append(np.tile(np.array(COL[kind]) * shade, (len(T) * 3, 1)))
    if not P:
        raise ValueError("Nothing to show")
    P = np.vstack(P); C = np.vstack(C)
    n = len(P) // 3
    if n > max_tris:                       # keep it quick for the browser
        keep = np.sort(np.random.default_rng(0).choice(n, max_tris, replace=False))
        idx = (keep[:, None] * 3 + np.arange(3)).ravel()
        P, C = P[idx], C[idx]
    return {"pos": _b64(P, np.float32), "col": _b64(C, np.float32), "tris": int(len(P) // 3),
            "counts": counts}


def mesh_preview(path, scale=1.0, max_tris=250_000):
    import trimesh
    m = trimesh.load(path, force="mesh", process=True)
    if scale != 1.0:
        m.apply_scale(scale)
    F = m.faces
    if len(F) > max_tris:
        try:
            import pyfqmr
            s = pyfqmr.Simplify(); s.setMesh(m.vertices, F)
            s.simplify_mesh(target_count=max_tris, aggressiveness=5, preserve_border=True, verbose=False)
            v, F, _ = s.getMesh(); m = trimesh.Trimesh(v, F)
        except Exception:
            F = F[np.random.default_rng(0).choice(len(F), max_tris, replace=False)]
    P = m.vertices[m.faces].reshape(-1, 3)
    C = np.tile([0.93, 0.62, 0.30], (len(P), 1))
    return {"pos": _b64(P, np.float32), "col": _b64(C, np.float32), "tris": int(len(P) // 3)}
