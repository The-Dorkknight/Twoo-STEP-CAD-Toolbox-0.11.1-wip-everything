# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""Solid -> hardpoint STL, for FreeCAD.

FreeCAD meshes the whole shape once (so the result is the same watertight mesh a normal
STL export gives) and remembers which triangles came from which CAD face. Those groups,
the kind of surface each face is, and the shape's vertices go into the STL's spare bytes
(see hstl.py). No GUI code here, so it also runs in FreeCADCmd.
"""
import math

import hstl

SURFACES = {"Plane": hstl.T_PLANE, "Cylinder": hstl.T_CYL, "Sphere": hstl.T_SPHERE,
            "Cone": hstl.T_CONE, "Toroid": hstl.T_TORUS}


def surface_type(face):
    """Plane / cylinder / sphere / cone / torus, anything else is freeform."""
    try:
        return SURFACES.get(type(face.Surface).__name__, hstl.T_FREE)
    except Exception:
        return hstl.T_UNKNOWN


def global_shape(obj):
    """The object's shape where it really sits (placements of parent containers included)."""
    s = obj.Shape.copy()
    try:
        s.Placement = obj.getGlobalPlacement()
    except Exception:
        pass
    return s


def collect(shapes, deviation=0.05, angle_deg=15.0, mesher=None):
    """-> (coords, tris, tri_face, face_type, hard_points, notes) for hstl.encode()."""
    if mesher is None:
        import MeshPart
        mesher = MeshPart.meshFromShape
    coords, tris, tri_face, face_type, hard, notes = [], [], [], {}, [], []
    n_faces = 0
    for shape in shapes:
        try:
            mesh = mesher(Shape=shape, LinearDeflection=float(deviation),
                          AngularDeflection=math.radians(float(angle_deg)), Relative=False, Segments=True)
        except TypeError as e:
            raise ValueError("this FreeCAD version can't report which triangles belong to which face "
                             f"(MeshPart.meshFromShape has no 'Segments' option): {e}")
        pts, facets = mesh.Topology
        base = len(coords) // 3
        for p in pts:
            coords += (p.x, p.y, p.z)
        n_seg = mesh.countSegments()
        seg_of = [-1] * len(facets)
        for si in range(n_seg):
            for fi in mesh.getSegment(si):
                seg_of[fi] = si
        faces = shape.Faces
        if n_seg == len(faces):           # one group of triangles per CAD face, in face order
            for si in range(n_seg):
                face_type[n_faces + si] = surface_type(faces[si])
        else:
            notes.append(f"FreeCAD gave {n_seg} triangle groups for {len(faces)} faces, so the faces "
                         "are labelled but their surface types are left as 'unknown'.")
        for fi, t in enumerate(facets):
            tris += (base + t[0], base + t[1], base + t[2])
            tri_face.append(n_faces + seg_of[fi] if seg_of[fi] >= 0 else -1)
        n_faces += n_seg
        for v in shape.Vertexes:
            hard.append((v.X, v.Y, v.Z))
    return coords, tris, tri_face, face_type, hard, notes


def export(shapes, path, deviation=0.05, angle_deg=15.0, mesher=None):
    """Write the shapes (already in global coordinates) as one hardpoint STL. -> info dict."""
    coords, tris, tri_face, face_type, hard, notes = collect(shapes, deviation, angle_deg, mesher)
    if not tris:
        raise ValueError("FreeCAD couldn't mesh the selected shape")
    info = hstl.write(path, coords, tris, tri_face, face_type, hard, units="mm", tol=deviation,
                      angle=angle_deg, by="freecad")
    info["notes"] = notes
    return info


def summary(info):
    kinds = ", ".join(f"{n} {k}" for k, n in sorted(info["types"].items(), key=lambda kv: -kv[1]))
    lines = [f"{info['triangles']:,} triangles, {info['faces']} faces ({kinds}), "
             f"{info['hardpoints']} hardpoints."]
    if not info["watertight"]:
        lines.append(f"Warning: the mesh isn't closed ({info['open_edges']} open edges, "
                     f"{info['bad_edges']} bad edges). A slicer may complain — check the solid.")
    lines += info.get("notes", [])
    return "\n".join(lines)
