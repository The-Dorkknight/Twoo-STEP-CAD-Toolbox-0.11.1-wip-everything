# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""
profile2d — STL -> 2D outline -> extruded or lofted solid -> STEP.

A second way to make a solid, for parts that are really a 2D profile pushed through a
height (brackets, plates, gaskets, logos) or a profile that changes along one axis
(tapered / stepped parts). Instead of rebuilding surfaces from the triangles, the part is
*redrawn*: trace its outline, clean it up, extrude (or loft) it with the CAD kernel.
Every face is an ideal plane / ruled surface; round holes become true circles.

  extrude : outline of the whole part seen along an axis (silhouette), or an exact
            cross-section at one height (slice), extruded over the part's full height
            (or a height you choose)
  loft    : a few cross-sections, picked where the profile actually changes shape,
            lofted into one smooth solid

Idea and loft slice picking adapted from the earlier stand-alone "stl2step" outline tool.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

import numpy as np
import trimesh

from OCP.gp import gp_Pnt, gp_Dir, gp_Ax2, gp_Circ, gp_Vec, gp_Pln, gp_Elips
from OCP.BRepBuilderAPI import (BRepBuilderAPI_MakePolygon, BRepBuilderAPI_MakeFace,
                                BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeWire)
from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
from OCP.BRepOffsetAPI import BRepOffsetAPI_ThruSections
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRep import BRep_Builder
from OCP.TopoDS import TopoDS_Compound
from OCP.ShapeFix import ShapeFix_Face, ShapeFix_Shape
from OCP.BRepCheck import BRepCheck_Analyzer

from tostep.plugins.mesh2step import classic as core

AXES = {"x": (0, 1, 2), "y": (1, 2, 0), "z": (2, 0, 1)}   # (dropped axis, u, v)


@dataclass
class Outline:
    """One island of a traced profile: outer loop plus holes, (N, 2) in mm."""
    outer: np.ndarray
    holes: list = field(default_factory=list)


@dataclass
class ProfileParams:
    mode: str = "extrude"          # "extrude" or "loft"
    source: str = "silhouette"     # extrude: "silhouette" or "slice"
    axis: str = "z"
    slice_at: float | None = None  # mm along the axis (None = middle)
    simplify: float = 0.0          # mm, 0 = auto (0.2 % of part size)
    smooth: bool = False           # extrude: fit loops with lines, arcs, ellipses and splines
    height: float = 0.0            # mm, 0 = the part's own extent along the axis
    symmetric: bool = False        # extrude: `height` is split both ways around the outline's plane
    heights: tuple | None = None   # loft: use these slice heights (mm along the axis) instead of picking
    max_slices: int = 15           # loft
    sensitivity: float = 0.02      # loft: lower = more slices
    scale: float = 1.0             # mesh units -> mm


# ---------------------------------------------------------------- tracing
def _to_polys(geom):
    from shapely.geometry import Polygon, MultiPolygon
    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom]
    if isinstance(geom, MultiPolygon):
        return list(geom.geoms)
    return [g for g in getattr(geom, "geoms", []) if isinstance(g, Polygon)]


def silhouette(mesh, axis):
    """Exact 2D shadow of the mesh along `axis` (union of all projected triangles)."""
    import shapely
    drop, u, v = AXES[axis]
    tri = mesh.vertices[mesh.faces][:, :, [u, v]]
    a = 0.5 * np.abs((tri[:, 1, 0] - tri[:, 0, 0]) * (tri[:, 2, 1] - tri[:, 0, 1])
                     - (tri[:, 2, 0] - tri[:, 0, 0]) * (tri[:, 1, 1] - tri[:, 0, 1]))
    tri = tri[a > 1e-12 * max(float(a.max()), 1e-300)]
    polys = shapely.make_valid(shapely.polygons(np.concatenate([tri, tri[:, :1]], axis=1)))
    return shapely.union_all(polys).buffer(0)


def section(mesh, axis, h):
    """Exact cross-section polygons (shapely, in the u/v plane) at height h along axis."""
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    drop, u, v = AXES[axis]
    normal = np.zeros(3); normal[drop] = 1.0
    origin = np.zeros(3); origin[drop] = h
    sec = mesh.section(plane_origin=origin, plane_normal=normal)
    if sec is None:
        return None
    # build polygons straight from the section loops in the fixed (u, v) frame
    loops = [np.asarray(d)[:, [u, v]] for d in sec.discrete if len(d) >= 4]
    rings = [Polygon(l).buffer(0) for l in loops]
    rings = [r for r in rings if not r.is_empty]
    if not rings:
        return None
    # even-odd: nested loops alternate between material and hole
    out = None
    for r in sorted(rings, key=lambda r: -r.area):
        out = r if out is None else out.symmetric_difference(r)
    return unary_union(out)


def outlines_from(geom, tol, min_area=0.0):
    """Simplified islands (largest first)."""
    polys = [p.simplify(tol, preserve_topology=True) if tol > 0 else p for p in _to_polys(geom)]
    polys = [p for p in polys if p.area > min_area]
    polys.sort(key=lambda p: -p.area)
    out = []
    for p in polys:
        holes = [np.array(h.coords)[:-1] for h in p.interiors
                 if abs(_area(np.array(h.coords))) > min_area]
        out.append(Outline(np.array(p.exterior.coords)[:-1], holes))
    return out


