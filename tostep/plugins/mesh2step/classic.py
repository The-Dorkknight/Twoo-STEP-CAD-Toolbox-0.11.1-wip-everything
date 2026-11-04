# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""
mesh2step.classic — convert triangle meshes (STL/OBJ/PLY/3MF) into solid STEP models.

Two modes
---------
faceted  : Every triangle becomes an exact planar face; coplanar areas are merged
           into single faces. Result matches the mesh exactly. Fast and robust.
surface  : "Reverse engineering". Flat regions become true planes, cylinders and
           spheres become analytic surfaces, and the remaining organic geometry is
           split into patches that are each fitted with a smooth NURBS surface.
           Patches that can't be fitted within tolerance fall back to facets.

Pipeline (surface mode)
  1. load + repair mesh (merge vertices, fix normals, fill holes), optional
     decimate / denoise
  2. detect planar regions by region growing; snap their vertices exactly onto
     the fitted planes (and plane-plane intersection lines)
  3. split the rest at sharp creases into smooth components; test each for a
     cylinder or sphere fit
  4. partition components into disk-shaped patches with limited bend
     (geodesic Voronoi + Lloyd relaxation, recursive)
  5. extract boundary chains between regions -> fit each as line / arc / B-spline
  6. build faces: planes and primitives trimmed by the chains, NURBS patches via
     plate filling with boundary + interior point constraints
  7. check each patch's deviation from the mesh, refine bad patches, fall back
     to facets where needed
  8. sew, make solid, fix orientation, merge same-surface faces, write STEP
"""
from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass, field

import numpy as np
import trimesh

from tostep.plugins.mesh2step import meshfix                     # trimesh.repair.fix_normals without the pure-Python graph walk
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components, dijkstra

# ---------------------------------------------------------------- OCP imports
from OCP.gp import gp_Pnt, gp_Pnt2d, gp_Dir, gp_Pln, gp_Ax3
from OCP.BRepClass import BRepClass_FaceClassifier
from OCP.BRep import BRep_Builder, BRep_Tool
from OCP.BRepBuilderAPI import (BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakeVertex,
                                BRepBuilderAPI_MakeWire, BRepBuilderAPI_MakeSolid, BRepBuilderAPI_Sewing)
from OCP.BRepFill import BRepFill_Filling
from OCP.BRepBndLib import BRepBndLib
from OCP.Bnd import Bnd_Box
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.BRepLib import BRepLib
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.IMeshTools import IMeshTools_Parameters
from OCP.BRepTools import BRepTools
from OCP.GC import GC_MakeArcOfCircle, GC_MakeSegment
from OCP.GeomAPI import GeomAPI_PointsToBSpline, GeomAPI_ProjectPointOnSurf
from OCP.GeomAbs import GeomAbs_C0, GeomAbs_C2
from OCP.Geom import Geom_CylindricalSurface, Geom_SphericalSurface, Geom_BSplineSurface
from OCP.ShapeFix import ShapeFix_Face, ShapeFix_Shell, ShapeFix_Solid, ShapeFix_Shape
from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain
from OCP.STEPControl import STEPControl_Writer, STEPControl_AsIs, STEPControl_Reader
from OCP.Interface import Interface_Static
from OCP.IFSelect import IFSelect_RetDone
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_SHELL, TopAbs_SOLID, TopAbs_VERTEX, TopAbs_OUT
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Shell, TopoDS_Compound
from OCP.TopLoc import TopLoc_Location

try:  # OCP >= 7.8 / 8.x
    from OCP.collections import (Array1_gp_Pnt as _ArrPnt, Array2_gp_Pnt as _ArrPnt2,
                                 Array1_double as _ArrReal, Array1_int as _ArrInt)
except ImportError:  # older OCP
    from OCP.TColgp import TColgp_Array1OfPnt as _ArrPnt, TColgp_Array2OfPnt as _ArrPnt2
    from OCP.TColStd import TColStd_Array1OfReal as _ArrReal, TColStd_Array1OfInteger as _ArrInt


def _S(cls, name):
    """Static-method compat: OCP exposes statics as Name_s in some versions."""
    return getattr(cls, name + "_s", None) or getattr(cls, name)


def _cast(kind, shape):
    fn = getattr(TopoDS, kind, None) or getattr(TopoDS, kind + "_s")
    return fn(shape)


def _explore(shape, kind):
    ex = TopExp_Explorer(shape, kind)
    while ex.More():
        yield ex.Current()
        ex.Next()


# ---------------------------------------------------------------- parameters
@dataclass
class Params:
    mode: str = "surface"          # "surface" or "faceted"
    tol: float = 0.0               # mm, 0 = auto (0.05% of part size, >= 0.01)
    scale: float = 1.0             # multiply mesh coordinates (e.g. 25.4 for inch)
    plane_angle: float = 3.0       # deg, max normal deviation inside a flat region
    crease_angle: float = 30.0     # deg, dihedral angle treated as a sharp edge
    patch_size: float = 0.30       # max NURBS patch extent as fraction of part size
    max_bend: float = 60.0         # deg, max normal cone of a single NURBS patch
    fit_primitives: bool = True    # try cylinders / spheres
    decimate: int = 0              # target triangle count (0 = keep)
    smooth: int = 0                # Taubin smoothing iterations (for noisy scans)
    max_points: int = 80           # interior point constraints per NURBS patch
    refine_rounds: int = 3
    verify: bool = True
    memory_guard: bool = True      # auto-reduce meshes too big for this computer's RAM
    rough: bool = False            # roughing stage: claim lathe shapes (surfaces of revolution) as
                                   # exact faces before the cylinder/sphere search and patch fitting
    use_labels: bool = True        # hardpoint STL input: use the face labels in the file (see hstl.py)
    save_labelled: str = ""        # path: also write the input mesh as a hardpoint STL of the faces found
    organic_tol: float = 0.0       # mm; > 0 = "guesstimate" mode: freeform (NURBS) areas may be this far
                                   # off inside a patch. Flats, cylinders, spheres and all edges keep `tol`.
                                   # Also reduces dense meshes within tol/3 first and skips the slow filler.
    organic_used: float = 0.0      # (internal) share of organic_tol already spent by mesh reduction
    # --- EXPERIMENTAL Ladder engine (see ladder.py): easy things first, caps on the rest
    ladder: bool = False           # look at the mesh first and pick a route (prismatic / lathe / organic / mixed)
    ladder_rough: bool = True      # ladder, prismatic route: claim extruded outlines first (Staged roughing);
                                   # the engine falls back to the plain fitter when that misses the tolerance
    route: str = "auto"            # force a route instead of letting the signals decide
    cover_check: bool = True       # a face must also cover its own region's mesh (0.10.2; off = the old check only)
    trip_check: bool = True        # Classic too: write, read back, rebuild faces that can't be drawn (0.10.2)
    ladder_guess: bool = True      # organic / mixed routes: guesstimate the problem areas (organic_tol below)
    ladder_organic_tol: float = 0.3  # mm, the Guesstimate tolerance the ladder switches on
    ladder_crease: bool = True     # sharp-edge angle from the edge-angle histogram's valley (when left at default)
    max_settle: int = 50           # most extra "settling" passes after refine_rounds (classic: 50)
    max_fills: int = 0             # most slow plate fills per conversion (0 = no limit)
    gap_fill: str = "plate"        # a patch the least-squares fit misses: "plate" (the plate filler, as before),
                                   # "coons" (a Coons patch on the patch's own outline, its inside fitted to the
                                   # mesh; EXPERIMENTAL), "coons+plate" (Coons first, the plate filler only if
                                   # that misses too) or "off"
    budget_s: float = 0.0          # seconds; when passed, patches still outside tolerance become facets at once
    plane_rule: str = "classic"    # "length": judge a flat region's soft/sharp border by edge length, not edge count
                                   # (keeps coarse cylinder facets from passing as flats; can drop tiny real flats)
    checkpoint: str = "auto"       # "on" / "off" / "auto": write a valid STEP (exact faces, the rest as facets)
                                   # before the slow freeform fitting starts, so a killed run still leaves a file
                                   # (auto: when there are freeform patches and the mesh has 20,000+ triangles)
    strip_rule: str = "classic"    # (internal) "sag": the density-independent narrow-strip test (Ladder, CAD-like routes)
    skip_planes: bool = False      # (internal) organic route: no flat-face search
    lathe_first: bool = False      # (internal) lathe route: surfaces of revolution claimed before the flat search


@dataclass
class Report:
    ok: bool = False
    out_path: str = ""
    n_input_tris: int = 0
    n_faces: int = 0
    n_planes: int = 0
    n_primitives: int = 0
    n_nurbs: int = 0
    n_faceted_regions: int = 0
    is_solid: bool = False
    is_valid: bool = False
    mesh_volume: float = 0.0
    step_volume: float = 0.0
    max_dev: float = float("nan")
    mean_dev: float = float("nan")
    seconds: float = 0.0
    route: str = ""               # Ladder engine: the route taken
    warnings: list = field(default_factory=list)
    tol: float = 0.0              # the tolerance used (mm), for the deviation note
    worst_at: tuple = ()          # where the mesh is furthest from the STEP (x, y, z in mm)

    def summary(self):
        lines = [
            f"Faces in STEP: {self.n_faces}  (planes {self.n_planes}, cyl/sphere {self.n_primitives}, "
            f"NURBS {self.n_nurbs}, faceted regions {self.n_faceted_regions})",
            f"Closed solid: {'yes' if self.is_solid else 'NO'}   geometry check: {'valid' if self.is_valid else 'has issues'}",
        ]
        if self.mesh_volume and self.step_volume:
            dv = 100.0 * (self.step_volume - self.mesh_volume) / self.mesh_volume
            lines.append(f"Volume: mesh {self.mesh_volume:.2f} mm³, STEP {self.step_volume:.2f} mm³ ({dv:+.3f}%)")
        if not math.isnan(self.max_dev):
            lines.append(f"Deviation from mesh: mean {self.mean_dev:.4f} mm, max {self.max_dev:.4f} mm")
            # 10x: below that, a coarse mesh's own chord sag against true round faces explains it
            if self.tol > 0 and self.max_dev > 10 * self.tol and self.worst_at:
                x, y, z = self.worst_at
                lines.append(f"  Note: in one place the STEP is {self.max_dev / self.tol:.0f} times the tolerance away from "
                             f"the mesh, near x {x:.1f}, y {y:.1f}, z {z:.1f} mm. A small feature there (a narrow "
                             "groove, a sharp inside corner) may have been bridged: check that spot in the preview.")
        lines.append(f"Time: {self.seconds:.1f} s" + (f"   (route: {self.route})" if self.route else ""))
        return "\n".join(lines)


class _Log:
    def __init__(self, log=None, progress=None):
        self._log = log or (lambda s: None)
        self._prog = progress or (lambda f: None)

    def __call__(self, msg):
        self._log(msg)

    def p(self, frac):
        self._prog(max(0.0, min(1.0, frac)))


# ---------------------------------------------------------------- mesh prep
def _profile_ceiling():
    """Most triangles the performance profile wants to convert at full size (0 = no ceiling). Set by perf.worker_env
    for the Potato profile only: on a 2-core test machine an 82,000-triangle organic mesh took 107 s whole, 86 s
    reduced to 40,000 and 45 s reduced to 20,000, all within tolerance (docs/guide/performance.md)."""
    try:
        return max(0, int(os.environ.get("TOSTEP_TRI_CEILING", "0") or 0))
    except ValueError:
        return 0


def load_mesh(path, prm: Params, log):
    m = trimesh.load(path, force="mesh", process=True)
    if not isinstance(m, trimesh.Trimesh) or len(m.faces) == 0:
        raise ValueError("No triangles found in the file")
    log(f"Loaded {len(m.faces)} triangles, {len(m.vertices)} vertices")
    if prm.scale != 1.0:
        m.apply_scale(prm.scale)
    m.merge_vertices()
    try:
        m.update_faces(m.nondegenerate_faces())
        m.update_faces(m.unique_faces())
    except AttributeError:  # older trimesh
        m.remove_degenerate_faces()
        m.remove_duplicate_faces()
    m.remove_unreferenced_vertices()
    m = _collapse_tiny_edges(m, prm, log)
    if prm.memory_guard:
        limit = safe_triangle_limit(prm.mode)
        if limit and len(m.faces) > limit and not (prm.decimate and prm.decimate <= limit):
            log(f"This mesh ({len(m.faces):,} triangles) needs more memory than this computer can "
                f"safely spare in {prm.mode} mode — reducing it to {limit:,} triangles.")
            prm.decimate = limit
        ceil = _profile_ceiling()
        if ceil and len(m.faces) > ceil and not (prm.decimate and prm.decimate <= ceil):
            log(f"This mesh ({len(m.faces):,} triangles) is large for the Potato profile — reducing it to "
                f"{ceil:,} triangles first (outlines, flat faces and round features are kept). "
                "Pick another profile, or tick off 'Protect memory', to convert it at full size.")
            prm.decimate = ceil
    if prm.smooth:
        trimesh.smoothing.filter_taubin(m, iterations=int(prm.smooth))
        log(f"Denoised ({prm.smooth} Taubin iterations)")
    meshfix.fix_normals(m)
    if not m.is_watertight:
        trimesh.repair.fill_holes(m)
        meshfix.fix_normals(m)
    if not m.is_watertight:
        log("Warning: mesh is not watertight — the result will be a surface, not a closed solid")
    if m.is_watertight and m.volume < 0:
        m.invert()
    return m


def reduce_within(m, max_err, log, min_tris=30000):
    """Quadric reduction of a dense mesh that provably stays within max_err of the original
    and stays closed. Tries smaller and smaller triangle counts and keeps the smallest that
    passes; returns the mesh unchanged if nothing passes or pyfqmr is missing."""
    if len(m.faces) < min_tris:
        return m
    try:
        import pyfqmr
    except ImportError:
        return m
    t0 = time.time()
    was_closed = m.is_watertight
    S0, _ = trimesh.sample.sample_surface(m, 6000, seed=1)
    best = None
    for frac in (0.5, 0.25, 0.125, 0.0625):
        target = int(len(m.faces) * frac)
        if target < 8000:
            break
        s = pyfqmr.Simplify()
        s.setMesh(np.asarray(m.vertices, np.float64), np.asarray(m.faces, np.int32))
        s.simplify_mesh(target_count=target, aggressiveness=5, preserve_border=True, verbose=False)
        v, f, _ = s.getMesh()
        if len(f) < 4:
            break
        r = trimesh.Trimesh(np.asarray(v, np.float64), np.asarray(f, np.int64), process=True)
        if was_closed and not r.is_watertight:
            break
        _, d1, _ = trimesh.proximity.closest_point(r, S0)
        S1, _ = trimesh.sample.sample_surface(r, 6000, seed=2)
        _, d2, _ = trimesh.proximity.closest_point(m, S1)
        err = float(max(d1.max(), d2.max()))
        if err > max_err:
            break
        best = (r, err)
    if best is None:
        return m
    r, err = best
    meshfix.fix_normals(r)
    if r.is_watertight and r.volume < 0:
        r.invert()
    log(f"Guesstimate: reduced {len(m.faces):,} → {len(r.faces):,} triangles, staying within "
        f"{err:.4f} mm of the original ({time.time() - t0:.1f} s)")
    return r


def reduce_regions(ctx, prm, tol, log, min_tris=30000):
    """Guesstimate-mode reduction for the staged engine: it knows what it is reducing.

    The mesh is cut into smooth pieces at its sharp edges. Pieces that are exact shapes —
    flat, a cylinder or sphere, a lathe shape or an extruded outline — may only move by a
    third of the tight tolerance, and round ones must still sit on their exact surface
    afterwards. Everything else is organic and may move by a sixth of the organic tolerance,
    which lets it shrink far more. Every piece keeps its outline point for point, so sharp
    edges and hole rims are untouched, and each reduced piece is checked both ways against
    the original before it is accepted.

    Returns (new mesh, organic tolerance used up, swept shapes found on the full mesh, and
    for each new triangle the index of the swept shape it came from or -1), or None if
    nothing was gained."""
    if ctx.nF < min_tris:
        return None
    try:
        import pyfqmr  # noqa: F401
    except ImportError:
        return None
    from scipy.spatial import cKDTree
    t0 = time.time()
    V0, F0 = ctx.V, ctx.F
    tight = tol / 3.0
    loose = max(tight, float(prm.organic_tol or 0.0) / 6.0)
    grow = float(prm.organic_tol or 0.0) / 6.0
    ang = ctx.adj_angles()
    smooth = ang < prm.crease_angle
    G = coo_matrix((np.ones(smooth.sum()), (ctx.adj[smooth, 0], ctx.adj[smooth, 1])), shape=(ctx.nF, ctx.nF))
    _, comp = connected_components(G, directed=False)
    order = np.argsort(comp, kind="stable")
    starts = np.r_[0, np.flatnonzero(np.diff(comp[order])) + 1, len(order)]
    pieces = []                                  # (face ids, budget, primitive or None)
    known = []                                   # swept shapes found here, on the full mesh
    for i in range(len(starts) - 1):
        fids = order[starts[i]:starts[i + 1]]
        if len(fids) < 200:
            pieces.append((fids, None, None))    # too small to be worth touching
            continue
        vids = np.unique(F0[fids])
        o, n = _fit_plane(V0[vids])
        if np.abs((V0[vids] - o) @ n).max() <= tol:
            pieces.append((fids, tight, None))   # flat: collapses freely at zero error anyway
            continue
        pr = fit_primitive(ctx, fids, tol) if prm.fit_primitives else None
        if pr is not None:
            pieces.append((fids, tight, pr))
            continue
        rest = fids
        if prm.rough and prm.fit_primitives:
            from tostep.plugins.mesh2step import rough
            revs, rest = rough.find_swept(ctx, fids, tol, grow_tol=grow)
            for p_ids, p_prim in revs:
                pieces.append((p_ids, tight, None if p_prim[0] == "pln" else p_prim))
                if p_prim[0] in SWEPT:
                    known.append(p_prim)
        if len(rest):
            pieces.append((rest, loose, None))
    rng = np.random.default_rng(1)

    def piece_ok(v0, f0, v1, f1, budget, prim):
        if prim is not None and _prim_residual(prim, v1) > max(0.25 * tol, _prim_residual(prim, v0)):
            return False                         # (a round piece must stay on its exact surface)
        a = trimesh.Trimesh(v0, f0, process=False); b = trimesh.Trimesh(v1, f1, process=False)
        d0, _ = cKDTree(v1).query(v0)
        far = v0[d0 > 1e-9]
        if len(far) > 6000:
            far = far[rng.choice(len(far), 6000, replace=False)]
        if len(far) and float(trimesh.proximity.closest_point(b, far)[1].max()) > budget:
            return False
        S = np.vstack([v1, v1[f1].mean(1)])
        dS, _ = cKDTree(v0).query(S)
        S = S[dS > 1e-9]
        if len(S) > 6000:
            S = S[rng.choice(len(S), 6000, replace=False)]
        return not len(S) or float(trimesh.proximity.closest_point(a, S)[1].max()) <= budget

    verts, faces, off = [], [], 0
    which = []                                   # per reduced triangle: index into `known`, or -1
    for fids, budget, prim in pieces:
        sub = F0[fids]
        uv, inv = np.unique(sub.ravel(), return_inverse=True)
        v = V0[uv]; f = inv.reshape(-1, 3).astype(np.int64)
        if budget is not None:
            best = None
            for frac in (0.5, 0.3, 0.18, 0.1, 0.06, 0.035):
                want = int(len(f) * frac)
                if want < 40:
                    break
                v2, f2 = _qem(v, f, want, v, f)
                if len(f2) >= 0.95 * (len(best[1]) if best else len(f)):
                    break                        # the locked outline dominates: no more to gain
                if not piece_ok(v, f, v2, f2, budget, prim):
                    break
                best = (v2, f2)
            if best is not None:
                v, f = best
        verts.append(v); faces.append(f + off); off += len(v)
        k = next((i for i, kp in enumerate(known) if prim is not None and kp is prim), -1)
        which.append(np.full(len(f), k, dtype=np.int64))
    V = np.vstack(verts); F = np.vstack(faces)
    which = np.concatenate(which) if which else np.zeros(0, np.int64)
    mid0 = V[F].mean(1)
    uniq, inv = np.unique(V, axis=0, return_inverse=True)            # outlines kept exact coordinates
    m = trimesh.Trimesh(uniq, inv.ravel()[F], process=True)
    m.remove_unreferenced_vertices()
    # the reduction can leave a corner a few millionths of a millimetre from a kept outline
    # corner: such needle triangles don't survive being written to STEP
    m = _collapse_tiny_edges(m, prm, lambda *a: None, eps=0.02 * tol)
    meshfix.fix_normals(m)
    # which known swept shape each triangle of the finished mesh came from (matched by its
    # middle point; a triangle the clean-up changed counts as organic)
    dm, im = cKDTree(mid0).query(np.asarray(m.triangles_center))
    labels = np.where(dm <= 1e-7 * max(ctx.diag, 1.0), which[im], -1) if len(which) else -np.ones(len(m.faces), np.int64)
    if len(m.faces) > 0.9 * ctx.nF:
        return None
    if ctx.m.is_watertight and not m.is_watertight:
        log("Guesstimate: the reduction couldn't keep the mesh closed — using the full mesh instead")
        return None
    if m.is_watertight and m.volume < 0:
        m.invert()
    log(f"Guesstimate: reduced {ctx.nF:,} → {len(m.faces):,} triangles in {time.time() - t0:.1f} s. Exact shapes kept "
        f"within {tight:.4f} mm, organic areas within {loose:.3f} mm, every sharp edge and hole rim untouched.")
    return m, (loose if loose > tight else 0.0), known, labels


def _collapse_tiny_edges(m, prm, log, eps=None):
    """Merge vertex clusters closer than a tiny epsilon. Removes the slivers and
    needle triangles that exporters and marching cubes leave behind."""
    from scipy.spatial import cKDTree
    diag = float(np.linalg.norm(m.extents))
    tol = prm.tol if prm.tol > 0 else max(0.01, 5e-4 * diag)
    med = float(np.median(m.edges_unique_length)) if len(m.edges_unique) else 0.0
    if eps is None:
        eps = min(0.25 * tol, 0.05 * med)
    if eps <= 0:
        return m
    V = np.array(m.vertices); F = np.array(m.faces)
    pairs = cKDTree(V).query_pairs(eps, output_type="ndarray")
    if len(pairs) == 0:
        return m
    G = coo_matrix((np.ones(len(pairs)), (pairs[:, 0], pairs[:, 1])), shape=(len(V), len(V)))
    nc, lab = connected_components(G, directed=False)
    cnt = np.bincount(lab)
    newV = np.zeros((nc, 3))
    np.add.at(newV, lab, V)
    newV /= cnt[:, None]
    F2 = lab[F]
    ok = (F2[:, 0] != F2[:, 1]) & (F2[:, 1] != F2[:, 2]) & (F2[:, 0] != F2[:, 2])
    F2 = F2[ok]
    # remove pairs of coincident faces (collapses can fold two faces onto each other)
    key = np.sort(F2, axis=1)
    _, inv, counts = np.unique(key, axis=0, return_inverse=True, return_counts=True)
    F2 = F2[counts[inv.ravel()] == 1]
    out = trimesh.Trimesh(newV, F2, process=True)
    out.remove_unreferenced_vertices()
    n_rm = len(F) - len(out.faces)
    if n_rm:
        log(f"Cleaned {n_rm} sliver triangle(s)")
    return out


def _smooth_components(ctx, label, crease_angle):
    """Split the non-planar faces into smooth components separated by sharp creases."""
    free = label < 0
    ang = ctx.adj_angles()
    keep = free[ctx.adj[:, 0]] & free[ctx.adj[:, 1]] & (ang < crease_angle)
    G = coo_matrix((np.ones(keep.sum()), (ctx.adj[keep, 0], ctx.adj[keep, 1])), shape=(ctx.nF, ctx.nF))
    _, comp = connected_components(G, directed=False)
    return comp


def _absorb_specks(ctx, region, nP):
    """Tiny curved 'regions' (a few crumpled or folded triangles) are mesh noise, not
    features: merge each into the neighbouring region it shares the most edges with, so
    its outline isn't locked and the simplifier can clean it up."""
    a, b = ctx.adj[:, 0], ctx.adj[:, 1]
    for _ in range(3):
        cnt = np.bincount(region - region.min(), minlength=1)
        size = cnt[region - region.min()]
        small = (size < max(20, int(2e-4 * ctx.nF))) & (region >= nP)
        if not small.any():
            break
        cross = (region[a] != region[b])
        moved = False
        best = {}
        for x, y in ((a, b), (b, a)):
            m = cross & small[x]
            for rx, ry in zip(region[x[m]].tolist(), region[y[m]].tolist()):
                best.setdefault(rx, {}).setdefault(ry, 0)
                best[rx][ry] += 1
        for rx, nbrs in best.items():
            # prefer a curved neighbour; a flat face only if nothing else touches
            curved = {k: v for k, v in nbrs.items() if k >= nP}
            pick = max((curved or nbrs).items(), key=lambda kv: kv[1])[0]
            if pick != rx and (pick >= nP or not curved):
                region[region == rx] = pick if pick >= nP else rx
                moved = moved or pick >= nP
        if not moved:
            break
    return region


