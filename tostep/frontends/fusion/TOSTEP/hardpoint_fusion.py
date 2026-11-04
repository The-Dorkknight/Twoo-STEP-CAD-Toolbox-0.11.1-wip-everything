# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""Solid body -> hardpoint STL, for Fusion.

Each face of the body is meshed on its own, so every triangle is known to belong to that
face. If those per-face meshes don't join up into one closed mesh, the body is meshed as a
whole instead (what Fusion's own STL export does) and each of its triangles takes the
face of the nearest per-face triangle. No adsk imports here: it only uses the objects it
is handed.
"""
import math

try:
    from . import hstl
except ImportError:
    import hstl

SURFACES = {"Plane": hstl.T_PLANE, "Cylinder": hstl.T_CYL, "Sphere": hstl.T_SPHERE,
            "Cone": hstl.T_CONE, "Torus": hstl.T_TORUS}
CM_TO_MM = 10.0                       # Fusion's API works in centimetres


def surface_type(face):
    """Plane / cylinder / sphere / cone / torus, anything else is freeform."""
    try:
        return SURFACES.get(face.geometry.objectType.split("::")[-1], hstl.T_FREE)
    except Exception:
        return hstl.T_UNKNOWN


def _mesh(owner, tol_mm, angle_deg):
    calc = owner.meshManager.createMeshCalculator()
    calc.surfaceTolerance = tol_mm / CM_TO_MM
    try:
        calc.maxNormalDeviation = math.radians(angle_deg)
    except Exception:
        pass
    m = calc.calculate()
    return list(m.nodeCoordinatesAsDouble), list(m.nodeIndices)


def collect(bodies, tol_mm, angle_deg):
    """Per-face meshes of all bodies -> (coords, tris, tri_face, face_type, hard_points), in cm."""
    coords, tris, tri_face, face_type, hard = [], [], [], {}, []
    n = 0
    for body in bodies:
        for face in body.faces:
            c, t = _mesh(face, tol_mm, angle_deg)
            base = len(coords) // 3
            coords += c
            tris += [base + i for i in t]
            tri_face += [n] * (len(t) // 3)
            face_type[n] = surface_type(face)
            n += 1
        for v in body.vertices:
            p = v.geometry
            hard.append((p.x, p.y, p.z))
    return coords, tris, tri_face, face_type, hard


def export(bodies, path, tol_mm=0.05, angle_deg=15.0):
    """Write the bodies as one hardpoint STL in mm. -> info dict (see hstl.encode)."""
    coords, tris, tri_face, face_type, hard = collect(bodies, tol_mm, angle_deg)
    if not tris:
        raise ValueError("Fusion returned no triangles for the selected bodies")
    kw = dict(scale=CM_TO_MM, units="mm", tol=tol_mm, angle=angle_deg, by="fusion")
    data, info = hstl.encode(coords, tris, tri_face, face_type, hard, **kw)
    info["method"] = "faces"
    if not info["watertight"]:
        bc, bt = [], []
        for body in bodies:
            c, t = _mesh(body, tol_mm, angle_deg)
            base = len(bc) // 3
            bc += c
            bt += [base + i for i in t]
        if bt:
            bf = hstl.transfer_faces(coords, tris, tri_face, bc, bt)
            data2, info2 = hstl.encode(bc, bt, bf, face_type, hard, **kw)
            worse = info["open_edges"] + info["bad_edges"]
            if info2["watertight"] or info2["open_edges"] + info2["bad_edges"] < worse:
                data, info = data2, info2
                info["method"] = "body"
    with open(path, "wb") as f:
        f.write(data)
    return info


def summary(info):
    kinds = ", ".join(f"{n} {k}" for k, n in sorted(info["types"].items(), key=lambda kv: -kv[1]))
    lines = [f"{info['triangles']:,} triangles, {info['faces']} faces ({kinds}), "
             f"{info['hardpoints']} hardpoints."]
    if info.get("method") == "body":
        lines.append("The faces were matched to the whole-body mesh by position, so a few triangles "
                     "right on a face border may carry the neighbouring face's label.")
    if not info["watertight"]:
        lines.append(f"Warning: the mesh isn't closed ({info['open_edges']} open edges, "
                     f"{info['bad_edges']} bad edges). A slicer may complain.")
    return "\n".join(lines)
