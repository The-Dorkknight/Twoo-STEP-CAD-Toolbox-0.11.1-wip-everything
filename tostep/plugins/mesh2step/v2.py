# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""
mesh2step.v2 — EXPERIMENTAL second-generation surface fitter (adaptive-knot NURBS,
per-face edges, STEP round-trip check). Same interface as mesh2step.classic; opt-in only.

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
import time
import warnings
from dataclasses import dataclass, field

import numpy as np
import trimesh

from tostep.plugins.mesh2step import meshfix                     # trimesh.repair.fix_normals without the pure-Python graph walk
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

# ---------------------------------------------------------------- OCP imports
from OCP.gp import gp_Pnt2d, gp_Dir, gp_Pln, gp_Ax3
from OCP.BRepClass import BRepClass_FaceClassifier
from OCP.BRep import BRep_Builder, BRep_Tool
from OCP.BRepBuilderAPI import (BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakeVertex,
                                BRepBuilderAPI_MakeWire)
from OCP.BRepFill import BRepFill_Filling
from OCP.BRepBndLib import BRepBndLib
from OCP.Bnd import Bnd_Box
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.BRepLib import BRepLib
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.GC import GC_MakeArcOfCircle, GC_MakeSegment
from OCP.GeomAPI import GeomAPI_PointsToBSpline
from OCP.GeomAbs import GeomAbs_C0, GeomAbs_C2
from OCP.Geom import Geom_CylindricalSurface, Geom_SphericalSurface, Geom_BSplineSurface
from OCP.ShapeFix import ShapeFix_Face, ShapeFix_Shape
from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain
from OCP.TopAbs import TopAbs_FACE, TopAbs_SOLID, TopAbs_OUT, TopAbs_EDGE
from OCP.TopoDS import TopoDS_Shell, TopoDS_Compound
from OCP.TopLoc import TopLoc_Location

# Identical to the Classic converter's own: shared from there instead of kept twice (0.6.0).
from tostep.plugins.mesh2step.classic import (  # noqa: E402,F401
    MeshCtx, _Graph, _Log, _S, _absorb_specks, _cast, _cone, _connected_from, _count_faces, _explore,
    _fit_circle2d, _fit_plane, _fit_primitive_raw, _fps, _fraction_inside, _free_edge_points, _gp,
    _is_disk, _largest_connected, _neighbors, _profile_ceiling, _pts_array, _qem, _quiet_occt, _refit_prim, _repaired_copy,
    _projector, _retriangulate_flat, _seal_report, _sealed, _settle, _sew, _shells_to_solid, _smooth_components,
    _split, _try, _try2, _volume, build_chains, layout_patches, read_step, safe_triangle_limit,
    snap_to_planes, write_step)

try:  # OCP >= 7.8 / 8.x
    from OCP.collections import (Array2_gp_Pnt as _ArrPnt2,
                                 Array1_double as _ArrReal, Array1_int as _ArrInt)
except ImportError:  # older OCP
    from OCP.TColgp import TColgp_Array2OfPnt as _ArrPnt2
    from OCP.TColStd import TColStd_Array1OfReal as _ArrReal, TColStd_Array1OfInteger as _ArrInt


# trimesh's point-to-triangle maths divides by zero on degenerate triangles and handles
# the result itself; the warnings are just noise in the log
warnings.filterwarnings("ignore", category=RuntimeWarning, module=r"trimesh\..*")


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
    use_labels: bool = True        # hardpoint STL input: use the face labels in the file (see hstl.py)
    save_labelled: str = ""        # path: also write the input mesh as a hardpoint STL of the faces found


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
    warnings: list = field(default_factory=list)

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
        lines.append(f"Time: {self.seconds:.1f} s")
        return "\n".join(lines)


# ---------------------------------------------------------------- mesh prep
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


def _collapse_tiny_edges(m, prm, log):
    """Merge vertex clusters closer than a tiny epsilon. Removes the slivers and
    needle triangles that exporters and marching cubes leave behind."""
    from scipy.spatial import cKDTree
    diag = float(np.linalg.norm(m.extents))
    tol = prm.tol if prm.tol > 0 else max(0.01, 5e-4 * diag)
    el = m.edges_unique_length
    med = float(np.median(el)) if len(el) else 0.0
    if len(el) > 100 and np.percentile(el, 90) < 8 * max(np.percentile(el, 10), 1e-12):
        # scan / voxel mesh (even triangle sizes): clusters closer than the tolerance are one
        # point as far as the result is concerned, and sewing would fold them onto themselves
        eps = min(tol, 0.2 * med)
    else:
        # CAD export: its points sit exactly on the true surfaces; move as few as possible
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
        import pyfqmr  # noqa: F401  (only: is it there?)
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


def _prim_residual(prim, pts):
    if prim[0] == "pln":
        return float(np.abs((pts - prim[1]) @ prim[2]).max())
    if prim[0] == "cyl":
        _, c, axis, r, _ = prim
        d = pts - c
        return float(np.abs(np.linalg.norm(d - np.outer(d @ axis, axis), axis=1) - r).max())
    _, c, r, _ = prim
    return float(np.abs(np.linalg.norm(pts - c, axis=1) - r).max())


# measured peak memory: fixed overhead + per-triangle cost (bytes), with some headroom
_MEM_MODEL = {"faceted": (500e6, 16e3), "surface": (500e6, 4e3)}


# ---------------------------------------------------------------- planar regions
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
    order = np.argsort(-ctx.area)
    sliver = ctx.sliver
    el = np.linalg.norm(ctx.V[ctx.edges_unique[:, 0]] - ctx.V[ctx.edges_unique[:, 1]], axis=1)
    med_edge = float(np.median(el)) if len(el) else 0.0
    scanlike = len(el) > 100 and np.percentile(el, 90) < 8 * max(np.percentile(el, 10), 1e-12)
    for seed in order:
        if label[seed] >= 0 or tried[seed] or sliver[seed]:
            continue
        tried[seed] = True
        n = ctx.N[seed].copy(); o = ctx.C[seed].copy()
        region = [seed]; inreg = {seed}; stack = [seed]; next_refit = 8
        while stack:
            f = stack.pop()
            for g in nb[f]:
                if g in inreg or label[g] >= 0:
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
        if m_in.any():
            d = np.einsum("ij,ij->i", ctx.N[ctx.adj[m_in, 0]], ctx.N[ctx.adj[m_in, 1]])
            smooth_frac = float((d > crease_cos).mean())
        # narrow strip with soft edges = one facet row of a curved surface
        if width < min_width and smooth_frac > 0.5:
            continue
        # small patch blending smoothly into its surroundings = flat spot on an organic shape
        if smooth_frac > 0.8 and a < 0.02 * total_area:
            continue
        # small flat faces (hole walls, little facets) count only if sharp edges bound them
        if ctx.area[idx].sum() < big_area and smooth_frac > 0.2:
            continue
        # scans / voxel meshes (even triangle sizes): a flat 'face' a few triangles across is
        # the mesher bevelling a sharp edge, not a real face
        if scanlike and len(idx) < 10 and float(np.ptp(pts, axis=0).max()) < 4 * med_edge:
            continue
        pid = len(planes)
        label[idx] = pid
        planes.append((o, n))
    log(f"Found {len(planes)} flat regions covering "
        f"{100 * ctx.area[label >= 0].sum() / total_area:.1f}% of the surface")
    return label, planes


# ---------------------------------------------------------------- primitives
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


def _row_norms(x, keepdims=False):
    """np.linalg.norm(x, axis=-1) without its Python-side argument handling: the same
    sums in the same order, so bit-identical, but cheaper on the small arrays the shape
    guessing calls it on hundreds of thousands of times (0.9.1)."""
    return np.sqrt(np.add.reduce(x * x, axis=x.ndim - 1, keepdims=keepdims))


def _prim_normal_ok(prim, pts, nrm, cos_lim):
    """Do these face normals agree with the primitive's own surface normal there?"""
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