def simplify_keep_features(ctx, label, planes, prm, tol, target, log):
    """Reduce the triangle count without harming features.

    Regions are found at full resolution first (flat faces, cylinders/spheres, smooth
    areas between sharp edges). Each region is then simplified on its own with its
    outline locked, so region borders — hole edges, sharp edges, face outlines — keep
    every original point. Cylinders and spheres are left untouched (their points are
    what lets them be recognised as true round surfaces), flat faces are reduced as far
    as their outline allows, and curved areas share the remaining budget.
    """
    try:
        import pyfqmr
    except ImportError:
        log("pyfqmr not installed — skipping mesh reduction")
        return None
    t0 = time.time()
    nP = len(planes)
    comp = _smooth_components(ctx, label, prm.crease_angle)
    region = label.copy()
    free = label < 0
    region[free] = nP + comp[free]
    region = _absorb_specks(ctx, region, nP)
    order = np.argsort(region, kind="stable")
    rs = region[order]
    starts = np.r_[0, np.flatnonzero(np.diff(rs)) + 1]
    groups = {int(rs[s0]): order[s0:s1] for s0, s1 in zip(starts, np.r_[starts[1:], len(rs)])}
    kind, prims = {}, {}
    for r, fids in groups.items():
        if r < nP:
            kind[r] = "plane"
            continue
        pr = fit_primitive(ctx, fids, tol) if prm.fit_primitives else None
        if pr is not None:
            kind[r], prims[r] = "round", pr
        else:
            kind[r] = "curved"
    # flat faces are rebuilt from their outline alone (about one triangle per outline edge)
    plane_est = sum(max(4, int(3 * math.sqrt(len(f)))) for r, f in groups.items() if kind[r] == "plane")
    shaped = {r: f for r, f in groups.items() if kind[r] != "plane"}
    shaped_area = sum(ctx.area[f].sum() for f in shaped.values()) or 1.0
    budget = max(target - plane_est, int(0.25 * target))
    verts, faces, off = [], [], 0
    for r, fids in groups.items():
        sub_f = ctx.F[fids]
        uv, inv = np.unique(sub_f.ravel(), return_inverse=True)
        v = ctx.V[uv]
        f = inv.reshape(-1, 3).astype(np.int64)
        if kind[r] == "plane":
            tri = _retriangulate_flat(v, f, planes[r][1])
            if tri is not None:
                f = tri
            else:
                v, f = _qem(v, f, 4, v, f)
        else:
            want = max(8, int(budget * ctx.area[fids].sum() / shaped_area))
            if want < len(fids) and len(fids) > 8:
                v2, f2 = _qem(v, f, want, v, f)
                if kind[r] == "round":
                    # keep the reduction only if it still sits on the exact round surface
                    if _prim_residual(prims[r], v2) <= 0.25 * tol:
                        v, f = v2, f2
                else:
                    v, f = v2, f2
        verts.append(v); faces.append(f + off); off += len(v)
    V = np.vstack(verts); F = np.vstack(faces)
    # stitch regions back together: locked borders kept their exact coordinates
    uniq, inv = np.unique(V, axis=0, return_inverse=True)
    # (zero-area triangles along straight outline stretches are kept on purpose:
    #  deleting them would open gaps; later stages handle slivers)
    m = trimesh.Trimesh(uniq, inv.ravel()[F], process=True)
    m.remove_unreferenced_vertices()
    m = _collapse_tiny_edges(m, prm, lambda *a: None)     # same clean-up a loaded mesh gets
    meshfix.fix_normals(m)
    if not m.is_watertight:
        log("Feature-preserving reduction couldn't keep the mesh closed — using the full mesh instead")
        return None
    n_r = sum(1 for k in kind.values() if k == "round")
    log(f"Reduced {ctx.nF:,} → {len(m.faces):,} triangles in {time.time() - t0:.1f} s. "
        f"Kept exact: all {nP} flat face outlines, {n_r} round feature(s), every sharp edge.")
    return m


def _qem(v, f, target, v0, f0):
    """Quadric simplification with the piece's outline locked."""
    import pyfqmr
    s = pyfqmr.Simplify()
    s.setMesh(np.asarray(v, np.float64), np.asarray(f, np.int32))
    s.simplify_mesh(target_count=int(target), aggressiveness=5, max_iterations=150,
                    preserve_border=True, verbose=False)
    v2, f2, _ = s.getMesh()
    if len(f2) == 0:
        return v0, f0
    return np.asarray(v2, np.float64), np.asarray(f2, np.int64)


def _prim_residual(prim, pts):
    if prim[0] in SWEPT:
        from tostep.plugins.mesh2step import rough
        return float(rough.residual(prim, pts).max()) if len(pts) else 0.0
    if prim[0] == "pln":
        return float(np.abs((pts - prim[1]) @ prim[2]).max())
    if prim[0] == "cyl":
        _, c, axis, r, _ = prim
        d = pts - c
        return float(np.abs(np.linalg.norm(d - np.outer(d @ axis, axis), axis=1) - r).max())
    _, c, r, _ = prim
    return float(np.abs(np.linalg.norm(pts - c, axis=1) - r).max())


def _retriangulate_flat(v, f, normal):
    """Minimal triangulation of a flat region from its outline (+ hole outlines).
    Uses only the outline points, so neighbours still line up exactly. None if unsure."""
    try:
        import mapbox_earcut as earcut
    except ImportError:
        return None
    n = np.asarray(normal, float)
    e = np.concatenate([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]])
    key = set(map(tuple, e.tolist()))
    bnd = [(a, b) for a, b in e.tolist() if (b, a) not in key]
    nxt = {}
    for a, b in bnd:
        if a in nxt:
            return None                      # pinched outline: let the simplifier handle it
        nxt[a] = b
    loops, seen = [], set()
    for a in nxt:
        if a in seen:
            continue
        loop, cur = [], a
        while cur not in seen:
            seen.add(cur); loop.append(cur); cur = nxt.get(cur)
            if cur is None:
                return None
        if cur != a:
            return None
        loops.append(loop)
    if not loops:
        return None
    u = np.cross(n, [1, 0, 0] if abs(n[0]) < 0.9 else [0, 1, 0]); u /= np.linalg.norm(u)
    w = np.cross(n, u)
    P2 = np.c_[v @ u, v @ w]

    def sarea(lp):
        p = P2[lp]
        return 0.5 * float(np.sum(p[:, 0] * np.roll(p[:, 1], -1) - np.roll(p[:, 0], -1) * p[:, 1]))
    areas = [sarea(lp) for lp in loops]
    outer = [i for i, a in enumerate(areas) if a > 0]
    if len(outer) != 1:
        return None
    order = [outer[0]] + [i for i in range(len(loops)) if i != outer[0]]
    idx = np.concatenate([loops[i] for i in order])
    ends = np.cumsum([len(loops[i]) for i in order]).astype(np.uint32)
    tri = earcut.triangulate_float64(P2[idx], ends).reshape(-1, 3)
    if len(tri) == 0:
        return None
    tri = idx[tri]
    # orient like the region, and make sure the area adds up (earcut can't see errors)
    cr = np.cross(v[tri[:, 1]] - v[tri[:, 0]], v[tri[:, 2]] - v[tri[:, 0]])
    flip = cr @ n < 0
    tri[flip] = tri[flip][:, [0, 2, 1]]
    a_new = 0.5 * np.abs(cr @ n).sum()
    tv = v[f]
    # signed: folded-over slivers subtract, which is the area the region really covers
    a_old = 0.5 * (np.cross(tv[:, 1] - tv[:, 0], tv[:, 2] - tv[:, 0]) @ n).sum()
    if abs(a_new - a_old) > 1e-6 * max(a_old, 1e-12) + 1e-9:
        return None
    # earcut also makes zero-area triangles out of collinear outline points; those cannot
    # become faces (the faceted solid then had gaps), so they go, and the fanning below puts
    # their points back with real triangles (0.10.0)
    tri = tri[0.5 * np.abs(cr @ n) > 1e-9 * max(a_old, 1e-12)]
    if len(tri) == 0:
        return None
    # earcut drops outline points that lie on a straight stretch; the neighbouring faces
    # still use them, so put them back by fanning the triangle along that stretch. Done one
    # stretch at a time on the current triangles (0.10.0: two stretches meeting at one
    # triangle used to fan the same original triangle twice, which left open edges and
    # made the whole reduction fail on a dense flat-sided mesh).
    used = set(tri.ravel().tolist())
    if any(p not in used for lp in loops for p in lp):
        out = {ti: tuple(t) for ti, t in enumerate(tri.tolist())}
        edge_tri = {}
        for ti, (x, y, z) in out.items():
            edge_tri[(x, y)] = ti; edge_tri[(y, z)] = ti; edge_tri[(z, x)] = ti
        nid = len(out)
        for lp in loops:
            kept = [i for i, p in enumerate(lp) if p in used]
            if not kept:
                return None
            for k, i in enumerate(kept):
                j = kept[(k + 1) % len(kept)]
                between = [lp[(i + t) % len(lp)] for t in range(1, (j - i) % len(lp) or len(lp))]
                if not between:
                    continue
                ti = edge_tri.get((lp[i], lp[j]))
                if ti is None:
                    return None
                x, y, z = out.pop(ti)
                apex = z if (x, y) == (lp[i], lp[j]) else (x if (y, z) == (lp[i], lp[j]) else y)
                for e in ((x, y), (y, z), (z, x)):
                    edge_tri.pop(e, None)
                chain = [lp[i]] + between + [lp[j]]
                for s in range(len(chain) - 1):
                    tnew = (chain[s], chain[s + 1], apex)
                    out[nid] = tnew
                    edge_tri[(tnew[0], tnew[1])] = nid; edge_tri[(tnew[1], tnew[2])] = nid; edge_tri[(tnew[2], tnew[0])] = nid
                    nid += 1
        tri = np.array(list(out.values()), dtype=np.int64)
    # the new triangles must have exactly the region's outline edges (anything else would
    # open the mesh when the regions are stitched back together), and real area each
    cr = np.cross(v[tri[:, 1]] - v[tri[:, 0]], v[tri[:, 2]] - v[tri[:, 0]])
    if np.any(0.5 * np.abs(cr @ n) <= 1e-9 * max(a_old, 1e-12)):
        return None
    e2 = np.concatenate([tri[:, [0, 1]], tri[:, [1, 2]], tri[:, [2, 0]]])
    k2 = set(map(tuple, e2.tolist()))
    if set(bnd) != {(a, b) for a, b in e2.tolist() if (b, a) not in k2}:
        return None
    return tri.astype(np.int64)


# measured peak memory: fixed overhead + per-triangle cost (bytes), with some headroom
_MEM_MODEL = {"faceted": (500e6, 16e3), "surface": (500e6, 4e3)}