def _area(pts):
    x, y = pts[:, 0], pts[:, 1]
    return 0.5 * float(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y))


# ---------------------------------------------------------------- loft slice picking
def pick_heights(mesh, axis, max_slices=15, sensitivity=0.02, samples=200, margin=0.01):
    """A few heights where the cross-section actually changes: sample the section area
    along the axis, simplify the (height, area) curve, keep its corners."""
    from shapely.geometry import LineString
    drop = AXES[axis][0]
    lo, hi = mesh.bounds[:, drop]
    span = hi - lo
    if span <= 0:
        raise ValueError("The mesh has no extent along that axis")
    lo, hi = lo + margin * span, hi - margin * span
    hs = np.linspace(lo, hi, samples)
    normal = np.zeros(3); normal[drop] = 1.0
    secs = mesh.section_multiplane(plane_origin=np.zeros(3), plane_normal=normal, heights=hs)
    H, A = [], []
    for h, s in zip(hs, secs):
        if s is None:
            continue
        try:
            a = sum(p.area for p in s.polygons_full)
        except Exception:
            continue
        if a > 0:
            H.append(h); A.append(a)
    if len(H) < 3:
        return np.array([lo, hi])
    H, A = np.array(H), np.array(A)
    aspan = A.max() - A.min()
    if aspan <= 1e-9 * max(A.max(), 1e-300):
        return np.array([H[0], H[-1]])          # constant section: a straight loft
    nh = (H - H[0]) / (H[-1] - H[0]) * aspan
    keep = np.array(LineString(np.c_[nh, A]).simplify(aspan * sensitivity).coords)[:, 0]
    key = np.unique(np.interp(keep, nh, H))
    if len(key) > max_slices:
        key = key[np.unique(np.linspace(0, len(key) - 1, max_slices).round().astype(int))]
    return np.sort(key) if len(key) >= 2 else np.array([H[0], H[-1]])


# ---------------------------------------------------------------- solids
def _p3(axis, uv, h):
    drop, u, v = AXES[axis]
    p = [0.0, 0.0, 0.0]
    p[drop], p[u], p[v] = h, float(uv[0]), float(uv[1])
    return gp_Pnt(*p)


def _circle_fit(pts):
    A = np.c_[2 * pts, np.ones(len(pts))]
    sol, *_ = np.linalg.lstsq(A, (pts ** 2).sum(1), rcond=None)
    c = sol[:2]; r = math.sqrt(max(sol[2] + c @ c, 0.0))
    return c, r, float(np.abs(np.linalg.norm(pts - c, axis=1) - r).max())


def _round_fit(pts, tol, closed=True):
    """(centre, radius) if the points are a tessellated circle (or arc when not closed), else
    None. Checks the corners AND the middle of every chord: a D-shaped hole or an octagon has
    all its corners on a circle too, but its straight sides cut far inside it."""
    pts = np.asarray(pts, float)
    if len(pts) < (8 if closed else 4):
        return None
    c, r, dev = _circle_fit(pts)
    if not (r > 0 and dev <= max(tol, 1e-3) and dev <= 0.02 * r):
        return None
    nxt = np.roll(pts, -1, axis=0) if closed else pts[1:]
    cur = pts if closed else pts[:-1]
    gap = np.abs(np.linalg.norm(0.5 * (cur + nxt) - c, axis=1) - r)
    if gap.max() > max(tol, 0.05 * r):
        return None
    return c, r


def _wire(axis, pts, h, tol, dense=None):
    """Closed wire for a loop: a true circle when the (unsimplified) loop is round,
    otherwise straight segments through the simplified points."""
    drop = AXES[axis][0]
    ref = dense if dense is not None and len(dense) >= 8 else pts
    rf = _round_fit(ref, tol)
    if rf is not None:
        c, r = rf
        d = [0.0, 0.0, 0.0]; d[drop] = 1.0
        circ = gp_Circ(gp_Ax2(_p3(axis, c, h), gp_Dir(*d)), r)
        return BRepBuilderAPI_MakeWire(BRepBuilderAPI_MakeEdge(circ).Edge()).Wire(), "circle"
    mp = BRepBuilderAPI_MakePolygon()
    for q in pts:
        mp.Add(_p3(axis, q, h))
    mp.Close()
    return mp.Wire(), "poly"


# ---------------------------------------------------------------- smooth sketch
# Turns a traced loop (hundreds of points along the mesh outline) into a clean sketch:
# straight runs become lines, round runs true arcs / circles / ellipses, and anything else
# a spline with as few control points as the tolerance allows.

def _clean_loop(pts, eps=1e-7):
    pts = np.asarray(pts, float)
    keep = [0]
    for i in range(1, len(pts)):
        if np.linalg.norm(pts[i] - pts[keep[-1]]) > eps:
            keep.append(i)
    p = pts[keep]
    if len(p) > 1 and np.linalg.norm(p[0] - p[-1]) <= eps:
        p = p[:-1]
    return p


def _turning(p):
    """Turning angle (degrees, unsigned) at every vertex of a closed polygon, and edge lengths
    (edge i runs from vertex i to i+1)."""
    n = len(p)
    a = p - p[np.arange(n) - 1]
    b = p[(np.arange(n) + 1) % n] - p
    ang = np.degrees(np.abs(np.arctan2(a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0], (a * b).sum(1))))
    return ang, np.linalg.norm(b, axis=1)


