# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""
Jetwash — clean junk out of STEP files.

Removes the debris old CAD models accumulate: micro-steps left by cut-extrudes that
were a hair off (0.0001 mm walls), sliver faces, knife-edge fins, tiny chamfers,
pin-holes, loose specks of solid, and faces/edges split for no reason.

How: faces smaller or thinner than the threshold are found and grouped into features;
each feature is removed with OpenCascade defeaturing, which deletes the faces and
heals the gap by extending the neighbouring faces until they meet. Micro-steps and
thin fins pull in the neighbouring face that has to move so the healing is possible.
Every removal is checked (valid solid, small volume change) and rolled back if not.
Finally split faces/edges are merged and tiny edges dropped.

Coarseness
  1 gentle   : only near-zero junk (< threshold/10), loose specks, merge splits
  2 normal   : everything thinner/smaller than the threshold
  3 aggressive: also holes, bosses, fillets and chamfers below the threshold
"""
from __future__ import annotations

import os
import tempfile
import time
from dataclasses import dataclass, field

import numpy as np

from tostep.plugins.mesh2step import classic as core
from tostep.plugins.mesh2step.classic import _S, _cast, _explore

from OCP.BRepAlgoAPI import BRepAlgoAPI_Defeaturing
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepGProp import BRepGProp
from OCP.BRep import BRep_Builder
from OCP.Bnd import Bnd_Box
from OCP.GProp import GProp_GProps
from OCP.GeomAbs import GeomAbs_Plane, GeomAbs_Cylinder, GeomAbs_Cone, GeomAbs_Sphere, GeomAbs_Torus
from OCP.ShapeFix import ShapeFix_Shape, ShapeFix_Wireframe
from OCP.TopAbs import TopAbs_FACE, TopAbs_EDGE, TopAbs_SOLID, TopAbs_SHELL, TopAbs_REVERSED
from OCP.TopoDS import TopoDS_Compound


@dataclass
class WashReport:
    ok: bool = False
    out_path: str = ""
    faces_before: int = 0
    faces_after: int = 0
    solids_before: int = 0
    solids_after: int = 0
    specks_removed: int = 0
    features_removed: int = 0
    features_kept: int = 0
    volume_before: float = 0.0
    volume_after: float = 0.0
    valid_before: bool = False
    valid_after: bool = False
    seconds: float = 0.0
    notes: list = field(default_factory=list)

    def summary(self):
        dv = 0.0
        if self.volume_before:
            dv = 100.0 * (self.volume_after - self.volume_before) / self.volume_before
        s = [f"Faces: {self.faces_before} → {self.faces_after}   Bodies: {self.solids_before} → {self.solids_after}",
             f"Removed: {self.features_removed} artifact feature(s), {self.specks_removed} loose speck(s)"]
        if self.features_kept:
            s.append(f"Left alone: {self.features_kept} candidate(s) that could not be removed cleanly")
        s.append(f"Volume: {self.volume_before:.4f} → {self.volume_after:.4f} mm³ ({dv:+.4f}%)")
        s.append(f"Geometry check: before {'valid' if self.valid_before else 'INVALID'}, "
                 f"after {'valid' if self.valid_after else 'INVALID'}")
        s.append(f"Time: {self.seconds:.1f} s")
        return "\n".join(s)


# ---------------------------------------------------------------- face analysis
class _FaceInfo:
    __slots__ = ("box", "face", "area", "perim", "width", "diag", "kind", "normal", "offset", "radius", "key")

    def __init__(self, face):
        self.face = face
        g = GProp_GProps(); _S(BRepGProp, "SurfaceProperties")(face, g)
        self.area = abs(g.Mass())
        g2 = GProp_GProps(); _S(BRepGProp, "LinearProperties")(face, g2)
        self.perim = g2.Mass()
        self.width = 2.0 * self.area / self.perim if self.perim > 0 else 0.0
        b = Bnd_Box(); _S(BRepBndLib, "Add")(face, b)
        self.box = b
        if b.IsVoid():
            self.diag = 0.0
        else:
            self.diag = b.CornerMin().Distance(b.CornerMax())
        a = BRepAdaptor_Surface(face)
        t = a.GetType()
        self.normal = None; self.offset = 0.0; self.radius = None
        if t == GeomAbs_Plane:
            self.kind = "plane"
            ax = a.Plane().Axis()
            d = ax.Direction(); p = ax.Location()
            n = np.array([d.X(), d.Y(), d.Z()])
            if face.Orientation() == TopAbs_REVERSED:
                n = -n
            self.normal = n
            self.offset = float(n @ np.array([p.X(), p.Y(), p.Z()]))
        elif t == GeomAbs_Cylinder:
            self.kind = "cyl"; self.radius = a.Cylinder().Radius()
        elif t == GeomAbs_Sphere:
            self.kind = "sph"; self.radius = a.Sphere().Radius()
        elif t == GeomAbs_Torus:
            self.kind = "tor"; self.radius = a.Torus().MinorRadius()
        elif t == GeomAbs_Cone:
            self.kind = "cone"
        else:
            self.kind = "free"
        c = g.CentreOfMass()          # (the same surface integral as the area: computed once)
        self.key = (round(c.X(), 4), round(c.Y(), 4), round(c.Z(), 4), round(self.area, 6))


def _faces(shape):
    return [_cast("Face", f) for f in _explore(shape, TopAbs_FACE)]


def _adjacency(faces):
    by_edge = {}
    for i, f in enumerate(faces):
        for e in _explore(f, TopAbs_EDGE):
            by_edge.setdefault(hash(e), set()).add(i)
    nb = [set() for _ in faces]
    for s in by_edge.values():
        for i in s:
            nb[i] |= s - {i}
    return nb


def _volume(shape):
    try:
        return core._volume(shape)
    except Exception:
        return 0.0


def _bbox_diag(shape):
    b = Bnd_Box(); _S(BRepBndLib, "Add")(shape, b)
    if b.IsVoid():
        return 0.0
    return b.CornerMin().Distance(b.CornerMax())


# ---------------------------------------------------------------- detection
def _candidates(infos, nb, thr, level):
    """Return list of removal groups (lists of face indices), each one artifact feature."""
    t = thr / 10.0 if level <= 1 else thr
    base = set()
    for i, fi in enumerate(infos):
        if fi.width < t or fi.diag < t:
            base.add(i)
        elif level >= 3:
            if fi.radius is not None and fi.radius < thr:
                base.add(i)          # pin-holes, tiny bosses, small fillets
            elif fi.kind == "cone" and fi.width < 3 * thr:
                base.add(i)          # countersink / chamfer cones
            elif fi.diag < 3 * thr and fi.width < 1.5 * thr:
                base.add(i)          # small chamfers and nubs
    links, fins, swaps = [], [], {}
    # thin walls (fins) and thin slots: two opposite parallel planes closer than t
    # that overlap each other
    planes = [i for i, fi in enumerate(infos) if fi.normal is not None]
    if planes:
        Nn = np.array([infos[i].normal for i in planes]); Of = np.array([infos[i].offset for i in planes])
        dots = Nn @ Nn.T
        gap = np.abs(Of[:, None] + Of[None, :])
        ii, jj = np.where(np.triu((dots < -0.99999) & (gap < t) & (gap > 1e-12), 1))
        for a, b in zip(ii, jj):
            A, B = infos[planes[a]], infos[planes[b]]
            bb = Bnd_Box(); bb.Add(A.box); bb.Enlarge(t)
            if not bb.IsOut(B.box):
                base.update((planes[a], planes[b]))
                links.append((planes[a], planes[b]))
                # material between the walls (fin) if their normals point away from each other
                pa = A.normal * A.offset; pb = B.normal * B.offset
                if (pb - pa) @ A.normal < 0:
                    fins.append((planes[a], planes[b]))
    if not base:
        return []
    groups_extra = {}

    def parallel(a, b, same):
        if a.normal is None or b.normal is None:
            return False
        d = float(a.normal @ b.normal)
        return d > 0.99999 if same else d < -0.99999

    # micro-steps: a thin face between two parallel, same-facing planes offset by < t.
    # Remove the smaller plane too, so the larger one can extend over it.
    # knife-edge fins: two opposite planes closer than t next to a thin face.
    pair_of = {}
    for a, b in links:
        pair_of.setdefault(a, set()).add(b); pair_of.setdefault(b, set()).add(a)
    for i in list(base):
        # a fin's two walls together touch the floors on both sides
        ring = set(nb[i])
        for p in pair_of.get(i, ()):
            ring |= nb[p]
        planes = sorted(j for j in ring if infos[j].normal is not None and j not in base)
        extra = set()
        for a_i in range(len(planes)):
            for b_i in range(a_i + 1, len(planes)):
                A, B = infos[planes[a_i]], infos[planes[b_i]]
                if parallel(A, B, True) and 1e-9 < abs(A.offset - B.offset) < t:
                    small, big = (planes[a_i], planes[b_i]) if A.area < B.area else (planes[b_i], planes[a_i])
                    extra.add(small)
                    swaps[small] = big
                elif parallel(A, B, False) and abs(A.offset + B.offset) < t:
                    extra.update((planes[a_i], planes[b_i]))
        if extra:
            groups_extra[i] = extra
    allrem = set(base)
    for i, e in groups_extra.items():
        allrem |= e
        links.extend((i, j) for j in e)
    # features = connected components of the removal set (by shared edges or by links)
    link_nb = {}
    for a, b in links:
        link_nb.setdefault(a, set()).add(b); link_nb.setdefault(b, set()).add(a)
    groups, seen = [], set()
    for i in sorted(allrem):
        if i in seen:
            continue
        comp, stack = [], [i]
        seen.add(i)
        while stack:
            k = stack.pop(); comp.append(k)
            for j in nb[k] | link_nb.get(k, set()):
                if j in allrem and j not in seen:
                    seen.add(j); stack.append(j)
        groups.append(comp)
    # never remove (nearly) a whole body
    total = sum(fi.area for fi in infos)
    groups = [g for g in groups if sum(infos[k].area for k in g) < 0.5 * total and len(g) < 0.5 * len(infos)]
    groups.sort(key=lambda g: sum(infos[k].area for k in g))
    out = []
    for g in groups:
        alts = []
        for k in g:
            if k in swaps and swaps[k] not in g:
                alts.append(sorted(set(g) - {k} | {swaps[k]}))
        out.append((g, [f for f in fins if f[0] in g], alts))
    return out


def _defeature(solid, faces):
    df = BRepAlgoAPI_Defeaturing()
    df.SetShape(solid)
    for f in faces:
        df.AddFaceToRemove(f)
    try:
        df.SetToFillHistory(False)
    except Exception:
        pass
    df.Build()
    if not df.IsDone():
        return None
    return df.Shape()


def _cut_fin(solid, A, B, thr):
    """Cut away the material between two close, outward-facing parallel walls."""
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.gp import gp_Ax2, gp_Pnt, gp_Dir
    from OCP.TopAbs import TopAbs_VERTEX
    from OCP.BRep import BRep_Tool
    n = A.normal  # points out of the fin on A's side
    u = np.cross(n, [1, 0, 0] if abs(n[0]) < 0.9 else [0, 1, 0]); u /= np.linalg.norm(u)
    v = np.cross(n, u)

    def pts(face):
        return np.array([[p.X(), p.Y(), p.Z()] for p in
                         (_S(BRep_Tool, "Pnt")(_cast("Vertex", x)) for x in _explore(face, TopAbs_VERTEX))])
    pa, pb = pts(A.face), pts(B.face)
    if len(pa) < 3 or len(pb) < 3:
        return None
    lo = np.maximum([(pa @ u).min(), (pa @ v).min()], [(pb @ u).min(), (pb @ v).min()])
    hi = np.minimum([(pa @ u).max(), (pa @ v).max()], [(pb @ u).max(), (pb @ v).max()])
    if np.any(hi - lo <= 0):
        return None
    ha = A.offset                      # plane A: n.x = ha ; fin material lies at n.x < ha
    hb = -B.offset                     # plane B: n.x = hb (B's normal is -n)
    thick = ha - hb
    if thick <= 0:
        return None
    eps = max(1e-6, min(0.25 * thr, thick))
    o = lo[0] * u + lo[1] * v + (hb - eps) * n
    box = BRepPrimAPI_MakeBox(gp_Ax2(gp_Pnt(*map(float, o)), gp_Dir(*map(float, n)), gp_Dir(*map(float, u))),
                              float(hi[0] - lo[0]), float(hi[1] - lo[1]), float(thick + 2 * eps)).Shape()
    cut = BRepAlgoAPI_Cut(solid, box)
    if not cut.IsDone():
        return None
    return cut.Shape()


_VOL = {}


def _vol_of(shape):
    """Volume of a solid, remembered for the one being washed (it is asked for again and again)."""
    key = id(shape)
    got = _VOL.get(key)
    if got is not None and got[0] is shape:
        return got[1]
    v = _volume(shape)
    if len(_VOL) > 8:
        _VOL.clear()
    _VOL[key] = (shape, v)
    return v


def _accept(old, new, removed_area, thr, grow=True):
    if new is None or new.IsNull():
        return False
    if core._count_faces(new) == core._count_faces(old) and abs(_vol_of(new) - _vol_of(old)) < 1e-12:
        return False  # nothing actually changed
    solids = list(_explore(new, TopAbs_SOLID))
    if len(solids) != 1:
        return False
    # the volume test first (0.05 s) and the full geometry check (0.15 s) only for what passes
    # it: the same answer, a third of the time on a body where most removals are rejected (0.10.0)
    v0, v1 = _vol_of(old), _vol_of(new)
    allowed = 1.0 * thr * removed_area + thr ** 3 + 1e-9 * v0
    if not grow and v1 > v0 + 1e-9 * v0:
        return False
    if abs(v1 - v0) > allowed:
        return False
    return bool(BRepCheck_Analyzer(new).IsValid())


FAST_FACES = 16        # most faces removed in one go: defeaturing costs about 0.3 s a face up to here on
                       # the test tray and far more beyond (81 faces: 38 s), so a big batch that fails
                       # costs more than removing its features one at a time would


def _clean_solid(solid, thr, level, log, rep, budget=200):
    failed = set()
    tried = 0
    fast = True            # the several-at-once shortcut (up to FAST_FACES faces): until it fails; it comes
                           # back after four single removals in a row succeed (0.10.0: it used to try every
                           # candidate at once, once, and on a body with 81 candidate faces lost 38 s to that)
    streak = 0
    t0 = time.time()
    last_note = t0
    for _ in range(budget):
        faces = _faces(solid)
        infos = [_FaceInfo(f) for f in faces]
        nb = _adjacency(faces)
        cands = _candidates(infos, nb, thr, level)
        cands = [c_ for c_ in cands if tuple(sorted(infos[k].key for k in c_[0])) not in failed]
        groups = [c_[0] for c_ in cands]
        fins = {tuple(c_[0]): c_[1] for c_ in cands}
        alts = {tuple(c_[0]): c_[2] for c_ in cands}
        if not groups:
            break
        progressed = False
        # fins: cut the paper-thin wall away (defeaturing would fill the pockets instead)
        for g in groups:
            for a, b in fins.get(tuple(g), []):
                new = _try(_cut_fin, solid, infos[a], infos[b], thr)
                if new is not None and _accept(solid, new, infos[a].area + infos[b].area, thr, grow=False):
                    solid = _tidy(new, thr)
                    rep.features_removed += 1
                    progressed = True
                    break
            if progressed:
                break
        if progressed:
            continue
        # try everything at once first (fast path), then one feature at a time. Once that has
        # failed for this body it is not tried again (0.7.2): on a part with many candidates it
        # cost a minute each time and kept failing.
        if fast and len(groups) > 1 and not any(fins.values()):
            batch, allf = [], []
            for g in groups:
                if allf and len(allf) + len(g) > FAST_FACES:
                    break
                batch.append(g); allf.extend(g)
            if len(batch) > 1:
                new = _try(_defeature, solid, [faces[k] for k in allf])
                if _accept(solid, new, sum(infos[k].area for k in allf), thr):
                    solid = _tidy(new, thr)
                    rep.features_removed += len(batch)
                    continue
            fast = False
            streak = 0
        for g in groups:
            variants = [g] + alts.get(tuple(g), [])
            thin = [k for k in g if infos[k].width < thr or infos[k].diag < thr]
            if thin and len(thin) < len(g):
                variants.append(thin)       # without the pulled-in neighbours
            tried += 1
            if time.time() - last_note > 20:
                last_note = time.time()
                log(f"  still washing: {rep.features_removed} removed, {len(failed)} left alone, "
                    f"{tried} tried so far ({time.time() - t0:.0f} s)")
            for var in variants:
                new = _try(_defeature, solid, [faces[k] for k in var])
                if _accept(solid, new, sum(infos[k].area for k in var), thr):
                    solid = _tidy(new, thr)
                    rep.features_removed += 1
                    progressed = True
                    streak += 1
                    if streak >= 4:
                        fast = True         # removals are succeeding: batches again
                    break
            if progressed:
                break  # topology changed — re-detect on the new solid
            streak = 0
            failed.add(tuple(sorted(infos[k].key for k in g)))
        if not progressed:
            break
    return solid, len(failed)


def _tidy(shape, thr):
    """Merge faces/edges the last operation left split, return the solid."""
    u = core._unify(shape, min(1e-4, 0.01 * thr))
    sol = next(_explore(u, TopAbs_SOLID), None)
    if sol is None or not BRepCheck_Analyzer(sol).IsValid():
        sol = next(_explore(shape, TopAbs_SOLID))
    return _cast("Solid", sol)


def _try(fn, *a):
    try:
        return fn(*a)
    except Exception:
        return None


# ---------------------------------------------------------------- entry point
def jetwash(in_path, out_path, threshold=0.05, coarseness=2, log=None, progress=None,
            stl_params: core.Params | None = None) -> WashReport:
    """Clean a STEP file (or convert an STL first, then clean). threshold in mm."""
    log = log or (lambda s: None)
    progress = progress or (lambda f: None)
    rep = WashReport(out_path=str(out_path))
    t0 = time.time()
    ext = os.path.splitext(str(in_path))[1].lower()
    if ext in (".stl", ".obj", ".ply", ".3mf", ".off"):
        log("Mesh input — converting to STEP first…")
        tmp = os.path.join(tempfile.gettempdir(), "jetwash_" + os.path.basename(str(in_path)) + ".step")
        prm = stl_params or core.Params()
        prm.verify = False
        core.convert(in_path, tmp, prm, log=lambda s: log("  " + s),
                     progress=lambda f: progress(0.4 * f))
        in_path = tmp
    shape = core.read_step(in_path)
    rep.faces_before = core._count_faces(shape)
    solids = [_cast("Solid", s) for s in _explore(shape, TopAbs_SOLID)]
    rep.solids_before = len(solids)
    rep.volume_before = sum(_volume(s) for s in solids)
    rep.valid_before = BRepCheck_Analyzer(shape).IsValid()
    log(f"Loaded {rep.faces_before} faces in {rep.solids_before} bod{'y' if rep.solids_before == 1 else 'ies'}"
        f"{'' if rep.valid_before else ' (file has geometry errors — fixing)'}")
    fix = ShapeFix_Shape(shape); fix.Perform(); shape = fix.Shape()
    solids = [_cast("Solid", s) for s in _explore(shape, TopAbs_SOLID)]
    progress(0.45)

    builder = BRep_Builder()
    out = TopoDS_Compound(); builder.MakeCompound(out)
    # loose specks: bodies smaller than the threshold
    keep = []
    for s in solids:
        if _bbox_diag(s) < thr_speck(threshold, coarseness) or _volume(s) < threshold ** 3:
            rep.specks_removed += 1
        else:
            keep.append(s)
    if not solids:
        # surfaces only (no solids): keep shells as they are, just merge/fix below
        for sh in _explore(shape, TopAbs_SHELL):
            builder.Add(out, sh)
    if rep.specks_removed:
        log(f"Removed {rep.specks_removed} loose speck(s)")
    keep = _join_near_bodies(keep, threshold, coarseness, log, rep)
    for i, s in enumerate(keep):
        log(f"Washing body {i + 1}/{len(keep)}…")
        s2, kept = _clean_solid(s, threshold, coarseness, log, rep)
        rep.features_kept += kept
        s2 = core._unify(s2, min(1e-4, 0.01 * threshold))
        builder.Add(out, s2)
        progress(0.45 + 0.45 * (i + 1) / max(1, len(keep)))
    shape = out
    # tidy: merge split faces/edges, drop tiny edges
    shape = core._unify(shape, min(1e-4, 0.01 * threshold))
    try:
        wf = ShapeFix_Wireframe(shape)
        wf.SetPrecision(min(threshold * 0.5, 0.01))
        wf.FixSmallEdges(); wf.FixWireGaps()
        s3 = wf.Shape()
        if BRepCheck_Analyzer(s3).IsValid() or not BRepCheck_Analyzer(shape).IsValid():
            shape = s3
    except Exception:
        pass
    fix = ShapeFix_Shape(shape); fix.Perform(); shape = fix.Shape()
    log("Writing STEP…")
    core.write_step(shape, out_path)
    back = core.read_step(out_path)
    rep.faces_after = core._count_faces(back)
    sols = list(_explore(back, TopAbs_SOLID))
    rep.solids_after = len(sols)
    rep.volume_after = sum(_volume(s) for s in sols)
    rep.valid_after = BRepCheck_Analyzer(back).IsValid()
    rep.seconds = time.time() - t0
    rep.ok = True
    progress(1.0)
    log(rep.summary())
    return rep


def _join_near_bodies(solids, thr, level, log, rep):
    """Fuse bodies that almost touch (gap below threshold) — e.g. a boss extruded a hair short."""
    if len(solids) < 2:
        return solids
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
    try:
        from OCP.collections import List_TopoDS_Shape as TopTools_ListOfShape
    except ImportError:
        from OCP.TopTools import TopTools_ListOfShape
    gap_max = thr / 10.0 if level <= 1 else thr
    solids = list(solids)
    joined = 0
    changed = True
    while changed and len(solids) > 1:
        changed = False
        for i in range(len(solids)):
            for j in range(i + 1, len(solids)):
                d = BRepExtrema_DistShapeShape(solids[i], solids[j])
                if not d.IsDone() or d.Value() > gap_max:
                    continue
                fu = BRepAlgoAPI_Fuse()
                a1 = TopTools_ListOfShape(); a1.Append(solids[i])
                a2 = TopTools_ListOfShape(); a2.Append(solids[j])
                fu.SetArguments(a1); fu.SetTools(a2)
                fu.SetFuzzyValue(max(d.Value() * 1.5, 1e-7))
                fu.Build()
                if not fu.IsDone():
                    continue
                res = [_cast("Solid", x) for x in _explore(fu.Shape(), TopAbs_SOLID)]
                if len(res) == 1 and BRepCheck_Analyzer(res[0]).IsValid():
                    solids = [s for k, s in enumerate(solids) if k not in (i, j)] + res
                    joined += 1
                    changed = True
                    break
            if changed:
                break
    if joined:
        log(f"Joined {joined} pair(s) of bodies that were almost touching")
        rep.notes.append(f"joined {joined} near-touching bodies")
    return solids


def thr_speck(threshold, coarseness):
    return threshold * (1.0 if coarseness <= 1 else 3.0 if coarseness == 2 else 10.0)