def system_memory():
    """(total, available) RAM in bytes, best effort, no extra libraries. None if unknown."""
    import os, sys
    try:
        if sys.platform.startswith("win"):
            import ctypes

            class MS(ctypes.Structure):
                _fields_ = [("len", ctypes.c_ulong), ("load", ctypes.c_ulong),
                            ("total", ctypes.c_ulonglong), ("avail", ctypes.c_ulonglong),
                            ("tp", ctypes.c_ulonglong), ("ap", ctypes.c_ulonglong),
                            ("tv", ctypes.c_ulonglong), ("av", ctypes.c_ulonglong),
                            ("ae", ctypes.c_ulonglong)]
            ms = MS(); ms.len = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms))
            return float(ms.total), float(ms.avail)
        total = float(os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE"))
        avail = None
        if os.path.exists("/proc/meminfo"):
            for line in open("/proc/meminfo"):
                if line.startswith("MemAvailable:"):
                    avail = float(line.split()[1]) * 1024
        if avail is None:        # macOS: compressed memory makes "free" misleading; assume 60 %
            avail = 0.6 * total
        return total, avail
    except Exception:
        return None


def safe_triangle_limit(mode):
    mem = system_memory()
    if not mem:
        return None
    total, avail = mem
    budget = min(0.8 * avail, 0.6 * total)
    base, per = _MEM_MODEL.get(mode, _MEM_MODEL["faceted"])
    return max(20000, int((budget - base) / per))


class MeshCtx:
    """Precomputed topology/geometry used by every stage."""

    def __init__(self, m: trimesh.Trimesh):
        self.m = m
        self.V = np.array(m.vertices, dtype=np.float64)
        self.F = np.array(m.faces, dtype=np.int64)
        self.nF = len(self.F)
        self.refresh_geometry()
        self.adj = np.array(m.face_adjacency, dtype=np.int64).reshape(-1, 2)
        self.adj_edges = np.array(m.face_adjacency_edges, dtype=np.int64).reshape(-1, 2)
        self.fue = np.array(m.faces_unique_edges, dtype=np.int64)
        self.edges_unique = np.array(m.edges_unique, dtype=np.int64)
        self.diag = float(np.linalg.norm(m.extents))
        self.face_sag = np.zeros(self.nF)
        self.face_comp = -np.ones(self.nF, dtype=np.int64)
        # vertex -> faces
        vf = [[] for _ in range(len(self.V))]
        for fi, (a, b, c) in enumerate(self.F):
            vf[a].append(fi); vf[b].append(fi); vf[c].append(fi)
        self.vert_faces = vf

    def refresh_geometry(self):
        tri = self.V[self.F]
        cr = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
        ar = np.linalg.norm(cr, axis=1)
        self.area = 0.5 * ar
        self.N = cr / np.maximum(ar, 1e-300)[:, None]
        self.C = tri.mean(axis=1)
        # near-degenerate needles have an unreliable normal: flag them (height < 0.1 % of
        # length). Long thin strips — normal in CAD meshes of rounds — are fine and not flagged.
        lmax2 = np.max(((tri[:, [1, 2, 0]] - tri) ** 2).sum(axis=2), axis=1)
        self.sliver = ar < 2e-3 * np.maximum(lmax2, 1e-300)

    def compute_sag(self):
        """Estimate how far each facet sits from the smooth surface it approximates."""
        e = self.V[self.adj_edges[:, 0]] - self.V[self.adj_edges[:, 1]]
        L = np.linalg.norm(e, axis=1)
        ang = self.adj_angles()
        th = np.radians(np.where(ang < 30.0, ang, 0.0))   # only across smooth edges: at a
        sag = L * th / 8.0                                  # sharp crease 'sag' means nothing
        fs = np.zeros(self.nF)
        np.maximum.at(fs, self.adj[:, 0], sag)
        np.maximum.at(fs, self.adj[:, 1], sag)
        # facets are ~as wide as edges are long in the other direction too
        self.face_sag = fs

    def adj_angles(self):
        d = np.einsum("ij,ij->i", self.N[self.adj[:, 0]], self.N[self.adj[:, 1]])
        return np.degrees(np.arccos(np.clip(d, -1, 1)))


# ---------------------------------------------------------------- planar regions
def _neighbors(ctx):
    nb = [[] for _ in range(ctx.nF)]
    for a, b in ctx.adj:
        nb[a].append(b); nb[b].append(a)
    return nb


def _fit_plane(pts, w=None):
    if w is None:
        w = np.ones(len(pts))
    c = (pts * w[:, None]).sum(0) / w.sum()
    q = (pts - c) * np.sqrt(w)[:, None]
    _, _, vt = np.linalg.svd(q, full_matrices=False)
    return c, vt[-1]


def _strip_width(ctx, m_in, vids, p2):
    """Width of a flat region: the short side of the smallest rotated rectangle around its
    boundary vertices (in-plane coordinates p2, in the order of vids). Independent of how
    many vertices the exporter put inside the face."""
    try:
        from shapely import MultiPoint
        bv = np.unique(ctx.adj_edges[m_in].ravel())
        loc = -np.ones(int(vids.max()) + 1, dtype=np.int64); loc[vids] = np.arange(len(vids))
        bidx = loc[bv]; bidx = bidx[bidx >= 0]
        pb = p2[bidx] if len(bidx) >= 3 else p2
        xy = np.asarray(MultiPoint(pb).minimum_rotated_rectangle.exterior.coords)[:4]
        s1 = float(np.hypot(*(xy[1] - xy[0]))); s2 = float(np.hypot(*(xy[2] - xy[1])))
        return min(s1, s2)
    except Exception:
        return float("inf")


def find_planes(ctx, prm, tol, log):
    """Region-grow near-coplanar faces. Returns (labels (-1 = not planar), planes)."""
    cos_t = math.cos(math.radians(prm.plane_angle))
    nb = _neighbors(ctx)
    label = -np.ones(ctx.nF, dtype=np.int64)
    tried = np.zeros(ctx.nF, dtype=bool)
    total_area = ctx.area.sum()
    min_area = (15 * tol) ** 2                   # absolute floor (below: mesh noise, not a face)
    big_area = max(total_area * 0.002, min_area)   # below this a flat face needs sharp edges all round
    min_width = 0.04 * ctx.diag
    crease_cos = math.cos(math.radians(max(prm.crease_angle, 30.0)))   # "sharp" for flat-face borders: a real edge
    planes = []
    if prm.skip_planes:
        log("Flat-face search skipped (organic route)")
        return label, planes
    claimed = getattr(ctx, "claimed", None)      # Ladder engine, lathe route: triangles already owned by a lathe face
    if claimed is not None:
        tried |= claimed
    order = np.argsort(-ctx.area)
    sliver = ctx.sliver
    for seed in order:
        if label[seed] >= 0 or tried[seed] or sliver[seed]:
            continue
        tried[seed] = True
        n = ctx.N[seed].copy(); o = ctx.C[seed].copy()
        region = [seed]; inreg = {seed}; stack = [seed]; next_refit = 8
        while stack:
            f = stack.pop()
            for g in nb[f]:
                if g in inreg or label[g] >= 0 or (claimed is not None and claimed[g]):
                    continue
                d = ctx.N[g] @ n
                # slivers are judged by their corners only; a face pointing exactly backwards
                # with all corners in the plane is a folded-over triangle, not a real wall
                if not sliver[g] and cos_t > d > -cos_t:
                    continue
                if np.abs((ctx.V[ctx.F[g]] - o) @ n).max() > tol:
                    continue
                inreg.add(g); region.append(g); stack.append(g)
                if len(region) >= next_refit:
                    idx = np.array(region)
                    w = ctx.area[idx]
                    nn = (ctx.N[idx] * w[:, None]).sum(0)
                    n = nn / np.linalg.norm(nn)
                    o = (ctx.C[idx] * w[:, None]).sum(0) / w.sum()
                    next_refit *= 2
        idx = np.array(region)
        # never start another search from these faces: on curved/organic meshes this
        # used to re-grow the same failed regions from almost every face (O(n²))
        tried[idx] = True
        a = ctx.area[idx].sum()
        if a < min_area:
            continue
        # robust refit: drop faces that sit off the plane relative to the region's own
        # noise level (catches the first strip of a tangent fillet on exact CAD meshes)
        for _ in range(3):
            vids = np.unique(ctx.F[idx])
            o, n2 = _fit_plane(ctx.V[vids])
            if n2 @ n < 0:
                n2 = -n2
            n = n2
            vd = np.abs((ctx.V - o) @ n)
            fd = vd[ctx.F[idx]].max(axis=1)
            mad = 1.4826 * np.median(vd[vids])
            thr = min(tol, max(4 * mad, 1e-6 * ctx.diag))
            keep = fd <= thr
            if keep.all():
                break
            idx = idx[keep]
            if len(idx) == 0:
                break
            # keep only the part connected to the seed (or the largest part)
            loc = -np.ones(ctx.nF, np.int64); loc[idx] = np.arange(len(idx))
            mm = (loc[ctx.adj[:, 0]] >= 0) & (loc[ctx.adj[:, 1]] >= 0)
            Gs = coo_matrix((np.ones(mm.sum()), (loc[ctx.adj[mm, 0]], loc[ctx.adj[mm, 1]])),
                            shape=(len(idx), len(idx)))
            ncomp, cl = connected_components(Gs, directed=False)
            if ncomp > 1:
                big = np.argmax(np.bincount(cl, weights=ctx.area[idx]))
                idx = idx[cl == big]
        if len(idx) == 0 or ctx.area[idx].sum() < min_area:
            continue
        vids = np.unique(ctx.F[idx])
        if np.abs((ctx.V[vids] - o) @ n).max() > 1.5 * tol:
            continue
        # Reject narrow strips that are really facets of a curved surface
        pts = ctx.V[vids] - o
        u = np.cross(n, [1, 0, 0] if abs(n[0]) < 0.9 else [0, 1, 0]); u /= np.linalg.norm(u)
        v = np.cross(n, u)
        p2 = np.c_[pts @ u, pts @ v]
        ev = np.linalg.eigvalsh(np.cov(p2.T)) if len(p2) > 2 else np.zeros(2)
        width = math.sqrt(max(ev[0], 0)) * 3.46
        inset = np.zeros(ctx.nF, bool); inset[idx] = True
        m_in = inset[ctx.adj[:, 0]] ^ inset[ctx.adj[:, 1]]
        smooth_frac = 0.0
        smooth_ang = 0.0
        if m_in.any():
            d = np.einsum("ij,ij->i", ctx.N[ctx.adj[m_in, 0]], ctx.N[ctx.adj[m_in, 1]])
            sm = d > crease_cos
            if sm.any():      # mean turning angle across the region's smooth borders (radians)
                e = ctx.adj_edges[m_in][sm]
                w = np.linalg.norm(ctx.V[e[:, 0]] - ctx.V[e[:, 1]], axis=1)
                smooth_ang = float((np.arccos(np.clip(d[sm], -1, 1)) * w).sum() / max(w.sum(), 1e-12))
            if prm.plane_rule == "length":
                # by border length: a tall facet of a coarse cylinder has two long smooth sides
                # and two short sharp ends, which an edge count calls half sharp
                e = ctx.adj_edges[m_in]
                w = np.linalg.norm(ctx.V[e[:, 0]] - ctx.V[e[:, 1]], axis=1)
                smooth_frac = float(w[d > crease_cos].sum() / max(w.sum(), 1e-12))
            else:
                smooth_frac = float((d > crease_cos).mean())
        # narrow strip with soft edges = one facet row of a curved surface. The strip's width
        # is the short side of the smallest rectangle around its boundary (the vertex
        # covariance above depends on how densely the face is triangulated: a subdivided
        # mesh lost 44 of 45 flats to this test), and a strip is only a facet row if it is
        # also within tolerance of the curved surface its soft borders turn through
        # (sagitta = width x turning angle / 8)
        if prm.strip_rule == "sag":
            # (Ladder engine, prismatic and lathe routes only: on a subdivided knob the test below lost 44 of 45 flats, and
            # with this rule Classic still produced one 0.6 mm gap on that mesh, so Classic
            # keeps its released behaviour)
            width = min(width, _strip_width(ctx, m_in, vids, p2))
            sag = width * smooth_ang / 8.0
            if width < min_width and sag < 0.5 * tol and smooth_frac > 0.5:
                continue
        elif width < min_width and smooth_frac > 0.5:
            continue
        # small patch blending smoothly into its surroundings = flat spot on an organic shape
        if smooth_frac > 0.8 and a < 0.02 * total_area:
            continue
        # small flat faces (hole walls, little facets) count only if sharp edges bound them
        if ctx.area[idx].sum() < big_area and smooth_frac > 0.2:
            continue
        pid = len(planes)
        label[idx] = pid
        planes.append((o, n))
    log(f"Found {len(planes)} flat regions covering "
        f"{100 * ctx.area[label >= 0].sum() / total_area:.1f}% of the surface")
    return label, planes


def snap_to_planes(ctx, label, planes, tol):
    """Move vertices exactly onto their planes / plane intersections."""
    if not planes:
        return
    P_n = np.array([p[1] for p in planes]); P_d = np.array([p[0] @ p[1] for p in planes])
    orig = ctx.V.copy()
    for vi, fl in enumerate(ctx.vert_faces):
        pl = {int(label[f]) for f in fl if label[f] >= 0}
        if not pl:
            continue
        pl = sorted(pl)
        Nm = P_n[pl]; d = P_d[pl]; p = ctx.V[vi]
        x = p + np.linalg.pinv(Nm, rcond=0.05) @ (d - Nm @ p)
        if np.linalg.norm(x - p) <= 3 * tol:
            ctx.V[vi] = x
    # undo snaps that created degenerate triangles
    for _ in range(3):
        old_area = ctx.area.copy()
        ctx.refresh_geometry()
        bad = np.where(ctx.area < 1e-3 * np.maximum(old_area, 1e-12))[0]
        if len(bad) == 0:
            break
        vids = np.unique(ctx.F[bad])
        ctx.V[vids] = orig[vids]
    ctx.refresh_geometry()


# ---------------------------------------------------------------- primitives
def _fit_circle2d(p):
    A = np.c_[2 * p, np.ones(len(p))]
    b = (p ** 2).sum(1)
    sol, *_ = np.linalg.lstsq(A, b, rcond=None)
    c = sol[:2]; r = math.sqrt(max(sol[2] + c @ c, 0))
    for _ in range(10):  # Gauss-Newton refine
        d = p - c; rr = np.linalg.norm(d, axis=1); rr[rr == 0] = 1e-12
        J = np.c_[-d / rr[:, None], -np.ones(len(p))]
        res = rr - r
        step, *_ = np.linalg.lstsq(J, -res, rcond=None)
        c = c + step[:2]; r = r + step[2]
    return c, abs(r)


def fit_primitive(ctx, fids, tol):
    """Cylinder/sphere fit with sanity rules (see _fit_primitive_raw)."""
    pr = _fit_primitive_raw(ctx, fids, tol)
    if pr is None:
        return None
    fids = np.asarray(fids)
    tri = ctx.V[ctx.F[fids]]
    lmax = np.linalg.norm(tri[:, [1, 2, 0]] - tri, axis=2).max(1)
    if np.any(_prim_residual_vec(pr, ctx.C[fids]) > 3 * tol + 0.05 * lmax):
        return None          # corners fit but triangle middles don't: wrong shape
    pts = ctx.V[np.unique(ctx.F[fids])]
    ext = float(np.ptp(pts, axis=0).max()) if len(pts) else 0.0
    rad = pr[3] if pr[0] == "cyl" else pr[2]
    # a shape that barely turns (< ~10° across) or whose radius dwarfs the region is a
    # nearly flat strip that happens to fit — not a real round
    if rad > 4 * max(ext, 1e-9) or _cone(ctx, np.asarray(fids)) < 10.0:
        return None
    return pr


def _fit_primitive_raw(ctx, fids, tol):
    """Try cylinder then sphere. Returns ('cyl', center, axis, r, outward) / ('sph', c, r, outward) / None."""
    vids = np.unique(ctx.F[fids])
    if len(vids) < 12:
        return None
    pts = ctx.V[vids]
    w = ctx.area[fids]
    Nf = ctx.N[fids]
    # normal spread must be significant or it's basically a plane
    mean_n = (Nf * w[:, None]).sum(0)
    if np.linalg.norm(mean_n) / w.sum() > 0.995:
        return None
    # --- cylinder: normals perpendicular to axis
    M = (Nf * w[:, None]).T @ Nf
    ev, evec = np.linalg.eigh(M)
    axis = evec[:, 0]
    if ev[0] / ev.sum() < 0.01:
        u = np.cross(axis, [1, 0, 0] if abs(axis[0]) < 0.9 else [0, 1, 0]); u /= np.linalg.norm(u)
        v = np.cross(axis, u)
        p2 = np.c_[pts @ u, pts @ v]
        c2, r = _fit_circle2d(p2)
        res = np.abs(np.linalg.norm(p2 - c2, axis=1) - r)
        if res.max() <= tol and r < 5 * ctx.diag:
            center = c2[0] * u + c2[1] * v
            radial = ctx.C[fids] - center
            radial -= np.outer(radial @ axis, axis)
            outward = float(np.sum(np.einsum("ij,ij->i", radial, Nf) * w)) > 0
            return ("cyl", center, axis, r, outward)
    # --- sphere
    A = np.c_[2 * pts, np.ones(len(pts))]
    b = (pts ** 2).sum(1)
    sol, *_ = np.linalg.lstsq(A, b, rcond=None)
    c = sol[:3]; r = math.sqrt(max(sol[3] + c @ c, 0))
    res = np.abs(np.linalg.norm(pts - c, axis=1) - r)
    if res.max() <= tol and r < 5 * ctx.diag:
        outward = float(np.sum(np.einsum("ij,ij->i", ctx.C[fids] - c, Nf) * w)) > 0
        return ("sph", c, r, outward)
    return None


def _row_norms(x, keepdims=False):
    """np.linalg.norm(x, axis=-1) without its Python-side argument handling: the same
    sums in the same order, so bit-identical, but cheaper on the small arrays the shape
    guessing calls it on hundreds of thousands of times (0.9.1)."""
    return np.sqrt(np.add.reduce(x * x, axis=x.ndim - 1, keepdims=keepdims))


def _prim_normal_ok(prim, pts, nrm, cos_lim):
    """Do these face normals agree with the primitive's own surface normal there?"""
    if prim[0] in SWEPT:
        from tostep.plugins.mesh2step import rough
        return rough.normal_ok(prim, pts, nrm, cos_lim)
    if prim[0] == "pln":
        return nrm @ prim[2] >= cos_lim
    if prim[0] == "cyl":
        _, c, axis, r, outward = prim
        d = pts - c
        d = d - (d @ axis)[:, None] * axis
    else:
        _, c, r, outward = prim
        d = pts - c
    n = _row_norms(d, keepdims=True)
    n[n == 0] = 1
    d = d / n * (1 if outward else -1)
    return np.einsum("ij,ij->i", d, nrm) >= cos_lim


def _hyp_from_pair(p1, n1, p2, n2, diag):
    """Cylinder and sphere guesses from two surface samples (point + normal)."""
    # 0.9.1: the cross product, the 3 x 2 matrix and the lengths written out (np.cross, np.c_
    # and np.linalg.norm cost more in argument handling than in arithmetic on 3 numbers);
    # the same operations in the same order, so the same bits. Called ~60,000 times a part.
    out = []
    a = np.array((n1[1] * n2[2] - n1[2] * n2[1], n1[2] * n2[0] - n1[0] * n2[2], n1[0] * n2[1] - n1[1] * n2[0]))
    la = np.sqrt(a.dot(a))
    if la > 0.08:                                   # normals differ by > ~5°
        a /= la
        q1 = n1 - (n1 @ a) * a; q2 = n2 - (n2 @ a) * a
        # intersect lines p1 + t q1 and p2 + s q2 in the plane across the axis
        M = np.empty((3, 2)); M[:, 0] = q1; M[:, 1] = -q2
        ts, *_ = np.linalg.lstsq(M, p2 - p1, rcond=None)
        c = p1 + ts[0] * q1
        c = c - ((c - p1) @ a) * a
        e1 = (p1 - c) - ((p1 - c) @ a) * a; e2 = (p2 - c) - ((p2 - c) @ a) * a
        r = 0.5 * (np.sqrt(e1.dot(e1)) + np.sqrt(e2.dot(e2)))
        if 1e-6 < r < diag:
            out.append(("cyl", c, a, r, float((p1 - c) @ n1) > 0))
    # sphere: closest approach of the two normal lines
    w0 = p1 - p2; b = n1 @ n2; d = n1 @ w0; e = n2 @ w0; den = 1 - b * b
    if den > 1e-4:
        t = (b * e - d) / den; u = (e - b * d) / den
        c1 = p1 + t * n1; c2 = p2 + u * n2
        c = 0.5 * (c1 + c2)
        e1 = p1 - c; e2 = p2 - c; e3 = c1 - c2
        r = 0.5 * (np.sqrt(e1.dot(e1)) + np.sqrt(e2.dot(e2)))
        if 1e-6 < r < diag and np.sqrt(e3.dot(e3)) < 0.1 * r:
            out.append(("sph", c, r, float((p1 - c) @ n1) > 0))
    return out


def _support(prim, Vf, Nf, Cf, tol, cos_lim):
    """Which faces agree with a primitive: all three corners on it, the triangle's middle
    near it (long thin triangles can have all corners on a wrong shape), same direction.

    The tests run one after another on the faces still in the running (0.7.2): most guesses
    are wrong and lose most faces at the first corner, so the later tests see few faces.
    Same answer as testing everything at once, a few times faster."""
    idx = np.flatnonzero(_prim_residual_vec(prim, Vf[:, 0]) <= tol)
    for k in (1, 2):
        if not len(idx):
            break
        idx = idx[_prim_residual_vec(prim, Vf[idx, k]) <= tol]
    ok = np.zeros(len(Vf), bool)
    if len(idx):
        V = Vf[idx]
        lmax = _row_norms(V[:, [1, 2, 0]] - V).max(1)
        idx = idx[_prim_residual_vec(prim, Cf[idx]) <= 3 * tol + 0.05 * lmax]
        if len(idx):
            idx = idx[_prim_normal_ok(prim, Cf[idx], Nf[idx], cos_lim)]
            ok[idx] = True
    return ok


def _prim_residual_vec(prim, P):
    if prim[0] in SWEPT:
        from tostep.plugins.mesh2step import rough
        return rough.residual(prim, P)
    if prim[0] == "pln":
        _, o, n = prim
        return np.abs((P - o) @ n)
    if prim[0] == "cyl":
        _, c, axis, r, _ = prim
        d = P - c
        return np.abs(_row_norms(d - (d @ axis)[:, None] * axis) - r)
    _, c, r, _ = prim
    return np.abs(_row_norms(P - c) - r)


def best_primitive_split(ctx, ids, tol, prm, tries=None):
    """Shape detection is randomised: run it with a few seeds and keep the split that
    explains the most area with real shapes (then the one with fewer pieces)."""
    total = max(float(ctx.area[ids].sum()), 1e-12)
    if tries is None:
        tries = 4 if len(ids) < 20000 else 2      # big areas: fewer (costly) retries
    best = None
    for k in range(tries):
        out, rest = grow_primitives(ctx, ids, tol, prm, _seed=k)
        covered = sum(float(ctx.area[r].sum()) for r, _ in out)
        key = (round(covered / total, 3), -len(out))
        if best is None or key > best[0]:
            best = (key, out, rest)
        if key[0] >= 0.995:
            break
        if k == 0:
            n0 = len(out)
            if key[0] < 0.75 or (n0 and key[0] * total / n0 < 0.015 * total):
                break      # looks organic: more tries won't change the verdict
    cover, n = best[0][0], len(best[1])
    # A blended CAD area (edge rounds into corner balls...) is explained almost entirely by
    # a few real shapes. An organic surface is not - tiny spheres fit any small patch -
    # so there the split is rejected and the area gets smooth fitted patches instead.
    if cover < 0.85 or (n and cover * total / n < 0.015 * total):
        return [], ids
    return best[1], best[2]


def grow_primitives(ctx, ids, tol, prm, log=None, _depth=0, _seed=0):
    """Split a smooth area made of several blended round shapes — edge rounds running
    into corner balls, fillets meeting other fillets — into separate cylinders and spheres.

    RANSAC: guess a shape from two nearby triangles, count how much of the area agrees,
    keep the best guess, refit it exactly to its triangles' corners, grow it to its full
    connected extent, take it out, repeat. Seeded, so results are repeatable.
    Returns ([(face_ids, primitive), ...], leftover_face_ids)."""
    rng = np.random.default_rng(12345 + 7919 * _depth + 104729 * _seed)
    nb = getattr(ctx, "_nb", None) or _neighbors(ctx)
    ctx._nb = nb
    total = ctx.area[ids].sum()
    min_area = max((6 * tol) ** 2, 0.001 * total, 1e-9)
    cos_lim = math.cos(math.radians(max(12.0, 4 * prm.plane_angle)))
    cos_pl = math.cos(math.radians(prm.plane_angle))
    remaining = np.zeros(ctx.nF, bool); remaining[ids] = True
    out = []
    fails = 0
    noseed = np.zeros(ctx.nF, bool)
    while fails < 25:
        rem = np.flatnonzero(remaining)
        if len(rem) < 4 or ctx.area[rem].sum() < min_area:
            break
        Vf, Nf, Cf, Af = ctx.V[ctx.F[rem]], ctx.N[rem], ctx.C[rem], ctx.area[rem]
        good = ~ctx.sliver[rem] & ~noseed[rem]
        if good.sum() < 2:
            break
        samp = rem if len(rem) <= 1500 else rng.choice(rem, 1500, replace=False)
        sV, sN, sC, sA = ctx.V[ctx.F[samp]], ctx.N[samp], ctx.C[samp], ctx.area[samp]
        prob = np.where(good, Af, 0.0); prob /= prob.sum()
        cands = []
        for _ in range(96):
            f1 = rem[rng.choice(len(rem), p=prob)]
            # partner within a few rings, so both samples likely sit on the same shape
            ring, seen = [f1], {f1}
            for _ in range(3):
                ring = [g for f in ring for g in nb[f] if remaining[g] and g not in seen]
                seen.update(ring)
            partner = [g for g in seen if g != f1 and not ctx.sliver[g]]
            if not partner:
                continue
            f2 = partner[rng.integers(len(partner))]
            hyps = _hyp_from_pair(ctx.C[f1], ctx.N[f1], ctx.C[f2], ctx.N[f2], ctx.diag)
            hyps.append(("pln", ctx.C[f1].copy(), ctx.N[f1].copy()))     # flat bands between rounds
            for hyp in hyps:
                # guesses from triangle centres are a little off (chord sag): score loosely
                cl = cos_pl if hyp[0] == "pln" else cos_lim
                cands.append((sA[_support(hyp, sV, sN, sC, 3 * tol, cl)].sum(), hyp, f1))
        if not cands:
            fails += 1
            continue
        # judge the best few by the CONNECTED area agreeing around their seed — overall
        # agreement can come from scattered faces that merely happen to fit
        cands.sort(key=lambda t: -t[0])
        best = (0.0, None, None, None)
        for sc, hyp, f1 in cands[:6]:
            ok = np.zeros(ctx.nF, bool)
            ok[rem[_support(hyp, Vf, Nf, Cf, 3 * tol, cos_pl if hyp[0] == "pln" else cos_lim)]] = True
            if not ok[f1]:
                continue
            conn = _connected_from(nb, f1, ok)
            ca = ctx.area[conn].sum()
            if ca > best[0]:
                best = (ca, hyp, f1, conn)
        if best[1] is None:
            fails += 1
            for _, _, f1 in cands[:6]:
                noseed[f1] = True
            continue
        prim, seed = best[1], best[2]
        if log: log(f"   ransac: best guess {prim[0]} connected support {best[0]:.1f}")
        region = None
        # alternate: pick the biggest connected patch that agrees, refit to its corners
        for k, t in enumerate((3 * tol, 2 * tol, tol, tol)):
            ok = np.zeros(ctx.nF, bool)
            ok[rem[_support(prim, Vf, Nf, Cf, t, cos_pl if prim[0] == "pln" else cos_lim)]] = True
            region = _connected_from(nb, seed, ok) if ok[seed] else _largest_connected(nb, ok, ctx.area)
            if region is None or len(region) < 2:
                region = None
                break
            p2 = _refit_prim(prim[0], ctx, region)
            if p2 is None:
                break
            prim = p2
        if region is None or ctx.area[region].sum() < min_area:
            if log: log(f"   ransac: region too small ({0 if region is None else ctx.area[region].sum():.1f} < {min_area:.1f})")
            fails += 1
            noseed[best[2]] = True          # just the failed seed: its area may hold other shapes
            continue
        final = None if prim[0] == "pln" else fit_primitive(ctx, region, tol)
        if final is None or final[0] != prim[0]:
            final = prim if _prim_residual(prim, ctx.V[np.unique(ctx.F[region])]) <= tol else None
        if final is not None and final[0] != "pln":
            # a nearly flat strip also fits a huge cylinder/sphere: if a plane does as well,
            # it's a plane — then give its faces back so the real round next to it can grow
            pl = _refit_prim("pln", ctx, region)
            if pl is not None and _prim_residual(pl, ctx.V[np.unique(ctx.F[region])]) <= tol:
                final = pl
            else:
                ext = np.ptp(ctx.V[np.unique(ctx.F[region])], axis=0).max()
                rad = final[3] if final[0] == "cyl" else final[2]
                if rad > 4 * max(ext, 1e-9) or _cone(ctx, region) < 10.0:
                    # radius far beyond the region, or it barely turns (< ~10° across):
                    # not a real round — a flat strip plus a sliver of its neighbour
                    final = None
        if final is None:
            if log: log(f"   ransac: refit failed, residual {_prim_residual(prim, ctx.V[np.unique(ctx.F[region])]):.3f}")
            fails += 1
            noseed[best[2]] = True          # just the failed seed: its area may hold other shapes
            continue
        final = _interior_refit(ctx, region, final, tol)
        out.append((region, final))
        remaining[region] = False
        fails = 0
        if len(out) >= 8 and sum(float(ctx.area[r].sum()) for r, _ in out) / len(out) < 0.015 * total:
            break      # finding only tiny shapes: an organic surface, stop early
    # final sweep: each connected leftover piece gets one direct fit — whole rounds can
    # slip through random sampling
    left = np.zeros(ctx.nF, bool); left[ids] = True
    for r, _ in out:
        left[r] = False
    seen = np.zeros(ctx.nF, bool)
    for f in np.flatnonzero(left):
        if seen[f]:
            continue
        piece = _connected_from(nb, f, left)
        seen[piece] = True
        if ctx.area[piece].sum() < min_area or len(piece) < 3:
            continue
        pr = fit_primitive(ctx, piece, tol)
        if pr is None:
            pl = _refit_prim("pln", ctx, piece)
            if pl is not None and _prim_residual(pl, ctx.V[np.unique(ctx.F[piece])]) <= tol:
                pr = pl
        if pr is not None:
            out.append((piece, _interior_refit(ctx, piece, pr, tol)))
            left[piece] = False
        elif _depth < 2 and len(piece) >= 12 and \
                sum(float(ctx.area[r].sum()) for r, _ in out) >= 0.6 * total:
            # (only when shapes already explain most of the area: on organic surfaces
            #  a second search just burns time before the split is rejected anyway)
            # a second, focused search on just this piece
            sub, _ = grow_primitives(ctx, piece, tol, prm, log, _depth + 1, _seed)
            for sp, spr in sub:
                out.append((sp, spr))
                left[sp] = False
    taken = np.zeros(ctx.nF, bool)
    for r, _ in out:
        taken[r] = True
    return out, ids[~taken[ids]]


def _largest_connected(nb, mask, area):
    faces = np.flatnonzero(mask)
    if not len(faces):
        return None
    seen = np.zeros(len(mask), bool)
    best, best_a = None, 0.0
    for f in faces:
        if seen[f]:
            continue
        comp = _connected_from(nb, f, mask)
        seen[comp] = True
        a = area[comp].sum()
        if a > best_a:
            best, best_a = comp, a
    return best


def _refit_prim(kind, ctx, faces):
    """Least-squares plane/cylinder/sphere through the faces' corners (no tolerance test)."""
    vids = np.unique(ctx.F[faces])
    pts = ctx.V[vids]
    if kind == "pln":
        if len(pts) < 3:
            return None
        o, n = _fit_plane(pts)
        w = ctx.area[faces]
        if (ctx.N[faces] * w[:, None]).sum(0) @ n < 0:
            n = -n
        return ("pln", o, n)
    if len(pts) < 5:
        return None
    Nf = ctx.N[faces]; w = ctx.area[faces]
    if kind == "cyl":
        M = (Nf * w[:, None]).T @ Nf
        ev, evec = np.linalg.eigh(M)
        axis = evec[:, 0]
        u = np.cross(axis, [1, 0, 0] if abs(axis[0]) < 0.9 else [0, 1, 0]); u /= np.linalg.norm(u)
        v = np.cross(axis, u)
        c2, r = _fit_circle2d(np.c_[pts @ u, pts @ v])
        center = c2[0] * u + c2[1] * v
        radial = ctx.C[faces] - center
        radial -= np.outer(radial @ axis, axis)
        outward = float(np.sum(np.einsum("ij,ij->i", radial, Nf) * w)) > 0
        return ("cyl", center, axis, r, outward) if 1e-6 < r < 5 * ctx.diag else None
    A = np.c_[2 * pts, np.ones(len(pts))]
    sol, *_ = np.linalg.lstsq(A, (pts ** 2).sum(1), rcond=None)
    c = sol[:3]; r = math.sqrt(max(sol[3] + c @ c, 0))
    outward = float(np.sum(np.einsum("ij,ij->i", ctx.C[faces] - c, Nf) * w)) > 0
    return ("sph", c, r, outward) if 1e-6 < r < 5 * ctx.diag else None


def _interior_refit(ctx, region, prim, tol):
    """Refit using only the region's interior corners — corners shared with a neighbouring
    shape (e.g. where an edge round runs into a corner ball) bias the radius."""
    if prim[0] == "pln" or prim[0] in SWEPT:
        return prim
    inreg = np.zeros(ctx.nF, bool); inreg[region] = True
    vids = np.unique(ctx.F[region])
    interior = [v for v in vids if all(inreg[f] for f in ctx.vert_faces[v])]
    if len(interior) < 8:
        return prim
    pts = ctx.V[np.array(interior)]
    if prim[0] == "cyl":
        _, c, axis, r, outward = prim
        u = np.cross(axis, [1, 0, 0] if abs(axis[0]) < 0.9 else [0, 1, 0]); u /= np.linalg.norm(u)
        v = np.cross(axis, u)
        c2, r2 = _fit_circle2d(np.c_[pts @ u, pts @ v])
        new = ("cyl", c2[0] * u + c2[1] * v + ((c @ axis) * axis), axis, r2, outward)
    else:
        _, c, r, outward = prim
        A = np.c_[2 * pts, np.ones(len(pts))]
        sol, *_ = np.linalg.lstsq(A, (pts ** 2).sum(1), rcond=None)
        c2 = sol[:3]; r2 = math.sqrt(max(sol[3] + c2 @ c2, 0))
        new = ("sph", c2, r2, outward)
    # keep the refit only if the whole region still fits it
    return new if _prim_residual(new, ctx.V[vids]) <= tol else prim


def _connected_from(nb, seed, mask):
    out, stack, seen = [seed], [seed], {seed}
    while stack:
        f = stack.pop()
        for g in nb[f]:
            if mask[g] and g not in seen:
                seen.add(g); out.append(g); stack.append(g)
    return np.array(out, dtype=np.int64)


# ---------------------------------------------------------------- patch layout
class _Graph:
    """Dual graph (faces as nodes) with smoothness-aware weights."""

    def __init__(self, ctx):
        a, b = ctx.adj[:, 0], ctx.adj[:, 1]
        dist = np.linalg.norm(ctx.C[a] - ctx.C[b], axis=1)
        bend = 1.0 - np.einsum("ij,ij->i", ctx.N[a], ctx.N[b])
        self.a, self.b = a, b
        self.w = dist * (1.0 + 8.0 * bend) + 1e-9

    def sub(self, ids, nF):
        loc = -np.ones(nF, dtype=np.int64); loc[ids] = np.arange(len(ids))
        m = (loc[self.a] >= 0) & (loc[self.b] >= 0)
        n = len(ids)
        G = coo_matrix((self.w[m], (loc[self.a[m]], loc[self.b[m]])), shape=(n, n)).tocsr()
        return G


def _is_disk(ctx, ids):
    e = ctx.fue[ids].ravel()
    ue, cnt = np.unique(e, return_counts=True)
    nv = len(np.unique(ctx.F[ids]))
    chi = nv - len(ue) + len(ids)
    if chi != 1:
        return False
    bnd = ue[cnt == 1]
    if len(bnd) == 0:
        return False
    deg = np.bincount(ctx.edges_unique[bnd].ravel())
    return deg.max() <= 2


def _cone(ctx, ids):
    """How far the patch bends: angle between its average direction and its faces —
    robust: the most extreme 3 % of area (crumpled/folded triangles in scans) is ignored."""
    ids = np.asarray(ids)
    w = ctx.area[ids]
    mn = (ctx.N[ids] * w[:, None]).sum(0)
    nrm = np.linalg.norm(mn)
    if nrm < 1e-12:
        return 180.0
    mn /= nrm
    ang = np.degrees(np.arccos(np.clip(ctx.N[ids] @ mn, -1, 1)))
    if len(ids) < 30:
        return float(ang.max())
    o = np.argsort(ang)
    cw = np.cumsum(w[o]) / max(w.sum(), 1e-300)
    return float(ang[o][min(np.searchsorted(cw, 0.97), len(o) - 1)])


def _split(ctx, graph, ids, k):
    """Geodesic Voronoi split of a connected face set into k parts, Lloyd-relaxed."""
    G = graph.sub(ids, ctx.nF)
    n = len(ids)
    k = max(2, min(k, n))
    cen = (ctx.C[ids] * ctx.area[ids, None]).sum(0) / ctx.area[ids].sum()
    seeds = [int(np.argmax(np.linalg.norm(ctx.C[ids] - cen, axis=1)))]
    dmin = dijkstra(G, directed=False, indices=seeds[0])
    for _ in range(k - 1):
        dd = np.where(np.isfinite(dmin), dmin, -1)
        s = int(np.argmax(dd))
        if s in seeds:
            break
        seeds.append(s)
        dmin = np.minimum(dmin, dijkstra(G, directed=False, indices=s))
    lab = None
    for _ in range(4):
        _, _, src = dijkstra(G, directed=False, indices=seeds, min_only=True,
                             return_predecessors=True)
        lab = np.searchsorted(np.array(sorted(seeds)), src)
        new = []
        for ci, s in enumerate(sorted(seeds)):
            mem = np.where(src == s)[0]
            if len(mem) == 0:
                continue
            c = (ctx.C[ids[mem]] * ctx.area[ids[mem], None]).sum(0) / ctx.area[ids[mem]].sum()
            new.append(int(mem[np.argmin(np.linalg.norm(ctx.C[ids[mem]] - c, axis=1))]))
        if sorted(new) == sorted(seeds):
            break
        seeds = new
    # one label per face; stray fragments (a label's smaller disconnected bits) are given
    # to the neighbouring part they touch most — noisy meshes otherwise shatter into specks
    lab = np.asarray(src, dtype=np.int64).copy()
    loc = -np.ones(ctx.nF, np.int64); loc[ids] = np.arange(len(ids))
    a, b = ctx.adj[:, 0], ctx.adj[:, 1]
    m = (loc[a] >= 0) & (loc[b] >= 0)
    la, lb = loc[a[m]], loc[b[m]]
    for _ in range(3):
        moved = False
        for s in np.unique(lab):
            if s < 0:
                continue
            mem = np.flatnonzero(lab == s)
            Gs = graph.sub(ids[mem], ctx.nF)
            nc, cl = connected_components(Gs, directed=False)
            if nc <= 1:
                continue
            keep = np.argmax(np.bincount(cl, weights=ctx.area[ids[mem]]))
            for c in range(nc):
                if c == keep:
                    continue
                frag = mem[cl == c]
                fset = np.zeros(len(ids), bool); fset[frag] = True
                nbl = np.r_[lab[lb[fset[la]]], lab[la[fset[lb]]]]
                nbl = nbl[(nbl != s) & (nbl >= 0)]
                if len(nbl):
                    lab[frag] = np.bincount(nbl).argmax(); moved = True
        if not moved:
            break
    parts = []
    for s in np.unique(lab):
        mem = ids[lab == s]
        if s < 0 or not len(mem):
            if len(mem):
                parts.append(mem)
            continue
        Gs = graph.sub(mem, ctx.nF)
        nc, cl = connected_components(Gs, directed=False)
        for c in range(nc):
            parts.append(mem[cl == c])
    return parts


def layout_patches(ctx, graph, comp_ids, max_area, max_bend, need_disk=True):
    """Recursively split a smooth component until every patch is a disk within limits."""
    out = []
    stack = [comp_ids]
    guard = 0
    while stack:
        ids = stack.pop()
        guard += 1
        if len(ids) <= 1 or guard > 20000:
            out.append(ids); continue
        a = ctx.area[ids].sum()
        ok_disk = (not need_disk) or _is_disk(ctx, ids)
        ok_cone = _cone(ctx, ids) <= max_bend
        ok_area = a <= max_area
        if ok_disk and ok_cone and ok_area:
            out.append(ids); continue
        k = 2
        if not ok_area:
            k = int(min(8, max(2, math.ceil(a / max_area))))
        parts = _split(ctx, graph, ids, k)
        if len(parts) <= 1:
            out.append(ids); continue
        stack.extend(parts)
    return out


# ---------------------------------------------------------------- boundary chains
def build_chains(ctx, label):
    """Polyline chains along region boundaries, split at junctions.

    Returns (chains, junction_vertices). Each chain: dict(verts=[...], regions=(r1, r2)).
    """
    # directed half-edges
    F = ctx.F
    he = np.concatenate([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]])
    hf = np.concatenate([np.arange(ctx.nF)] * 3)
    key = np.sort(he, axis=1)
    uk, inv = np.unique(key, axis=0, return_inverse=True)
    inv = inv.ravel()
    order = np.argsort(inv, kind="stable")
    starts = np.searchsorted(inv[order], np.arange(len(uk)))
    ends = np.r_[starts[1:], len(order)]
    bedges = {}  # (a,b) sorted -> set of labels
    vadj = {}
    for ei in range(len(uk)):
        hs = order[starts[ei]:ends[ei]]
        labs = {int(label[hf[h]]) for h in hs}
        if len(hs) == 1:
            labs.add(-1)
        if len(labs) >= 2:
            a, b = int(uk[ei, 0]), int(uk[ei, 1])
            bedges[(a, b)] = tuple(sorted(labs))
            vadj.setdefault(a, []).append(b)
            vadj.setdefault(b, []).append(a)
    # vertex label sets
    junction = set()
    for v, nbrs in vadj.items():
        labs = {int(label[f]) for f in ctx.vert_faces[v]}
        if len(nbrs) != 2 or len(labs) >= 3:
            junction.add(v)
        else:
            k1 = bedges[tuple(sorted((v, nbrs[0])))]
            k2 = bedges[tuple(sorted((v, nbrs[1])))]
            if k1 != k2:
                junction.add(v)
    hard = getattr(ctx, "hard_verts", None)
    if hard:                       # hardpoint STL: CAD vertices (e.g. where a line meets an arc)
        junction.update(v for v in hard if v in vadj)
    visited = set()
    chains = []

    def walk(start, nxt):
        verts = [start, nxt]
        visited.add(tuple(sorted((start, nxt))))
        prev, cur = start, nxt
        while cur not in junction:
            cands = [w for w in vadj[cur] if w != prev and tuple(sorted((cur, w))) not in visited]
            if not cands:
                break
            w = cands[0]
            visited.add(tuple(sorted((cur, w))))
            verts.append(w); prev, cur = cur, w
            if cur == start:
                break
        return verts

    for j in list(junction):
        for w in vadj[j]:
            if tuple(sorted((j, w))) in visited:
                continue
            chains.append(walk(j, w))
    # closed loops without junctions
    for (a, b) in list(bedges.keys()):
        if (a, b) in visited:
            continue
        loop = walk(a, b)
        if loop[-1] != loop[0]:
            loop.append(loop[0]) if loop[0] in vadj[loop[-1]] else None
        chains.append(loop)
    # split closed chains (start == end) into 3, or 2 if short
    final = []
    for c in chains:
        if c[0] == c[-1]:
            L = len(c) - 1
            if L >= 3:
                cuts = [0, L // 3, (2 * L) // 3, L]
            else:
                cuts = [0, 1, L]
            for i in range(len(cuts) - 1):
                seg = c[cuts[i]:cuts[i + 1] + 1]
                if len(seg) >= 2:
                    final.append(seg)
            junction.update(c[x] for x in cuts)
        else:
            final.append(c)
    out = []
    for c in final:
        regs = bedges[tuple(sorted((c[0], c[1])))]
        out.append({"verts": c, "regions": regs})
    return out, junction


# ---------------------------------------------------------------- OCC helpers
def _gp(p):
    return gp_Pnt(float(p[0]), float(p[1]), float(p[2]))


def _wire_order(edges):
    """The same edge objects chained end to end (each kept as given), starting from the first.
    The plate filler chains loose edges itself through a map hashed on memory addresses, so
    with loose edges which edge it started from (and whether it ended up asking a null vertex
    for its point) changed from one run to the next (0.10.0: the vase's result differed between
    runs). Given a chain it has nothing to guess. Edges that do not connect stay where they are."""
    def ends(e):
        vs = [_S(BRep_Tool, "Pnt")(_cast("Vertex", v)) for v in _explore(e, TopAbs_VERTEX)]
        return [(p.X(), p.Y(), p.Z()) for p in (vs[0], vs[-1])] if vs else []

    def near(a, b):
        return abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2]) < 1e-7

    try:
        left = list(range(1, len(edges)))
        pts = [ends(e) for e in edges]
        if not all(pts):
            return edges
        out, tail = [0], list(pts[0])
        while left:
            k = next((i for i in left if any(near(tail[1], q) for q in pts[i])), None)
            if k is None:
                k = next((i for i in left if any(near(tail[0], q) for q in pts[i])), None)
                if k is None:
                    return edges
                out.insert(0, k)                         # grows at the front
                tail[0] = pts[k][1] if near(tail[0], pts[k][0]) else pts[k][0]
            else:
                out.append(k)
                tail[1] = pts[k][1] if near(tail[1], pts[k][0]) else pts[k][0]
            left.remove(k)
        return [edges[i] for i in out]
    except Exception:
        return edges