def _two_way(sample, ref, closed):
    """(data -> curve, curve -> data) distances: every traced point to the sampled curve,
    and every curve sample to the traced polyline."""
    import shapely
    q = np.vstack([ref, ref[:1]]) if closed else ref
    cur = shapely.linestrings(np.vstack([sample, sample[:1]]) if closed else sample)
    pol = shapely.linestrings(q)
    return (float(shapely.distance(cur, shapely.points(ref)).max()),
            float(shapely.distance(pol, shapely.points(sample)).max()))


def _sag_allow(ref, closed):
    """How far the true curve bulges past the chords of its own tessellation: the traced
    points lie on the curve, the chords between them cut inside it. Estimated by drawing a
    smooth curve through every point and measuring its largest distance from the chords."""
    from scipy.interpolate import CubicSpline
    import shapely
    if len(ref) < 4:
        return 0.0
    q = np.vstack([ref, ref[:1]]) if closed else np.asarray(ref)
    t = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(q, axis=0), axis=1))]
    if np.any(np.diff(t) <= 0):
        return 0.0
    try:
        cs = CubicSpline(t, q, bc_type="periodic" if closed else "natural")
    except Exception:
        return 0.0
    S = cs(np.linspace(0.0, t[-1], max(400, 6 * len(q))))
    return float(shapely.distance(shapely.linestrings(q), shapely.points(S)).max())


def _ellipse_fit(ref):
    """Least-squares ellipse: (centre, semi-major, semi-minor, unit major direction) or None."""
    c0 = ref.mean(0)
    x, y = (ref - c0).T
    sol, *_ = np.linalg.lstsq(np.c_[x * x, x * y, y * y, x, y], np.ones(len(x)), rcond=None)
    a, b, c, d, e = sol
    M = np.array([[a, b / 2], [b / 2, c]])
    if np.linalg.det(M) <= 1e-18:
        return None
    ctr = np.linalg.solve(2 * M, -np.array([d, e]))
    k = 1 - (ctr @ M @ ctr + d * ctr[0] + e * ctr[1])
    w, v = np.linalg.eigh(M)
    if k <= 0 or w.min() <= 0:
        return None
    ax = np.sqrt(k / w)
    i = int(np.argmax(ax))
    return ctr + c0, float(ax[i]), float(ax[1 - i]), v[:, i]


def _bspline_edge(axis, h, tck, periodic, check):
    """An OCC edge from a SciPy spline (tck); None if the conversion doesn't reproduce it."""
    from OCP.Geom import Geom_BSplineCurve
    from OCP.collections import Array1_gp_Pnt, Array1_double, Array1_int
    from scipy.interpolate import splev
    t, c, k = tck
    n = len(c[0])
    if periodic:
        m = n - k
        poles = np.c_[c[0][:m], c[1][:m]]
        knots = np.asarray(t[k:n + 1]); mults = np.ones(len(knots), int)
    else:
        poles = np.c_[c[0], c[1]]
        knots, mults = np.unique(t, return_counts=True)
    if len(poles) < k + 1 or np.any(np.diff(knots) <= 0):
        return None
    ap = Array1_gp_Pnt(1, len(poles))
    for i, q in enumerate(poles):
        ap.SetValue(i + 1, _p3(axis, q, h))
    ak = Array1_double(1, len(knots)); am = Array1_int(1, len(knots))
    for i in range(len(knots)):
        ak.SetValue(i + 1, float(knots[i])); am.SetValue(i + 1, int(mults[i]))
    cur = Geom_BSplineCurve(ap, ak, am, int(k), bool(periodic))
    u, v = AXES[axis][1], AXES[axis][2]
    for x, want in zip(check, np.array(splev(check, tck)).T):
        g = cur.Value(float(x)); g = (g.X(), g.Y(), g.Z())
        if abs(g[u] - want[0]) + abs(g[v] - want[1]) > 1e-6:
            return None
    return BRepBuilderAPI_MakeEdge(cur).Edge()


def _fit_spline(axis, h, run, tol, closed):
    """Smoothest spline (fewest control points) that stays within tol of the traced run.
    Returns (edge, number of control points) or (None, 0)."""
    from scipy.interpolate import splprep, splev
    k = 3
    if len(run) < (k + 2 if not closed else k + 3):
        return None, 0
    allow = tol + 1.2 * _sag_allow(run, closed)
    data = np.vstack([run, run[:1]]) if closed else run
    w = np.ones(len(data))
    if not closed:
        w[0] = w[-1] = 1000.0                      # hold the ends: they are shared with neighbours
    best = None
    for sfac in np.geomspace(1.0, 0.002, 26) * tol * 2.0:
        try:
            tck, _ = splprep(data.T, w=w, per=closed, s=len(data) * sfac ** 2, k=k, quiet=2)
        except Exception:
            continue
        t, c, _k = tck
        if not closed:
            c = [np.array(ci, float) for ci in c]
            c[0][0], c[1][0] = run[0]; c[0][-1], c[1][-1] = run[-1]      # exact ends
            tck = (t, c, _k)
        us = np.linspace(t[k], t[len(c[0])], 1200, endpoint=not closed)
        S = np.array(splev(us, tck)).T
        d1, d2 = _two_way(S, run, closed)
        if d1 <= tol and d2 <= allow:
            best = tck
            break
    if best is None:
        return None, 0
    t, c, _k = best
    npole = len(c[0]) - (k if closed else 0)
    chk = np.linspace(t[k], t[len(c[0])], 9, endpoint=not closed)
    e = _bspline_edge(axis, h, best, closed, chk)
    return (e, npole) if e is not None else (None, 0)