def _batch_hypotheses(ctx, pairs, sV, sN, sC, sA, tol, cos_lim, cos_pl):
    """Cylinder / sphere / plane guesses from face pairs, scored on a sample of faces in
    one vectorised sweep. Returns [(score, hypothesis, seed_face), ...]."""
    if not pairs:
        return []
    f1 = np.array([p[0] for p in pairs]); f2 = np.array([p[1] for p in pairs])
    p1, n1, p2, n2 = ctx.C[f1], ctx.N[f1], ctx.C[f2], ctx.N[f2]
    diag = ctx.diag
    lmax = np.linalg.norm(sV[:, [1, 2, 0]] - sV, axis=2).max(1)
    ctol = 3 * tol + 0.05 * lmax

    def score(kind, prm_rows):
        """support area per hypothesis (rows of parameters of one kind)"""
        out = np.zeros(len(prm_rows))
        _dev = None
        try:
            from tostep.core import accel
            _dev = accel.device()
        except Exception:
            _dev = None
        if _dev is not None:
            # (0.11.2) one pass over the card: every block is queued without waiting, then ONE read-back.
            # Blocks of 1024 guesses (the largest batch a round makes is ~100-300): memory stays small
            # (1024 x 1500 triangles x 3 corners x 4 bytes = 18 MB per array) and the launches are few.
            try:
                parts = [accel.score_blocks(kind, prm_rows[s0:s0 + 1024], sV, sN, sC, sA, tol, ctol,
                                            cos_lim, cos_pl, _dev, sync=False)
                         for s0 in range(0, len(prm_rows), 1024)]
                return accel.gather(parts)
            except Exception:
                _dev = None                              # fall back to NumPy for all of it
        step = 32
        for s0 in range(0, len(prm_rows), step):          # chunks keep memory small
            blk = prm_rows[s0:s0 + step]
            if kind == "pln":
                o, n = blk[:, 0:3], blk[:, 3:6]
                rc = np.abs(np.einsum("sck,hk->hsc", sV, n) - (o * n).sum(1)[:, None, None]).max(2)
                rm = np.abs(sC @ n.T - (o * n).sum(1)).T
                ok_n = (sN @ n.T).T >= cos_pl
            elif kind == "cyl":
                c, a, r, out_ = blk[:, 0:3], blk[:, 3:6], blk[:, 6], blk[:, 7] > 0.5
                d = sV[None] - c[:, None, None]                          # h,s,3corners,3
                h = np.einsum("hsck,hk->hsc", d, a)
                rad = np.linalg.norm(d - h[..., None] * a[:, None, None], axis=3)
                rc = np.abs(rad - r[:, None, None]).max(2)
                dc = sC[None] - c[:, None]
                hc = np.einsum("hsk,hk->hs", dc, a)
                radv = dc - hc[..., None] * a[:, None]
                nr = np.linalg.norm(radv, axis=2)
                rm = np.abs(nr - r[:, None])
                dirn = radv / np.maximum(nr, 1e-12)[..., None] * np.where(out_, 1.0, -1.0)[:, None, None]
                ok_n = np.einsum("hsk,sk->hs", dirn, sN) >= cos_lim
            else:
                c, r, out_ = blk[:, 0:3], blk[:, 3], blk[:, 4] > 0.5
                rc = np.abs(np.linalg.norm(sV[None] - c[:, None, None], axis=3) - r[:, None, None]).max(2)
                dc = sC[None] - c[:, None]
                nr = np.linalg.norm(dc, axis=2)
                rm = np.abs(nr - r[:, None])
                dirn = dc / np.maximum(nr, 1e-12)[..., None] * np.where(out_, 1.0, -1.0)[:, None, None]
                ok_n = np.einsum("hsk,sk->hs", dirn, sN) >= cos_lim
            ok = (rc <= tol) & (rm <= ctol[None]) & ok_n
            out[s0:s0 + len(blk)] = ok.astype(float) @ sA
        return out

    cands = []
    # planes through the first face of each pair
    rows = np.c_[p1, n1]
    for sc, i in zip(score("pln", rows), range(len(pairs))):
        cands.append((sc, ("pln", p1[i].copy(), n1[i].copy()), int(f1[i])))
    # cylinders: axis across both normals
    a = np.cross(n1, n2); la = np.linalg.norm(a, axis=1)
    m = la > 0.08
    if m.any():
        a_ = a[m] / la[m, None]
        q1 = n1[m] - np.einsum("ij,ij->i", n1[m], a_)[:, None] * a_
        q2 = n2[m] - np.einsum("ij,ij->i", n2[m], a_)[:, None] * a_
        dp = p2[m] - p1[m]
        A11 = np.einsum("ij,ij->i", q1, q1); A12 = -np.einsum("ij,ij->i", q1, q2); A22 = np.einsum("ij,ij->i", q2, q2)
        b1 = np.einsum("ij,ij->i", q1, dp); b2 = -np.einsum("ij,ij->i", q2, dp)
        det = A11 * A22 - A12 * A12
        good = np.abs(det) > 1e-12
        t = np.where(good, (b1 * A22 - A12 * b2) / np.where(good, det, 1), 0)
        c = p1[m] + t[:, None] * q1
        c = c - np.einsum("ij,ij->i", c - p1[m], a_)[:, None] * a_
        def rperp(p):
            d = p - c
            return np.linalg.norm(d - np.einsum("ij,ij->i", d, a_)[:, None] * a_, axis=1)
        r = 0.5 * (rperp(p1[m]) + rperp(p2[m]))
        outw = np.einsum("ij,ij->i", p1[m] - c, n1[m]) > 0
        keep = good & (r > 1e-6) & (r < diag)
        idx = np.flatnonzero(m)[keep]
        if len(idx):
            rows = np.c_[c[keep], a_[keep], r[keep], outw[keep]]
            for sc, k, i in zip(score("cyl", rows), range(len(idx)), idx):
                cands.append((sc, ("cyl", c[keep][k], a_[keep][k], float(r[keep][k]), bool(outw[keep][k])), int(f1[i])))
    # spheres: closest approach of the two normal lines
    w0 = p1 - p2; b = np.einsum("ij,ij->i", n1, n2)
    d_ = np.einsum("ij,ij->i", n1, w0); e_ = np.einsum("ij,ij->i", n2, w0); den = 1 - b * b
    m = den > 1e-4
    if m.any():
        t = (b[m] * e_[m] - d_[m]) / den[m]; u = (e_[m] - b[m] * d_[m]) / den[m]
        c1 = p1[m] + t[:, None] * n1[m]; c2 = p2[m] + u[:, None] * n2[m]
        c = 0.5 * (c1 + c2)
        r = 0.5 * (np.linalg.norm(p1[m] - c, axis=1) + np.linalg.norm(p2[m] - c, axis=1))
        keep = (r > 1e-6) & (r < diag) & (np.linalg.norm(c1 - c2, axis=1) < 0.1 * r)
        outw = np.einsum("ij,ij->i", p1[m] - c, n1[m]) > 0
        idx = np.flatnonzero(m)[keep]
        if len(idx):
            rows = np.c_[c[keep], r[keep], outw[keep]]
            for sc, k, i in zip(score("sph", rows), range(len(idx)), idx):
                cands.append((sc, ("sph", c[keep][k], float(r[keep][k]), bool(outw[keep][k])), int(f1[i])))
    return cands


def _support(prim, Vf, Nf, Cf, tol, cos_lim):
    """Which faces agree with a primitive: all three corners on it, the triangle's middle
    near it (long thin triangles can have all corners on a wrong shape), same direction."""
    res = np.array([_prim_residual_vec(prim, Vf[:, k]) for k in range(3)]).max(0)
    lmax = _row_norms(Vf[:, [1, 2, 0]] - Vf).max(1)
    ok = (res <= tol) & (_prim_residual_vec(prim, Cf) <= 3 * tol + 0.05 * lmax)
    if ok.any():
        ok[ok] = _prim_normal_ok(prim, Cf[ok], Nf[ok], cos_lim)
    return ok


def _prim_residual_vec(prim, P):
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
        pairs = []
        for f1 in rem[rng.choice(len(rem), size=96, p=prob)]:
            # partner within a few rings, so both samples likely sit on the same shape
            ring, seen = [f1], {f1}
            for _ in range(3):
                ring = [g for f in ring for g in nb[f] if remaining[g] and g not in seen]
                seen.update(ring)
            partner = [g for g in seen if g != f1 and not ctx.sliver[g]]
            if partner:
                pairs.append((f1, partner[rng.integers(len(partner))]))
        # all guesses at once (cylinders, spheres, and planes for flat bands between rounds),
        # scored loosely - guesses from triangle centres are a little off (chord sag)
        cands = _batch_hypotheses(ctx, pairs, sV, sN, sC, sA, 3 * tol, cos_lim, cos_pl)
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


def _interior_refit(ctx, region, prim, tol):
    """Refit using only the region's interior corners — corners shared with a neighbouring
    shape (e.g. where an edge round runs into a corner ball) bias the radius."""
    if prim[0] == "pln":
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


# ---------------------------------------------------------------- patch layout
def _split_by_residual(ctx, graph, ids, why, tol, with_band=False):
    """Split a patch into the faces the fitted surface missed ('hot' band) and the rest.
    None if the misses aren't a compact part of the patch."""
    if not why or "vres" not in why:
        return None
    vids, res = why["vres"]
    vr = np.zeros(len(ctx.V)); vr[vids] = res
    ids = np.asarray(ids)
    hot = vr[ctx.F[ids]].max(1) > 0.7 * tol
    frac = ctx.area[ids[hot]].sum() / max(ctx.area[ids].sum(), 1e-300)
    if not (0.03 <= frac <= 0.6):
        return None
    # grow the band by one ring inside the patch, so its borders sit on well-fitted ground
    inp = np.zeros(ctx.nF, bool); inp[ids] = True
    hs = np.zeros(ctx.nF, bool); hs[ids[hot]] = True
    a, b = ctx.adj[:, 0], ctx.adj[:, 1]
    grow = hs.copy()
    grow[b[hs[a] & inp[b]]] = True; grow[a[hs[b] & inp[a]]] = True
    masks = [~grow & inp]
    band = np.flatnonzero(grow & inp)
    if len(band):
        # the band usually runs along a tight rounded (or sharp) edge: cut it down the
        # middle, one side per direction the surface faces, so each half is smooth
        Nb, wb = ctx.N[band], ctx.area[band]
        mean = (Nb * wb[:, None]).sum(0)
        c1 = Nb[np.argmin(Nb @ mean)]
        c2 = Nb[np.argmin(Nb @ c1)]
        for _ in range(6):
            side = (Nb @ c1) >= (Nb @ c2)
            if side.all() or (~side).all():
                break
            c1 = (Nb[side] * wb[side, None]).sum(0); c1 /= np.linalg.norm(c1)
            c2 = (Nb[~side] * wb[~side, None]).sum(0); c2 /= np.linalg.norm(c2)
        if not side.all() and not (~side).all() and c1 @ c2 < math.cos(math.radians(15)):
            for sd in (side, ~side):
                m = np.zeros(ctx.nF, bool); m[band[sd]] = True
                masks.append(m)
        else:
            masks.append(grow & inp)
    parts, is_band = [], []
    for k, mask in enumerate(masks):
        sel = np.flatnonzero(mask)
        if not len(sel):
            continue
        G = graph.sub(sel, ctx.nF)
        nc, cl = connected_components(G, directed=False)
        parts.extend(sel[cl == c] for c in range(nc))
        is_band.extend([k > 0] * nc)
    # crumbs join the neighbour they touch most
    lab = -np.ones(ctx.nF, np.int64)
    for i, p in enumerate(parts):
        lab[p] = i
    for _ in range(3):
        sizes = np.bincount(lab[ids], minlength=len(parts))
        small = [i for i in range(len(parts)) if 0 < sizes[i] < 6]
        if not small:
            break
        for i in small:
            m = (lab[a] == i) & (lab[b] >= 0) & (lab[b] != i)
            m2 = (lab[b] == i) & (lab[a] >= 0) & (lab[a] != i)
            nb = np.r_[lab[b[m]], lab[a[m2]]]
            if len(nb):
                lab[lab == i] = np.bincount(nb).argmax()
    keep_ids = np.unique(lab[ids])
    out = [ids[lab[ids] == i] for i in keep_ids]
    if len(out) < 2:
        return None
    if with_band:
        return [(p, is_band[i]) for p, i in zip(out, keep_ids)]
    return out


def _merge_crumbs(ctx, regions, min_faces=12):
    """Tiny NURBS patches (a handful of triangles left over by splitting) can't be fitted
    well on their own: merge each into the neighbouring patch of the same smooth area."""
    a, b = ctx.adj[:, 0], ctx.adj[:, 1]
    for _ in range(3):
        lab = -np.ones(ctx.nF, np.int64)
        for ri, r in enumerate(regions):
            lab[r["faces"]] = ri
        merged = False
        gone = set()
        for ri, r in enumerate(regions):
            if r["type"] != "nurbs" or ri in gone or len(r["faces"]) >= min_faces:
                continue
            inr = lab == ri
            nb = np.r_[lab[b[inr[a]]], lab[a[inr[b]]]]
            nb = nb[(nb >= 0) & (nb != ri)]
            for cand in np.unique(nb)[np.argsort(-np.bincount(nb)[np.unique(nb)])]:
                c = regions[cand]
                if cand in gone or c["type"] != "nurbs" or c.get("comp") != r.get("comp"):
                    continue
                f2 = np.r_[c["faces"], r["faces"]]
                if _is_disk(ctx, f2):
                    c["faces"] = f2
                    lab[r["faces"]] = cand
                    gone.add(ri); merged = True
                    break
        regions[:] = [r for i, r in enumerate(regions) if i not in gone]
        if not merged:
            break
    # what's still tiny sits alone between sharp edges: a few triangles of mesh noise
    for r in regions:
        if r["type"] == "nurbs" and len(r["faces"]) < min_faces // 2:
            r["type"] = "facet"
    return regions