def _own_wire_edges(edges):
    """A private copy of a chain of edges (vertices shared inside the copy), in the same order."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy, BRepBuilderAPI_MakeWire
    try:
        mw = BRepBuilderAPI_MakeWire()
        for e in edges:
            mw.Add(e)
        if not mw.IsDone():
            return []
        cp = BRepBuilderAPI_Copy(mw.Wire()).Shape()
        out = [_cast("Edge", e) for e in _explore(cp, TopAbs_EDGE)]
        return out if len(out) == len(edges) else []
    except Exception:
        return []


def _edge_length(e):
    """Straight-line distance between an edge's two end vertices (0 for a degenerate edge)."""
    try:
        vs = [_S(BRep_Tool, "Pnt")(_cast("Vertex", v)) for v in _explore(e, TopAbs_VERTEX)]
        return vs[0].Distance(vs[-1]) if len(vs) >= 2 else 0.0
    except Exception:
        return 0.0


def _pts_array(pts):
    arr = _ArrPnt(1, len(pts))
    for i, p in enumerate(pts):
        arr.SetValue(i + 1, _gp(p))
    return arr


def _chain_curve(P, tol):
    """Fit a polyline with a line, circular arc, or B-spline. Returns (Geom_Curve, kind)."""
    n = len(P)
    p0, p1 = P[0], P[-1]
    d = p1 - p0
    L = np.linalg.norm(d)
    if n == 2 or L > 1e-9:
        if n == 2:
            return GC_MakeSegment(_gp(p0), _gp(p1)).Value(), "line"
        u = d / L
        off = (P - p0) - np.outer((P - p0) @ u, u)
        if np.linalg.norm(off, axis=1).max() <= 0.05 * tol:
            return GC_MakeSegment(_gp(p0), _gp(p1)).Value(), "line"
    # circle through first, middle, last point
    pm = P[n // 2]
    a, b = pm - p0, p1 - p0
    nrm = np.cross(a, b)
    if np.linalg.norm(nrm) > 1e-9 * max(1.0, np.linalg.norm(a) * np.linalg.norm(b)):
        axb = np.cross(a, b)
        nrm = axb / np.linalg.norm(axb)
        denom = 2 * (axb @ axb)
        if denom > 1e-18:
            c = p0 + ((a @ a) * np.cross(b, axb) + (b @ b) * np.cross(axb, a)) / denom
            r = np.linalg.norm(p0 - c)
            # distance from points to circle
            q = P - c
            h = q @ nrm
            rad = np.linalg.norm(q - np.outer(h, nrm), axis=1)
            dev = np.sqrt(h ** 2 + (rad - r) ** 2)
            if dev.max() <= 0.25 * tol and r < 1e4:
                try:
                    arc = GC_MakeArcOfCircle(_gp(p0), _gp(pm), _gp(p1))
                    if arc.IsDone():
                        return arc.Value(), "arc"
                except Exception:
                    pass
    if n == 3:
        try:
            return GC_MakeArcOfCircle(_gp(P[0]), _gp(P[1]), _gp(P[2])).Value(), "arc"
        except Exception:
            return GC_MakeSegment(_gp(p0), _gp(p1)).Value(), "line"
    if n > 400:  # subsample long polylines (keep ends)
        idx = np.unique(np.r_[np.linspace(0, n - 1, 400).astype(int), n - 1])
        P = P[idx]
    try:
        fit = GeomAPI_PointsToBSpline(_pts_array(P), 3, 8, GeomAbs_C2, 0.5 * tol)
        if fit.IsDone():
            return fit.Curve(), "bspline"
    except Exception:
        pass
    return None, "polyline"


def _curve_bulge(curve, P):
    """How far a fitted curve strays from the polyline it replaces (largest distance from
    points along the curve to the nearest polyline segment)."""
    a, b = curve.FirstParameter(), curve.LastParameter()
    k = int(min(600, 6 * (len(P) - 1) + 1))
    Q = np.array([[q.X(), q.Y(), q.Z()] for q in (curve.Value(float(t)) for t in np.linspace(a, b, k))])
    A, Bp = P[:-1], P[1:]
    ab = Bp - A
    den = np.maximum((ab * ab).sum(1), 1e-30)
    s = np.clip(np.einsum("kmj,mj->km", Q[:, None, :] - A[None, :, :], ab) / den, 0.0, 1.0)
    foot = A[None, :, :] + ab[None, :, :] * s[:, :, None]
    return float(np.linalg.norm(Q[:, None, :] - foot, axis=2).min(1).max())


def _chain_curve_safe(P, tol, round_side=False):
    """Boundary curve for the staged engine: the same fits as _chain_curve, but a curve is
    only used if it stays with the polyline it replaces.

    - a chain with a real corner in it (a turn over 50°) stays a polyline;
    - an arc or spline that bows away from the polyline by more than the tolerance is
      refused — unless it is an arc over evenly spaced points (how a mesher samples a true
      circle), where the bow is just the chord sag of a coarse mesh;
    - three points alone prove nothing: an arc through them is used only when it barely
      differs from the polyline, or when the chain borders a round face."""
    n = len(P)
    seg = np.diff(P, axis=0)
    ln = np.linalg.norm(seg, axis=1)
    if n >= 3 and ln.min() > 1e-12:
        cosang = np.einsum("ij,ij->i", seg[:-1], seg[1:]) / (ln[:-1] * ln[1:])
        if cosang.min() < math.cos(math.radians(50.0)):
            return None, "polyline"
    curve, kind = _chain_curve(P, tol)
    if curve is None or kind == "line":
        return curve, kind
    try:
        bulge = _curve_bulge(curve, P)
    except Exception:
        return None, "polyline"
    if bulge <= tol:
        return curve, kind
    even = ln.min() > 1e-12 and ln.max() <= 2.5 * ln.min()
    if kind == "arc":
        if even and (n >= 4 or round_side):
            return curve, kind
        if n >= 4:
            try:
                Q = P if n <= 400 else P[np.unique(np.r_[np.linspace(0, n - 1, 400).astype(int), n - 1])]
                fit = GeomAPI_PointsToBSpline(_pts_array(Q), 3, 8, GeomAbs_C2, 0.5 * tol)
                if fit.IsDone() and _curve_bulge(fit.Curve(), P) <= tol:
                    return fit.Curve(), "bspline"
            except Exception:
                pass
    return None, "polyline"


class Builder:
    def __init__(self, ctx, label, regions, chains, tol, prm, log):
        self.ctx, self.label, self.regions, self.chains = ctx, label, regions, chains
        self.tol, self.prm, self.log = tol, prm, log
        # (the last settling passes accept 1.5x the limit, so aim at organic_tol / 1.5)
        self.otol = max(tol, (float(getattr(prm, "organic_tol", 0.0) or 0.0)
                              - float(getattr(prm, "organic_used", 0.0) or 0.0)) / 1.5)
        self.prox_full = False     # True when `prox` is the full original mesh (not a reduced one)
        self.loose = self.otol > tol * 1.0001          # guesstimate mode
        self.vtx = {}
        self.edges = []  # per chain: list of (TopoDS_Edge) forward order
        self.mesh_edges = {}  # (lower vertex index, higher): (edge, start vertex index) for every
                              # chain edge that is one straight mesh edge; facets reuse them (0.10.0)
        self.kinds = {"line": 0, "arc": 0, "bspline": 0, "polyline": 0}
        self.dir_he = set(map(tuple, np.concatenate([ctx.F[:, [0, 1]], ctx.F[:, [1, 2]], ctx.F[:, [2, 0]]])))
        he_face = {}
        for fi, (a, b, c) in enumerate(ctx.F):
            he_face[(a, b)] = fi; he_face[(b, c)] = fi; he_face[(c, a)] = fi
        self.he_face = he_face
        self.prox = None
        self.method = {}
        self._make_edges()
        self.region_loops = self._loops()
        if getattr(prm, "rough", False):
            from tostep.plugins.mesh2step import rough
            rough.fix_outlines(self)       # lathe-face outlines must not cross themselves

    def _v(self, vi):
        if vi not in self.vtx:
            self.vtx[vi] = BRepBuilderAPI_MakeVertex(_gp(self.ctx.V[vi])).Vertex()
        return self.vtx[vi]

    def _make_edges(self):
        V = self.ctx.V
        for ch in self.chains:
            vs = ch["verts"]
            P = ch["pts"] if "pts" in ch else V[vs]
            facet_side = any(r >= 0 and self.regions[r]["type"] == "facet" for r in ch["regions"])
            if facet_side:  # match the facet region's straight mesh edges exactly
                curve, kind = None, "polyline"
            else:
                if getattr(self.prm, "rough", False):
                    round_side = any(r >= 0 and self.regions[r]["type"] == "prim" for r in ch["regions"])
                    curve, kind = _chain_curve_safe(P, self.tol, round_side)
                else:
                    curve, kind = _chain_curve(P, self.tol)
            elist = []
            if curve is not None:
                me = BRepBuilderAPI_MakeEdge(curve, self._v(vs[0]), self._v(vs[-1]))
                if me.IsDone():
                    elist = [me.Edge()]
                else:
                    kind = "polyline"
            if not elist:  # polyline fallback: one straight edge per segment
                for i in range(len(vs) - 1):
                    me = BRepBuilderAPI_MakeEdge(self._v(vs[i]), self._v(vs[i + 1]))
                    if me.IsDone():
                        elist.append(me.Edge())
                        self._share(vs[i], vs[i + 1], elist[-1])
                kind = "polyline"
            self.kinds[kind] += 1
            ch["kind"] = kind
            self.edges.append(elist)

    def _share(self, a, b, edge):
        a, b = int(a), int(b)
        self.mesh_edges[(a, b) if a < b else (b, a)] = (edge, a)

    def demote_chain(self, ci):
        """Rebuild one chain as straight segments between its mesh vertices."""
        ch = self.chains[ci]
        if ch.get("kind") == "polyline":
            return False
        vs = ch["verts"]
        elist = []
        for i in range(len(vs) - 1):
            me = BRepBuilderAPI_MakeEdge(self._v(vs[i]), self._v(vs[i + 1]))
            if me.IsDone():
                elist.append(me.Edge())
                self._share(vs[i], vs[i + 1], elist[-1])
        self.kinds[ch["kind"]] -= 1
        self.kinds["polyline"] += 1
        ch["kind"] = "polyline"
        self.edges[ci] = elist
        return True

    def _loops(self):
        """For each region: list of loops, each loop = list of (chain index, reversed)."""
        per_region = {}
        for ci, ch in enumerate(self.chains):
            vs = ch["verts"]
            for r in ch["regions"]:
                if r < 0:
                    continue
                a, b = vs[0], vs[1]
                f = self.he_face.get((a, b))
                fwd = f is not None and self.label[f] == r
                if not fwd:
                    f2 = self.he_face.get((b, a))
                    if f2 is None or self.label[f2] != r:
                        fwd = True  # fallback
                start, end = (vs[0], vs[-1]) if fwd else (vs[-1], vs[0])
                per_region.setdefault(r, []).append((ci, not fwd, start, end))
        loops = {}
        for r, items in per_region.items():
            by_start = {}
            for it in items:
                by_start.setdefault(it[2], []).append(it)
            used = set()
            lps = []
            for it in items:
                if it[0] in used and (it[0], it[1]) in used:
                    continue
                if (it[0], it[1]) in used:
                    continue
                loop = []
                cur = it
                first = it[2]
                while cur is not None and (cur[0], cur[1]) not in used:
                    used.add((cur[0], cur[1]))
                    loop.append((cur[0], cur[1]))
                    if cur[3] == first:
                        break
                    nxt = [c for c in by_start.get(cur[3], []) if (c[0], c[1]) not in used]
                    cur = nxt[0] if nxt else None
                lps.append(loop)
            loops[r] = lps
        return loops

    def wire(self, loop, own=False):
        """The loop's outline as a wire of the shared chain edges; own=True gives a private copy
        of it instead (vertices still shared inside the copy), for a face whose repair must
        not touch the outline the other faces use (see _trim)."""
        mw = BRepBuilderAPI_MakeWire()
        for ci, rev in loop:
            es = self.edges[ci]
            seq = reversed(es) if rev else es
            for e in seq:
                mw.Add(_cast("Edge", e.Reversed()) if rev else e)
        if not mw.IsDone():
            return None
        if own:
            from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy
            return _cast("Wire", BRepBuilderAPI_Copy(mw.Wire()).Shape())
        return mw.Wire()

    def loop_points(self, loop):
        pts = []
        for ci, rev in loop:
            vs = self.chains[ci]["verts"]
            vs = vs[::-1] if rev else vs
            pts.extend(vs[:-1])
        return self.ctx.V[pts]

    # -- faces
    def _outline_all_round(self, r, loops):
        """Does every chain of this region's outline separate it from another round (prim) face?"""
        for lp in loops:
            for ci, _ in lp:
                others = [o for o in self.chains[ci]["regions"] if o != r]
                if not others or any(o < 0 or self.regions[o]["type"] != "prim" for o in others):
                    return False
        return True

    def plane_face(self, r, info):
        o, n = info["plane"]
        loops = self.region_loops.get(r, [])
        if not loops:
            return None
        ax = gp_Ax3(_gp(o), gp_Dir(*map(float, n)))
        u = np.array([ax.XDirection().X(), ax.XDirection().Y(), ax.XDirection().Z()])
        v = np.cross(n, u)

        def sarea(lp):
            p = self.loop_points(lp) - o
            x, y = p @ u, p @ v
            return 0.5 * np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y)
        areas = [sarea(lp) for lp in loops]
        oi = int(np.argmax(np.abs(areas)))
        outer = self.wire(loops[oi], own=True)       # own copy: the repair must not widen shared vertices
        if outer is None:
            return None
        mf = BRepBuilderAPI_MakeFace(gp_Pln(ax), outer, True)
        if not mf.IsDone():
            return None
        for i, lp in enumerate(loops):
            if i != oi:
                w = self.wire(lp, own=True)
                if w is not None:
                    mf.Add(w)
        return self._fix(mf.Face())

    def prim_face(self, r, info):
        loops = self.region_loops.get(r, [])
        if not loops:
            return None
        surf = info["surf_geom"]
        flip = info.get("flip", False)
        if info.get("prim") is not None and info["prim"][0] in SWEPT:
            from tostep.plugins.mesh2step import rough
            f = _try(rough.trimmed_face, self, r, info)      # exact trimming in the surface's own coordinates
            if f is not None:
                return f
            if len(loops) > 1:
                oi = rough.loop_order(info["prim"], [self.loop_points(lp) for lp in loops])
                loops = [loops[oi]] + [lp for i, lp in enumerate(loops) if i != oi]
        if flip:  # surface normal points away from the solid: build reversed, then flip face
            loops = [[(ci, not rev) for ci, rev in reversed(lp)] for lp in loops]
        target = float(self.ctx.area[info["faces"]].sum())

        def build(lps, fix, own):
            outer = self.wire(lps[0], own=own)
            if outer is None:
                return None
            mf = BRepBuilderAPI_MakeFace(surf, outer, True)
            if not mf.IsDone():
                return None
            for lp in lps[1:]:
                w = self.wire(lp, own=own)
                if w is not None:
                    mf.Add(w)
            f = mf.Face()
            return self._fix(f) if fix else f

        # On a closed surface (sphere, full cylinder) the outline alone doesn't say which
        # side is the face, and the standard face repair may "correct" it into the
        # complement — a whole ball where a corner patch belongs. Build without repair,
        # and keep whichever side matches the mesh area.
        rev = [[(ci, not r_) for ci, r_ in reversed(lp)] for lp in loops]
        # sample points of the region's own mesh: the right side must contain them
        fc = info["faces"]
        pick = fc if len(fc) <= 12 else fc[np.linspace(0, len(fc) - 1, 12).astype(int)]
        probe = self.ctx.C[pick]
        # Private outline (0.10.1): the face repair widens outline vertices to make the wire
        # close on a round surface that misses them a little, and on a shared outline that
        # reached the faces next door (the ridged test knob, whose dome is a sphere patch: 963
        # faces, mostly facets, instead of 69). Only a round face whose whole outline runs
        # along other round faces keeps the shared outline: the two halves of a sphere only
        # build because the first half's repair fixes the shared seam for the second.
        own = not self._outline_all_round(r, loops)
        best = None
        for lps, fix in ((loops, True), (loops, False), (rev, True), (rev, False)):
            f = _try(build, lps, fix, own)
            a = _face_area(f)
            if a is None:
                continue
            inside = _fraction_inside(f, probe)
            valid = BRepCheck_Analyzer(f).IsValid()
            # must contain the mesh, then match its area, then be valid as built
            score = (inside >= 0.9, abs(a - target) <= 0.1 * target, valid, -abs(a - target))
            if best is None or score > best[0]:
                best = (score, f)
            if score[:3] == (True, True, True):
                break
        if best is None:
            return None
        f = best[1]
        if flip:
            f = _cast("Face", f.Reversed())
        return f

    def nurbs_face_fill(self, r, info):
        loops = self.region_loops.get(r, [])
        if len(loops) != 1:
            return None, float("inf")
        fids = info["faces"]
        vids = np.unique(self.ctx.F[fids])
        bverts = set()
        for ci, _ in loops[0]:
            bverts.update(self.chains[ci]["verts"])
        near = set(bverts)
        for fi in fids:  # boundary 1-ring: smoothed boundary curves may cut through it
            tri = self.ctx.F[fi]
            if bverts.intersection(tri.tolist()):
                near.update(tri.tolist())
        interior = np.array([v for v in vids if v not in near], dtype=np.int64)
        if len(interior) < 3:
            interior = np.array([v for v in vids if v not in bverts], dtype=np.int64)
        V = self.ctx.V
        cons = list(interior[_fps(V[interior], self.prm.max_points)]) if len(interior) else []
        meas = vids
        if len(meas) > 400:
            meas = np.random.default_rng(0).choice(meas, 400, replace=False)
        best = (None, float("inf"))
        # An edge shorter than the filler's own 3D tolerance (two all-but-coincident outline
        # points) carries no boundary and only confuses it: left out (0.10.0; the gap it leaves
        # is below that tolerance). The edges then go in chained, see _wire_order.
        fill_tol = max(1e-4, 0.2 * self.tol)
        bound = [e for ci, rev in loops[0] for e in self.edges[ci] if _edge_length(e) > fill_tol]
        if not bound:
            return None, float("inf")
        # The filler alters the edges it is given: it raised the tolerance of shared outline
        # vertices to millimetres (0.029 -> 4.4 mm on the knob), after which every check let a
        # neighbouring face that was 1.3 mm off through. It gets its own copy of the outline
        # (one wire, so the copy still shares its vertices inside) and the originals stay as
        # they are (0.10.0).
        bound = _own_wire_edges(_wire_order(bound))
        if not bound:
            return None, float("inf")
        for attempt in range(4):
            fl = BRepFill_Filling(3, 30, 3, False, 1e-5, fill_tol, 0.05, 0.1, 12, 30)
            for e in bound:
                fl.Add(e, GeomAbs_C0, True)
            for vi in cons:
                fl.Add(_gp(V[vi]))
            try:
                fl.Build()
            except Exception:
                break
            if not fl.IsDone():
                break
            face = fl.Face()
            surf = _S(BRep_Tool, "Surface")(face)
            # measure patch vertices whose projection falls inside the trimmed face
            # (outside the trim the plate extrapolates freely and doesn't count);
            # the boundary itself is covered by G0Error
            dev, n_in, worst = 0.0, 0, []
            pr = _projector(surf)
            for vi in meas:
                pr.Perform(_gp(V[vi]))
                if pr.NbPoints() == 0:
                    continue
                u, v = pr.LowerDistanceParameters()
                if BRepClass_FaceClassifier(face, gp_Pnt2d(u, v), 1e-6).State() == TopAbs_OUT:
                    continue
                n_in += 1
                d = pr.LowerDistance()
                dev = max(dev, d)
                if d > 0.4 * self.tol:
                    worst.append((d, int(vi)))
            if n_in < max(1, len(meas) // 4):
                dev = float("inf")  # surface wandered away from the patch
            dev = max(dev, fl.G0Error())
            if dev <= 1.5 * self.tol and self.prox is not None:
                dev = max(dev, self._reverse_dev(face, fids))
            if dev < best[1]:
                best = (face, dev)
            if dev <= 0.8 * self.tol:
                break
            # pin the worst-fitting vertices and try again
            cs = set(cons)
            add = [vi for d, vi in sorted(worst, reverse=True) if vi not in cs][:40]
            if not add:
                break
            cons.extend(add)
        return best

    def nurbs_face(self, r, info):
        """Least-squares B-spline surface fitted to the patch (plus a margin of
        neighbouring vertices), trimmed by the boundary curves. Falls back to plate
        filling if that doesn't work out."""
        face, dev = _try2(self.nurbs_face_lsq, r, info)
        self.method[r] = "lsq"
        if face is not None and dev <= self.tol:
            return face, dev
        how = str(getattr(self.prm, "gap_fill", "plate") or "plate")
        st = _fill_stats(self.ctx)
        if how in ("coons", "coons+plate"):
            # (D18) tried in guesstimate mode too, where the plate filler never runs
            t = time.time()
            f3, d3 = _try2(self.nurbs_face_coons, r, info)
            st["coons_s"] += time.time() - t
            st["coons_tried"] += 1
            if f3 is not None and d3 < dev:
                st["coons_used"] += 1
                self.method[r] = "coons"
                face, dev = f3, d3
                if dev <= self.tol:
                    return face, dev
        if os.environ.get("TOSTEP_FILL_COMPARE") == "1":     # research: every filler on every patch
            t = time.time(); _, dc = _try2(self.nurbs_face_coons, r, info); tc = time.time() - t
            t = time.time(); _, dp = _try2(self.nurbs_face_fill, r, info); tp = time.time() - t
            _, dl = _try2(self.nurbs_face_lsq, r, info)
            _DEBUG.setdefault("fill_compare", []).append((len(info["faces"]), dl, dc, tc, dp, tp, self.tol))
        if how not in ("plate", "coons+plate"):
            return face, dev
        if self.loose:
            return face, dev          # guesstimate mode: no slow plate filling
        cap = int(getattr(self.prm, "max_fills", 0) or 0)
        if cap and getattr(self.ctx, "n_fills", 0) >= cap:
            return face, dev          # the plate-fill allowance is spent
        if _memory_tight():
            self.log("  (memory is tight: skipping the plate filler for this patch)")
            return face, dev
        self.ctx.n_fills = getattr(self.ctx, "n_fills", 0) + 1
        t = time.time()
        f2, d2 = _try2(self.nurbs_face_fill, r, info)
        st["plate_s"] += time.time() - t
        st["plate_tried"] += 1
        if f2 is not None and d2 < dev:
            st["plate_used"] += 1
            self.method[r] = "fill"
            return f2, d2
        return face, dev

    # ------------------------------------------------------------ Coons fill (D18)
    def _loop_polyline(self, loop, n=240):
        """The loop's outline sampled evenly by arc length (closed, n points, no repeat), from the
        very edges the face will be trimmed by."""
        from OCP.BRepAdaptor import BRepAdaptor_CompCurve
        from OCP.GCPnts import GCPnts_QuasiUniformAbscissa
        w = self.wire(loop, own=True)
        if w is None:
            return None
        cc = BRepAdaptor_CompCurve(w)
        ab = GCPnts_QuasiUniformAbscissa(cc, n + 1)
        if not ab.IsDone() or ab.NbPoints() < 8:
            return None
        P = np.array([[cc.Value(ab.Parameter(i)).X(), cc.Value(ab.Parameter(i)).Y(), cc.Value(ab.Parameter(i)).Z()]
                      for i in range(1, ab.NbPoints() + 1)])
        if np.linalg.norm(P[0] - P[-1]) < 1e-6 * max(1.0, np.ptp(P, axis=0).max()):
            P = P[:-1]
        return P

    @staticmethod
    def _corner_candidates(P):
        """Sets of four corners to try on a closed outline: four of its sharpest turns (local
        peaks, up to ten of them), and four evenly spaced starts by arc length."""
        from itertools import combinations
        n = len(P)
        w = max(2, n // 60)
        d1 = P - np.roll(P, w, axis=0)
        d2 = np.roll(P, -w, axis=0) - P
        c = (d1 * d2).sum(1) / np.maximum(np.linalg.norm(d1, axis=1) * np.linalg.norm(d2, axis=1), 1e-12)
        turn = np.degrees(np.arccos(np.clip(c, -1, 1)))
        peaks = [i for i in range(n) if turn[i] >= 12.0 and turn[i] >= turn[i - 1] and turn[i] >= turn[(i + 1) % n]]
        keep = []
        for i in sorted(peaks, key=lambda i: -turn[i]):
            if all(min(abs(i - j), n - abs(i - j)) > w for j in keep):
                keep.append(i)
            if len(keep) == 10:
                break
        out = [tuple(sorted(c4)) for c4 in combinations(keep, 4)
               if min(np.diff(sorted(c4) + [sorted(c4)[0] + n])) >= 3]
        s0 = int(np.argmax(turn))
        for k in range(4):
            st = s0 + k * n // 16
            out.append(tuple(sorted((st + j * n // 4) % n for j in range(4))))
        return list(dict.fromkeys(out))

    @staticmethod
    def _sides(P, cs, m):
        """The four sides between the corners, each resampled to m points evenly by length."""
        n = len(P)
        sides = []
        for k in range(4):
            i0, i1 = cs[k], cs[(k + 1) % 4]
            idx = np.arange(i0, i1 + (n if i1 <= i0 else 0) + 1) % n
            seg = P[idx]
            L = np.r_[0, np.cumsum(np.linalg.norm(np.diff(seg, axis=0), axis=1))]
            if L[-1] < 1e-9:
                return None
            t = np.linspace(0, L[-1], m)
            sides.append(np.c_[[np.interp(t, L, seg[:, j]) for j in range(3)]].T)
        return sides

    @staticmethod
    def _coons_grid(sides):
        """Bilinearly blended Coons surface of four point-sampled sides on an m x m grid
        (numpy, for choosing the corners before the real surface is built)."""
        c0, c1, c2, c3 = sides                      # contour order: c0: A->B, c1: B->C, c2: C->D, c3: D->A
        m = len(c0)
        u = np.linspace(0, 1, m)[:, None, None]
        v = np.linspace(0, 1, m)[None, :, None]
        bot, top = c0[:, None, :], c2[::-1][:, None, :]          # along u at v=0 / v=1
        left, right = c3[::-1][None, :, :], c1[None, :, :]      # along v at u=0 / u=1
        A, B, C, D = c0[0], c0[-1], c2[0], c2[-1]
        return ((1 - v) * bot + v * top + (1 - u) * left + u * right
                - ((1 - u) * (1 - v) * A + u * (1 - v) * B + u * v * C + (1 - u) * v * D))

    def nurbs_face_coons(self, r, info):
        """(D18) A Coons patch on four sides of the patch's own outline (the corners at its
        sharpest turns), with its inner control points fitted to the mesh by least squares (the
        same fit as the Quad patches engine), trimmed by the outline. Unlike the least-squares
        fit it needs no flat projection of the patch, so it also follows patches that curl
        round. Returns (face, deviation). Measured in the paper (D18): within the tolerance on
        more patches than the plate filler, but slower per patch."""
        from scipy.spatial import cKDTree
        from OCP.GeomFill import GeomFill_BSplineCurves, GeomFill_FillingStyle
        from tostep.plugins.mesh2step import quadfit
        loops = self.region_loops.get(r, [])
        if len(loops) != 1:
            return None, float("inf")
        P = self._loop_polyline(loops[0], 600)
        if P is None or len(P) < 16:
            return None, float("inf")
        fids = info["faces"]
        # the patch's triangles and two rings of neighbours: a smoothed outline cuts across the
        # triangles along the patch's edge (on the coarse blob it ran up to 2 mm outside them)
        near = set(int(f) for f in fids)
        for _ in range(2):
            ring = set()
            for f in near:
                for vi in self.ctx.F[f]:
                    ring.update(int(g) for g in self.ctx.vert_faces[vi])
            near |= ring
        # The Coons patch is built on the outline grown outwards along the mesh by a margin, so
        # the real outline lies inside it and the face is trimmed by the shared outline like
        # every other fitted face. (An untrimmed patch, its own four edges standing in for the
        # outline, does not share its neighbours' edges: on the blob the assembly's repairs
        # then left 2,688 faces.)
        grow = 0.04 * float(np.ptp(P, axis=0).max()) + 3.0 * self.tol
        tri_tree = getattr(self.ctx, "_c_tree", None)
        if tri_tree is None:
            tri_tree = self.ctx._c_tree = cKDTree(self.ctx.C)
        for idx in tri_tree.query_ball_point(P[::4], r=grow * 1.6 + float(self.ctx.diag) * 1e-3):
            near.update(int(i) for i in idx)
        sub = trimesh.Trimesh(self.ctx.V, self.ctx.F[sorted(near)], process=False)
        # outward: in the mesh's tangent plane, at right angles to the outline, away from the
        # middle of the patch triangles near each outline point
        T = np.roll(P, -1, axis=0) - np.roll(P, 1, axis=0)
        T /= np.maximum(np.linalg.norm(T, axis=1), 1e-12)[:, None]
        fa = np.asarray(fids)
        reg_tree = cKDTree(self.ctx.C[fa])
        _, k1 = reg_tree.query(P)
        O = np.cross(T, self.ctx.N[fa[k1]])
        O /= np.maximum(np.linalg.norm(O, axis=1), 1e-12)[:, None]
        kk = min(12, len(fa))
        _, kn = reg_tree.query(P, k=kk)
        inward = self.ctx.C[fa[np.asarray(kn).reshape(len(P), kk)]].mean(axis=1) - P
        O[(inward * O).sum(1) > 0] *= -1
        P = quadfit._closest(sub, P + grow * O)
        vids = np.unique(self.ctx.F[fids])
        # the patch's inside: its vertices away from its edge (the outline may cut across the
        # edge triangles, so the edge vertices can lie outside it)
        Fr = self.ctx.F[fids]
        es = np.sort(np.vstack([Fr[:, [0, 1]], Fr[:, [1, 2]], Fr[:, [2, 0]]]), axis=1)
        ue, cnt = np.unique(es, axis=0, return_counts=True)
        edge_v = set(ue[cnt == 1].ravel().tolist())
        edge_ring = set(Fr[np.isin(Fr, list(edge_v)).any(axis=1)].ravel().tolist()) if edge_v else set()
        inside = np.array([v for v in vids if v not in edge_ring], dtype=np.int64)
        Vr = self.ctx.V[inside] if len(inside) >= 3 else self.ctx.V[vids]
        # the corners: whichever candidate set gives the plain Coons surface closest to the
        # patch, both ways (surface -> mesh and mesh -> surface)
        scored = []
        for c4 in self._corner_candidates(P):
            sd = self._sides(P, c4, 25)
            if sd is None:
                continue
            G = self._coons_grid(sd).reshape(-1, 3)
            d1 = float(np.linalg.norm(quadfit._closest(sub, G) - G, axis=1).max())
            d2 = float(cKDTree(G).query(Vr)[0].max())
            scored.append((max(d1, d2), c4))
        if not scored:
            return None, float("inf")
        cs = min(scored)[1]
        # each side least-squares fitted with the same uniform knots (so the Coons patch has
        # K x K control points, not the union of four knot vectors), K the smallest that brings
        # every side within half the tolerance of its outline points (13 interpolated points
        # missed the blob's outline by up to 0.5 mm; separately approximated sides gave Coons
        # patches of up to 60 x 100 control points)
        n = len(P)
        segs = []
        for k in range(4):
            i0, i1 = cs[k], cs[(k + 1) % 4]
            seg = P[np.arange(i0, i1 + (n if i1 <= i0 else 0) + 1) % n]
            L = np.r_[0, np.cumsum(np.linalg.norm(np.diff(seg, axis=0), axis=1))]
            if L[-1] < 1e-9:
                return None, float("inf")
            segs.append((seg, L / L[-1]))
        poles = None
        for K in (6, 8, 11, 15, 20, 26):
            out, worst = [], 0.0
            for seg, x in segs:
                Bm, _ = _clamped_basis(x, K)
                # ends pinned: corners shared by two sides
                Ci = np.zeros((K, 3)); Ci[0], Ci[-1] = seg[0], seg[-1]
                rhs = seg - Bm[:, [0, -1]] @ Ci[[0, -1]]
                Am = Bm[:, 1:-1]
                Ci[1:-1] = np.linalg.lstsq(Am.T @ Am + 1e-9 * np.eye(K - 2), Am.T @ rhs, rcond=None)[0]
                worst = max(worst, float(np.linalg.norm(Bm @ Ci - seg, axis=1).max()))
                out.append(Ci)
            poles = out
            if worst <= 0.5 * self.tol:
                break
        _, inner_k = _clamped_basis(np.zeros(1), K)
        curves = [_occ_bspline_curve(C, inner_k) for C in poles]

        def coons():
            return GeomFill_BSplineCurves(curves[0], curves[1], curves[2], curves[3],
                                          GeomFill_FillingStyle.GeomFill_CoonsStyle).Surface()
        mv = inside if len(inside) >= 3 else vids
        meas = mv if len(mv) <= 400 else np.random.default_rng(0).choice(mv, 400, replace=False)
        best = (None, float("inf"))
        for extra in (2, 5, 9):
            surf = coons()
            if max(surf.NbUPoles(), surf.NbVPoles()) > 30:
                return None, float("inf")             # (memory: the fit's matrix grows with the 4th power)
            p = quadfit._Patch(surf, extra, 16)
            if not p.movable:
                break
            for _ in range(3):
                T = quadfit._closest(sub, p.points())
                p.solve(T)
            surf = p.apply()
            face = self._trim_pc(surf, loops[0])
            if face is None:
                continue
            # measured as the plate filler's result is: patch vertices whose nearest surface point
            # falls inside the trimmed face (the outline's own distance from the mesh is the
            # chains' business, and the surface -> mesh check below covers the rest)
            dev, n_in = 0.0, 0
            pr = _projector(surf)
            for vi in meas:
                pr.Perform(_gp(self.ctx.V[vi]))
                if pr.NbPoints() == 0:
                    continue
                u, v = pr.LowerDistanceParameters()
                if BRepClass_FaceClassifier(face, gp_Pnt2d(u, v), 1e-6).State() == TopAbs_OUT:
                    continue
                n_in += 1
                dev = max(dev, pr.LowerDistance())
            if n_in < max(1, len(meas) // 4):
                continue
            if dev <= 1.5 * self.tol and self.prox is not None:
                dev = max(dev, self._reverse_dev(face, fids))
            if self.loose:
                dev = dev * self.tol / self.otol
            if dev < best[1]:
                best = (face, dev)
            if dev <= 0.8 * self.tol:
                break
        return best

    def nurbs_face_lsq(self, r, info):
        loops = self.region_loops.get(r, [])
        if len(loops) != 1:
            return None, float("inf")
        ctx = self.ctx
        fids = info["faces"]
        vids = np.unique(ctx.F[fids])
        w = ctx.area[fids]
        nrm = (ctx.N[fids] * w[:, None]).sum(0)
        if np.linalg.norm(nrm) < 1e-12:
            return None, float("inf")
        nrm /= np.linalg.norm(nrm)
        # margin: neighbouring vertices on the same smooth component, so the surface
        # extends a little past the trim curves
        ring = set()
        comp = info.get("comp")
        vset = set(vids.tolist())
        for vi in vids:
            for f in ctx.vert_faces[vi]:
                if ctx.face_comp[f] == comp:
                    ring.update(ctx.F[f].tolist())
        ring = np.array(sorted(ring - vset), dtype=np.int64)
        P = ctx.V[vids]
        Pr = ctx.V[ring] if len(ring) else np.zeros((0, 3))
        # parametrise by projection onto the mean-normal plane
        c0 = P.mean(0)
        q = (P - c0) - np.outer((P - c0) @ nrm, nrm)
        _, _, vt = np.linalg.svd(q, full_matrices=False)
        e1 = vt[0] - (vt[0] @ nrm) * nrm; e1 /= np.linalg.norm(e1)
        e2 = np.cross(nrm, e1)
        allP = np.vstack([P, Pr])
        S = np.c_[(allP - c0) @ e1, (allP - c0) @ e2]
        lo, hi = S[:len(P)].min(0), S[:len(P)].max(0)
        span = hi - lo
        if span.min() < 1e-9:
            return None, float("inf")
        lo = lo - 0.15 * span; hi = hi + 0.15 * span
        keep = np.all((S >= lo) & (S <= hi), axis=1)
        keep[:len(P)] = True
        allP, S = allP[keep], S[keep]
        Wt = np.r_[np.ones(len(P)), np.full(len(allP) - len(P), 0.35)]
        U = (S - lo) / (hi - lo)
        # also fit the boundary curve points so the surface hugs the trim
        bpts = []
        for ci, _ in loops[0]:
            ch = self.chains[ci]
            bpts.append(ch["pts"] if "pts" in ch else ctx.V[ch["verts"]])
        B = np.vstack(bpts)
        Ub = np.c_[(B - c0) @ e1, (B - c0) @ e2]
        Ub = (Ub - lo) / (hi - lo)
        okb = np.all((Ub >= 0) & (Ub <= 1), axis=1)
        allP = np.vstack([allP, B[okb]]); U = np.vstack([U, Ub[okb]])
        n_b = int(okb.sum())
        # guesstimate mode: the inside of the patch may drift, so pull harder on its outline
        Wt = np.r_[Wt, np.full(n_b, 6.0 if self.loose else 2.0)]
        n_data = len(P)
        ar = span[0] / span[1]
        best = (None, float("inf"))
        tried = set()
        for dens in (0.25, 0.5, 1.0):
            k = max(16, min(576, int(dens * n_data / 2)))
            nu = int(np.clip(round(math.sqrt(k * ar)), 4, 24))
            nv = int(np.clip(round(math.sqrt(k / ar)), 4, 24))
            if (nu, nv) in tried:
                continue
            tried.add((nu, nv))
            C, res = _lsq_bspline(allP, U, Wt, nu, nv)
            if C is None:
                continue
            fit_dev = float(res[:n_data].max())
            if self.loose:
                # inside: up to the organic tolerance. Outline: still tight, so neighbours sew
                # exactly as in the normal mode.
                bdev = float(res[-n_b:].max()) if n_b else 0.0
                if (fit_dev > 1.5 * self.otol or bdev > 3.0 * self.tol) and dens < 1.0:
                    continue
            elif fit_dev > 1.5 * self.tol and dens < 1.0:
                continue
            surf = _occ_bspline_surface(C, nu, nv)
            face = self._trim(surf, loops[0])
            if face is None:
                continue
            dev = max(fit_dev, self._reverse_dev(face, fids))
            if self.loose:
                # scale so "dev <= tol" everywhere else means: inside <= organic tol, outline <= 2*tol
                dev = max(dev * self.tol / self.otol, bdev / 2.0)
            if dev < best[1]:
                best = (face, dev)
            if dev <= self.tol:
                break
        return best

    def _trim(self, surf, loop):
        # The repair below widens vertices to make the wire close on a surface that misses
        # them: on the knob a shared outline vertex went from 0.03 mm to 4.7 mm of tolerance,
        # after which every check let a neighbouring face that was 1.3 mm off through. A
        # fitted surface can miss by a lot, so this face gets its own copy of the outline
        # (0.10.0); flat and round faces, whose surfaces pass through the outline's points,
        # keep sharing it as before (on a sphere the shared seam is what makes the second
        # half build valid).
        w = self.wire(loop, own=True)
        if w is None:
            return None
        before = _max_tolerance(w)
        mf = BRepBuilderAPI_MakeFace(surf, w, True)
        if not mf.IsDone():
            return None
        sf = ShapeFix_Face(mf.Face())
        sf.Perform()
        face = sf.Face()
        if not BRepCheck_Analyzer(face).IsValid():
            fx = ShapeFix_Shape(face); fx.Perform()
            face = _cast("Face", fx.Shape()) if fx.Shape().ShapeType().name.endswith("FACE") else face
        # How much the repair had to widen the outline's tolerances is the size of the gap
        # between the surface and its outline. A face closed by widening of more than twice
        # the tolerance (what the assembly's own sewing closes anyway) is not a fit: it would
        # bridge a gap that big, and with a private outline nothing else would notice. Such a
        # face is refused here. (Exactly the tolerance was tried: it also refused good patches,
        # the organic blob went from 24 to 46 faces and 0.06 to 0.13 mm.)
        if _max_tolerance(face) > before + 2.0 * self.tol:
            return None
        return face

    def _trim_pc(self, surf, loop):
        """(D18) Like _trim, but every outline edge first gets its curve on the surface from its
        own points projected onto the surface (sampled, then interpolated), instead of leaving
        that to the face builder, whose own projection gave curves thousands of millimetres off
        on the fitted Coons surfaces. Refused like _trim if the outline is more than twice the
        tolerance off the surface."""
        from OCP.BRepTools import BRepTools_WireExplorer
        from OCP.Geom2dAPI import Geom2dAPI_Interpolate
        try:
            from OCP.collections import HArray1_gp_Pnt2d as _HP2, HArray1_double as _HR
        except ImportError:
            from OCP.TColgp import TColgp_HArray1OfPnt2d as _HP2
            from OCP.TColStd import TColStd_HArray1OfReal as _HR
        from OCP.gp import gp_Pnt2d as _P2
        w = self.wire(loop, own=True)
        if w is None:
            return None
        pr = _projector(surf)
        bb = BRep_Builder()
        ex = BRepTools_WireExplorer(w)
        worst = 0.0
        u1, u2, v1, v2 = surf.Bounds()
        span = max(u2 - u1, v2 - v1)
        while ex.More():
            e = ex.Current()
            from OCP.BRepAdaptor import BRepAdaptor_Curve
            c3 = BRepAdaptor_Curve(e)
            a, b = c3.FirstParameter(), c3.LastParameter()
            k = 12
            ts = np.linspace(a, b, k)
            arr = _HP2(1, k)
            par = _HR(1, k)
            prev = None
            for i, t in enumerate(ts):
                q = c3.Value(float(t))
                pr.Perform(q)
                if pr.NbPoints() == 0:
                    return None
                u, v = pr.LowerDistanceParameters()
                worst = max(worst, pr.LowerDistance())
                if prev is not None and math.hypot(u - prev[0], v - prev[1]) > 0.5 * span:
                    return None                         # the projection jumped: a fold
                prev = (u, v)
                arr.SetValue(i + 1, _P2(u, v))
                par.SetValue(i + 1, float(t))
            it = Geom2dAPI_Interpolate(arr, par, False, 1e-9)
            it.Perform()
            if not it.IsDone():
                return None
            bb.UpdateEdge(e, it.Curve(), surf, TopLoc_Location(), max(1e-7, 1.5 * worst))
            ex.Next()
        if worst > 2.0 * self.tol:
            return None
        mf = BRepBuilderAPI_MakeFace(surf, w, True)
        if not mf.IsDone():
            return None
        sf = ShapeFix_Face(mf.Face())
        sf.Perform()
        face = sf.Face()
        if _max_tolerance(face) > 3.0 * self.tol:
            return None
        return face

    def _reverse_dev(self, face, fids):
        """Surface -> mesh check: catches patches that bulge or fold where there are no
        mesh vertices to notice it. Allows for the mesh's own chord sag."""
        # quick reject before meshing: a patch much bigger than its region is wrong, and
        # meshing a runaway surface finely can take forever
        rv = self.ctx.V[np.unique(self.ctx.F[fids])]
        rdiag = float(np.linalg.norm(np.ptp(rv, axis=0))) if len(rv) else 0.0
        b = Bnd_Box()
        try:   # tight box of the trimmed face (the plain one includes the whole surface's poles)
            _S(BRepBndLib, "AddOptimal")(face, b, False, False)
        except Exception:
            _S(BRepBndLib, "Add")(face, b)
        if b.IsVoid() or b.CornerMin().Distance(b.CornerMax()) > 1.5 * rdiag + 4 * self.tol:
            return float("inf")
        tm = shape_to_mesh(face, max(0.25 * self.tol, rdiag / 300, 1e-3))
        if tm is None:
            return float("inf")
        pts = np.asarray(tm.vertices)
        if len(pts) > 600:
            pts = pts[np.random.default_rng(1).choice(len(pts), 600, replace=False)]
        _, d, _ = trimesh.proximity.closest_point(self.prox, pts)
        # (measured against the full original mesh there is no coarse-triangle sag to allow for)
        sag = float(self.ctx.face_sag[fids].max()) if (len(fids) and not self.prox_full) else 0.0
        excess = d.max() - 1.5 * sag
        area_ratio = tm.area / max(self.ctx.area[fids].sum(), 1e-12)
        if area_ratio > 1.5 or area_ratio < 0.5:
            return float("inf")
        return max(0.0, excess)

    def facet_faces(self, fids):
        """Exact triangle faces for a set of mesh faces, on the same edges and vertices as the
        faces built next to them (so the whole lot can be joined without sewing)."""
        return triangle_faces(self.ctx, fids, shared=self.mesh_edges, verts=self.vtx)

    def _fix(self, face):
        sf = ShapeFix_Face(face)
        sf.Perform()
        return sf.Face()


_DEBUG = {}


def _settle(face):
    """Return the face unchanged. (Repairing it here would modify edges it shares with
    its neighbours; the check in _face_ok works on a repaired copy instead.)"""
    return face


def _memory_tight(frac=0.6):
    """Is this process already using most of the computer's memory? (The plate filler and
    whole-shape checks are the steps that can push an 8 GB machine into swapping; on a 4 GB
    machine the "available" test is the one that fires first.)"""
    try:
        import psutil
        vm = psutil.virtual_memory()
        return (psutil.Process().memory_info().rss > frac * vm.total
                or vm.available < max(600 * 2**20, 0.12 * vm.total))
    except Exception:
        return False


def _fresh_copy(face):
    """A deep copy of a face (new TShapes), so a cached face can be sewn again without
    carrying any in-place edits (tolerances, pcurves) that sewing or fixing made to the first."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy
    if face is None:
        return None
    return _cast("Face", BRepBuilderAPI_Copy(face).Shape())


def _repaired_copy(face):
    """A repaired copy of a face — its edges are shared with neighbours, so never fix
    the original in place (that silently changes the faces built next to it)."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy
    if face is None:
        return None
    try:
        face = _cast("Face", BRepBuilderAPI_Copy(face).Shape())
        if BRepCheck_Analyzer(face).IsValid():
            return face
        fx = ShapeFix_Shape(face); fx.Perform()
        out = fx.Shape()
        if out.ShapeType().name.endswith("FACE"):
            return _cast("Face", out)
        fs = list(_explore(out, TopAbs_FACE))
        return _cast("Face", fs[0]) if len(fs) == 1 else face
    except Exception:
        return face


def _fill_stats(ctx):
    """Per-conversion counters of the gap fillers (tried, used, seconds), for the log."""
    st = getattr(ctx, "fill_stats", None)
    if st is None:
        st = ctx.fill_stats = {"coons_tried": 0, "coons_used": 0, "coons_s": 0.0,
                               "plate_tried": 0, "plate_used": 0, "plate_s": 0.0}
    return st


def _projector(surf):
    """A point-on-surface projector set up once for `surf`, to call Perform(point) on: the same
    extrema as a fresh GeomAPI_ProjectPointOnSurf(point, surf) per point (same bounds, same
    tolerance, same algorithm, so the same answers), without rebuilding the surface's sampling
    for every point (0.10.0: a tenth to a third of the time on plate surfaces)."""
    from OCP.Precision import Precision
    u1, u2, v1, v2 = surf.Bounds()
    pr = GeomAPI_ProjectPointOnSurf()
    pr.Init(surf, u1, u2, v1, v2, Precision.Confusion_s())
    return pr


def _max_tolerance(shape):
    """The largest vertex or edge tolerance in a shape."""
    worst = 0.0
    try:
        for v in _explore(shape, TopAbs_VERTEX):
            worst = max(worst, _S(BRep_Tool, "Tolerance")(_cast("Vertex", v)))
        for e in _explore(shape, TopAbs_EDGE):
            worst = max(worst, _S(BRep_Tool, "Tolerance")(_cast("Edge", e)))
    except Exception:
        pass
    return worst


def _fraction_inside(face, pts):
    """Share of the points whose projection onto the face's surface falls inside the face."""
    try:
        surf = _S(BRep_Tool, "Surface")(face)
        n_in = 0
        pr = _projector(surf)
        for p in pts:
            pr.Perform(_gp(p))
            if pr.NbPoints() == 0:
                continue
            u, v = pr.LowerDistanceParameters()
            if BRepClass_FaceClassifier(face, gp_Pnt2d(u, v), 1e-7).State() != TopAbs_OUT:
                n_in += 1
        return n_in / max(len(pts), 1)
    except Exception:
        return 0.0


def _face_area(face):
    c = _repaired_copy(face)
    if c is None:
        return None
    g = GProp_GProps(); _S(BRepGProp, "SurfaceProperties")(c, g)
    return round(abs(g.Mass()), 2)


def _face_ok(face, mesh_area, rel=0.15):
    """A built face must be valid and cover about the same area as the mesh it replaces."""
    face = _repaired_copy(face)
    if face is None:
        return False
    try:
        if not BRepCheck_Analyzer(face).IsValid():
            return False
        g = GProp_GProps()
        _S(BRepGProp, "SurfaceProperties")(face, g)
        a = abs(g.Mass())
    except Exception:
        return False
    if mesh_area <= 0:
        return a < 1e-9
    return abs(a - mesh_area) <= rel * mesh_area + 1e-9


def _try2(fn, *a):
    try:
        return fn(*a)
    except Exception:
        return None, float("inf")


def _clamped_basis(x, n, deg=3):
    from scipy.interpolate import BSpline
    inner = np.linspace(0.0, 1.0, n - deg + 1)
    t = np.r_[[0.0] * deg, inner, [1.0] * deg]
    Bm = BSpline.design_matrix(np.clip(x, 0.0, 1.0), t, deg).toarray()
    return Bm, inner


def _lsq_bspline(P, U, W, nu, nv, smooth=1e-4):
    """Weighted least-squares tensor B-spline fit with a small bending penalty."""
    Bu, _ = _clamped_basis(U[:, 0], nu)
    Bv, _ = _clamped_basis(U[:, 1], nv)
    A = (Bu[:, :, None] * Bv[:, None, :]).reshape(len(P), nu * nv)
    AtA = A.T @ (A * W[:, None])
    # second-difference penalties along u and v on the control grid
    def d2(n):
        D = np.zeros((max(n - 2, 0), n))
        for i in range(n - 2):
            D[i, i:i + 3] = (1, -2, 1)
        return D
    Du = np.kron(d2(nu), np.eye(nv)); Dv = np.kron(np.eye(nu), d2(nv))
    R = Du.T @ Du + Dv.T @ Dv
    lam = smooth * np.trace(AtA) / max(np.trace(R), 1e-12)
    M = AtA + lam * R + 1e-12 * np.eye(nu * nv)
    try:
        C = np.linalg.solve(M, A.T @ (P * W[:, None]))
    except np.linalg.LinAlgError:
        return None, None
    res = np.linalg.norm(A @ C - P, axis=1)
    return C, res


def _occ_bspline_curve(C, inner, deg=3):
    """Clamped cubic B-spline curve from its control points and inner knots (0..1)."""
    from OCP.Geom import Geom_BSplineCurve
    n = len(C)
    pol = _ArrPnt(1, n)
    for i, q in enumerate(C):
        pol.SetValue(i + 1, _gp(q))
    kn = _ArrReal(1, len(inner))
    mu = _ArrInt(1, len(inner))
    for i, t in enumerate(inner):
        kn.SetValue(i + 1, float(t))
        mu.SetValue(i + 1, deg + 1 if i in (0, len(inner) - 1) else 1)
    return Geom_BSplineCurve(pol, kn, mu, deg)


def _occ_bspline_surface(C, nu, nv, deg=3):
    poles = _ArrPnt2(1, nu, 1, nv)
    for i in range(nu):
        for j in range(nv):
            poles.SetValue(i + 1, j + 1, _gp(C[i * nv + j]))

    def kv(n):
        inner = np.linspace(0.0, 1.0, n - deg + 1)
        kn = _ArrReal(1, len(inner)); mu = _ArrInt(1, len(inner))
        for i, x in enumerate(inner):
            kn.SetValue(i + 1, float(x))
            mu.SetValue(i + 1, deg + 1 if i in (0, len(inner) - 1) else 1)
        return kn, mu
    uk, um = kv(nu); vk, vm = kv(nv)
    return Geom_BSplineSurface(poles, uk, vk, um, vm, deg, deg)


def _try(fn, *a):
    try:
        return fn(*a)
    except Exception:
        return None


def _fps(pts, k):
    n = len(pts)
    if n <= k:
        return np.arange(n)
    sel = [0]
    d = np.linalg.norm(pts - pts[0], axis=1)
    for _ in range(k - 1):
        i = int(np.argmax(d)); sel.append(i)
        d = np.minimum(d, np.linalg.norm(pts - pts[i], axis=1))
    return np.array(sel)


def triangle_faces(ctx, fids, shared=None, verts=None):
    """One planar face per mesh triangle, edges shared inside the set. `shared` (0.10.0): edges
    that exist already, {(lower vertex index, higher): (edge, index of the vertex it starts
    from)}, used instead of new ones so the facets join the faces built from those edges
    without sewing; new mesh edges are added to it. `verts`: likewise for vertices."""
    V, F = ctx.V, ctx.F
    verts = {} if verts is None else verts
    edges = {}

    def vx(i):
        if i not in verts:
            verts[i] = BRepBuilderAPI_MakeVertex(_gp(V[i])).Vertex()
        return verts[i]

    faces = []
    for fi in fids:
        tri = F[fi]
        if ctx.area[fi] <= 1e-14:
            continue
        mw = BRepBuilderAPI_MakeWire()
        ok = True
        for k in range(3):
            a, b = int(tri[k]), int(tri[(k + 1) % 3])
            key = (a, b) if a < b else (b, a)
            e = edges.get(key)
            if e is None:
                got = shared.get(key) if shared is not None else None
                if got is not None:
                    e, a0 = got
                    e = e if a0 == key[0] else _cast("Edge", e.Reversed())   # as running low -> high
                else:
                    me = BRepBuilderAPI_MakeEdge(vx(key[0]), vx(key[1]))
                    if not me.IsDone():
                        ok = False; break
                    e = me.Edge()
                    if shared is not None:
                        shared[key] = (e, key[0])
                edges[key] = e
            mw.Add(e if a < b else _cast("Edge", e.Reversed()))
        if not ok or not mw.IsDone():
            continue
        n = ctx.N[fi]
        pln = gp_Pln(_gp(V[tri[0]]), gp_Dir(*map(float, n)))
        mf = BRepBuilderAPI_MakeFace(pln, mw.Wire(), True)
        if mf.IsDone():
            faces.append(mf.Face())
    return faces


# ---------------------------------------------------------------- assembly
def _sew(faces, tol):
    sew = BRepBuilderAPI_Sewing(tol, True, True, True, False)
    for f in faces:
        sew.Add(f)
    sew.Perform()
    return sew.SewedShape()


def _join_or_sew(faces, tol, log):
    """A solid from faces that share their edges and vertices by construction (0.10.0): put
    straight into one shell; only when that does not seal is the slow sewing used, as before.
    On the 48,000-facet test tray sewing alone took 69 s of the assembly."""
    try:
        b = BRep_Builder()
        shell = TopoDS_Shell(); b.MakeShell(shell)
        for f in faces:
            b.Add(shell, f)
        if _S(BRep_Tool, "IsClosed")(shell):
            joined, ns, no = _shells_to_solid(shell, lambda *a: None)
            if ns and not no and _sealed(joined):
                return joined
    except Exception:  # noqa
        pass
    shape, ns, no = _shells_to_solid(_sew(faces, tol), lambda *a: None)
    return shape


def _shells_to_solid(shape, log):
    builder = BRep_Builder()
    comp = TopoDS_Compound(); builder.MakeCompound(comp)
    n_solid = 0; n_open = 0
    for sh in _explore(shape, TopAbs_SHELL):
        shell = _cast("Shell", sh)
        fx = ShapeFix_Shell(shell); fx.Perform()
        shell = fx.Shell()
        closed = _S(BRep_Tool, "IsClosed")(shell)
        if closed:
            ms = BRepBuilderAPI_MakeSolid(shell)
            if ms.IsDone():
                solid = ms.Solid()
                fs = ShapeFix_Solid(solid); fs.Perform()
                solid = _cast("Solid", fs.Shape())
                try:
                    _S(BRepLib, "OrientClosedSolid")(solid)
                except Exception:
                    pass
                builder.Add(comp, solid); n_solid += 1
                continue
        builder.Add(comp, shell); n_open += 1
    if n_open:
        log(f"Warning: {n_open} shell(s) are not closed — exported as surfaces")
    return comp, n_solid, n_open


_ROUND = ("Cylinder", "Cone", "Sphere", "Torus")
SWEPT = ("rev", "ext")      # primitive kinds made by the roughing stage (rough.py)


def _round_cut_edges(shape, every=False):
    """Edges between two round faces (cylinder, cone, sphere) that must not be merged away.

    Merging the pieces of a round face into a full ring leaves one cut as the seam, and a
    seam has to run exactly along the surface's own "straight up" line. A cut that wanders
    (it followed zig-zag mesh edges) makes a face that reads back from STEP broken, so such
    cuts are kept. every=True keeps every cut between round faces."""
    from OCP.TopTools import TopTools_IndexedDataMapOfShapeListOfShape
    from OCP.TopExp import TopExp
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.BRepAdaptor import BRepAdaptor_Curve2d, BRepAdaptor_Curve
    keep = []
    m = TopTools_IndexedDataMapOfShapeListOfShape()
    TopExp.MapShapesAndAncestors_s(shape, TopAbs_EDGE, TopAbs_FACE, m)
    for i in range(1, m.Extent() + 1):
        fs = [_cast("Face", f) for f in m.FindFromIndex(i)]
        if len(fs) != 2:
            continue
        ads = [BRepAdaptor_Surface(f) for f in fs]
        kinds = [a.GetType().name.replace("GeomAbs_", "") for a in ads]
        if kinds[0] != kinds[1] or kinds[0] not in _ROUND:
            continue
        e = _cast("Edge", m.FindKey(i))
        if every:
            keep.append(e); continue
        try:
            c2 = BRepAdaptor_Curve2d(e, fs[0])
            ts = np.linspace(c2.FirstParameter(), c2.LastParameter(), 7)
            uv = np.array([[c2.Value(float(t)).X(), c2.Value(float(t)).Y()] for t in ts])
            c3 = BRepAdaptor_Curve(e)
            p = c3.Value(0.5 * (c3.FirstParameter() + c3.LastParameter()))
            if kinds[0] == "Cylinder":
                ax = ads[0].Cylinder().Axis()
            elif kinds[0] == "Cone":
                ax = ads[0].Cone().Axis()
            elif kinds[0] == "Sphere":
                ax = ads[0].Sphere().Position().Axis()
            else:
                ax = ads[0].Torus().Axis()
            o = np.array([ax.Location().X(), ax.Location().Y(), ax.Location().Z()])
            d = np.array([ax.Direction().X(), ax.Direction().Y(), ax.Direction().Z()])
            q = np.array([p.X(), p.Y(), p.Z()]) - o
            rad = float(np.linalg.norm(q - (q @ d) * d))
            du = float(np.ptp(uv[:, 0])); dv = float(np.ptp(uv[:, 1]))
            iso_u = du * rad <= 2e-4
            iso_v = dv <= 1e-6 * max(1.0, float(np.abs(uv[:, 1]).max()))
            if not (iso_u or iso_v):
                keep.append(e)
        except Exception:
            keep.append(e)
    return keep


def _unify(shape, tol, merge_bsplines=False, protect=0):
    """protect: 0 = merge freely (classic); 1 = keep wandering cuts between round faces;
    2 = keep every cut between round faces."""
    try:
        u = ShapeUpgrade_UnifySameDomain(shape, True, True, merge_bsplines)
        u.SetLinearTolerance(tol)
        u.SetAngularTolerance(math.radians(0.5))
        if protect:
            for e in _round_cut_edges(shape, every=protect >= 2):
                u.KeepShape(e)
        u.Build()
        return u.Shape()
    except Exception:
        return shape


def _round_faces_broken(shape, tol, inside=False):
    """Round faces whose triangulation doesn't cover them (a face OCC can't draw after the
    STEP round trip, so other OCC-based CAD programs can't either). Returns points on them.
    inside: the whole shape is meshed at once, as finely as the deviation is measured (meshing a
    face on its own can succeed where meshing it in its shell fails), and the points returned
    lie on the broken faces themselves."""
    pts = []
    if inside:
        return _faces_not_drawn(shape, tol)
    for f in _explore(shape, TopAbs_FACE):
        face = _cast("Face", f)
        if BRepAdaptor_Surface(face).GetType().name.replace("GeomAbs_", "") not in _ROUND + ("SurfaceOfRevolution", "SurfaceOfExtrusion"):
            continue
        g = GProp_GProps(); _S(BRepGProp, "SurfaceProperties")(face, g)
        exact = abs(g.Mass())
        tm = shape_to_mesh(face, max(4.0 * tol, 1e-3))      # (coarse is enough to compare areas)
        got = float(tm.area) if tm is not None else 0.0
        if abs(got - exact) > 0.03 * exact + 1e-9:
            from OCP.BRepAdaptor import BRepAdaptor_Curve
            from OCP.TopAbs import TopAbs_EDGE
            for e in _explore(face, TopAbs_EDGE):
                cu = BRepAdaptor_Curve(_cast("Edge", e))
                p = cu.Value(0.5 * (cu.FirstParameter() + cu.LastParameter()))
                pts.append((p.X(), p.Y(), p.Z()))
    return np.array(pts)


def _faces_not_drawn(shape, tol, round_only=True):
    """Faces that OpenCascade's mesher leaves (partly) empty when the whole shape is meshed at
    the deviation measurement's fineness: a point on each (its centre of mass pulled onto it)."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.TopLoc import TopLoc_Location
    sh = BRepBuilderAPI_Copy(shape).Shape()
    _S(BRepTools, "Clean")(sh)
    BRepMesh_IncrementalMesh(sh, max(tol * 0.2, 1e-3), False, 0.5, True)
    pts = []
    for f in _explore(sh, TopAbs_FACE):
        face = _cast("Face", f)
        if round_only and BRepAdaptor_Surface(face).GetType().name.replace("GeomAbs_", "") not in \
                _ROUND + ("SurfaceOfRevolution", "SurfaceOfExtrusion"):
            continue
        g = GProp_GProps(); _S(BRepGProp, "SurfaceProperties")(face, g)
        exact = abs(g.Mass())
        loc = TopLoc_Location()
        tri = _S(BRep_Tool, "Triangulation")(face, loc)
        got = 0.0
        if tri is not None and tri.NbTriangles():
            V = np.array([[q.X(), q.Y(), q.Z()] for q in (tri.Node(i) for i in range(1, tri.NbNodes() + 1))])
            T = np.array([tri.Triangle(i).Get() for i in range(1, tri.NbTriangles() + 1)]) - 1
            got = float(0.5 * np.linalg.norm(np.cross(V[T[:, 1]] - V[T[:, 0]], V[T[:, 2]] - V[T[:, 0]]), axis=1).sum())
        if abs(got - exact) > 0.03 * exact + 1e-9:
            c = g.CentreOfMass()
            try:
                pr = GeomAPI_ProjectPointOnSurf(c, _S(BRep_Tool, "Surface")(face))
                if pr.NbPoints():
                    c = pr.NearestPoint()
            except Exception:  # noqa
                pass
            pts.append((c.X(), c.Y(), c.Z()))
    return np.array(pts)


def _uncovered(shape, ctx, tol, lim, most=30000):
    """Triangle centres of the working mesh with no face of the finished solid over them (more
    than three times the tolerance away, allowing for the mesh's own chord sag): what a face
    built in the wrong place, or trimmed short of a narrow cusp, leaves behind. Returns them."""
    # on a copy: meshing a shape a second time (the deviation measurement does it again later)
    # can make OpenCascade's mesher refine without end (the vase: 0.9 s the first time, over five
    # minutes the second)
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy
    sh = BRepBuilderAPI_Copy(shape).Shape()
    _S(BRepTools, "Clean")(sh)
    tm = shape_to_mesh(sh, max(tol * 0.2, 1e-3))
    if tm is None or not len(tm.faces):
        return np.zeros((0, 3))
    idx = np.arange(ctx.nF)
    if len(idx) > most:
        idx = np.sort(np.random.default_rng(7).choice(len(idx), most, replace=False))
    # triangle centres (they sit inside the true surface by the chord sag) and the corners
    # (they lie on it: the tip of a narrow cusp is a corner)
    vid = np.arange(len(ctx.V))
    if len(vid) > most:
        vid = np.sort(np.random.default_rng(8).choice(len(vid), most, replace=False))
    P = np.vstack([ctx.C[idx], ctx.V[vid]])
    allow = np.r_[3.0 * lim + 1.5 * ctx.face_sag[idx], np.full(len(vid), 3.0 * lim)]
    d = np.concatenate([trimesh.proximity.closest_point(tm, P[i:i + 500])[1] for i in range(0, len(P), 500)])
    return P[d > allow]


def _free_edge_points(shape):
    """Midpoints of edges used by only one face (the holes in a sewn shell)."""
    from OCP.ShapeAnalysis import ShapeAnalysis_Shell
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.TopAbs import TopAbs_EDGE
    pts = []
    for sh in _explore(shape, TopAbs_SHELL):
        sa = ShapeAnalysis_Shell(); sa.LoadShells(sh); sa.CheckOrientedShells(sh, True, True)
        for e in _explore(sa.FreeEdges(), TopAbs_EDGE):
            cu = BRepAdaptor_Curve(_cast("Edge", e))
            for t in (0.1, 0.5, 0.9):
                p = cu.Value(cu.FirstParameter() + t * (cu.LastParameter() - cu.FirstParameter()))
                pts.append((p.X(), p.Y(), p.Z()))
    return np.array(pts)


def _invalid_face_points(shape, outline=False):
    """A few points on each face that fails the geometry check after sewing.
    outline=True (staged engine): a face too broken to mesh still reports points, taken
    from its outline edges."""
    pts = []
    for f in _explore(shape, TopAbs_FACE):
        if BRepCheck_Analyzer(f).IsValid():
            continue
        tm = shape_to_mesh(_cast("Face", f), 0.5)
        if tm is not None and len(tm.vertices):
            V = np.asarray(tm.vertices)
            pts.extend(V[np.linspace(0, len(V) - 1, min(8, len(V))).astype(int)].tolist())
        elif outline:
            from OCP.BRepAdaptor import BRepAdaptor_Curve
            from OCP.TopAbs import TopAbs_EDGE
            got = []
            for e in _explore(f, TopAbs_EDGE):
                try:
                    cu = BRepAdaptor_Curve(_cast("Edge", e))
                    p = cu.Value(0.5 * (cu.FirstParameter() + cu.LastParameter()))
                    got.append((p.X(), p.Y(), p.Z()))
                except Exception:
                    pass
            if got:
                got = np.array(got)
                pts.extend(got[np.linspace(0, len(got) - 1, min(12, len(got))).astype(int)].tolist())
                pts.append(got.mean(0).tolist())
    return np.array(pts)


def _seal_report(shape):
    from OCP.ShapeAnalysis import ShapeAnalysis_FreeBounds
    from OCP.TopAbs import TopAbs_EDGE
    fb = ShapeAnalysis_FreeBounds(shape, 1e-6)
    n_free = sum(1 for _ in _explore(fb.GetClosedWires(), TopAbs_EDGE)) + \
        sum(1 for _ in _explore(fb.GetOpenWires(), TopAbs_EDGE))
    bad = []
    for f in _explore(shape, TopAbs_FACE):
        if not BRepCheck_Analyzer(f).IsValid():
            a = BRepAdaptor_Surface(_cast("Face", f)).GetType().name.replace("GeomAbs_", "")
            bad.append(a)
    from collections import Counter
    return n_free, dict(Counter(bad)), len(list(_explore(shape, TopAbs_SOLID)))


def _sealed(shape):
    """True if every shell is closed (no free edges) and the shape passes the geometry check."""
    from OCP.ShapeAnalysis import ShapeAnalysis_Shell
    if shape is None or shape.IsNull() or not any(True for _ in _explore(shape, TopAbs_FACE)):
        return False
    if not any(True for _ in _explore(shape, TopAbs_SOLID)):
        return False
    for sh in _explore(shape, TopAbs_SHELL):
        sa = ShapeAnalysis_Shell()
        sa.LoadShells(sh)
        sa.CheckOrientedShells(sh, True)
        if sa.HasFreeEdges():
            return False
    return BRepCheck_Analyzer(shape).IsValid()


def _safe_unify(shape, tol, log, protect=0, sealed=None):
    """Merge same-surface faces, but never at the cost of a watertight, valid solid.
    sealed: what _sealed(shape) gives, when the caller has just checked (saves a check)."""
    if not (_sealed(shape) if sealed is None else sealed):
        return _unify(shape, tol, protect=protect)   # was already open: merging can't make it worse
    merged = _unify(shape, tol, protect=protect)
    if _sealed(merged):
        return merged
    resewn = _sew([_cast("Face", f) for f in _explore(merged, TopAbs_FACE)], max(tol, 1e-6))
    resewn, _, n_open = _shells_to_solid(resewn, lambda *a: None)
    if n_open == 0 and _sealed(resewn):
        return resewn
    log("  (kept faces unmerged in places so the solid stays watertight)")
    return shape


def _drop_outline_less(shape):
    """Remove faces that have a surface but no outline. Reading a STEP file can strip the
    outline from a face smaller than the edge tolerances around it (a tiny facet between
    sewn faces); what is left is an unbounded plane with nothing attached to it. The solid
    around it is already closed without it. Returns (shape, number removed)."""
    from OCP.BRepTools import BRepTools_ReShape
    from OCP.TopAbs import TopAbs_EDGE
    gone = [f for f in _explore(shape, TopAbs_FACE) if not any(True for _ in _explore(f, TopAbs_EDGE))]
    if not gone:
        return shape, 0
    rs = BRepTools_ReShape()
    for f in gone:
        rs.Remove(f)
    return rs.Apply(shape), len(gone)


def _volume(shape):
    props = GProp_GProps()
    _S(BRepGProp, "VolumeProperties")(shape, props)
    return abs(props.Mass())


def _count_faces(shape):
    return sum(1 for _ in _explore(shape, TopAbs_FACE))


def _quiet_occt():
    """Stop OpenCascade printing its coloured transfer statistics to the console."""
    try:
        from OCP.Message import Message, Message_Gravity
        for p in _S(Message, "DefaultMessenger")().Printers():
            p.SetTraceLevel(Message_Gravity.Message_Fail)
    except Exception:
        pass


_quiet_occt()


def _ascii_safe(path):
    """OpenCascade's file I/O can fail on non-ASCII paths (notably on Windows)."""
    try:
        str(path).encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def write_step(shape, path):
    import os, shutil, tempfile
    _S(Interface_Static, "SetCVal")("write.step.unit", "MM")
    try:
        _S(Interface_Static, "SetCVal")("write.step.schema", "AP214IS")
    except Exception:
        pass
    w = STEPControl_Writer()
    w.Transfer(shape, STEPControl_AsIs)
    target = str(path)
    tmp = None
    if not _ascii_safe(target):
        fd, tmp = tempfile.mkstemp(suffix=".step"); os.close(fd)
    if w.Write(tmp or target) != IFSelect_RetDone:
        raise IOError(f"Failed to write {path}")
    if tmp:
        shutil.move(tmp, target)


def _lathe_tip(ctx, L):
    """Classic engine on a turned part: say once that the experimental Ladder engine is usually
    faster on these (0.8.0 test set: up to 17 times, with as few or fewer faces).
    Only a log line; Classic carries on exactly as before. Costs a few milliseconds."""
    try:
        from tostep.plugins.mesh2step import ladder
        sig = ladder.signals(ctx)
        if ladder.route(sig)[0] == "lathe" and ctx.nF > 2000:
            L(f"  Tip: this looks like a turned part ({100 * sig['lathe_curved']:.0f}% of the curved area turns "
              "about one axis). On turned parts the Quick preset (the experimental Ladder engine) was up to 17 "
              "times faster in the tests (a vase: 164 s against 9.5 s), with as few or fewer faces.")
    except Exception:  # noqa - a hint must never stop a conversion
        pass


def read_step(path):
    import os, shutil, tempfile
    src = str(path)
    tmp = None
    if not _ascii_safe(src):
        fd, tmp = tempfile.mkstemp(suffix=".step"); os.close(fd)
        shutil.copyfile(src, tmp)
    try:
        r = STEPControl_Reader()
        if r.ReadFile(tmp or src) != IFSelect_RetDone:
            raise IOError(f"Could not read {path}")
        r.TransferRoots()
        return r.OneShape()
    finally:
        if tmp:
            os.remove(tmp)


_RUNAWAY_TRIANGLES = 20000


def _loose_edged(shape, defl):
    """A single face whose edges carry a tolerance far above the deflection asked for (a
    trial NURBS patch that fits badly at its outline). The mesher can spend a minute on such
    a face and still give up (0.8.0: 78 s of a 290 s conversion went on 30 of them)."""
    if shape.ShapeType().name.endswith("FACE"):
        worst = max((_S(BRep_Tool, "Tolerance")(_cast("Edge", e)) for e in _explore(shape, TopAbs_EDGE)), default=0.0)
        return worst > 5.0 * defl
    return False


def _mesh_min_size(shape, defl):
    prm = IMeshTools_Parameters()
    prm.Deflection = defl
    prm.Angle = 0.2
    prm.Relative = False
    prm.InParallel = True
    prm.MinSize = 2.0 * defl
    BRepMesh_IncrementalMesh(shape, prm)


def shape_to_mesh(shape, defl):
    if _loose_edged(shape, defl):
        _mesh_min_size(shape, defl)            # the same remedy as for runaway faces below, up front
    else:
        BRepMesh_IncrementalMesh(shape, defl, False, 0.2, True)
    # Safety net: a face whose edges carry a tolerance far above the deflection asked for can
    # make the mesher refine without end (one such face became 110,000 triangles and the
    # check ran out of memory). Re-mesh any such face with a minimum edge size. Only
    # runaway faces are touched, so normal results are unchanged.
    for f in _explore(shape, TopAbs_FACE):
        face = _cast("Face", f)
        t = _S(BRep_Tool, "Triangulation")(face, TopLoc_Location())
        if t is not None and t.NbTriangles() > _RUNAWAY_TRIANGLES:
            BRepTools.Clean_s(face)
            _mesh_min_size(face, defl)
    verts, faces = [], []
    off = 0
    for f in _explore(shape, TopAbs_FACE):
        face = _cast("Face", f)
        loc = TopLoc_Location()
        tri = _S(BRep_Tool, "Triangulation")(face, loc)
        if tri is None:
            # the mesher gave up on this face at the deflection asked for (seen on a valid
            # surface-of-revolution face): try it coarser rather than leave a hole, which
            # would be reported as a large deviation that isn't there
            for k in (3.0, 10.0, 30.0):
                BRepTools.Clean_s(face)
                BRepMesh_IncrementalMesh(face, defl * k, False, 0.2, True)
                tri = _S(BRep_Tool, "Triangulation")(face, loc)
                if tri is not None:
                    break
        if tri is None:
            continue
        trsf = loc.Transformation()
        nb = tri.NbNodes()
        for i in range(1, nb + 1):
            p = tri.Node(i).Transformed(trsf)
            verts.append((p.X(), p.Y(), p.Z()))
        for i in range(1, tri.NbTriangles() + 1):
            a, b, c = tri.Triangle(i).Get()
            faces.append((a - 1 + off, b - 1 + off, c - 1 + off))
        off += nb
    if not faces:
        return None
    return trimesh.Trimesh(np.array(verts), np.array(faces), process=False)


def measure_deviation(mesh, shape, tol):
    tm = shape_to_mesh(shape, max(tol * 0.2, 1e-3))
    if tm is None:
        return float("nan"), float("nan")
    pts, _ = trimesh.sample.sample_surface_even(mesh, 4000, seed=1) if hasattr(trimesh.sample, "sample_surface_even") \
        else trimesh.sample.sample_surface(mesh, 4000)
    pts = np.vstack([pts, mesh.vertices[:: max(1, len(mesh.vertices) // 4000)]])
    # in blocks: on a mesh with long thin triangles one query can pull in thousands of
    # candidate triangles per point, which for all points at once took 2.5 GB
    d = np.concatenate([trimesh.proximity.closest_point(tm, pts[i:i + 250])[1]
                        for i in range(0, len(pts), 250)])
    _DEBUG["worst_at"] = tuple(float(v) for v in pts[int(np.argmax(d))])   # (for the report's note)
    return float(d.mean()), float(d.max())


# ---------------------------------------------------------------- main entry
def _auto_crease(ctx, prm, L):
    """Sharp-edge threshold from the mesh itself (only when the user left the default)."""
    if prm.crease_angle == Params.crease_angle:
        # on dense smooth meshes (scans, marching cubes) real edges get rounded over 2–3
        # triangles and never reach 30°; on coarse CAD meshes round strips can be 20°+, so
        # there it stays high
        a = ctx.adj_angles(); a = a[a < 30]
        if len(a) > 50:
            prm.crease_angle = float(min(30.0, max(10.0, 3 * np.percentile(a, 90))))
            if prm.crease_angle < 29.5:
                L(f"Sharp-edge angle set to {prm.crease_angle:.0f}° for this mesh")


def convert(in_path, out_path, prm: Params | None = None, log=None, progress=None) -> Report:
    prm = prm or Params()
    L = _Log(log, progress)
    rep = Report(out_path=str(out_path))
    t0 = time.time()
    from tostep.plugins.mesh2step import hardpoints
    if prm.use_labels:
        hardpoints.apply_units(in_path, prm, L)
    mesh = load_mesh(in_path, prm, L)
    rep.n_input_tris = len(mesh.faces)
    if mesh.is_watertight:
        rep.mesh_volume = float(abs(mesh.volume))
    L.p(0.05)
    mesh_full = mesh                   # deviation is always reported against the full mesh
    full = None                        # the full mesh as a distance reference, when a reduced one is fitted
    ctx = MeshCtx(mesh)
    ctx.t0 = t0
    tol = prm.tol if prm.tol > 0 else max(0.01, 5e-4 * ctx.diag)
    L(f"Part size {ctx.diag:.2f} mm (diagonal), tolerance {tol:.4g} mm")
    if prm.ladder and prm.mode != "faceted":
        from tostep.plugins.mesh2step import ladder
        rep.route, _ = ladder.choose(ctx, prm, L)
    elif prm.mode != "faceted" and not prm.rough:
        _lathe_tip(ctx, L)
    if prm.organic_tol and prm.organic_tol > tol and prm.mode != "faceted":
        L(f"Guesstimate mode: freeform areas may be up to {prm.organic_tol:.3g} mm off; "
          f"flat faces, cylinders, spheres and edges keep {tol:.4g} mm")
        if prm.rough:
            # Staged engine: the swept shapes are found on the full mesh first, then each
            # kind of area is reduced by its own budget (exact shapes a third of the tight
            # tolerance, organic areas a sixth of the organic one) with every outline locked.
            auto = prm.crease_angle == Params.crease_angle
            _auto_crease(ctx, prm, lambda *a: None)
            got = reduce_regions(ctx, prm, tol, L)
            if auto:
                prm.crease_angle = Params.crease_angle      # re-estimated below on the mesh actually used
            if got is not None:
                mesh, prm.organic_used, known, labels = got
                ctx = MeshCtx(mesh); ctx.t0 = t0     # the time budget counts from the start
                if known and len(labels) == ctx.nF:
                    ctx.known_swept = (known, labels)   # swept shapes found on the full mesh, and their triangles
                full = trimesh.Trimesh(np.array(mesh_full.vertices), np.array(mesh_full.faces), process=False)
        else:
            # Classic engine: as released — one overall reduction within a third of the tolerance
            small = reduce_within(mesh, tol / 3.0, L)
            if small is not mesh:
                mesh = small
                ctx = MeshCtx(mesh); ctx.t0 = t0     # the time budget counts from the start
    _auto_crease(ctx, prm, L)

    hints = None
    if prm.use_labels and prm.mode != "faceted" and mesh is mesh_full:
        hints = hardpoints.load(in_path, prm.scale, ctx, tol, L)
    if hints is not None:
        # hardpoint STL: the file says which triangles form which face (each one is still
        # checked against the tolerance before it is believed)
        label, planes = hints["label"], hints["planes"]
        ctx.hints, ctx.hard_verts = hints, hints["hard"]
    else:
        if prm.lathe_first and prm.mode != "faceted" and mesh is mesh_full:
            from tostep.plugins.mesh2step import ladder
            if ladder.claim_lathe_first(ctx, tol, L) is None and prm.plane_rule == "classic":
                # the normals say "turned part" but no revolve was found (stepped shafts do
                # this): coarse cylinder facets would now pass as flats, so judge flat-face
                # borders by length instead of by count
                prm.plane_rule = "length"
                L("  flat-face borders judged by edge length on this part")
        label, planes = find_planes(ctx, prm, tol, L)
    snap_to_planes(ctx, label, planes, tol)
    if prm.decimate and ctx.nF > prm.decimate:
        small = simplify_keep_features(ctx, label, planes, prm, tol, prm.decimate, L)
        if small is not None:
            if hints is not None:
                L("  (the mesh was reduced, so the file's face labels can't be used — detecting faces)")
            ctx = MeshCtx(small); ctx.t0 = t0     # the time budget counts from the start
            label, planes = find_planes(ctx, prm, tol, L)
            snap_to_planes(ctx, label, planes, tol)
    L.p(0.12)

    written = None                     # the STEP as read back, if the builder already wrote and checked it
    if prm.mode == "faceted":
        shape = _build_faceted(ctx, label, planes, tol, L, rep)
    else:
        trip = {}
        check = None
        if prm.rough or prm.trip_check:
            # (0.10.2: Classic too. A round face OpenCascade can't draw after the round trip
            # can't be drawn by other OCC-based CAD programs either, and it measured as a hole:
            # the blobs plate's "3 mm off" was three such cylinder faces)
            def check(cand):
                """Write the candidate, read it back: (survived?, points on what didn't)."""
                write_step(cand, out_path)
                back = read_step(out_path)
                ok = any(True for _ in _explore(back, TopAbs_SOLID)) and _sealed(back)
                broken = _round_faces_broken(back, tol, inside=True) if ok else []
                ok = ok and not len(broken)
                far = []
                if ok and prm.cover_check:
                    far = _uncovered(back, ctx, tol, max(tol, float(prm.organic_tol or 0.0)))
                trip.update(shape=cand, back=back, ok=ok and not len(far))
                if ok and not len(far):
                    return True, None
                if len(broken):
                    return False, ("merge", broken)
                if len(far):
                    return False, ("far", far)
                pts = _free_edge_points(back)
                bad = _invalid_face_points(back, outline=True)
                if len(bad):
                    pts = np.vstack([pts, bad]) if len(pts) else bad
                return False, pts
        ctx.out_path = str(out_path)
        try:
            shape = _build_surface(ctx, label, planes, tol, prm, L, rep, full=full, check=check)
        except Exception as e:  # noqa  (MemoryError included: a potato must never end with nothing)
            L(f"The surface fitter stopped ({e.__class__.__name__}: {str(e)[:120]})")
            if getattr(ctx, "checkpoint_written", False):
                L("  keeping the checkpoint STEP written earlier (exact faces, the rest as facets)")
                shape = read_step(out_path)
                written = shape
                trip.clear()
            else:
                L("  writing the exact faceted solid instead")
                shape = _build_faceted(ctx, label, planes, tol, L, rep)
        if trip.get("ok") and trip.get("shape") is shape:
            written = trip["back"]

    L("Writing STEP…")
    if written is None:
        write_step(shape, out_path)
    L.p(0.93)
    if prm.verify:
        L("Verifying (re-reading STEP, measuring deviation)…")
    if (written is None and prm.ladder and prm.mode != "faceted" and rep.n_faceted_regions == 0
            and _count_faces(shape) < 300 and _sealed(shape)):
        written = shape           # small, closed, no facets: the file holds exactly this shape
    back = written if written is not None else read_step(out_path)   # report on what was actually written
    for _ in range(2):
        cleaned, n_gone = _drop_outline_less(back)
        if not n_gone:
            break
        if _sealed(back) and not _sealed(cleaned):
            break                      # (it was holding something together after all: leave it)
        L(f"  {n_gone} facet(s) too small to survive in the STEP file were dropped (the solid stays closed)")
        write_step(cleaned, out_path)
        back = read_step(out_path)
    shape = back
    rep.is_solid = any(True for _ in _explore(shape, TopAbs_SOLID)) and _sealed(shape)
    if prm.mode != "faceted" and mesh.is_watertight and not rep.is_solid:
        # last line of defence: never hand back a broken shell for a closed mesh
        L("The surface result isn't a closed solid — writing the exact faceted solid instead")
        shape = _build_faceted(ctx, label, planes, tol, L, rep)
        write_step(shape, out_path)
        shape = read_step(out_path)
        back = shape                    # (volume and deviation below must describe the file as written)
        rep.n_primitives = rep.n_nurbs = 0; rep.n_faceted_regions = 1
        rep.is_solid = any(True for _ in _explore(shape, TopAbs_SOLID)) and _sealed(shape)
        ctx.final_regions = None
    rep.n_faces = _count_faces(shape)
    # a sealed shape has passed the geometry check already (_sealed ends with it)
    rep.is_valid = True if rep.is_solid else BRepCheck_Analyzer(shape).IsValid()
    if prm.verify:
        if rep.is_solid:
            rep.step_volume = _volume(back)
        orig = trimesh.Trimesh(np.array(mesh_full.vertices), np.array(mesh_full.faces), process=False)
        rep.mean_dev, rep.max_dev = measure_deviation(orig, back, tol)
        rep.tol, rep.worst_at = max(tol, prm.organic_tol or 0.0), _DEBUG.pop("worst_at", ())
    if prm.save_labelled and prm.mode != "faceted":
        try:
            hardpoints.save(in_path, prm.save_labelled, prm.scale, ctx, getattr(ctx, "final_regions", None),
                            tol, prm.crease_angle, L)
        except Exception as e:  # noqa  (never lose a finished conversion over the extra file)
            L(f"Labelled STL not saved ({e or e.__class__.__name__})")
    rep.seconds = time.time() - t0
    rep.ok = True
    L.p(1.0)
    L(rep.summary())
    return rep


FACET_CAP = 20000     # a STEP of more faces than this is slow to write and troubles some readers


def _build_faceted(ctx, label, planes, tol, L, rep):
    L("Building exact faceted solid…")
    if ctx.nF > FACET_CAP:
        # keep the file readable: reduce within half the tolerance first (the deviation
        # is still measured against the full mesh)
        small = reduce_within(ctx.m, tol / 2.0, L, min_tris=FACET_CAP)
        if small is not ctx.m and len(small.faces) < ctx.nF:
            L(f"  {ctx.nF} triangles reduced to {len(small.faces)} (within {tol / 2:.4g} mm) for the faceted solid")
            ctx = MeshCtx(small)
    faces = triangle_faces(ctx, np.arange(ctx.nF))
    L.p(0.45)
    b = BRep_Builder()
    shell = TopoDS_Shell(); b.MakeShell(shell)
    for f in faces:
        b.Add(shell, f)
    comp = TopoDS_Compound(); b.MakeCompound(comp); b.Add(comp, shell)
    if not _S(BRep_Tool, "IsClosed")(shell):
        L("Shell has gaps — sewing…")
        shape = _sew(faces, tol)
    else:
        shape = comp
    shape, ns, no = _shells_to_solid(shape, L)
    L.p(0.6)
    L("Merging coplanar facets into single faces…")
    shape = _safe_unify(shape, max(1e-6, tol * 0.05), L)
    L.p(0.85)
    rep.n_planes = len(planes)
    return shape


def _checkpoint_faceted(ctx):
    """The mesh being fitted as a closed solid of triangles, quickly: no merging of coplanar
    facets (that is the slow part of the full faceted fallback) — a safety copy, not the result."""
    faces = triangle_faces(ctx, np.arange(ctx.nF))
    b = BRep_Builder()
    shell = TopoDS_Shell(); b.MakeShell(shell)
    for f in faces:
        b.Add(shell, f)
    comp = TopoDS_Compound(); b.MakeCompound(comp); b.Add(comp, shell)
    shape, _, _ = _shells_to_solid(comp, lambda *a: None)
    return shape


def _labelled_prim(ctx, ids, kind, tol):
    """The file's label says this face is a cylinder / sphere, but the usual sanity rules
    turned it down (they can't tell a shallow round from a flat strip; a CAD label can).
    Fit it anyway and keep it only if every corner is within tolerance."""
    pr = _refit_prim(kind, ctx, ids)
    if pr is None:
        return None
    pts = ctx.V[np.unique(ctx.F[ids])]
    return pr if float(np.max(_prim_residual_vec(pr, pts))) <= tol else None


def _layout_regions(ctx, graph, label, planes, tol, prm, L, max_area):
    """Divide the mesh into regions: flat, round (cylinder / sphere / lathe) and freeform patches."""
    nP = len(planes)
    hints = getattr(ctx, "hints", None)
    if hints is not None:
        comps = [c["ids"] for c in hints["comps"]]
        for ci, ids in enumerate(comps):
            ctx.face_comp[ids] = ci
        L(f"{len(comps)} curved face(s) taken from the file's labels")
    else:
        free = np.where(label < 0)[0]
        # smooth components: split at creases and at planes
        ang = ctx.adj_angles()
        fmask = np.zeros(ctx.nF, bool); fmask[free] = True
        keep = fmask[ctx.adj[:, 0]] & fmask[ctx.adj[:, 1]] & (ang < prm.crease_angle)
        G = coo_matrix((np.ones(keep.sum()), (ctx.adj[keep, 0], ctx.adj[keep, 1])), shape=(ctx.nF, ctx.nF))
        nc, comp = connected_components(G, directed=False)
        # scan-like meshes have crumpled/folded triangles whose edges look sharp: they cut
        # the surface into tiny islands. Merge those specks into the area around them.
        comp = comp.astype(np.int64)
        reg = np.where(label >= 0, label, nP + comp)
        reg = _absorb_specks(ctx, reg, nP)
        comp = np.where(reg >= nP, reg - nP, -1)
        comps = {}
        for f in free:
            if comp[f] >= 0:
                comps.setdefault(int(comp[f]), []).append(f)
        comps = [np.array(v) for v in comps.values()]
        for ci, ids in enumerate(comps):
            ctx.face_comp[ids] = ci
        L(f"{len(comps)} smooth curved component(s)")

    regions = [{"type": "plane", "plane": planes[i], "faces": np.where(label == i)[0]} for i in range(nP)]
    n_prim = 0
    next_comp = len(comps)

    def add_prim(ids, prim, comp_id):
        flip = _prim_geom(prim)[1]
        if prim[0] in SWEPT:
            from tostep.plugins.mesh2step import rough
            pieces = rough.sectors(ctx, ids, prim)      # a few wide pieces; holes are allowed
        else:
            pieces = layout_patches(ctx, graph, ids, float("inf"), 100.0)
        for p in pieces:
            # each patch gets its own surface object: a shared Geom handle would make
            # OCC treat the edge between two patches as a seam
            regions.append({"type": "prim", "faces": p, "surf_geom": _prim_geom(prim, pts=ctx.V[np.unique(ctx.F[p])])[0],
                            "flip": flip, "prim": prim, "comp": comp_id})

    n_split = 0
    n_rev, n_ext, rev_area = 0, 0, 0.0
    for ci, ids in enumerate(comps):
        kind = hints["comps"][ci]["kind"] if hints is not None else ""
        if kind == "facet":                    # the file says: keep this area as it is
            regions.append({"type": "facet", "faces": ids, "comp": ci})
            continue
        prim = fit_primitive(ctx, ids, tol) if (prm.fit_primitives and kind != "free") else None
        if prim is None and kind in ("cyl", "sph") and prm.fit_primitives:
            prim = _labelled_prim(ctx, ids, kind, tol)
        if prim is not None:
            add_prim(ids, prim, ci)
            n_prim += 1
            continue
        rest = ids
        whole = float(ctx.area[ids].sum())

        def take(found):
            nonlocal next_comp, n_rev, n_ext, rev_area
            for p_ids, p_prim in found:
                ctx.face_comp[p_ids] = next_comp      # its own component: its borders are real edges
                if p_prim[0] == "pln":
                    regions.append({"type": "plane", "plane": (p_prim[1], p_prim[2]), "faces": p_ids,
                                    "comp": next_comp})
                else:
                    add_prim(p_ids, p_prim, next_comp)
                n_rev += 1
                n_ext += p_prim[0] == "ext"
                rev_area += float(ctx.area[p_ids].sum())
                next_comp += 1

        grow = float(prm.organic_tol or 0.0) / 6.0
        swept = prm.rough and prm.fit_primitives and len(ids) >= 40 and kind != "free"
        if swept:
            from tostep.plugins.mesh2step import rough
            known = getattr(ctx, "known_swept", None)
            if known:
                # Guesstimate mode: the swept shapes were found on the full mesh, before it was
                # reduced, and each reduced triangle remembers which one it came from. Nothing
                # is looked for again here: on the thinned-out mesh, coarse triangles hide
                # detail and a shape could be claimed where the real surface is not on it.
                prims, lab = known
                found = []
                for k, kp in enumerate(prims):
                    mine = rest[lab[rest] == k]
                    if len(mine) >= 12:
                        found.extend((part, kp) for part in rough._connected(ctx, mine) if len(part) >= 12)
                if found:
                    rest = np.setdiff1d(rest, np.concatenate([q for q, _ in found]))
                    take(found)
            elif len(rest) >= 40:
                found, rest = rough.find_revolves(ctx, rest, tol, grow_tol=grow)    # lathe shapes first
                take(found)

        def split(faces):
            """The classic cylinder / sphere split (with its own "CAD blend or organic?" verdict)."""
            return best_primitive_split(ctx, faces, tol, prm)

        parts = []
        after = rest
        if prm.fit_primitives and len(rest) >= 12 and kind != "free":
            parts, after = split(rest)
        if swept and len(rest) >= 40 and not getattr(ctx, "known_swept", None):
            # Extruded profiles in what the lathe search left. A profile that is just a few
            # flats and rounds (a CAD blend) is better written as true planes and cylinders,
            # which is what the split above does — so an extrusion face is only used where
            # planes and cylinders can't explain the area with a handful of pieces (ovals,
            # splines, gear flanks), or where the split found nothing at all.
            exts, _ = rough.find_extrudes(ctx, rest, tol, grow_tol=grow, total=whole)
            free = []
            for p_ids, p_prim in exts:
                if p_prim[0] != "ext":                 # already a plain plane or cylinder
                    if not parts:
                        free.append((p_ids, p_prim))
                    continue
                sub, left = grow_primitives(ctx, p_ids, tol, prm)
                got = sum(float(ctx.area[q].sum()) for q, _ in sub)
                if sub and len(sub) <= 3 and got >= 0.98 * float(ctx.area[p_ids].sum()):
                    if not parts:
                        free.extend(sub)
                else:
                    free.append((p_ids, p_prim))
            if free:
                taken = np.concatenate([q for q, _ in free])
                swept_parts = [(q, p) for q, p in free if p[0] in SWEPT or p[0] == "pln"]
                classic_parts = [(q, p) for q, p in free if not (p[0] in SWEPT or p[0] == "pln")]
                take(swept_parts)
                after = np.setdiff1d(rest, taken)
                if parts and len(after) >= 12:
                    parts, after = split(after)
                else:
                    parts = []
                parts = classic_parts + list(parts)
        rest = after
        for p_ids, p_prim in parts:
            ctx.face_comp[p_ids] = next_comp      # its own component: its borders are real edges
            if p_prim[0] == "pln":
                regions.append({"type": "plane", "plane": (p_prim[1], p_prim[2]), "faces": p_ids,
                                "comp": next_comp})
            else:
                add_prim(p_ids, p_prim, next_comp)
                n_prim += 1
            next_comp += 1
        if parts:
            n_split += 1
        if len(rest):
            for p in layout_patches(ctx, graph, rest, max_area, prm.max_bend):
                regions.append({"type": "nurbs", "faces": p, "comp": ci})
    if n_rev:
        L(f"Roughing: {n_rev} swept surface(s) ({n_rev - n_ext} lathe / round, {n_ext} extruded) cover "
          f"{100.0 * rev_area / max(float(ctx.area.sum()), 1e-12):.0f}% of the surface as exact faces")
    if n_prim:
        L(f"{n_prim} cylinder/sphere surface(s) recognised"
          + (f" ({n_split} blended area(s) split into separate round shapes)" if n_split else ""))
    return regions


def _build_surface(ctx, label, planes, tol, prm, L, rep, full=None, regions0=None, check=None):
    graph = _Graph(ctx)
    max_area = (prm.patch_size * ctx.diag) ** 2
    if regions0 is not None:
        regions = regions0          # (testing) start from a region layout worked out earlier
    else:
        regions = _layout_regions(ctx, graph, label, planes, tol, prm, L, max_area)
    L.p(0.2)

    # what fitted surfaces are measured against: the mesh being fitted, or (when that mesh is
    # a reduced one) the full original, so reduction and fitting can't add up past the limit
    prox = full if full is not None else trimesh.Trimesh(ctx.V.copy(), ctx.F.copy(), process=False)
    ctx.compute_sag()
    def prepare(regions):
        lab = -np.ones(ctx.nF, dtype=np.int64)
        for ri, r in enumerate(regions):
            lab[r["faces"]] = ri
        chains, _ = build_chains(ctx, lab)
        _smooth_soft_chains(ctx, chains, regions, prox)
        B = Builder(ctx, lab, regions, chains, tol, prm, L)
        B.prox = prox
        B.prox_full = full is not None
        res = {}
        for ri, r in enumerate(regions):
            if r["type"] == "nurbs":
                key = (r["faces"].tobytes(),
                       tuple((tuple(chains[ci]["verts"]), chains[ci]["kind"], "pts" in chains[ci])
                             for lp in B.region_loops.get(ri, []) for ci, _ in lp))
                if key not in cache:
                    cache[key] = B.nurbs_face(ri, r)
                res[ri] = cache[key]
        return B, res

    # Built-and-checked faces, keyed by the region's triangles and the exact state of its
    # boundary chains. When one face fails and the whole solid is assembled again, a region
    # whose key did not change would be rebuilt into the same face and pass or fail the same
    # checks, so its previous result is reused (the knob skipped about half its regions per
    # repeat cycle; outputs are identical because every reuse is a deep copy).
    face_cache = {}

    def loop_key(B, regions, ri):
        chains = B.chains
        return tuple((tuple(chains[ci]["verts"]), chains[ci]["kind"], "pts" in chains[ci],
                      tuple(regions[s]["type"] for s in chains[ci]["regions"] if s >= 0))
                     for lp in B.region_loops.get(ri, []) for ci, _ in lp)

    def build_all(regions, B, results):
        """Build every face and check it: valid geometry, and an area that matches the
        mesh it replaces (catches e.g. a whole ball built where a corner patch belongs)."""
        faces, failed, kinds = {}, [], {}
        for ri, r in enumerate(regions):
            if r["type"] == "facet":
                continue
            key = (r["type"], r["faces"].tobytes(), loop_key(B, regions, ri))
            hit = face_cache.get(key)
            if hit is not None:
                f, kind, ok = hit
                kinds[ri] = kind
                if ok:
                    faces[ri] = _fresh_copy(f)
                else:
                    failed.append(ri)
                continue
            mesh_a = float(ctx.area[r["faces"]].sum())
            f = None
            if r["type"] == "plane":
                f = _try(B.plane_face, ri, r)
                kinds[ri] = "plane"
            elif r["type"] == "prim":
                f = _settle(_try(B.prim_face, ri, r))
                kinds[ri] = "prim"
                if not _face_ok(f, mesh_a):     # other surface orientation, then a fitted patch
                    f = _settle(_try(B.prim_face, ri, {**r, "surf_geom": _prim_geom(r["prim"])[0]}))
                if not _face_ok(f, mesh_a) and r["prim"][0] not in SWEPT:   # (a swept patch is far too big for that)
                    f2, dev = _try2(B.nurbs_face, ri, r)
                    f = f2 if (f2 is not None and dev <= 2 * tol) else None
                    kinds[ri] = "nurbs"
            else:
                f = results.get(ri, (None, float("inf")))[0]
                kinds[ri] = "nurbs"
            f = _settle(f)
            is_rev = r["type"] == "prim" and r["prim"][0] in SWEPT and kinds.get(ri) == "prim"
            if is_rev and f is not None and not BRepCheck_Analyzer(f).IsValid():
                f = None          # (only a repaired copy passed: as built it would spoil the sewing)
            if _face_ok(f, mesh_a) and kinds.get(ri) != "nurbs" and not is_rev and not _near_mesh(f, r["faces"]):
                f = None          # right size, wrong place: it bulges away from the mesh
            ok = _face_ok(f, mesh_a)
            if ok:
                faces[ri] = f
            else:
                failed.append(ri)
                _DEBUG.setdefault("failed", []).append((ri, r["type"], kinds.get(ri), mesh_a, _face_area(f),
                                                        ctx.C[r["faces"]].mean(0).round(1).tolist(),
                                                        (r.get("prim") or ("",))[0]))
            face_cache[key] = (_fresh_copy(f) if ok else None, kinds.get(ri), ok)
        return faces, failed, kinds

    def _near_mesh(face, fids):
        """Every part of the face must lie on the mesh, allowing for the mesh's chord sag."""
        face = _repaired_copy(face)       # meshing an unrepaired face (e.g. a pole with no
        if face is None:                  # seam yet) can run almost forever
            return False
        tm = shape_to_mesh(face, max(0.5 * tol, 1e-3))
        if tm is None or not len(tm.vertices):
            return False
        V = np.asarray(tm.vertices)
        if len(V) > 800:
            V = V[np.random.default_rng(3).choice(len(V), 800, replace=False)]
        _, d, _ = trimesh.proximity.closest_point(prox, V)
        if full is not None:      # (against the full original mesh there is no coarse-triangle sag)
            allow = 2 * tol
        else:
            allow = 2 * tol + 1.5 * float(ctx.face_sag[fids].max() if len(fids) else 0.0)
        return float(d.max()) <= allow

    def assemble(regions, B, results):
        for _ in range(8):
            faces, failed, kinds = build_all(regions, B, results)
            if not failed:
                break
            L(f"  {len(failed)} face(s) failed the geometry check — keeping them as exact facets")
            fs = set(failed)
            regions = [{**r, "type": "facet"} if i in fs else r for i, r in enumerate(regions)]
            B, results = prepare(regions)       # neighbours now meet those facets exactly
        facet_faces = []
        for ri, r in enumerate(regions):
            if r["type"] == "facet" or ri not in faces:
                facet_faces.extend(B.facet_faces(r["faces"]))
        shape = _join_or_sew(list(faces.values()) + facet_faces, max(2 * tol, 1e-4), L)
        fix = ShapeFix_Shape(shape); fix.Perform(); shape = fix.Shape()
        counts = dict(plane=sum(1 for i in faces if kinds.get(i) == "plane"),
                      prim=sum(1 for i in faces if kinds.get(i) == "prim"),
                      nurbs=sum(1 for i in faces if kinds.get(i) == "nurbs"),
                      facet=sum(1 for i, r in enumerate(regions) if r["type"] == "facet" or i not in faces))
        return shape, counts, regions

    cache = {}
    n_free = sum(r["type"] == "nurbs" for r in regions)
    want_ckpt = prm.checkpoint == "on" or (prm.checkpoint == "auto" and ctx.nF >= 20000)
    if want_ckpt and n_free and getattr(ctx, "out_path", ""):
        # a valid STEP before the slow part: exact faces as found, freeform areas as facets
        t_ck = time.time()
        try:
            regs0 = [{**r, "type": "facet"} if r["type"] == "nurbs" else r for r in regions]
            B0, res0 = prepare(regs0)
            sh0, c0, _ = assemble(regs0, B0, res0)
            if _sealed(sh0):
                tmp = ctx.out_path + ".part"
                write_step(sh0, tmp)
                try:
                    with open(tmp, "rb+") as fh:
                        os.fsync(fh.fileno())
                except OSError:
                    pass
                os.replace(tmp, ctx.out_path)
                ctx.checkpoint_written = True
                L(f"Checkpoint written: {os.path.basename(ctx.out_path)} ({c0['plane']} planes, {c0['prim']} round faces, "
                  f"{n_free} area(s) as facets, {time.time() - t_ck:.1f} s) — a usable file exists from here on")
            else:
                # the exact faces with facets between them didn't close (common on dense
                # organic meshes): the plain faceted solid is still a usable file
                tmp = ctx.out_path + ".part"
                write_step(_checkpoint_faceted(ctx), tmp)
                os.replace(tmp, ctx.out_path)
                ctx.checkpoint_written = True
                L(f"Checkpoint written: {os.path.basename(ctx.out_path)} (the mesh as an exact faceted solid, "
                  f"{ctx.nF} facets, {time.time() - t_ck:.1f} s) — a usable file exists from here on")
        except Exception as e:  # noqa
            L(f"  (no checkpoint: {e.__class__.__name__})")
    rnd = 0
    budget_hit = False
    while True:
        refining = rnd < prm.refine_rounds
        if prm.budget_s and (time.time() - getattr(ctx, "t0", time.time())) > prm.budget_s:
            if not budget_hit:
                L(f"  time budget of {prm.budget_s:.0f} s reached — patches still outside tolerance become facets now")
                budget_rnd = rnd
            budget_hit = True
            refining = False
        lab = -np.ones(ctx.nF, dtype=np.int64)
        for ri, r in enumerate(regions):
            lab[r["faces"]] = ri
        chains, _ = build_chains(ctx, lab)
        _smooth_soft_chains(ctx, chains, regions, prox)
        B = Builder(ctx, lab, regions, chains, tol, prm, L)
        B.prox = prox
        B.prox_full = full is not None
        n_nurbs = sum(r["type"] == "nurbs" for r in regions)
        L(f"Pass {rnd + 1}: {len(regions)} regions, {n_nurbs} NURBS patches, "
          f"{len(chains)} boundary curves ({B.kinds['line']} lines, {B.kinds['arc']} arcs, "
          f"{B.kinds['bspline']} splines)")
        bad, results = [], {}
        limit = tol if refining else 1.5 * tol
        for ri, r in enumerate(regions):
            if r["type"] == "nurbs":
                key = (r["faces"].tobytes(),
                       tuple((tuple(chains[ci]["verts"]), chains[ci]["kind"], "pts" in chains[ci])
                             for lp in B.region_loops.get(ri, []) for ci, _ in lp))
                if key not in cache:
                    cache[key] = B.nurbs_face(ri, r)
                face, dev = cache[key]
                results[ri] = (face, dev)
                if face is None or dev > limit:
                    bad.append(ri)
            L.p(0.2 + 0.55 * min(1.0, (rnd + (ri + 1) / len(regions)) / (prm.refine_rounds + 2)))
        if not bad or rnd >= prm.refine_rounds + max(0, prm.max_settle) or (budget_hit and rnd >= budget_rnd + 2):
            if bad:
                # out of passes: what is still outside tolerance is kept as exact facets
                # (the assemble step below rebuilds their neighbours to meet them)
                badset = set(bad)
                regions = [{**r, "type": "facet"} if ri in badset else r for ri, r in enumerate(regions)]
                L(f"  {len(bad)} patch(es) kept as facets (no more passes)")
                B, results = prepare(regions)
            break  # (settling always converges: every pass turns >=1 patch into facets)
        badset = set(bad)
        new_regions = []
        if refining:
            # split patches that missed tolerance
            n_split = 0
            for ri, r in enumerate(regions):
                if ri in badset and len(r["faces"]) >= 8:
                    parts = _split(ctx, graph, r["faces"], 2)
                    for p in parts:
                        for q in layout_patches(ctx, graph, p, max_area, prm.max_bend):
                            new_regions.append({"type": "nurbs", "faces": q, "comp": r["comp"]})
                    n_split += 1
                elif ri in badset:
                    new_regions.append({**r, "type": "facet"})
                else:
                    new_regions.append(r)
            L(f"  {len(bad)} patch(es) outside tolerance — refining")
        else:
            # give up on these: keep them as exact facets. Their borders go back to the
            # raw mesh edges, so neighbours get rebuilt in the next pass to match.
            for ri, r in enumerate(regions):
                new_regions.append({**r, "type": "facet"} if ri in badset else r)
            L(f"  {len(bad)} patch(es) will be kept as facets — re-fitting neighbours")
        regions = new_regions
        rnd += 1

    _DEBUG.update(regions=regions, B=B, results=results, ctx=ctx)
    L("Building faces…")
    st = getattr(ctx, "fill_stats", None)
    if st and (st["coons_tried"] or st["plate_tried"]):
        L(f"  gap fills: Coons {st['coons_used']}/{st['coons_tried']} used ({st['coons_s']:.1f} s), "
          f"plate {st['plate_used']}/{st['plate_tried']} used ({st['plate_s']:.1f} s)")
    shape, counts, regions = assemble(regions, B, results)
    L.p(0.82)
    from scipy.spatial import cKDTree
    tree = cKDTree(ctx.C)

    def near_regions(pts, wide, kinds):
        """Regions of the given kinds under (or, wide, around) the problem points."""
        lab = -np.ones(ctx.nF, dtype=np.int64)
        for ri, r in enumerate(regions):
            lab[r["faces"]] = ri

        def want(i):
            if lab[i] < 0:
                return False
            r = regions[lab[i]]
            if r["type"] not in ("nurbs", "prim"):
                return False
            if kinds == "any":
                return True
            if kinds == "nurbs":
                return r["type"] == "nurbs"
            return r["type"] == "nurbs" or r["prim"][0] not in SWEPT   # "not lathe / extruded"
        near = set()
        if not wide:
            _, idx = tree.query(pts, k=4 if kinds == "any" else 8)
            for row in np.atleast_2d(idx):
                for i in row:
                    if want(i):
                        near.add(int(lab[i])); break
        else:
            for idx in tree.query_ball_point(pts, r=max(3 * tol, 0.02 * ctx.diag)):
                near.update(int(lab[i]) for i in idx if want(i))
        return near

    def demote(near):
        nonlocal regions, B, results, shape, counts
        regions = [{**r, "type": "facet"} if i in near else r for i, r in enumerate(regions)]
        B, results = prepare(regions)
        shape, counts, regions = assemble(regions, B, results)

    def mend():
        """Targeted repair: regions around open edges or invalid faces become exact facets,
        then everything is sewn again. Returns True once the shape is a clean closed solid."""
        for attempt in range(5):
            if _sealed(shape):
                return True
            pts = _free_edge_points(shape)
            bad = _invalid_face_points(shape, outline=prm.rough)
            near = set()
            if prm.rough and len(bad):
                near = near_regions(bad, False, "any")      # the face that is invalid itself
            if len(bad):
                pts = np.vstack([pts, bad]) if len(pts) else bad
            if not len(pts):
                break
            if prm.rough:
                # around open edges: freeform patches first; a big exact swept face is the
                # last thing to give up
                for kinds, wide in (("nurbs", False), ("nurbs", True), ("notlathe", False), ("any", False), ("any", True)):
                    if near or (wide and attempt < 1):
                        continue
                    near = near_regions(pts, wide, kinds)
            else:
                if attempt < 2:      # first only the region actually under each problem spot
                    near = near_regions(pts, False, "any")
                if not near:
                    near = near_regions(pts, True, "any")
            if not near:
                break
            L(f"  {len(near)} region(s) didn't sew cleanly (open edges or invalid faces) — rebuilding them as exact facets")
            demote(near)
        return _sealed(shape)

    sealed_now = mend()   # is the shape as it stands a clean closed solid? (re-checked only when it changes)
    final = None
    if check is not None:
        # The file is what counts: write it, read it back, and repair the same way anything
        # that didn't survive the trip (instead of giving up on every curved face at once).
        protect = 0
        for trip in range(6):
            if not sealed_now:
                break
            cand = _safe_unify(shape, max(1e-6, tol * 0.05), lambda *a: None, protect=protect, sealed=True)
            ok, pts = check(cand)
            if ok:
                final = cand
                break
            if isinstance(pts, tuple):
                if pts[0] == "merge" and protect < 2:
                    protect += 1     # a merged round face read back broken: merge more carefully
                    continue
                near = near_regions(pts[1], False, "any")   # not the merging: the faces themselves
                if not near:                                 # become facets
                    break
                L(f"  {len(near)} face(s) " + ("couldn't be drawn after being written to STEP" if pts[0] == "merge"
                  else "left part of the mesh uncovered (a narrow cusp bridged, or a face in the wrong place)")
                  + " — rebuilding them as exact facets")
                demote(near)
                sealed_now = mend()
                continue
            near = set()
            if pts is not None and len(pts):
                for kinds, wide in (("nurbs", False), ("nurbs", True), ("notlathe", False), ("any", False)):
                    near = near_regions(pts, wide, kinds)
                    if near:
                        break
            if not near:
                break
            L(f"  {len(near)} region(s) didn't survive being written to STEP and read back — rebuilding them as exact facets")
            demote(near)
            sealed_now = mend()
    if not sealed_now:
        # something still doesn't close up: keep flat and round faces, make the rest facets
        nf, bad, nsol = _seal_report(shape)
        _DEBUG["seal_shape"] = shape
        L(f"  The fitted surfaces didn't close into a clean solid ({nf} open edges, invalid faces {bad or 'none'}) "
          "— rebuilding curved areas as exact facets")
        regions = [{**r, "type": "facet"} if r["type"] == "nurbs" else r for r in regions]
        B, results = prepare(regions)
        shape, counts, regions = assemble(regions, B, results)
        sealed_now = _sealed(shape)
    if not sealed_now:
        nf, bad, nsol = _seal_report(shape)
        L(f"  Still not closed ({nf} open edges, invalid faces {bad or 'none'}) — keeping only flat faces as surfaces")
        regions = [{**r, "type": "facet"} if r["type"] != "plane" else r for r in regions]
        B, results = prepare(regions)
        shape, counts, regions = assemble(regions, B, results)
        sealed_now = _sealed(shape)
    if not sealed_now:
        L("  Falling back to the exact faceted solid")
        return _build_faceted(ctx, label, planes, tol, L, rep)
    if counts["facet"]:
        L(f"  {counts['facet']} region(s) kept as exact facets")
    _DEBUG.update(final_regions=regions, final_B=B)
    ctx.final_regions = regions
    rep.n_planes, rep.n_primitives, rep.n_nurbs, rep.n_faceted_regions = \
        counts["plane"], counts["prim"], counts["nurbs"], counts["facet"]
    if final is not None:
        shape = final                # (already merged, written and read back successfully)
    else:
        shape = _safe_unify(shape, max(1e-6, tol * 0.05), L, protect=2 if prm.rough else 0, sealed=True)
    L.p(0.9)
    return shape


def _smooth_soft_chains(ctx, chains, regions, prox, iters=24):
    """Straighten patch-to-patch boundaries (they are arbitrary cuts through a smooth
    surface, so their zig-zag along mesh edges is noise). Real features — creases,
    plane edges, primitive edges — are left untouched."""
    soft = []
    for ch in chains:
        if len(ch["regions"]) != 2:      # a junction shared by more than two regions is a real feature
            continue
        a, b = ch["regions"]
        if a < 0 or b < 0:
            continue
        ra, rb = regions[a], regions[b]
        if ra["type"] == rb["type"] and ra["type"] in ("nurbs", "prim") \
                and ra.get("comp") == rb.get("comp") and len(ch["verts"]) >= 3:
            ch["on_prim"] = ra.get("prim") if ra["type"] == "prim" else None
            soft.append(ch)
    if not soft:
        return
    # resample each chain by arc length so smoothing is even
    P_list = []
    for ch in soft:
        P = ctx.V[ch["verts"]]
        seg = np.linalg.norm(np.diff(P, axis=0), axis=1)
        s = np.r_[0, np.cumsum(seg)]
        n = max(len(P), 6)
        t = np.linspace(0, s[-1], n)
        Q = np.c_[[np.interp(t, s, P[:, k]) for k in range(3)]].T
        P_list.append(Q)
    for it in range(iters):
        for Q in P_list:
            Q[1:-1] = 0.5 * Q[1:-1] + 0.25 * (Q[:-2] + Q[2:])
        if it % 4 == 3 or it == iters - 1:
            allp = np.vstack([Q[1:-1] for Q in P_list])
            if len(allp):
                cp, _, _ = trimesh.proximity.closest_point(prox, allp)
                k = 0
                for Q in P_list:
                    m = len(Q) - 2
                    Q[1:-1] = cp[k:k + m]; k += m
    for ch, Q in zip(soft, P_list):
        prim = ch.get("on_prim")
        if prim is not None:        # split line inside one round feature: lay it on the true surface
            Q = _project_to_prim(prim, Q)
        ch["pts"] = Q


def _project_to_prim(prim, P):
    P = np.array(P, float)
    if prim[0] in SWEPT:
        from tostep.plugins.mesh2step import rough
        return rough.project(prim, P)
    if prim[0] == "cyl":
        _, c, axis, r, _ = prim
        d = P - c
        h = d @ axis
        rad = d - np.outer(h, axis)
        n = np.linalg.norm(rad, axis=1, keepdims=True)
        n[n == 0] = 1
        return c + np.outer(h, axis) + rad / n * r
    _, c, r, _ = prim
    d = P - c
    n = np.linalg.norm(d, axis=1, keepdims=True)
    n[n == 0] = 1
    return c + d / n * r


def _prim_geom(prim, towards=None, pts=None):
    """Analytic surface (always right-handed) and whether faces on it must be flipped.

    `towards` = a point in the middle of the patch that will be cut from this surface.
    A cylinder's parameter seam is turned to face away from it, so the patch never
    straddles the seam — straddling makes the trimmed face invalid."""
    if prim[0] in SWEPT:
        from tostep.plugins.mesh2step import rough
        return rough.geom(prim, towards, pts)
    if towards is None and pts is not None and len(pts):
        towards = np.asarray(pts, float).mean(0)
    if prim[0] == "cyl":
        _, c, axis, r, outward = prim
        axis = np.asarray(axis, float)
        xd = None
        if towards is not None:
            d = np.asarray(towards, float) - c
            d -= (d @ axis) * axis
            if np.linalg.norm(d) > 1e-9:
                xd = -d / np.linalg.norm(d)
        ax = gp_Ax3(_gp(c), gp_Dir(*map(float, axis)), gp_Dir(*map(float, xd))) if xd is not None \
            else gp_Ax3(_gp(c), gp_Dir(*map(float, axis)))
        return Geom_CylindricalSurface(ax, float(r)), not outward
    _, c, r, outward = prim
    ax = gp_Ax3(_gp(c), gp_Dir(0, 0, 1))
    if pts is not None and len(pts) >= 3:
        # put the patch on the equator, opposite the seam, and as far from both poles as
        # possible (a patch touching a pole or the seam can't be trimmed cleanly)
        U = np.asarray(pts, float) - c
        U /= np.maximum(np.linalg.norm(U, axis=1, keepdims=True), 1e-12)
        d = U.mean(0)
        if np.linalg.norm(d) > 1e-6:
            d /= np.linalg.norm(d)
            e1 = np.cross(d, [1, 0, 0] if abs(d[0]) < 0.9 else [0, 1, 0]); e1 /= np.linalg.norm(e1)
            e2 = np.cross(d, e1)
            best = None
            for t in np.linspace(0, np.pi, 24, endpoint=False):
                a = math.cos(t) * e1 + math.sin(t) * e2
                m = float(np.abs(U @ a).max())
                if best is None or m < best[0]:
                    best = (m, a)
            if best[0] < 0.97:
                ax = gp_Ax3(_gp(c), gp_Dir(*map(float, best[1])), gp_Dir(*map(float, -d)))
            else:
                # too big to keep clear of both poles (e.g. a half sphere): centre it on a
                # pole instead — a polar cap, which the face repair knows how to close
                ax = gp_Ax3(_gp(c), gp_Dir(*map(float, d)))
    return Geom_SphericalSurface(ax, float(r)), not outward