def _straight_stretches(full, eps):
    """Split the closed loop into maximal dead-straight stretches. Returns a list of
    (start index, end index) into `full` (end may wrap past the last point); consecutive
    stretches share their end points. A tessellated curve gives one stretch per chord."""
    n = len(full)
    ang, _ = _turning(full)
    start = int(np.argmax(ang))                 # begin at the sharpest vertex: never mid-line
    out = []
    i = start
    travelled = 0
    while travelled < n:
        j = 1
        while travelled + j < n:
            a, bq = full[i], full[(i + j + 1) % n]
            ch = bq - a; L = float(np.linalg.norm(ch))
            if L < 1e-12:
                break
            mid = full[[(i + m) % n for m in range(1, j + 1)]]
            off = np.abs((mid - a)[:, 0] * ch[1] - (mid - a)[:, 1] * ch[0]) / L
            # also the points must advance along the chord (no doubling back)
            along = ((mid - a) @ ch) / L
            if off.max() > eps or along.min() < -eps or along.max() > L + eps:
                break
            j += 1
        out.append((i, i + j))
        i = (i + j) % n
        travelled += j
    return out


def _bulge(full, s0, e, corner, W=6):
    """If the outline were one smooth curve here, how far would it bow away from the straight
    stretch s0..e? A smooth (natural cubic) curve is drawn through the stretch's two ends and
    a few traced points on each smooth side; the answer is its largest distance from the chord.
    For an ordinary chord of a tessellated curve this is about the mesher's chord error; for a
    real straight line between curves it is far larger (the mesher put no points there because
    none were needed). None when there is too little around the stretch to tell."""
    from scipy.interpolate import CubicSpline
    n = len(full)
    p0, p1 = full[s0 % n], full[e % n]
    ch = p1 - p0; L = float(np.linalg.norm(ch))
    if L < 1e-12:
        return None
    uy = np.array([-ch[1], ch[0]]) / L
    left, right = [], []
    if not corner[s0 % n]:
        i, walked = s0, 0.0
        for _ in range(3 * W):
            i -= 1
            walked += float(np.linalg.norm(full[i % n] - full[(i + 1) % n]))
            left.insert(0, i)
            if corner[i % n] or (len(left) >= W and walked >= 0.25 * L):
                break
    if not corner[e % n]:
        i, walked = e, 0.0
        for _ in range(3 * W):
            i += 1
            walked += float(np.linalg.norm(full[i % n] - full[(i - 1) % n]))
            right.append(i)
            if corner[i % n] or (len(right) >= W and walked >= 0.25 * L):
                break
    idx = left + [s0, e] + right
    if len(idx) < 4 or len(idx) > n:
        return None
    pts = full[[k % n for k in idx]]
    t = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(pts, axis=0), axis=1))]
    if np.any(np.diff(t) <= 0):
        return None
    cs = CubicSpline(t, pts, bc_type="natural")
    a = len(left)
    return float(np.abs((cs(np.linspace(t[a], t[a + 1], 40)) - p0) @ uy).max())