# ---------------------------------------------------------------- boundary chains
# ---------------------------------------------------------------- OCC helpers
def _curve_strays(curve, P, tol):
    """True if the curve leaves the polyline P by more than half the tolerance anywhere
    (fits are only guaranteed at the points; between them they can swing out)."""
    from scipy.spatial import cKDTree
    from OCP.GeomAdaptor import GeomAdaptor_Curve
    seg = np.linspace(0.0, 1.0, 12)[:, None]
    dense = np.vstack([P[i] + seg * (P[i + 1] - P[i]) for i in range(len(P) - 1)])
    ad = GeomAdaptor_Curve(curve)
    ts = np.linspace(ad.FirstParameter(), ad.LastParameter(), max(40, 4 * len(P)))
    X = np.array([[q.X(), q.Y(), q.Z()] for q in (ad.Value(float(t)) for t in ts)])
    return float(cKDTree(dense).query(X)[0].max()) > 0.5 * tol


def _chain_curve(P, tol):
    """Fit a polyline with a line, circular arc, or B-spline, checked along its whole
    length. Returns (Geom_Curve, kind); (None, "polyline") keeps the mesh edges."""
    n = len(P)
    p0, p1 = P[0], P[-1]
    d = p1 - p0
    L = np.linalg.norm(d)
    if n == 2:
        return GC_MakeSegment(_gp(p0), _gp(p1)).Value(), "line"
    if L > 1e-9:
        u = d / L
        off = (P - p0) - np.outer((P - p0) @ u, u)
        if np.linalg.norm(off, axis=1).max() <= 0.05 * tol:
            return GC_MakeSegment(_gp(p0), _gp(p1)).Value(), "line"
    # circle through first, middle, last point (a circle through 3 points always fits
    # them - the check along the curve is what tells a real arc from a corner)
    pm = P[n // 2]
    a, b = pm - p0, p1 - p0
    axb = np.cross(a, b)
    if np.linalg.norm(axb) > 1e-9 * max(1.0, np.linalg.norm(a) * np.linalg.norm(b)):
        nrm = axb / np.linalg.norm(axb)
        denom = 2 * (axb @ axb)
        if denom > 1e-18:
            c = p0 + ((a @ a) * np.cross(b, axb) + (b @ b) * np.cross(axb, a)) / denom
            r = np.linalg.norm(p0 - c)
            q = P - c
            h = q @ nrm
            rad = np.linalg.norm(q - np.outer(h, nrm), axis=1)
            dev = np.sqrt(h ** 2 + (rad - r) ** 2)
            if dev.max() <= 0.25 * tol and r < 1e4:
                try:
                    arc = GC_MakeArcOfCircle(_gp(p0), _gp(pm), _gp(p1))
                    if arc.IsDone() and not _curve_strays(arc.Value(), P, tol):
                        return arc.Value(), "arc"
                except Exception:
                    pass
    if n <= 3:
        return None, "polyline"
    Q = P
    if n > 400:  # subsample long polylines (keep ends)
        idx = np.unique(np.r_[np.linspace(0, n - 1, 400).astype(int), n - 1])
        Q = P[idx]
    for dmax in (4, 8):
        try:
            fit = GeomAPI_PointsToBSpline(_pts_array(Q), 3, dmax, GeomAbs_C2, 0.5 * tol)
            if fit.IsDone() and not _curve_strays(fit.Curve(), P, tol):
                return fit.Curve(), "bspline"
        except Exception:
            pass
    return None, "polyline"


class Builder:
    def __init__(self, ctx, label, regions, chains, tol, prm, log):
        self.ctx, self.label, self.regions, self.chains = ctx, label, regions, chains
        self.tol, self.prm, self.log = tol, prm, log
        self.vtx = {}
        self.edges = []  # per chain: list of (TopoDS_Edge) forward order
        self.kinds = {"line": 0, "arc": 0, "bspline": 0, "polyline": 0}
        self.dir_he = set(map(tuple, np.concatenate([ctx.F[:, [0, 1]], ctx.F[:, [1, 2]], ctx.F[:, [2, 0]]])))
        he_face = {}
        for fi, (a, b, c) in enumerate(ctx.F):
            he_face[(a, b)] = fi; he_face[(b, c)] = fi; he_face[(c, a)] = fi
        self.he_face = he_face
        self.prox = None
        self.method = {}
        self.why = {}          # diagnostics: why a NURBS patch missed tolerance
        el = np.linalg.norm(ctx.V[ctx.edges_unique[:, 0]] - ctx.V[ctx.edges_unique[:, 1]], axis=1)
        self._med_edge = float(np.median(el)) if len(el) else 1.0
        self._make_edges()
        self.region_loops = self._loops()

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
                curve, kind = _chain_curve(P, self.tol)
            elist = []
            if curve is not None:
                me = BRepBuilderAPI_MakeEdge(curve, self._v(vs[0]), self._v(vs[-1]))
                if me.IsDone():
                    elist = [me.Edge()]
                else:
                    kind = "polyline"
            if not elist and "pts" in ch and not facet_side:
                # smoothed patch-to-patch border: straight pieces through the smoothed points
                # (the patches on both sides were fitted to those, not to the raw zigzag)
                vx = [self._v(vs[0])] + [BRepBuilderAPI_MakeVertex(_gp(q)).Vertex() for q in P[1:-1]] \
                    + [self._v(vs[-1])]
                for i in range(len(vx) - 1):
                    me = BRepBuilderAPI_MakeEdge(vx[i], vx[i + 1])
                    if me.IsDone():
                        elist.append(me.Edge())
                if len(elist) != len(vx) - 1:
                    elist = []
                kind = "polyline"
            if not elist:  # polyline fallback: one straight edge per segment
                for i in range(len(vs) - 1):
                    me = BRepBuilderAPI_MakeEdge(self._v(vs[i]), self._v(vs[i + 1]))
                    if me.IsDone():
                        elist.append(me.Edge())
                kind = "polyline"
            self.kinds[kind] += 1
            ch["kind"] = kind
            self.edges.append(elist)

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

    def wire(self, loop):
        """A wire for one face. Every face gets its own copies of the edges (see
        fresh_wire): building and repairing a face then can't disturb its neighbours."""
        fw = _try(self.fresh_wire, loop)
        return fw[0] if fw else None

    def loop_points(self, loop):
        pts = []
        for ci, rev in loop:
            vs = self.chains[ci]["verts"]
            vs = vs[::-1] if rev else vs
            pts.extend(vs[:-1])
        return self.ctx.V[pts]

    # -- faces
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
        outer = self.wire(loops[oi])
        if outer is None:
            return None
        mf = BRepBuilderAPI_MakeFace(gp_Pln(ax), outer, True)
        if not mf.IsDone():
            return None
        for i, lp in enumerate(loops):
            if i != oi:
                w = self.wire(lp)
                if w is not None:
                    mf.Add(w)
        return self._fix(mf.Face())

    def prim_face(self, r, info):
        loops = self.region_loops.get(r, [])
        if not loops:
            return None
        surf = info["surf_geom"]
        flip = info.get("flip", False)
        if flip:  # surface normal points away from the solid: build reversed, then flip face
            loops = [[(ci, not rev) for ci, rev in reversed(lp)] for lp in loops]
        target = float(self.ctx.area[info["faces"]].sum())

        def build(lps, fix):
            outer = self.wire(lps[0])
            if outer is None:
                return None
            mf = BRepBuilderAPI_MakeFace(surf, outer, True)
            if not mf.IsDone():
                return None
            for lp in lps[1:]:
                w = self.wire(lp)
                if w is not None:
                    mf.Add(w)
            f = mf.Face()
            sf = ShapeFix_Face(f)
            if not fix:
                sf.FixOrientationMode = 0      # add what's missing, but keep the side we gave
            sf.Perform()
            return sf.Face()

        # On a closed surface (sphere, full cylinder) the outline alone doesn't say which
        # side is the face, and the standard face repair may "correct" it into the
        # complement — a whole ball where a corner patch belongs. Build both sides with
        # the side kept as given, then with full repair, and keep the one that holds the
        # region's own mesh and matches its area. (Each face has its own edges, so
        # repairing a candidate can't disturb the neighbours.)
        rev = [[(ci, not r_) for ci, r_ in reversed(lp)] for lp in loops]
        # sample points of the region's own mesh: the right side must contain them
        fc = info["faces"]
        pick = fc if len(fc) <= 12 else fc[np.linspace(0, len(fc) - 1, 12).astype(int)]
        probe = self.ctx.C[pick]
        best = None
        for lps, fix in ((loops, False), (rev, False), (loops, True), (rev, True)):
            f = _try(build, lps, fix)
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
        # plate filling solves one dense system over every constraint point: with a boundary
        # made of hundreds of little edges (next to facets) it can run for hours. Keep the
        # total bounded, and don't try at all on very ragged outlines.
        n_edges = sum(len(self.edges[ci]) for ci, _ in loops[0])
        if n_edges > 120:
            return best
        per_edge = int(np.clip(480 // max(n_edges, 1), 3, 30))
        for attempt in range(4):
            fl = BRepFill_Filling(3, per_edge, 2, False, 1e-5, max(1e-4, 0.2 * self.tol), 0.05, 0.1, 12, 30)
            fw = self.fresh_wire(loops[0])      # own edge copies (filling alters the edges)
            if fw is None:
                break
            for e, _, _ in fw[1]:
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
        if len(info["faces"]) > 80:
            # plate filling only ever rescues small patches (measured); on big ones it
            # costs many seconds each and still misses
            return face, dev
        f2, d2 = _try2(self.nurbs_face_fill, r, info)
        if f2 is not None and d2 < dev:
            self.method[r] = "fill"
            return f2, d2
        return face, dev

    def nurbs_face_lsq(self, r, info):
        loops = self.region_loops.get(r, [])
        why = self.why.setdefault(r, {})
        why["loops"] = len(loops)
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
        ring, ring_faces = set(), set()
        comp = info.get("comp")
        vset = set(vids.tolist())
        fset = set(fids.tolist())
        cos_ring = math.cos(math.radians(25.0))
        for vi in vids:
            pf = [f for f in ctx.vert_faces[vi] if f in fset]
            if not pf:
                continue
            nv = (ctx.N[pf] * ctx.area[pf, None]).sum(0)
            nv /= max(np.linalg.norm(nv), 1e-300)
            for f in ctx.vert_faces[vi]:
                # margin faces only where the surface carries on smoothly: across a tight
                # bend they'd pin the surface to points folded back over the patch
                if f not in fset and ctx.face_comp[f] == comp and ctx.N[f] @ nv >= cos_ring:
                    ring.update(ctx.F[f].tolist()); ring_faces.add(f)
        ring = np.array(sorted(ring - vset), dtype=np.int64)
        P = ctx.V[vids]
        Pr = ctx.V[ring] if len(ring) else np.zeros((0, 3))
        allP = np.vstack([P, Pr])
        # the boundary curve points are fitted too, so the surface hugs the trim
        bpts = []
        for ci, _ in loops[0]:
            ch = self.chains[ci]
            bpts.append(ch["pts"] if "pts" in ch else ctx.V[ch["verts"]])
        B = np.vstack(bpts)
        uv = None
        if _cone(ctx, fids) > 35.0:
            # strongly bent patch: flatten it along the mesh (conformal map) - a plain
            # projection squashes the steep parts, which then need far more control points
            uv = _try(_lscm_uv, ctx, np.fromiter(ring_faces | set(fids.tolist()), np.int64),
                      np.r_[vids, ring], B)
            why["param"] = "lscm" if uv is not None else "proj"
        if uv is not None:
            S, Sb, map2d = uv
        else:
            # parametrise by projection onto the mean-normal plane
            c0 = P.mean(0)
            q = (P - c0) - np.outer((P - c0) @ nrm, nrm)
            _, _, vt = np.linalg.svd(q, full_matrices=False)
            e1 = vt[0] - (vt[0] @ nrm) * nrm; e1 /= np.linalg.norm(e1)
            e2 = np.cross(nrm, e1)
            S = np.c_[(allP - c0) @ e1, (allP - c0) @ e2]
            Sb = np.c_[(B - c0) @ e1, (B - c0) @ e2]

            def map2d(X, c0=c0, e1=e1, e2=e2):
                X = np.asarray(X, float)
                return np.c_[(X - c0) @ e1, (X - c0) @ e2]
        # the surface must extend past the whole trim outline (boundary points included),
        # or the part of the outline beyond it gets clipped and the face comes out short
        lo = np.minimum(S[:len(P)].min(0), Sb.min(0)); hi = np.maximum(S[:len(P)].max(0), Sb.max(0))
        span = hi - lo
        if span.min() < 1e-9:
            return None, float("inf")
        lo = lo - 0.15 * span; hi = hi + 0.15 * span
        # triangle middles too: with only corner points, a surface with many control
        # points is free to ripple between them (it then bulges off the mesh and its area
        # grows). The middles tie it to the mesh everywhere.
        row = -np.ones(len(ctx.V), np.int64); row[np.r_[vids, ring]] = np.arange(len(vids) + len(ring))
        cf = np.fromiter(set(fids.tolist()) | ring_faces, np.int64)
        cr = row[ctx.F[cf]]
        okc = (cr >= 0).all(1)
        cf, cr = cf[okc], cr[okc]
        Pc, Sc = ctx.C[cf], S[cr].mean(1)
        in_patch = np.isin(cf, fids)
        # ...and edge middles (a ripple can also run along an edge between two corners)
        Fe = ctx.F[cf]
        E = np.unique(np.sort(np.r_[Fe[:, [0, 1]], Fe[:, [1, 2]], Fe[:, [2, 0]]], axis=1), axis=0)
        er = row[E]
        E_in = np.isin(E, ctx.F[fids]).all(1)
        Pc = np.vstack([Pc, 0.5 * (ctx.V[E[:, 0]] + ctx.V[E[:, 1]])])
        Sc = np.vstack([Sc, 0.5 * (S[er[:, 0]] + S[er[:, 1]])])
        in_patch = np.r_[in_patch, E_in]
        keep = np.all((S >= lo) & (S <= hi), axis=1)
        keep[:len(P)] = True
        allP, S = allP[keep], S[keep]
        kc = in_patch | np.all((Sc >= lo) & (Sc <= hi), axis=1)
        Pc, Sc, in_patch = Pc[kc], Sc[kc], in_patch[kc]
        Wt = np.r_[np.ones(len(P)), np.full(len(allP) - len(P), 0.35)]
        U = (S - lo) / (hi - lo)
        Ub = (Sb - lo) / (hi - lo)
        okb = np.all((Ub >= 0) & (Ub <= 1), axis=1)
        allP = np.vstack([allP, B[okb], Pc]); U = np.vstack([U, Ub[okb], (Sc - lo) / (hi - lo)])
        Wt = np.r_[Wt, np.full(okb.sum(), 2.0), np.where(in_patch, 0.9, 0.3)]   # middles: not judged
        n_data = len(P)
        ar = span[0] / span[1]
        best = (None, float("inf"))
        n_all = len(allP)          # patch + margin + boundary samples all pin the surface
        k = max(16, min(576, int(0.125 * n_all)))
        nu0 = int(np.clip(round(math.sqrt(k * ar)), 4, 24))
        nv0 = int(np.clip(round(math.sqrt(k / ar)), 4, 24))
        judge = Wt >= 1.0           # patch points and trim-outline points must be hit
        for smooth in (1e-4, 1e-3):
            # start coarse, then add knots only where the surface misses (tight curves,
            # small details): few control points on gentle areas, many where needed
            fit = _lsq_adaptive(allP, U, Wt, judge, self.tol, nu0, nv0, smooth)
            if fit is None:
                continue
            C, ku, kv, res = fit
            fit_dev = float(res[judge].max())
            why.setdefault("fit", []).append(round(fit_dev / self.tol, 2))
            if "vres" not in why or fit_dev < float(why["vres"][1].max()):
                why["vres"] = (vids, res[:n_data].copy())     # where it misses: guides splitting
            if fit_dev > 1.5 * self.tol:
                continue
            surf = _occ_bspline_surface(C, len(ku) + 2, len(kv) + 2, ku, kv)
            # trim with outline curves drawn in the surface's own parameters (we know where
            # every outline point sits on the patch); projecting the 3D edges onto the
            # surface instead lands in the wrong place wherever the surface curls away
            face = _try(self._trim_uv, surf, loops[0], lambda X: (map2d(X) - lo) / (hi - lo))
            why.setdefault("uvtrim", []).append(face is not None)
            if face is None:
                face = self._trim(surf, loops[0])
            if face is None:
                why["trim_fail"] = why.get("trim_fail", 0) + 1
                continue
            self._rev_reason = None
            rd = self._reverse_dev(face, fids)
            why.setdefault("rev", []).append(round(rd / self.tol, 2) if np.isfinite(rd) else self._rev_reason)
            dev = max(fit_dev, rd)
            if dev < best[1]:
                best = (face, dev)
            if dev <= self.tol:
                break
        return best

    def _trim_uv(self, surf, loop, to_uv):
        """Face on `surf` bounded by the loop's edges, with explicit parameter-space curves
        computed from `to_uv` (3D points -> surface parameters)."""
        from OCP.TopoDS import TopoDS_Face
        from OCP.Geom2dAPI import Geom2dAPI_PointsToBSpline
        try:
            from OCP.collections import Array1_gp_Pnt2d as TColgp_Array1OfPnt2d, Array1_double as TColStd_Array1OfReal
        except ImportError:
            from OCP.TColgp import TColgp_Array1OfPnt2d
            from OCP.TColStd import TColStd_Array1OfReal
        from OCP.BRepAdaptor import BRepAdaptor_Curve
        from OCP.GeomAbs import GeomAbs_C1
        fw = self.fresh_wire(loop)
        if fw is None:
            return None
        wire, items = fw
        b = BRep_Builder()
        face = TopoDS_Face()
        b.MakeFace(face, surf, TopLoc_Location(), 1e-7)
        area2 = 0.0
        med = self._med_edge
        for e, rev, single in items:
                cu = BRepAdaptor_Curve(e)
                t0, t1 = cu.FirstParameter(), cu.LastParameter()
                if cu.GetType().name.endswith("Line"):
                    n = 5
                else:     # enough samples to follow the curve's own wiggles
                    L = sum(cu.Value(float(a)).Distance(cu.Value(float(b_))) for a, b_ in
                            zip(np.linspace(t0, t1, 17)[:-1], np.linspace(t0, t1, 17)[1:]))
                    n = int(np.clip(4 * L / max(med, 1e-9), 9, 80))
                ts = np.linspace(t0, t1, n)
                X = np.array([[p.X(), p.Y(), p.Z()] for p in (cu.Value(float(t)) for t in ts)])
                uv = to_uv(X)
                if not np.all(np.isfinite(uv)):
                    return None
                pa = TColgp_Array1OfPnt2d(1, n); pr = TColStd_Array1OfReal(1, n)
                for i in range(n):
                    pa.SetValue(i + 1, gp_Pnt2d(float(uv[i, 0]), float(uv[i, 1]))); pr.SetValue(i + 1, float(ts[i]))
                fit = Geom2dAPI_PointsToBSpline(pa, pr, 1, 3, GeomAbs_C1, 1e-6)
                if not fit.IsDone():
                    return None
                pc = fit.Curve()
                # how far the edge's 3D curve is from the surface along this parameter curve:
                # that is the edge's tolerance (and if it's large, the trim is no good)
                tt = np.linspace(t0, t1, max(2 * n, 24))
                dev = max(cu.Value(float(t)).Distance(surf.Value(pc.Value(float(t)).X(), pc.Value(float(t)).Y()))
                          for t in tt)
                if dev > 3 * self.tol:
                    return None
                b.UpdateEdge(e, pc, face, max(1.3 * dev, 1e-7))
                b.UpdateEdge(e, max(1.3 * dev, 1e-7))
                b.SameParameter(e, True); b.SameRange(e, True)
                seq = uv[::-1] if rev else uv          # outline direction, for the orientation
                area2 += float(np.sum(seq[:-1, 0] * seq[1:, 1] - seq[1:, 0] * seq[:-1, 1]))
        # The outer wire must run anticlockwise in (u, v). If the outline runs clockwise
        # there, the surface's own normal points into the solid: use the wire reversed
        # (a valid face facing inwards), then flip the face so it faces out again.
        b.Add(face, wire if area2 >= 0 else _cast("Wire", wire.Reversed()))
        _S(BRepLib, "UpdateTolerances")(face, False)
        if area2 < 0:
            face = _cast("Face", face.Reversed())
        self._uv_valid = BRepCheck_Analyzer(face).IsValid()
        return face if self._uv_valid else None

    def fresh_wire(self, loop):
        """The loop as a wire of this face's own new edges (same curves and points as the
        shared ones). Fitting attempts add parameter curves to the edges they trim with
        and adjust their tolerances; on shared edges a failed attempt would leave that
        behind on the neighbours' edges too. The faces are sewn together afterwards anyway.
        Returns (wire, [(edge, reversed_in_loop, is_single_edge_chain), ...])."""
        from OCP.TopExp import TopExp
        from OCP.TopoDS import TopoDS_Wire
        from OCP.BRepAdaptor import BRepAdaptor_Curve
        vmap = {}
        b = BRep_Builder()

        def fv(v):
            v = _cast("Vertex", v)
            p = _S(BRep_Tool, "Pnt")(v)
            key = (round(p.X(), 9), round(p.Y(), 9), round(p.Z(), 9))
            if key not in vmap:
                nv = BRepBuilderAPI_MakeVertex(p).Vertex()
                b.UpdateVertex(nv, max(_S(BRep_Tool, "Tolerance")(v), 1e-7))   # same reach as the original
                vmap[key] = nv
            return vmap[key]
        w = TopoDS_Wire(); b.MakeWire(w)
        items = []
        for ci, rev in loop:
            es = self.edges[ci]
            for e in (reversed(es) if rev else es):
                ad = BRepAdaptor_Curve(e)
                t0, t1 = ad.FirstParameter(), ad.LastParameter()
                crv = _S(BRep_Tool, "Curve")(e, t0, t1)
                if isinstance(crv, tuple):
                    crv = crv[0]
                v0, v1 = fv(TopExp.FirstVertex_s(e)), fv(TopExp.LastVertex_s(e))
                me = BRepBuilderAPI_MakeEdge(crv, v0, v1, t0, t1)
                if not me.IsDone():
                    # curve ends a hair off the points: widen the points' reach and retry
                    for vv, tt in ((v0, t0), (v1, t1)):
                        gap = _S(BRep_Tool, "Pnt")(vv).Distance(ad.Value(tt))
                        b.UpdateVertex(vv, max(_S(BRep_Tool, "Tolerance")(vv), 1.5 * gap, 1e-7))
                    me = BRepBuilderAPI_MakeEdge(crv, v0, v1, t0, t1)
                    if not me.IsDone():
                        return None
                ne = me.Edge()
                b.Add(w, _cast("Edge", ne.Reversed()) if rev else ne)
                items.append((ne, rev, len(es) == 1))
        w.Closed(True)
        return w, items

    def _trim(self, surf, loop):
        fw = self.fresh_wire(loop)
        w = fw[0] if fw else None
        if w is None:
            return None
        mf = BRepBuilderAPI_MakeFace(surf, w, True)
        if not mf.IsDone():
            return None
        sf = ShapeFix_Face(mf.Face())
        sf.Perform()
        face = sf.Face()
        if not BRepCheck_Analyzer(face).IsValid():
            fx = ShapeFix_Shape(face); fx.Perform()
            face = _cast("Face", fx.Shape()) if fx.Shape().ShapeType().name.endswith("FACE") else face
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
            self._rev_reason = "bbox"
            return float("inf")
        if not BRepCheck_Analyzer(face).IsValid():
            self._rev_reason = "invalid"       # (meshing a broken face can take very long)
            return float("inf")
        pts = _face_samples(face, 26)
        if len(pts) < 8:
            self._rev_reason = "nomesh"
            return float("inf")
        _, d, _ = trimesh.proximity.closest_point(self.prox, pts)
        sag = float(self.ctx.face_sag[fids].max()) if len(fids) else 0.0
        excess = d.max() - 1.5 * sag
        area_ratio = _exact_area(face) / max(self.ctx.area[fids].sum(), 1e-12)
        if area_ratio > 1.15 or area_ratio < 0.85:
            # doesn't cover its patch (same rule the final face check applies): better to
            # find out now, while the patch can still be split and refitted
            self._rev_reason = f"area {area_ratio:.2f}"
            return float("inf")
        return max(0.0, excess)

    def facet_faces(self, fids):
        """Exact triangle faces for a set of mesh faces (shared edges inside the set)."""
        return triangle_faces(self.ctx, fids, min_height=0.5 * max(2 * self.tol, 1e-4))

    def _fix(self, face):
        sf = ShapeFix_Face(face)
        sf.Perform()
        return sf.Face()


_DEBUG = {}


def _face_samples(face, n=24):
    """Points spread over a face: a grid over its parameter range, kept where it falls
    inside the face's outline (drawn in parameter space), evaluated on the surface.
    (Used instead of the mesher, which can run for a very long time on a bad trimmed face.)"""
    from OCP.BRepTools import BRepTools, BRepTools_WireExplorer
    from OCP.BRepAdaptor import BRepAdaptor_Curve2d
    from OCP.TopAbs import TopAbs_WIRE
    from matplotlib.path import Path
    try:
        u0, u1, v0, v1 = _S(BRepTools, "UVBounds")(face)
        loops = []
        for w in _explore(face, TopAbs_WIRE):
            pts = []
            we = BRepTools_WireExplorer(_cast("Wire", w), face)
            while we.More():
                e = we.Current()
                c2 = BRepAdaptor_Curve2d(e, face)
                ts = np.linspace(c2.FirstParameter(), c2.LastParameter(), 9)
                if e.Orientation().name.endswith("REVERSED"):
                    ts = ts[::-1]
                pts.extend((c2.Value(float(t)).X(), c2.Value(float(t)).Y()) for t in ts)
                we.Next()
            if len(pts) >= 3:
                loops.append(Path(np.array(pts)))
        if not loops:
            return np.zeros((0, 3))
        g = np.stack(np.meshgrid(np.linspace(u0, u1, n), np.linspace(v0, v1, n)), -1).reshape(-1, 2)
        cnt = np.zeros(len(g), int)
        for pth in loops:
            cnt += pth.contains_points(g)
        g = g[cnt % 2 == 1]
        surf = BRepAdaptor_Surface(face)
        return np.array([[q.X(), q.Y(), q.Z()] for q in (surf.Value(float(u), float(v)) for u, v in g)]).reshape(-1, 3)
    except Exception:
        return np.zeros((0, 3))


def _exact_area(face):
    try:
        g = GProp_GProps(); _S(BRepGProp, "SurfaceProperties")(face, g, 1e-3)
        return abs(g.Mass())
    except Exception:
        return 0.0


def _face_area(face):
    c = _repaired_copy(face)
    if c is None:
        return None
    g = GProp_GProps(); _S(BRepGProp, "SurfaceProperties")(c, g, 1e-3)
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
        _S(BRepGProp, "SurfaceProperties")(face, g, 1e-3)
        a = abs(g.Mass())
    except Exception:
        return False
    if mesh_area <= 0:
        return a < 1e-9
    return abs(a - mesh_area) <= rel * mesh_area + 1e-9


def _lscm(V, F):
    """Least-squares conformal map of a triangle patch (local vertex indices) -> (n, 2)."""
    from scipy.sparse.linalg import spsolve
    n = len(V)
    P = V[F]
    e1 = P[:, 1] - P[:, 0]; e2 = P[:, 2] - P[:, 0]
    nrm = np.cross(e1, e2); A2 = np.linalg.norm(nrm, axis=1)
    ok = A2 > 1e-12 * max(float(A2.max()), 1e-300)
    F, e1, e2, nrm, A2 = F[ok], e1[ok], e2[ok], nrm[ok], A2[ok]
    ex = e1 / np.linalg.norm(e1, axis=1, keepdims=True)
    ey = np.cross(nrm / A2[:, None], ex)
    x1 = np.einsum("ij,ij->i", e1, ex); x2 = np.einsum("ij,ij->i", e2, ex); y2 = np.einsum("ij,ij->i", e2, ey)
    z = np.zeros(len(F))
    s = 1.0 / np.sqrt(A2)
    a = np.c_[x2 - x1, z - x2, x1 - z] * s[:, None]
    b = np.c_[y2 - z, z - y2, z - z] * s[:, None]
    m = len(F)
    r1 = np.repeat(np.arange(m), 3); r2 = r1 + m
    rows = np.r_[r1, r1, r2, r2]
    cols = np.r_[F.ravel(), F.ravel() + n, F.ravel() + n, F.ravel()]
    vals = np.r_[a.ravel(), -b.ravel(), a.ravel(), b.ravel()]
    M = coo_matrix((vals, (rows, cols)), shape=(2 * m, 2 * n)).tocsc()
    used = np.unique(F)
    p0 = used[np.argmax(np.linalg.norm(V[used] - V[used].mean(0), axis=1))]
    p1 = used[np.argmax(np.linalg.norm(V[used] - V[p0], axis=1))]
    d = float(np.linalg.norm(V[p1] - V[p0]))
    fixed = np.array([p0, p1, p0 + n, p1 + n]); fv = np.array([0.0, d, 0.0, 0.0])
    free = np.setdiff1d(np.arange(2 * n), fixed)
    Mf = M[:, free]
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")        # a singular system shows up as NaNs, handled below
        sol = spsolve((Mf.T @ Mf).tocsc(), Mf.T @ (-(M[:, fixed] @ fv)))
    X = np.zeros(2 * n); X[fixed] = fv; X[free] = sol
    if not np.all(np.isfinite(X)):
        raise ValueError("lscm failed")
    return np.c_[X[:n], X[n:]]


def _lscm_uv(ctx, faces, verts, B):
    """Conformal parameters for mesh vertices `verts` (and points B lying on the mesh
    near them), flattened over `faces`; axes aligned with the patch's long direction."""
    uvid = np.unique(ctx.F[faces])
    loc = -np.ones(len(ctx.V), np.int64); loc[uvid] = np.arange(len(uvid))
    Fl = loc[ctx.F[faces]]
    uv = _lscm(ctx.V[uvid], Fl)
    sub = trimesh.Trimesh(ctx.V[uvid], Fl, process=False)
    cp, _, tid = trimesh.proximity.closest_point(sub, B)
    bary = trimesh.triangles.points_to_barycentric(sub.triangles[tid], cp)
    Sb = np.einsum("ij,ijk->ik", bary, uv[Fl[tid]])
    S = uv[loc[verts]]
    # rotate to the principal axes (a tensor-product grid fits best along them)
    c = S.mean(0)
    _, _, vt = np.linalg.svd(S - c, full_matrices=False)

    def to_uv(X):
        cp, _, tid = trimesh.proximity.closest_point(sub, np.asarray(X, float))
        bary = trimesh.triangles.points_to_barycentric(sub.triangles[tid], cp)
        return (np.einsum("ij,ijk->ik", bary, uv[Fl[tid]]) - c) @ vt.T
    return (S - c) @ vt.T, (Sb - c) @ vt.T, to_uv


def _bspline_design(x, inner, deg=3):
    """Cubic B-spline basis at x for breakpoints `inner` (clamped): (indices, values, n)."""
    from scipy.interpolate import BSpline
    t = np.r_[[inner[0]] * deg, inner, [inner[-1]] * deg]
    B = BSpline.design_matrix(np.clip(x, inner[0], inner[-1]), t, deg).tocsr()
    B.sort_indices()
    n = len(x)
    return B.indices.reshape(n, deg + 1), B.data.reshape(n, deg + 1), len(inner) + deg - 1


def _bend_matrix(inner, deg=3):
    """Second divided differences of the control points at their Greville abscissae,
    with integration weights: sum of squares ~ integral of the squared 2nd derivative.
    Narrow knot spans are stiffer (1/h^4), so refined areas can't ripple between points."""
    from scipy import sparse
    t = np.r_[[inner[0]] * deg, inner, [inner[-1]] * deg]
    n = len(inner) + deg - 1
    g = np.array([t[i + 1:i + deg + 1].mean() for i in range(n)])
    h = np.maximum(np.diff(g), 1e-9)
    rows, cols, vals, w = [], [], [], []
    for i in range(n - 2):
        span = 0.5 * (h[i] + h[i + 1])
        a, c = 1.0 / h[i], 1.0 / h[i + 1]
        rows += [i] * 3; cols += [i, i + 1, i + 2]
        vals += [a / span, -(a + c) / span, c / span]
        w.append(span)
    D = sparse.csr_matrix((vals, (rows, cols)), shape=(max(n - 2, 0), n))
    # weights of each control point's share of the parameter range (for the other direction)
    cw = np.r_[h[0] / 2, 0.5 * (h[:-1] + h[1:]), h[-1] / 2] if n > 1 else np.ones(n)
    return D, np.array(w), cw


def _lsq_knots(P, U, W, ku, kv, smooth=1e-4, lam=None):
    """Weighted least-squares tensor B-spline over breakpoints ku x kv plus a bending
    penalty (sparse, so thousands of control points stay cheap). Returns
    (control points, residuals, lambda used) or None."""
    from scipy import sparse
    from scipy.sparse.linalg import spsolve
    iu, bu, nu = _bspline_design(U[:, 0], ku)
    iv, bv, nv = _bspline_design(U[:, 1], kv)
    n = len(P)
    A = sparse.csr_matrix(((bu[:, :, None] * bv[:, None, :]).ravel(),
                           (np.repeat(np.arange(n), 16), (iu[:, :, None] * nv + iv[:, None, :]).ravel())),
                          shape=(n, nu * nv))
    AtA = (A.T @ A.multiply(W[:, None])).tocsc()
    Du, wu, cu = _bend_matrix(ku); Dv, wv, cv = _bend_matrix(kv)
    Ru = sparse.kron(Du.T @ sparse.diags(wu) @ Du, sparse.diags(cv))
    Rv = sparse.kron(sparse.diags(cu), Dv.T @ sparse.diags(wv) @ Dv)
    R = (Ru + Rv).tocsc()
    if lam is None:
        lam = smooth * AtA.diagonal().sum() / max(R.diagonal().sum(), 1e-12)
    M = (AtA + lam * R + 1e-10 * AtA.diagonal().mean() * sparse.identity(nu * nv)).tocsc()
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        C = np.asarray(spsolve(M, A.T @ (P * W[:, None]))).reshape(nu * nv, 3)
    if not np.all(np.isfinite(C)):
        return None
    return C, np.linalg.norm(A @ C - P, axis=1), lam


def _lsq_adaptive(P, U, W, judge, tol, nu0, nv0, smooth=1e-4, rounds=5, max_spans=40):
    """Fit, then halve the knot spans the misses fall in, and refit; keep the best.
    The bending stiffness is fixed at the first (coarse) fit, so added knots make the
    surface more flexible only where points actually pull on it.
    Returns (control points, u breakpoints, v breakpoints, residuals) or None."""
    ku = np.linspace(0.0, 1.0, max(nu0 - 2, 2)); kv = np.linspace(0.0, 1.0, max(nv0 - 2, 2))
    best, lam = None, None
    for it in range(rounds + 1):
        fit = _lsq_knots(P, U, W, ku, kv, smooth, lam)
        if fit is None:
            break
        C, res, lam = fit
        dev = float(res[judge].max())
        if best is None or dev < best[0]:
            best = (dev, C, ku.copy(), kv.copy(), res)
        if dev <= 0.9 * tol or it == rounds:
            break
        hot = judge & (res > 0.8 * tol)
        su = np.clip(np.searchsorted(ku, U[hot, 0], side="right") - 1, 0, len(ku) - 2)
        sv = np.clip(np.searchsorted(kv, U[hot, 1], side="right") - 1, 0, len(kv) - 2)
        spl_u, spl_v = np.unique(su), np.unique(sv)
        room_u, room_v = max_spans - (len(ku) - 1), max_spans - (len(kv) - 1)
        if len(spl_u) > room_u:
            spl_u = spl_u[np.argsort(-np.bincount(su)[spl_u])][:max(room_u, 0)]
        if len(spl_v) > room_v:
            spl_v = spl_v[np.argsort(-np.bincount(sv)[spl_v])][:max(room_v, 0)]
        if not len(spl_u) and not len(spl_v):
            break
        ku = np.unique(np.r_[ku, 0.5 * (ku[spl_u] + ku[spl_u + 1])])
        kv = np.unique(np.r_[kv, 0.5 * (kv[spl_v] + kv[spl_v + 1])])
    if best is None:
        return None
    return best[1], best[2], best[3], best[4]


def _occ_bspline_surface(C, nu, nv, ku=None, kv_=None, deg=3):
    poles = _ArrPnt2(1, nu, 1, nv)
    for i in range(nu):
        for j in range(nv):
            poles.SetValue(i + 1, j + 1, _gp(C[i * nv + j]))

    def kv(n, given=None):
        inner = np.linspace(0.0, 1.0, n - deg + 1) if given is None else np.asarray(given, float)
        kn = _ArrReal(1, len(inner)); mu = _ArrInt(1, len(inner))
        for i, x in enumerate(inner):
            kn.SetValue(i + 1, float(x))
            mu.SetValue(i + 1, deg + 1 if i in (0, len(inner) - 1) else 1)
        return kn, mu
    uk, um = kv(nu, ku); vk, vm = kv(nv, kv_)
    return Geom_BSplineSurface(poles, uk, vk, um, vm, deg, deg)


def triangle_faces(ctx, fids, min_height=0.0):
    """One exact planar face per triangle, sharing edges. With min_height, needle
    triangles thinner than that are left out: sewing (which runs at a larger tolerance)
    would fold them onto themselves, leaving a face whose orientation can't agree with
    both neighbours. The gap they leave is narrower than the sewing tolerance."""
    V, F = ctx.V, ctx.F
    if min_height > 0:
        tri = V[F[fids]]
        lmax = np.linalg.norm(tri[:, [1, 2, 0]] - tri, axis=2).max(1)
        fids = np.asarray(fids)[2 * ctx.area[fids] >= min_height * np.maximum(lmax, 1e-300)]
    verts = {}
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
                me = BRepBuilderAPI_MakeEdge(vx(key[0]), vx(key[1]))
                if not me.IsDone():
                    ok = False; break
                e = me.Edge(); edges[key] = e
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
def _unify(shape, tol, merge_bsplines=False):
    try:
        u = ShapeUpgrade_UnifySameDomain(shape, True, True, merge_bsplines)
        u.SetLinearTolerance(tol)
        u.SetAngularTolerance(math.radians(0.5))
        u.Build()
        return u.Shape()
    except Exception:
        return shape


def _invalid_face_points(shape):
    """A few points on each face that fails the geometry check after sewing."""
    pts = []
    for f in _explore(shape, TopAbs_FACE):
        if BRepCheck_Analyzer(f).IsValid():
            continue
        tm = shape_to_mesh(_cast("Face", f), 0.5)
        if tm is not None and len(tm.vertices):
            V = np.asarray(tm.vertices)
            pts.extend(V[np.linspace(0, len(V) - 1, min(8, len(V))).astype(int)].tolist())
    return np.array(pts)


def _safe_unify(shape, tol, log):
    """Merge same-surface faces, but never at the cost of a watertight, valid solid."""
    if not _sealed(shape):
        return _unify(shape, tol)          # was already open: merging can't make it worse
    merged = _unify(shape, tol)
    if _sealed(merged):
        return merged
    resewn = _sew([_cast("Face", f) for f in _explore(merged, TopAbs_FACE)], max(tol, 1e-6))
    resewn, _, n_open = _shells_to_solid(resewn, lambda *a: None)
    if n_open == 0 and _sealed(resewn):
        return resewn
    log("  (kept faces unmerged in places so the solid stays watertight)")
    return shape


_quiet_occt()


def shape_to_mesh(shape, defl):
    BRepMesh_IncrementalMesh(shape, defl, False, 0.2, True)
    verts, faces = [], []
    off = 0
    for f in _explore(shape, TopAbs_FACE):
        face = _cast("Face", f)
        loc = TopLoc_Location()
        tri = _S(BRep_Tool, "Triangulation")(face, loc)
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
    """Distance from the mesh to the STEP (mean, max). The STEP side is its tessellation
    plus points sampled straight off every face (the mesher occasionally leaves a face
    out at fine settings, which would otherwise read as a huge error)."""
    tm = shape_to_mesh(shape, max(tol * 0.2, 1e-3))
    if tm is None:
        return float("nan"), float("nan")
    pts, _ = trimesh.sample.sample_surface_even(mesh, 4000, seed=1) if hasattr(trimesh.sample, "sample_surface_even") \
        else trimesh.sample.sample_surface(mesh, 4000)
    pts = np.vstack([pts, mesh.vertices[:: max(1, len(mesh.vertices) // 4000)]])
    _, d, _ = trimesh.proximity.closest_point(tm, pts)
    far = d > 2 * tol
    if far.any():
        # faces the mesher skipped (it refuses deflections finer than their edge tolerance)
        cloud = []
        for f in _explore(shape, TopAbs_FACE):
            face = _cast("Face", f)
            loc = TopLoc_Location()
            tri = _S(BRep_Tool, "Triangulation")(face, loc)
            a_ex = _exact_area(face)
            a_tr = 0.0
            if tri is not None and tri.NbTriangles():
                P = np.array([[tri.Node(i).X(), tri.Node(i).Y(), tri.Node(i).Z()] for i in range(1, tri.NbNodes() + 1)])
                T = np.array([tri.Triangle(i).Get() for i in range(1, tri.NbTriangles() + 1)]) - 1
                a_tr = 0.5 * np.linalg.norm(np.cross(P[T[:, 1]] - P[T[:, 0]], P[T[:, 2]] - P[T[:, 0]]), axis=1).sum()
            if a_tr < 0.98 * a_ex:
                cloud.append(face)
        # measure straight to those faces: project each far point onto the surface and
        # keep it if it lands inside the face
        idx = np.flatnonzero(far)
        for face in cloud:
            surf = _S(BRep_Tool, "Surface")(face)
            cl = BRepClass_FaceClassifier()
            pr = _projector(surf)
            for i in idx:
                pr.Perform(_gp(pts[i]))
                if pr.NbPoints() == 0:
                    continue
                u, v = pr.LowerDistanceParameters()
                cl.Perform(face, gp_Pnt2d(u, v), 1e-7)
                if cl.State() != TopAbs_OUT:
                    d[i] = min(d[i], pr.LowerDistance())
    return float(d.mean()), float(d.max())


# ---------------------------------------------------------------- main entry
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
    ctx = MeshCtx(mesh)
    tol = prm.tol if prm.tol > 0 else max(0.01, 5e-4 * ctx.diag)
    L(f"Part size {ctx.diag:.2f} mm (diagonal), tolerance {tol:.4g} mm")
    if prm.crease_angle == Params.crease_angle:
        # sharp-edge threshold from the mesh itself: on dense smooth meshes (scans, marching
        # cubes) real edges get rounded over 2–3 triangles and never reach 30°; on coarse CAD
        # meshes round strips can be 20°+, so there it stays high
        a = ctx.adj_angles(); a = a[a < 30]
        if len(a) > 50:
            prm.crease_angle = float(min(30.0, max(10.0, 3 * np.percentile(a, 90))))
            if prm.crease_angle < 29.5:
                L(f"Sharp-edge angle set to {prm.crease_angle:.0f}° for this mesh")

    hints = None
    if prm.use_labels and prm.mode != "faceted" and True:
        hints = hardpoints.load(in_path, prm.scale, ctx, tol, L)
    if hints is not None:
        # hardpoint STL: the file says which triangles form which face (each one is still
        # checked against the tolerance before it is believed)
        label, planes = hints["label"], hints["planes"]
        ctx.hints, ctx.hard_verts = hints, hints["hard"]
    else:
        label, planes = find_planes(ctx, prm, tol, L)
    snap_to_planes(ctx, label, planes, tol)
    if prm.decimate and ctx.nF > prm.decimate:
        small = simplify_keep_features(ctx, label, planes, prm, tol, prm.decimate, L)
        if small is not None:
            if hints is not None:
                L("  (the mesh was reduced, so the file's face labels can't be used — detecting faces)")
            ctx = MeshCtx(small)
            label, planes = find_planes(ctx, prm, tol, L)
            snap_to_planes(ctx, label, planes, tol)
    L.p(0.12)

    if prm.mode == "faceted":
        shape = _build_faceted(ctx, label, planes, tol, L, rep)
    else:
        shape = _build_surface(ctx, label, planes, tol, prm, L, rep)

    L("Writing STEP…")
    write_step(shape, out_path)
    L.p(0.93)
    if prm.verify:
        L("Verifying (re-reading STEP, measuring deviation)…")
    back = read_step(out_path)          # report on what was actually written
    shape = back
    rep.is_solid = any(True for _ in _explore(shape, TopAbs_SOLID)) and _sealed(shape)
    if prm.mode != "faceted" and mesh.is_watertight and not rep.is_solid:
        # last line of defence: never hand back a broken shell for a closed mesh
        L("The surface result isn't a closed solid — writing the exact faceted solid instead")
        shape = _build_faceted(ctx, label, planes, tol, L, rep)
        write_step(shape, out_path)
        shape = read_step(out_path)
        rep.is_solid = any(True for _ in _explore(shape, TopAbs_SOLID)) and _sealed(shape)
        ctx.final_regions = None
    rep.n_faces = _count_faces(shape)
    rep.is_valid = BRepCheck_Analyzer(shape).IsValid()
    if prm.verify:
        if rep.is_solid:
            rep.step_volume = _volume(back)
        orig = trimesh.Trimesh(np.array(mesh.vertices), np.array(mesh.faces), process=False)
        rep.mean_dev, rep.max_dev = measure_deviation(orig, back, tol)
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


def _build_faceted(ctx, label, planes, tol, L, rep):
    L("Building exact faceted solid…")
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


def _labelled_prim(ctx, ids, kind, tol):
    """The file's label says this face is a cylinder / sphere, but the usual sanity rules
    turned it down (they can't tell a shallow round from a flat strip; a CAD label can).
    Fit it anyway and keep it only if every corner is within tolerance."""
    pr = _refit_prim(kind, ctx, ids)
    if pr is None:
        return None
    pts = ctx.V[np.unique(ctx.F[ids])]
    return pr if float(np.max(_prim_residual_vec(pr, pts))) <= tol else None


def _build_surface(ctx, label, planes, tol, prm, L, rep):
    graph = _Graph(ctx)
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
    max_area = (prm.patch_size * ctx.diag) ** 2
    n_prim = 0
    next_comp = len(comps)

    def add_prim(ids, prim, comp_id):
        flip = _prim_geom(prim)[1]
        for p in layout_patches(ctx, graph, ids, float("inf"), 100.0):
            # each patch gets its own surface object: a shared Geom handle would make
            # OCC treat the edge between two patches as a seam
            regions.append({"type": "prim", "faces": p, "surf_geom": _prim_geom(prim, pts=ctx.V[np.unique(ctx.F[p])])[0],
                            "flip": flip, "prim": prim, "comp": comp_id})

    n_split = 0
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
        if prm.fit_primitives and len(ids) >= 12 and kind != "free":
            parts, rest = best_primitive_split(ctx, ids, tol, prm)
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
    if n_prim:
        L(f"{n_prim} cylinder/sphere surface(s) recognised"
          + (f" ({n_split} blended area(s) split into separate round shapes)" if n_split else ""))
    L.p(0.2)

    _merge_crumbs(ctx, regions)
    n_snap = _snap_analytic_boundaries(ctx, regions, tol)
    if n_snap:
        L(f"  {n_snap} edge point(s) moved exactly onto the flat/round faces they separate")
    prox = trimesh.Trimesh(ctx.V.copy(), ctx.F.copy(), process=False)
    ctx.compute_sag()
    cache, whys = {}, {}
    rnd = 0
    while True:
        refining = rnd < prm.refine_rounds
        lab = -np.ones(ctx.nF, dtype=np.int64)
        for ri, r in enumerate(regions):
            lab[r["faces"]] = ri
        chains, _ = build_chains(ctx, lab)
        _smooth_soft_chains(ctx, chains, regions, prox)
        B = Builder(ctx, lab, regions, chains, tol, prm, L)
        B.prox = prox
        n_nurbs = sum(r["type"] == "nurbs" for r in regions)
        L(f"Pass {rnd + 1}: {len(regions)} regions, {n_nurbs} NURBS patches, "
          f"{len(chains)} boundary curves ({B.kinds['line']} lines, {B.kinds['arc']} arcs, "
          f"{B.kinds['bspline']} splines)")
        bad, results, keys = [], {}, {}
        limit = tol if refining else 1.5 * tol
        for ri, r in enumerate(regions):
            if r["type"] == "nurbs":
                key = (r["faces"].tobytes(),
                       tuple((tuple(chains[ci]["verts"]), chains[ci]["kind"], "pts" in chains[ci])
                             for lp in B.region_loops.get(ri, []) for ci, _ in lp))
                keys[ri] = key
                if key not in cache:
                    cache[key] = B.nurbs_face(ri, r)
                    whys[key] = B.why.get(ri)
                face, dev = cache[key]
                results[ri] = (face, dev)
                if face is None or dev > limit:
                    bad.append(ri)
            L.p(0.2 + 0.55 * min(1.0, (rnd + (ri + 1) / len(regions)) / (prm.refine_rounds + 2)))
        _DEBUG.setdefault("why", []).append({ri: B.why.get(ri) for ri in bad})
        if not bad or rnd >= prm.refine_rounds + 50:
            break  # (settling always converges: every pass turns >=1 patch into facets)
        badset = set(bad)
        new_regions = []
        if refining:
            # split patches that missed tolerance
            n_split = 0
            for ri, r in enumerate(regions):
                if ri in badset and len(r["faces"]) >= 8:
                    # split where the fit misses (typically a tight rounded edge running
                    # through the patch): the smooth sides then fit, the tight band gets its
                    # own narrow patch. Otherwise just halve it.
                    parts = _split_by_residual(ctx, graph, r["faces"], whys.get(keys.get(ri)), tol) \
                        or _split(ctx, graph, r["faces"], 2)
                    for p in parts:
                        for q in layout_patches(ctx, graph, p, max_area, prm.max_bend):
                            new_regions.append({"type": "nurbs", "faces": q, "comp": r["comp"]})
                    n_split += 1
                elif ri in badset:
                    new_regions.append({**r, "type": "facet"})
                else:
                    new_regions.append(r)
            _merge_crumbs(ctx, new_regions)
            L(f"  {len(bad)} patch(es) outside tolerance — refining")
        else:
            # give up on these: keep them as exact facets. Their borders go back to the
            # raw mesh edges, so neighbours get rebuilt in the next pass to match.
            # A big patch that only misses along a band keeps the rest as a surface: just
            # the band becomes facets (once - if the rest misses again, it goes too).
            n_part = 0
            for ri, r in enumerate(regions):
                if ri not in badset:
                    new_regions.append(r); continue
                parts = None
                if not r.get("settled") and len(r["faces"]) >= 30:
                    parts = _split_by_residual(ctx, graph, r["faces"], whys.get(keys.get(ri)), tol,
                                               with_band=True)
                if parts and any(not b for _, b in parts):
                    for p, band in parts:
                        for q in ([p] if band else layout_patches(ctx, graph, p, max_area, prm.max_bend)):
                            new_regions.append({**r, "faces": q, "type": "facet" if band else "nurbs",
                                                "settled": True})
                    n_part += 1
                else:
                    new_regions.append({**r, "type": "facet"})
            L(f"  {len(bad)} patch(es) will be kept as facets" + (f" ({n_part} only along a tight band)" if n_part else "")
              + " — re-fitting neighbours")
        regions = new_regions
        rnd += 1

    def prepare(regions):
        lab = -np.ones(ctx.nF, dtype=np.int64)
        for ri, r in enumerate(regions):
            lab[r["faces"]] = ri
        chains, _ = build_chains(ctx, lab)
        _smooth_soft_chains(ctx, chains, regions, prox)
        B = Builder(ctx, lab, regions, chains, tol, prm, L)
        B.prox = prox
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

    def build_all(regions, B, results):
        """Build every face and check it: valid geometry, and an area that matches the
        mesh it replaces (catches e.g. a whole ball built where a corner patch belongs)."""
        faces, failed, kinds = {}, [], {}
        for ri, r in enumerate(regions):
            if r["type"] == "facet":
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
                if not _face_ok(f, mesh_a):
                    f2, dev = _try2(B.nurbs_face, ri, r)
                    f = f2 if (f2 is not None and dev <= 2 * tol) else None
                    kinds[ri] = "nurbs"
            else:
                f, r["_dev"] = results.get(ri, (None, float("inf")))
                r["_method"] = B.method.get(ri)
                kinds[ri] = "nurbs"
                if r["_dev"] > 1.5 * tol:     # refitted after a neighbour changed, and missed
                    f = None
            f = _settle(f)
            if _face_ok(f, mesh_a) and kinds.get(ri) != "nurbs" and not _near_mesh(f, r["faces"]):
                f = None          # right size, wrong place: it bulges away from the mesh
            if _face_ok(f, mesh_a):
                faces[ri] = f
            else:
                failed.append(ri)
                _DEBUG.setdefault("failed", []).append((ri, r["type"], kinds.get(ri), mesh_a, _face_area(f),
                                                        ctx.C[r["faces"]].mean(0).round(1).tolist(),
                                                        (r.get("prim") or ("",))[0]))
        return faces, failed, kinds

    def _near_mesh(face, fids):
        """Every part of the face must lie on the mesh, allowing for the mesh's chord sag."""
        face = _repaired_copy(face)       # meshing an unrepaired face (e.g. a pole with no
        if face is None:                  # seam yet) can run almost forever
            return False
        V = _face_samples(face, 24)
        if not len(V):
            return False
        _, d, _ = trimesh.proximity.closest_point(prox, V)
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
        shape = _sew(list(faces.values()) + facet_faces, max(2 * tol, 1e-4))
        shape, ns, no = _shells_to_solid(shape, lambda *a: None)
        fix = ShapeFix_Shape(shape); fix.Perform(); shape = fix.Shape()
        counts = dict(plane=sum(1 for i in faces if kinds.get(i) == "plane"),
                      prim=sum(1 for i in faces if kinds.get(i) == "prim"),
                      nurbs=sum(1 for i in faces if kinds.get(i) == "nurbs"),
                      facet=sum(1 for i, r in enumerate(regions) if r["type"] == "facet" or i not in faces))
        return shape, counts, regions

    _DEBUG.update(regions=regions, B=B, results=results, ctx=ctx)
    L("Building faces…")
    shape, counts, regions = assemble(regions, B, results)
    # final check on the assembled solid: every curved face must still lie on the mesh
    # (joining the faces can move edges; a face that ended up off the mesh is replaced)
    for _ in range(4):
        off = _off_mesh_regions(shape, ctx, regions, prox, tol)
        if not off:
            break
        L(f"  {len(off)} face(s) ended up off the mesh after joining — keeping them as exact facets")
        regions = [{**r, "type": "facet"} if i in off else r for i, r in enumerate(regions)]
        B, results = prepare(regions)
        shape, counts, regions = assemble(regions, B, results)
    L.p(0.82)
    # targeted repair: regions around open edges become exact facets, then sew again
    from scipy.spatial import cKDTree
    tree = None
    for attempt in range(5):
        if _sealed(shape):
            break
        pts = _free_edge_points(shape)
        bad = _invalid_face_points(shape)
        if len(bad):
            pts = np.vstack([pts, bad]) if len(pts) else bad
        if not len(pts):
            break
        if tree is None:
            tree = cKDTree(ctx.C)
        lab = -np.ones(ctx.nF, dtype=np.int64)
        for ri, r in enumerate(regions):
            lab[r["faces"]] = ri
        near = set()
        if attempt < 2:
            # first only the region actually under each problem spot
            _, idx = tree.query(pts, k=4)
            for row in np.atleast_2d(idx):
                for i in row:
                    if lab[i] >= 0 and regions[lab[i]]["type"] in ("nurbs", "prim"):
                        near.add(int(lab[i])); break
        if not near:
            for idx in tree.query_ball_point(pts, r=max(3 * tol, 0.02 * ctx.diag)):
                near.update(int(lab[i]) for i in idx if lab[i] >= 0 and regions[lab[i]]["type"] in ("nurbs", "prim"))
        if not near:
            break
        L(f"  {len(near)} region(s) didn't sew cleanly (open edges or invalid faces) — rebuilding them as exact facets")
        regions = [{**r, "type": "facet"} if i in near else r for i, r in enumerate(regions)]
        B, results = prepare(regions)
        shape, counts, regions = assemble(regions, B, results)
    if not _sealed(shape):
        # something still doesn't close up: keep flat and round faces, make the rest facets
        nf, bad, nsol = _seal_report(shape)
        _DEBUG["seal_shape"] = shape
        L(f"  The fitted surfaces didn't close into a clean solid ({nf} open edges, invalid faces {bad or 'none'}) "
          "— rebuilding curved areas as exact facets")
        regions = [{**r, "type": "facet"} if r["type"] == "nurbs" else r for r in regions]
        B, results = prepare(regions)
        shape, counts, regions = assemble(regions, B, results)
    if not _sealed(shape):
        nf, bad, nsol = _seal_report(shape)
        L(f"  Still not closed ({nf} open edges, invalid faces {bad or 'none'}) — keeping only flat faces as surfaces")
        regions = [{**r, "type": "facet"} if r["type"] != "plane" else r for r in regions]
        B, results = prepare(regions)
        shape, counts, regions = assemble(regions, B, results)
    if not _sealed(shape):
        L("  Falling back to the exact faceted solid")
        return _build_faceted(ctx, label, planes, tol, L, rep)
    if counts["facet"]:
        L(f"  {counts['facet']} region(s) kept as exact facets")
    _DEBUG.update(final_regions=regions, final_B=B)
    ctx.final_regions = regions
    shape = _safe_unify(shape, max(1e-6, tol * 0.05), L)
    # what counts is the STEP file: save it and read it back here. A face that only held
    # together in memory (the reader recomputes some curves) becomes exact facets.
    from scipy.spatial import cKDTree
    tree = cKDTree(ctx.C)
    for attempt in range(3):
        back = _step_roundtrip(shape)
        if back is not None and _sealed(back):
            break
        if back is None:
            break
        pts = _free_edge_points(back)
        badp = _invalid_face_points(back)
        if len(badp):
            pts = np.vstack([pts, badp]) if len(pts) else badp
        if not len(pts):
            break
        lab = -np.ones(ctx.nF, dtype=np.int64)
        for ri, r in enumerate(regions):
            lab[r["faces"]] = ri
        near = set()
        for idx in tree.query_ball_point(pts, r=max(2 * tol, 0.005 * ctx.diag)):
            near.update(int(lab[i]) for i in idx if lab[i] >= 0 and regions[lab[i]]["type"] in ("nurbs", "prim", "plane"))
        if not near:
            break
        L(f"  {len(near)} face(s) didn't come back cleanly from the saved STEP — keeping them as exact facets")
        regions = [{**r, "type": "facet"} if i in near else r for i, r in enumerate(regions)]
        B, results = prepare(regions)
        shape, counts, regions = assemble(regions, B, results)
        shape = _safe_unify(shape, max(1e-6, tol * 0.05), L)
    rep.n_planes, rep.n_primitives, rep.n_nurbs, rep.n_faceted_regions = \
        counts["plane"], counts["prim"], counts["nurbs"], counts["facet"]
    L.p(0.9)
    return shape


def _step_roundtrip(shape):
    """Write the shape to a temporary STEP file and read it back (None if that fails)."""
    import os
    import tempfile
    fd, tmp = tempfile.mkstemp(suffix=".step")
    os.close(fd)
    try:
        write_step(shape, tmp)
        return read_step(tmp)
    except Exception:
        return None
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def _snap_analytic_boundaries(ctx, regions, tol):
    """Mesh points on the border between flat and round faces (plane, cylinder, sphere)
    are moved onto both surfaces at once (their intersection or line of contact), so the
    edge between the faces lies on both exactly instead of within the fit tolerance.
    Returns how many points moved."""
    def proj(r, P):
        if r["type"] == "plane":
            o, n = r["plane"]
            return P - np.outer((P - o) @ n, n)
        return _project_to_prim(r["prim"], P)
    lab = -np.ones(ctx.nF, dtype=np.int64)
    for ri, r in enumerate(regions):
        lab[r["faces"]] = ri
    analytic = np.array([r["type"] in ("plane", "prim") for r in regions] + [False])
    moved = 0
    groups = {}
    for vi, fl in enumerate(ctx.vert_faces):
        rs = {int(lab[f]) for f in fl}
        if len(rs) < 2 or any(x < 0 or not analytic[x] for x in rs):
            continue
        if all(regions[x]["type"] == "plane" for x in rs):
            continue            # plane corners were already placed exactly (snap_to_planes)
        groups.setdefault(tuple(sorted(rs)), []).append(vi)
    for rs, vs in groups.items():
        vs = np.array(vs)
        P0 = ctx.V[vs].copy(); P = P0.copy()
        for _ in range(30):
            Q = P.copy()
            for x in rs:
                P = proj(regions[x], P)
            if np.abs(P - Q).max() < 1e-7:
                break
        # accept only points that ended up on every surface and didn't travel far
        err = np.max([np.linalg.norm(proj(regions[x], P) - P, axis=1) for x in rs], axis=0)
        ok = (np.linalg.norm(P - P0, axis=1) <= 2 * tol) & (err <= 0.01 * tol)
        ctx.V[vs[ok]] = P[ok]
        moved += int(ok.sum())
    if moved:
        ctx.refresh_geometry()
    return moved


def _off_mesh_regions(shape, ctx, regions, prox, tol):
    """Regions whose final (sewn) curved face strays from the mesh, or can't be meshed."""
    lab = -np.ones(ctx.nF, dtype=np.int64)
    for ri, r in enumerate(regions):
        lab[r["faces"]] = ri
    pts, owner, missing = [], [], []
    small_flat = 4.0 * float(np.max(ctx.area)) if ctx.nF else 0.0
    loose = set()
    for fi, f in enumerate(_explore(shape, TopAbs_FACE)):
        face = _cast("Face", f)
        if BRepAdaptor_Surface(face).GetType().name.endswith("Plane"):
            if _exact_area(face) < small_flat:
                continue            # a mesh triangle kept as it is: exact by definition
            smp = _face_samples(face, 10)
        else:
            smp = _face_samples(face, 20)
        if not len(smp):
            missing.append(face)
            continue
        etol = max((_S(BRep_Tool, "Tolerance")(_cast("Edge", e)) for e in _explore(face, TopAbs_EDGE)), default=0.0)
        if etol > 6 * tol:
            loose.add(fi)      # edges far from the surface: sloppy geometry other CAD may reject
        pts.extend(smp.tolist()); owner.extend([fi] * len(smp))
    bad = set()
    if pts:
        pts = np.array(pts); owner = np.array(owner)
        _, d, tid = trimesh.proximity.closest_point(prox, pts)
        allow = 2 * tol + np.minimum(1.5 * ctx.face_sag[tid], tol)     # chord sag, but within reason
        over = (d > allow) | np.isin(owner, list(loose))
        for fi in np.unique(owner[over]):
            sel = owner == fi
            # the region under the face: the label most of its points land on
            labs = lab[tid[sel]]
            labs = labs[labs >= 0]
            if len(labs):
                ri = int(np.bincount(labs).argmax())
                if regions[ri]["type"] in ("nurbs", "prim", "plane"):
                    bad.add(ri)
                    _DEBUG.setdefault("offmesh", []).append(
                        (ri, regions[ri]["type"], regions[ri].get("_method"), regions[ri].get("_dev"),
                         float(d[sel].max()), float(over[sel].mean()), len(regions[ri]["faces"])))
    for face in missing:     # couldn't even be meshed: find its region by its middle
        try:
            g = GProp_GProps(); _S(BRepGProp, "SurfaceProperties")(face, g, 1e-3)
            c = g.CentreOfMass()
            _, _, tid = trimesh.proximity.closest_point(prox, np.array([[c.X(), c.Y(), c.Z()]]))
            ri = int(lab[tid[0]])
            if ri >= 0 and regions[ri]["type"] in ("nurbs", "prim"):
                bad.add(ri)
        except Exception:
            pass
    return bad


def _smooth_soft_chains(ctx, chains, regions, prox, iters=24):
    """Straighten patch-to-patch boundaries (they are arbitrary cuts through a smooth
    surface, so their zig-zag along mesh edges is noise). Real features — creases,
    plane edges, primitive edges — are left untouched."""
    soft = []
    for ch in chains:
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