def _corners(full, tol, corner_deg=25.0):
    """Sharp corners of the loop. A vertex counts when the outline turns sharply right at it
    AND still does when looked at from a little further away (half the tolerance along the
    outline each way) — so the jitter of a noisy or scanned mesh isn't taken for corners."""
    n = len(full)
    ang, ln = _turning(full)
    cand = np.flatnonzero(ang > corner_deg)
    corner = np.zeros(n, bool)
    if not len(cand):
        return corner
    reach = max(0.5 * tol, 2.0 * float(np.median(ln)))

    def walk(i, step):
        d, j = 0.0, i
        for _ in range(n // 2):
            k = j + step
            d += float(np.linalg.norm(full[k % n] - full[j % n]))
            j = k
            if d >= reach:
                break
        return full[j % n]

    for i in cand:
        a = full[i] - walk(i, -1); b = walk(i, 1) - full[i]
        wide = np.degrees(abs(np.arctan2(a[0] * b[1] - a[1] * b[0], a @ b)))
        if wide > 0.8 * corner_deg:
            corner[i] = True
    return corner


def _segment_loop(full, tol, corner_deg=25.0, factor=5.0):
    """Break points of a traced loop: corners and the two ends of every real straight line.

    A straight stretch is a real line when it runs between two corners, or when a smooth
    curve through its surroundings would have to bow away from it far more (factor x) than
    any ordinary chord of this loop does — see _bulge.
    Returns (pieces, n_corners) where pieces = [(start, end, is_line)] in loop order, or
    ([], 0) for a loop with no corners and no lines."""
    n = len(full)
    # "dead straight" = within the rounding noise of an STL's 32-bit coordinates
    eps = min(max(2e-5, 5e-7 * float(np.abs(full).max())), tol / 100.0)
    st = _straight_stretches(full, eps)
    corner = _corners(full, tol, corner_deg)
    length = np.array([np.linalg.norm(full[e % n] - full[s0 % n]) for s0, e in st])
    is_line = np.zeros(len(st), bool)
    bulges = np.full(len(st), np.nan)
    for k, (s0, e) in enumerate(st):
        if corner[s0 % n] and corner[e % n]:
            is_line[k] = True                       # an edge between two corners
        else:
            bv = _bulge(full, s0, e, corner)
            if bv is not None:
                bulges[k] = bv
    cand = np.flatnonzero(np.isfinite(bulges))
    if len(cand) >= 3:
        # Ordinary chords all bow by about the mesher's chord error or less; real lines stand
        # far above them. Look for that gap from the top of the sorted list.
        order = cand[np.argsort(bulges[cand])]
        b = bulges[order]
        typical = float(np.median(length[cand]))
        for i in range(len(b) - 2, -1, -1):
            above = len(b) - 1 - i
            if above > 0.4 * len(b):
                break
            if b[i + 1] >= factor * max(b[i], 1e-12) and b[i + 1] >= 20 * eps:
                for k in order[i + 1:]:
                    if length[k] >= 3.0 * typical:
                        is_line[k] = True
                break
    brk = set(i for i in range(n) if corner[i])
    for k, (s0, e) in enumerate(st):
        if is_line[k]:
            brk.add(s0 % n); brk.add(e % n)
    line_starts = {s0 % n: e % n for k, (s0, e) in enumerate(st) if is_line[k]}
    if not brk:
        return [], 0
    order = sorted(brk)
    pieces = []
    for a_i, b0 in enumerate(order):
        b1 = order[(a_i + 1) % len(order)]
        end = b1 if b1 > b0 else b1 + n
        pieces.append((b0, end, line_starts.get(b0) == b1))
    return pieces, int(corner.sum())


def _smooth_wire(axis, dense, h, tol, notes):
    """Closed wire of lines / arcs / circles / ellipses / splines for one traced loop, or None
    (the caller then falls back to straight segments)."""
    from shapely.geometry import LineString
    from OCP.GC import GC_MakeArcOfCircle
    drop = AXES[axis][0]
    d3 = [0.0, 0.0, 0.0]; d3[drop] = 1.0
    full = _clean_loop(dense)
    if len(full) >= 3:
        full = _clean_loop(full, eps=1e-6 * float(np.ptp(full, axis=0).max()))
    if len(full) < 3:
        return None
    n = len(full)
    # exact shapes: the traced points lie on the true curve, its chords cut inside
    rf = _round_fit(full, tol)
    if rf is not None:
        c, r = rf
        notes.append("circle")
        return BRepBuilderAPI_MakeWire(BRepBuilderAPI_MakeEdge(gp_Circ(gp_Ax2(_p3(axis, c, h), gp_Dir(*d3)), r)).Edge()).Wire()
    pieces, _ = _segment_loop(full, tol)
    if not pieces:
        if n >= 8:
            el = _ellipse_fit(full)
            if el is not None:
                ctr, major, minor, dirx = el
                t = np.linspace(0, 2 * np.pi, 1440, endpoint=False)
                perp = np.array([-dirx[1], dirx[0]])
                S = ctr + np.outer(np.cos(t), dirx) * major + np.outer(np.sin(t), perp) * minor
                d1, d2 = _two_way(S, full, True)
                if d1 <= tol and d2 <= tol + 1.2 * _sag_allow(full, True) and minor > 1e-6:
                    p0, p1 = _p3(axis, ctr, h), _p3(axis, ctr + dirx, h)
                    xd = gp_Dir(p1.X() - p0.X(), p1.Y() - p0.Y(), p1.Z() - p0.Z())
                    notes.append("ellipse")
                    return BRepBuilderAPI_MakeWire(BRepBuilderAPI_MakeEdge(gp_Elips(gp_Ax2(p0, gp_Dir(*d3), xd), major, minor)).Edge()).Wire()
        e, npole = _fit_spline(axis, h, full, tol, True)
        if e is None:
            return None
        notes.append(f"closed spline ({npole} control points)")
        return BRepBuilderAPI_MakeWire(e).Wire()
    mw = BRepBuilderAPI_MakeWire()
    kinds = []

    def add_line(p, q):
        mw.Add(BRepBuilderAPI_MakeEdge(_p3(axis, p, h), _p3(axis, q, h)).Edge()); kinds.append("line")

    for s0, e0, line in pieces:
        run = full[[i % n for i in range(s0, e0 + 1)]]
        p0, p1 = run[0], run[-1]
        if line or len(run) == 2:
            add_line(p0, p1)
        else:
            chord = p1 - p0; L = float(np.linalg.norm(chord))
            done = False
            rf = _round_fit(run, tol, closed=False) if L > 1e-9 else None
            if rf is not None and rf[1] < 50 * L:
                c, r = rf
                v = run[len(run) // 2] - c; nv = np.linalg.norm(v)
                if nv > 1e-12:
                    arc = GC_MakeArcOfCircle(_p3(axis, p0, h), _p3(axis, c + v / nv * r, h), _p3(axis, p1, h))
                    if arc.IsDone():
                        mw.Add(BRepBuilderAPI_MakeEdge(arc.Value()).Edge()); kinds.append("arc"); done = True
            if not done:
                e, npole = _fit_spline(axis, h, run, tol, False)
                if e is not None:
                    mw.Add(e); kinds.append(f"spline ({npole} control points)"); done = True
            if not done:
                # couldn't fit this stretch: straight segments, as without smoothing
                simp = np.array(LineString(run).simplify(tol).coords)
                for a, b in zip(simp[:-1], simp[1:]):
                    add_line(a, b)
        if not mw.IsDone():
            return None
    w = mw.Wire()
    if not w.Closed():
        return None
    from collections import Counter
    cnt = Counter(k.split(" (")[0] for k in kinds)
    sp = [k for k in kinds if k.startswith("spline")]
    txt = ", ".join(f"{v} {k}{'s' if v > 1 else ''}" for k, v in cnt.items())
    if sp:
        txt += " (" + ", ".join(x.split("(")[1].rstrip(")") for x in sp[:4]) + (", …" if len(sp) > 4 else "") + ")"
    notes.append(txt)
    return w


def _face(axis, ol, h, tol, dense=None, smooth=False, notes=None):
    drop = AXES[axis][0]
    d = [0.0, 0.0, 0.0]; d[drop] = 1.0
    pln = gp_Pln(_p3(axis, (0, 0), h), gp_Dir(*d))

    def loop_wire(pts, dn, name):
        if smooth and dn is not None:
            got = []
            try:
                w = _smooth_wire(axis, dn, h, tol, got)
            except Exception:
                w = None
            if w is not None:
                if notes is not None:
                    notes.append(f"{name}: {len(_clean_loop(dn))} traced points → {got[-1] if got else 'smoothed'}")
                return w
            if notes is not None:
                notes.append(f"{name}: kept as {len(pts)} straight segments (no smooth fit within tolerance)")
        return _wire(axis, pts, h, tol, dn)[0]

    def dense_hole(i, hole):
        """The unsimplified loop for this hole (matched by position, not just by index)."""
        if not dense or not dense.holes:
            return None
        if not smooth:
            return dense.holes[i] if i < len(dense.holes) else None
        c = np.asarray(hole, float).mean(0)
        j = int(np.argmin([np.linalg.norm(np.asarray(dh, float).mean(0) - c) for dh in dense.holes]))
        span = float(np.ptp(np.asarray(hole, float), axis=0).max())
        return dense.holes[j] if np.linalg.norm(np.asarray(dense.holes[j], float).mean(0) - c) <= 0.25 * span + tol else None

    w = loop_wire(ol.outer, dense.outer if dense else None, "outline")
    mf = BRepBuilderAPI_MakeFace(pln, w, True)
    for i, hole in enumerate(ol.holes):
        hw = loop_wire(hole, dense_hole(i, hole), f"hole {i + 1}")
        mf.Add(hw)
    sf = ShapeFix_Face(mf.Face())        # fixes loop orientation (outer vs holes)
    sf.Perform()
    return sf.Face()


def extrude(outlines, axis, h0, height, tol, dense=None, smooth=False, notes=None):
    drop = AXES[axis][0]
    d = [0.0, 0.0, 0.0]; d[drop] = height
    b = BRep_Builder(); comp = TopoDS_Compound(); b.MakeCompound(comp)
    for i, ol in enumerate(outlines):
        f = _face(axis, ol, h0, tol, dense[i] if dense and i < len(dense) else None, smooth, notes)
        b.Add(comp, BRepPrimAPI_MakePrism(f, gp_Vec(*d)).Shape())
    return comp


def loft(sections, heights, axis, tol, log):
    """sections: per height, a list of islands (largest first). Lofts the largest island;
    holes too when every slice has the same number of them (matched by size)."""
    main = [s[0] for s in sections]
    if any(len(s) > 1 for s in sections):
        log("Note: some slices have several separate islands — only the biggest is lofted")

    def thru(loops):
        ts = BRepOffsetAPI_ThruSections(True, True, 1e-6)
        ts.CheckCompatibility(True)
        for pts, h in loops:
            ts.AddWire(_wire(axis, pts, h, tol)[0])
        ts.Build()
        return ts.Shape()
    solid = thru([(o.outer, h) for o, h in zip(main, heights)])
    counts = {len(o.holes) for o in main}
    if len(counts) == 1 and counts != {0}:
        n = counts.pop()
        for k in range(n):
            loops = []
            for o, h in zip(main, heights):
                hs = sorted(o.holes, key=lambda q: -abs(_area(q)))
                loops.append((hs[k], h))
            cut = BRepAlgoAPI_Cut(solid, thru(loops))
            if cut.IsDone():
                solid = cut.Shape()
    elif any(o.holes for o in main):
        log("Note: the slices have different numbers of holes — holes were left out of the loft")
    return solid


# ---------------------------------------------------------------- entry points
def trace(mesh, pp: ProfileParams, tol, log=lambda s: None):
    """The outlines the conversion would use: (heights, [islands per height], dense)."""
    axis = pp.axis.lower()
    drop = AXES[axis][0]
    lo, hi = mesh.bounds[:, drop]
    if pp.mode == "loft":
        if pp.heights:
            hs = sorted({min(max(float(h), float(lo)), float(hi)) for h in pp.heights})
        else:
            hs = pick_heights(mesh, axis, pp.max_slices, pp.sensitivity)
        secs, keep = [], []
        for h in hs:
            g = section(mesh, axis, float(h))
            ol = outlines_from(g, tol, min_area=tol * tol)
            if ol:
                secs.append(ol); keep.append(float(h))
        if len(secs) < 2:
            raise ValueError("Fewer than 2 usable cross-sections along that axis — loft needs at least 2")
        return keep, secs, None
    if pp.source == "slice":
        h = pp.slice_at if pp.slice_at is not None else 0.5 * (lo + hi)
        g = section(mesh, axis, float(h))
        if g is None:
            raise ValueError(f"No cross-section at {axis} = {h:.3f} mm (the part spans {lo:.3f} … {hi:.3f})")
    else:
        g = silhouette(mesh, axis)
    ol = outlines_from(g, tol, min_area=tol * tol)
    if not ol:
        raise ValueError("No outline could be traced along that axis")
    return [float(lo)], [ol], [outlines_from(g, 0.0, min_area=tol * tol)]


def convert(in_path, out_path, pp: ProfileParams, log=None, progress=None):
    L = core._Log(log, progress)
    rep = core.Report(out_path=str(out_path))
    t0 = time.time()
    m = trimesh.load(in_path, force="mesh", process=True)
    if not isinstance(m, trimesh.Trimesh) or not len(m.faces):
        raise ValueError("No triangles found in the file")
    if pp.scale != 1.0:
        m.apply_scale(pp.scale)
    rep.n_input_tris = len(m.faces)
    if m.is_watertight:
        rep.mesh_volume = float(abs(m.volume))
    axis = pp.axis.lower()
    if axis not in AXES:
        raise ValueError("axis must be x, y or z")
    diag = float(np.linalg.norm(m.extents))
    tol = pp.simplify if pp.simplify > 0 else max(0.01, 2e-3 * diag)
    L(f"Loaded {len(m.faces):,} triangles; part size {diag:.2f} mm; outline tolerance {tol:.3g} mm")
    L.p(0.1)
    drop = AXES[axis][0]
    lo, hi = m.bounds[:, drop]
    heights, secs, dense = trace(m, pp, tol, L)
    L.p(0.5)
    if pp.mode == "loft":
        L(f"Lofting through {len(heights)} cross-sections at {axis} = "
          + ", ".join(f"{h:.2f}" for h in heights))
        shape = loft(secs, heights, axis, tol, L)
    else:
        ol = secs[0]
        height = pp.height if pp.height > 0 else float(hi - lo)
        start = float(lo)
        if pp.symmetric:        # half the height each way around the cross-section (or the part's middle)
            centre = (pp.slice_at if pp.slice_at is not None else 0.5 * (lo + hi)) if pp.source == "slice" \
                else 0.5 * (lo + hi)
            start = float(centre) - height / 2.0
        L(f"{'Silhouette' if pp.source != 'slice' else 'Cross-section'}: {len(ol)} island(s), "
          f"{sum(len(o.holes) for o in ol)} hole(s); extruding {height:.3f} mm along {axis}"
          + (" (half each way)" if pp.symmetric else ""))
        notes = []
        shape = extrude(ol, axis, start, height, tol, dense[0], pp.smooth, notes)
        if pp.smooth:
            fx0 = ShapeFix_Shape(shape); fx0.Perform()
            if not BRepCheck_Analyzer(fx0.Shape()).IsValid():
                L("Smooth sketch gave an invalid solid — using straight segments instead")
                shape = extrude(ol, axis, start, height, tol, dense[0])
            else:
                L("Smooth sketch:")
                for nline in notes[:40]:
                    L("  " + nline)
                if len(notes) > 40:
                    L(f"  … and {len(notes) - 40} more loop(s)")
    fx = ShapeFix_Shape(shape); fx.Perform(); shape = fx.Shape()
    shape = core._unify(shape, 1e-6)
    L.p(0.8)
    L("Writing STEP…")
    core.write_step(shape, out_path)
    back = core.read_step(out_path)
    rep.is_solid = any(True for _ in core._explore(back, core.TopAbs_SOLID))
    rep.is_valid = BRepCheck_Analyzer(back).IsValid()
    rep.n_faces = core._count_faces(back)
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    for f in core._explore(back, core.TopAbs_FACE):
        t = BRepAdaptor_Surface(core._cast("Face", f)).GetType().name
        if t.endswith("Plane"):
            rep.n_planes += 1
        elif t.endswith(("Cylinder", "Sphere", "Cone")):
            rep.n_primitives += 1
        else:
            rep.n_nurbs += 1          # lofted / ruled surfaces
    if rep.is_solid:
        rep.step_volume = core._volume(back)
    rep.mean_dev, rep.max_dev = core.measure_deviation(m, back, tol)
    rep.seconds = time.time() - t0
    rep.ok = True
    L.p(1.0)
    L(rep.summary().replace("Deviation from mesh", "Deviation from mesh (the profile is a redraw, so "
                                                   "parts that aren't a pure profile differ)"))
    return rep


def convert_stl_to_step(stl_path, step_path, axis="z", mode="silhouette", slice_height=None, simplify_tol=0.0,
                        extrude_height=0.0, symmetric=False, max_slices=15, loft_tol_ratio=0.02,
                        manual_heights=None, scale=1.0, log=None):
    """One-call wrapper in the shape of the library function of the first STL2STEP (this app's earlier name) trace-and-extrude
    tool. mode: "silhouette" | "slice" (straight extrude) | "loft". Returns (step_path, report)."""
    if mode not in ("silhouette", "slice", "loft"):
        raise ValueError('mode must be "silhouette", "slice" or "loft"')
    pp = ProfileParams(mode="loft" if mode == "loft" else "extrude", source="slice" if mode == "slice" else "silhouette",
                       axis=axis, slice_at=slice_height, simplify=simplify_tol, height=extrude_height,
                       symmetric=symmetric, max_slices=max_slices, sensitivity=loft_tol_ratio,
                       heights=tuple(manual_heights) if manual_heights else None, scale=scale)
    return str(step_path), convert(stl_path, step_path, pp, log=log)


def _wire_polyline(axis, wire, per_edge=48):
    """Sampled 2D points along a wire, plus the points where one edge ends and the next begins."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.BRepTools import BRepTools_WireExplorer
    u, v = AXES[axis][1], AXES[axis][2]
    pts, joints = [], []
    ex = BRepTools_WireExplorer(wire)
    while ex.More():
        e = ex.Current()
        c = BRepAdaptor_Curve(e)
        ts = np.linspace(c.FirstParameter(), c.LastParameter(), per_edge)
        if e.Orientation().name.endswith("REVERSED"):
            ts = ts[::-1]
        seg = []
        for t in ts:
            p = c.Value(float(t)); q = (p.X(), p.Y(), p.Z()); seg.append((q[u], q[v]))
        joints.append(seg[0])
        pts.extend(seg)
        ex.Next()
    return np.array(pts), np.array(joints)


def preview_svg(in_path, pp: ProfileParams, width=520):
    """Small SVG of the outline(s) the conversion would use (for the app's preview)."""
    m = trimesh.load(in_path, force="mesh", process=True)
    if pp.scale != 1.0:
        m.apply_scale(pp.scale)
    diag = float(np.linalg.norm(m.extents))
    tol = pp.simplify if pp.simplify > 0 else max(0.01, 2e-3 * diag)
    heights, secs, dense = trace(m, pp, tol)
    allp = np.vstack([np.vstack([o.outer] + o.holes) for s in secs for o in s])
    mn, mx = allp.min(0), allp.max(0)
    span = max(float((mx - mn).max()), 1e-9)
    k = (width - 20) / span
    hgt = int((mx[1] - mn[1]) * k) + 20
    paths = []
    n = len(secs)
    smooth_notes = []
    for i, s in enumerate(secs):
        shade = 70 + int(150 * (1 - i / max(n - 1, 1))) if n > 1 else 60
        col = f"rgb({shade},{shade},{min(255, shade + 60)})"
        for oi, o in enumerate(s):
            d = ""
            dots = []
            dn = dense[i][oi] if (pp.smooth and pp.mode != "loft" and dense and oi < len(dense[i])) else None
            for li, loop in enumerate([o.outer] + o.holes):
                drawn = None
                if dn is not None:
                    # the smoothed sketch: curves drawn as curves, a dot where two pieces meet
                    src = dn.outer if li == 0 else None
                    if li > 0 and dn.holes:
                        c = np.asarray(loop, float).mean(0)
                        j = int(np.argmin([np.linalg.norm(np.asarray(h, float).mean(0) - c) for h in dn.holes]))
                        src = dn.holes[j]
                    if src is not None:
                        got = []
                        try:
                            w = _smooth_wire(pp.axis.lower(), src, 0.0, tol, got)
                        except Exception:
                            w = None
                        if w is not None:
                            drawn, joints = _wire_polyline(pp.axis.lower(), w)
                            if len(joints) > 1:
                                dots.extend(joints.tolist())
                            smooth_notes.append(("outline" if li == 0 else f"hole {li}") + ": " + (got[-1] if got else "smoothed"))
                        else:
                            smooth_notes.append(("outline" if li == 0 else f"hole {li}") + f": {len(loop)} straight segments")
                q = ((drawn if drawn is not None else loop) - mn) * k + 10
                d += "M" + " L".join(f"{x:.1f},{hgt - y:.1f}" for x, y in q) + " Z "
            fill = "rgba(47,111,237,.10)" if n == 1 else "none"
            paths.append(f'<path d="{d}" fill="{fill}" fill-rule="evenodd" stroke="{col}" stroke-width="1.5"/>')
            for x, y in dots:
                qx, qy = (x - mn[0]) * k + 10, hgt - ((y - mn[1]) * k + 10)
                paths.append(f'<circle cx="{qx:.1f}" cy="{qy:.1f}" r="2.4" fill="#e5392e"/>')
    info = (f"{n} cross-sections" if n > 1 else
            f"{len(secs[0])} island(s), {sum(len(o.holes) for o in secs[0])} hole(s), "
            f"{sum(len(o.outer) + sum(len(h) for h in o.holes) for o in secs[0])} points")
    if smooth_notes:
        info = "Smooth sketch (red dots = where two pieces meet) — " + "; ".join(smooth_notes[:8]) + \
               (f"; … {len(smooth_notes) - 8} more" if len(smooth_notes) > 8 else "")
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {hgt}" width="100%" '
            f'style="max-height:360px">{"".join(paths)}</svg>', info)
