# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""
quadfit — the EXPERIMENTAL "Quad patches" engine for organic shapes (0.10.1).

A wireframe first, then surfaces filled between the wires:

1. The mesh is laid out as a coarse quad mesh that follows its curvature, by QuadriFlow
   (BSD-3, Huang et al., SGP 2018) when its program is found, otherwise by Instant Meshes
   (BSD-3, Jakob et al., SIGGRAPH Asia 2015; the `pynanoinstantmeshes` package), the method
   QuadriFlow builds on.
2. Every quad edge becomes one curve that lies on the mesh: points along the edge are moved
   to the nearest point of the mesh and a cubic B-spline is drawn through them.
3. Every quad is filled with a Coons surface from its four curves (OpenCascade's
   GeomFill_BSplineCurves): untrimmed, no plate solve, and neighbours meet exactly along the
   curve they share.
4. The faces are joined (they share their boundary curves, so the joining has nothing to
   guess), written, read back and measured against the mesh. If the result is further from the
   mesh than the tolerance, the quad layout is made finer and the whole is built again, up to
   a cap on faces and time.

What it is for: smooth, closed organic parts (figures, blobs, sculpted shapes), where the
Classic fitter makes trimmed patches and facets. What it is not for: parts with sharp edges
and flat faces (a quad layout rounds sharp edges over unless they line up with quad edges);
the engine says so and gives the part back to the caller.

Where to find QuadriFlow: the program `quadriflow` (`quadriflow.exe` on Windows) in
app/bin/<system>-<machine>/ (it ships for linux-x86_64), or anywhere on PATH, or the path in the
environment variable TOSTEP_QUADRIFLOW.
`tools/build_quadriflow.py` builds it from its source (needs CMake and a C++ compiler).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
MAX_FACES = 6000          # quads at most: beyond this the result is no simpler than facets
SHARP_MAX = 0.002         # share of edge length sharper than 30° above which a part is not for quad patches
FIRST_FACES = int(os.environ.get("TOSTEP_QUAD_FIRST", "60"))


class QuadError(RuntimeError):
    pass


# ---------------------------------------------------------------- remeshers
def platform_dir():
    """app/bin/<this>: linux-x86_64, darwin-arm64, windows-x86_64, …"""
    import platform
    return "%s-%s" % ("windows" if sys.platform.startswith("win") else sys.platform.rstrip("0123456789"),
                      platform.machine().lower().replace("amd64", "x86_64"))


def find_quadriflow():
    """The QuadriFlow program, or None."""
    exe = "quadriflow.exe" if sys.platform.startswith("win") else "quadriflow"
    for p in (os.environ.get("TOSTEP_QUADRIFLOW", ""), os.path.join(HERE, "bin", platform_dir(), exe),
              os.path.join(HERE, "bin", exe), shutil.which("quadriflow") or ""):
        if p and os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


def have_instant_meshes():
    try:
        import pynanoinstantmeshes  # noqa: F401 (only whether it is there)
        return True
    except Exception:
        return False


def remesher_name():
    if find_quadriflow() and not _QF_BROKEN:
        return "QuadriFlow"
    if have_instant_meshes():
        return "Instant Meshes"
    return None


def split_to_quads(V, F):
    """Every face with n corners becomes n quads (corner, edge midpoints, centre); edges are split
    the same way on both sides, so the result is a closed all-quad mesh when the input was closed."""
    V = [np.asarray(v, float) for v in V]
    mids = {}

    def mid(a, b):
        k = (a, b) if a < b else (b, a)
        if k not in mids:
            mids[k] = len(V); V.append(0.5 * (V[a] + V[b]))
        return mids[k]
    out = []
    for f in F:
        n = len(f)
        c = len(V); V.append(np.mean([V[i] for i in f], axis=0))
        for k in range(n):
            a, b, z = f[k], f[(k + 1) % n], f[(k - 1) % n]
            out.append([a, mid(a, b), c, mid(z, a)])
    return np.array(V), out


def _write_obj(path, V, F):
    with open(path, "w") as fh:
        fh.write("".join("v %.9g %.9g %.9g\n" % tuple(v) for v in V))
        fh.write("".join("f %d %d %d\n" % (a + 1, b + 1, c + 1) for a, b, c in F))


def _read_obj(path):
    V, F = [], []
    with open(path) as fh:
        for line in fh:
            if line.startswith("v "):
                V.append([float(x) for x in line.split()[1:4]])
            elif line.startswith("f "):
                F.append([int(t.split("/")[0]) - 1 for t in line.split()[1:]])
    return np.array(V, float), F


_QF_BROKEN = []


def quad_layout(V, F, n_faces, seed=0, timeout=900):
    """(quad vertices, list of faces as vertex index lists) with about n_faces faces.
    QuadriFlow when it is there and runs; Instant Meshes otherwise (a QuadriFlow program that
    cannot start here, such as the shipped Linux one on a Linux older than its C library, is
    passed over for the rest of the run)."""
    exe = None if _QF_BROKEN else find_quadriflow()
    if exe:
        try:
            return _quadriflow(exe, V, F, n_faces, seed, timeout)
        except (OSError, QuadError) as e:
            if not have_instant_meshes():
                raise QuadError(str(e))
            _QF_BROKEN.append(str(e))
    return _instant_meshes(V, F, n_faces)


def _quadriflow(exe, V, F, n_faces, seed, timeout):
    d = tempfile.mkdtemp(prefix="e2s_quad_")
    try:
        src, dst = os.path.join(d, "in.obj"), os.path.join(d, "out.obj")
        _write_obj(src, V, F)
        kw = {"creationflags": 0x08000000} if sys.platform.startswith("win") else {}
        r = subprocess.run([exe, "-i", src, "-o", dst, "-f", str(int(n_faces)), "-seed", str(int(seed))],
                           capture_output=True, text=True, timeout=timeout, **kw)
        if r.returncode != 0 or not os.path.isfile(dst):
            raise QuadError("QuadriFlow failed: " + (r.stdout + r.stderr)[-300:])
        return _read_obj(dst)
    finally:
        shutil.rmtree(d, ignore_errors=True)


class _quiet_stdout:
    """Send what compiled code writes to standard output to nowhere for a moment (the job's own
    messages go through Python and are flushed first; anything that fails just runs unquieted)."""
    def __enter__(self):
        self.saved = None
        try:
            sys.stdout.flush()
            self.saved = os.dup(1)
            null = os.open(os.devnull, os.O_WRONLY)
            os.dup2(null, 1)
            os.close(null)
        except (OSError, ValueError, AttributeError):
            self.saved = None
        return self

    def __exit__(self, *exc):
        if self.saved is not None:
            try:
                os.dup2(self.saved, 1)
                os.close(self.saved)
            except OSError:
                pass
        return False


def _instant_meshes(V, F, n_faces):
    if have_instant_meshes():
        import pynanoinstantmeshes as pim
        # Instant Meshes gives a quad-dominant mesh (a few triangles and other faces); split
        # once, every face into quads, so it is all quads (and four times as many)
        with _quiet_stdout():          # (it prints about 70 lines of progress from C++)
            Q, QF = pim.remesh(np.asarray(V, np.float32), np.asarray(F, np.uint32), vertex_count=-1,
                               face_count=int(max(8, n_faces // 4)), deterministic=True)
        faces = [[int(i) for i in f if i < len(Q)] for f in np.asarray(QF)]
        return split_to_quads(np.asarray(Q, float), [f for f in faces if len(set(f)) >= 3])
    raise QuadError("No quad remesher: build QuadriFlow (tools/build_quadriflow.py) or "
                    "install Instant Meshes (pip install pynanoinstantmeshes)")


# ---------------------------------------------------------------- geometry
_TREES = {}


def _closest(mesh, P, k=16):
    """Nearest points of the mesh to P: the k triangles with the nearest centres are tried and
    the closest point on any of them kept (vectorised; for points near a mesh of small, even
    triangles this is the exact answer; the final deviation is measured with the exact search
    of the converter). About ten times faster than trimesh's search on these meshes."""
    from scipy.spatial import cKDTree
    from trimesh.triangles import closest_point as on_tri
    P = np.asarray(P, float)
    key = id(mesh)
    got = _TREES.get(key)
    if got is None or got[0] is not mesh:
        tri = np.asarray(mesh.triangles)
        _TREES.clear()
        got = _TREES[key] = (mesh, tri, cKDTree(tri.mean(axis=1)))
    _, tri, tree = got
    k = min(k, len(tri))
    out = np.empty_like(P)
    for s in range(0, len(P), 4000):
        Q = P[s:s + 4000]
        _, idx = tree.query(Q, k=k)
        idx = idx.reshape(len(Q), k)
        C = on_tri(tri[idx.ravel()], np.repeat(Q, k, axis=0)).reshape(len(Q), k, 3)
        d = np.linalg.norm(C - Q[:, None, :], axis=2)
        out[s:s + 4000] = C[np.arange(len(Q)), d.argmin(axis=1)]
    return out


def _clean_quads(QV, QF):
    """Only proper quads with four distinct corners, unused vertices dropped."""
    faces = [f for f in QF if len(f) == 4 and len(set(f)) == 4]
    used = sorted({i for f in faces for i in f})
    remap = {o: n for n, o in enumerate(used)}
    return QV[used], [[remap[i] for i in f] for f in faces]


def _is_closed(QF):
    from collections import Counter
    c = Counter()
    for f in QF:
        for k in range(4):
            a, b = f[k], f[(k + 1) % 4]
            c[(a, b) if a < b else (b, a)] += 1
    return len(c) > 0 and all(v == 2 for v in c.values())


def _knots(surf, u=True):
    """Full knot vector and degree of a B-spline surface in one direction."""
    n = surf.NbUKnots() if u else surf.NbVKnots()
    t = []
    for i in range(1, n + 1):
        k = surf.UKnot(i) if u else surf.VKnot(i)
        m = surf.UMultiplicity(i) if u else surf.VMultiplicity(i)
        t += [k] * m
    return np.array(t), (surf.UDegree() if u else surf.VDegree())


EXTRA_KNOTS = int(os.environ.get("TOSTEP_QUAD_KNOTS", "2"))


_DESIGN = {}


class _Patch:
    """One Coons patch prepared for the interior fit: its design matrix over a grid of
    parameters, its control points, which of them may move."""

    def __init__(self, surf, extra, grid):
        from scipy.interpolate import BSpline
        if extra:
            # more inner control points without touching the boundary: knots inserted between
            # the existing ones keep the surface (and its edges) exactly as it is
            u1, u2, v1, v2 = surf.Bounds()
            for k in range(1, extra + 1):
                surf.InsertUKnot(u1 + (u2 - u1) * k / (extra + 1), 1, 1e-9)
                surf.InsertVKnot(v1 + (v2 - v1) * k / (extra + 1), 1, 1e-9)
        self.surf = surf
        nu, nv = surf.NbUPoles(), surf.NbVPoles()
        self.nu, self.nv = nu, nv
        g = max(grid, 2 * (max(nu, nv) + 1))
        tu, du = _knots(surf, True)
        tv, dv = _knots(surf, False)
        u1, u2, v1, v2 = surf.Bounds()
        # one design matrix for every patch with the same knots (all of them, as every edge
        # curve has the same parameters): 0.5 MB each would be 3 GB on a 6,000-quad layout
        key = (tuple(np.round(tu, 12)), du, tuple(np.round(tv, 12)), dv, g, round(u1, 12), round(u2, 12),
               round(v1, 12), round(v2, 12))
        if key not in _DESIGN:
            if len(_DESIGN) > 16:
                _DESIGN.clear()
            Bu = BSpline.design_matrix(np.linspace(u1, u2, g), tu, du).toarray()
            Bv = BSpline.design_matrix(np.linspace(v1, v2, g), tv, dv).toarray()
            _DESIGN[key] = np.einsum("ia,jb->ijab", Bu, Bv).reshape(g * g, nu * nv)
        self.A = _DESIGN[key]
        self.key = key
        self.P = np.array([[[surf.Pole(i + 1, j + 1).X(), surf.Pole(i + 1, j + 1).Y(), surf.Pole(i + 1, j + 1).Z()]
                            for j in range(nv)] for i in range(nu)]).reshape(nu * nv, 3)
        inner = np.zeros((nu, nv), bool)
        inner[1:-1, 1:-1] = True
        self.inner = inner.ravel()
        self.movable = nu >= 3 and nv >= 3 and not (surf.IsURational() or surf.IsVRational())

    def points(self):
        return self.A @ self.P

    def solve(self, T, lam=1e-3):
        """Inner poles by least squares against the targets T (a small pull towards the current
        poles keeps a sparse grid from folding the patch)."""
        Ai, Ab = self.A[:, self.inner], self.A[:, ~self.inner]
        rhs = T - Ab @ self.P[~self.inner]
        M = Ai.T @ Ai + lam * np.eye(Ai.shape[1])
        self.P[self.inner] = np.linalg.solve(M, Ai.T @ rhs + lam * self.P[self.inner])

    def apply(self):
        from tostep.plugins.mesh2step import classic as core
        k = 0
        for i in range(self.nu):
            for j in range(self.nv):
                if self.inner[k]:
                    self.surf.SetPole(i + 1, j + 1, core._gp(self.P[k]))
                k += 1
        return self.surf


SMOOTH_SEAMS = os.environ.get("TOSTEP_QUAD_SMOOTH", "1") != "0"
SEAM_WEIGHT = float(os.environ.get("TOSTEP_QUAD_SEAM", "3.0"))   # 1: seams up to 3.3°; 3: 1.1°; 10: worse fit


def _sides(p):
    """The four boundary rows of a patch's control net, each with the row next to it inside:
    lists of flat pole indices (corner to corner)."""
    nu, nv = p.nu, p.nv
    ix = lambda i, j: i * nv + j  # noqa: E731
    return [([ix(0, j) for j in range(nv)], [ix(1, j) for j in range(nv)]),
            ([ix(nu - 1, j) for j in range(nv)], [ix(nu - 2, j) for j in range(nv)]),
            ([ix(i, 0) for i in range(nu)], [ix(i, 1) for i in range(nu)]),
            ([ix(i, nv - 1) for i in range(nu)], [ix(i, nv - 2) for i in range(nu)])]


def _seam_pairs(patches, digits=7):
    """For every edge two patches share: the pairs of control points either side of it, with
    the boundary point between them, as ((patch a, pole a), (patch b, pole b), boundary pole).
    Corner points (where three or five patches may meet) are left out."""
    seen = {}
    for pi, p in enumerate(patches):
        for b, inner in _sides(p):
            key = tuple(np.round(p.P[b], digits).ravel())
            rkey = tuple(np.round(p.P[b[::-1]], digits).ravel())
            if rkey in seen and rkey != key:
                seen[rkey].append((pi, b[::-1], inner[::-1]))
            else:
                seen.setdefault(key, []).append((pi, b, inner))
    out = []
    for pair in seen.values():
        if len(pair) == 2:
            (pa, ba, ia), (pb, bb, ib) = pair
            out += [((pa, ia[k]), (pb, ib[k]), (pa, ba[k])) for k in range(1, len(ba) - 1)]
    return out


def _cg_has_rtol():
    import inspect
    from scipy.sparse.linalg import cg
    try:
        return "rtol" in inspect.signature(cg).parameters
    except (TypeError, ValueError):
        return True


_CG_RTOL = _cg_has_rtol()          # scipy 1.12 renamed cg's tol to rtol


def _fit_together(patches, targets, seams, mu, reg=1e-3):
    """All patches' inner control points at once: each patch fitted to its targets (least
    squares, as _Patch.solve), plus for every seam pair a soft condition that the two points
    either side of the shared edge lie on one straight line through it, with the ratio of
    their distances as it is now (the condition for the surfaces to meet without a crease).
    mu weighs that condition against the fit. Patches must share one design matrix (they do:
    every edge curve has the same parameters). Solved by conjugate gradients without forming
    the whole matrix, so memory grows only with the number of control points."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.linalg import LinearOperator, cg
    A, inner = patches[0].A, patches[0].inner
    if any(p.A is not A or not np.array_equal(p.inner, inner) for p in patches):
        for p, T in zip(patches, targets):
            p.solve(T)
        return
    Ai, Ab = A[:, inner], A[:, ~inner]
    m = int(inner.sum())
    G = Ai.T @ Ai + reg * np.eye(m)
    cur = np.stack([p.P[inner] for p in patches])                        # (patches, m, 3)
    rhs = np.stack([Ai.T @ (T - Ab @ p.P[~inner]) for p, T in zip(patches, targets)]) + reg * cur
    pos = -np.ones(len(inner), int)
    pos[inner] = np.arange(m)
    srows, scols, svals, srhs = [], [], [], []
    k = 0
    for (pa, ia), (pb, ib), (pc, bc) in seams:
        xa, xb = pos[ia], pos[ib]
        if xa < 0 or xb < 0:
            continue
        P0 = patches[pa].P[bc]
        la = np.linalg.norm(patches[pa].P[ia] - P0)
        lb = np.linalg.norm(patches[pb].P[ib] - P0)
        if la < 1e-12 or lb < 1e-12:
            continue
        lam = la / lb
        srows += [k, k]; scols += [pa * m + xa, pb * m + xb]; svals += [mu, mu * lam]
        srhs.append(mu * (1 + lam) * P0)
        k += 1
    n = len(patches) * m
    rhs = rhs.reshape(n, 3)
    if k:
        S = coo_matrix((svals, (srows, scols)), shape=(k, n)).tocsr()
        St = S.T.tocsr()
        rhs = rhs + St @ np.vstack(srhs)
        dS = np.asarray(S.multiply(S).sum(axis=0)).ravel()
    else:
        S = St = None
        dS = np.zeros(n)
    np_ = len(patches)

    def mv(x):
        y = (x.reshape(np_, m) @ G).ravel()                               # G is symmetric
        if S is not None:
            y = y + St @ (S @ x)
        return y
    N = LinearOperator((n, n), matvec=mv, dtype=float)
    dinv = 1.0 / np.maximum(np.tile(np.diag(G), np_) + dS, 1e-30)
    pre = LinearOperator((n, n), matvec=lambda x: dinv * x, dtype=float)
    X = cur.reshape(n, 3).copy()
    for c in range(3):
        if _CG_RTOL:
            X[:, c], _ = cg(N, rhs[:, c], x0=X[:, c], M=pre, rtol=1e-10, maxiter=3000)
        else:
            X[:, c], _ = cg(N, rhs[:, c], x0=X[:, c], M=pre, tol=1e-10, maxiter=3000)
    X = X.reshape(np_, m, 3)
    for p, x in zip(patches, X):
        p.P[inner] = x


def fit_interiors(surfs, mesh, grid=12, rounds=2, extra=None):
    """Move the inner control points of every Coons patch so the surfaces follow the mesh
    (least squares against the nearest mesh points of a grid of surface points). The boundary
    control points, and so the curves shared with neighbouring patches, stay exactly as they
    are. All patches' grid points go to the nearest-point search together (one call per round)."""
    extra = EXTRA_KNOTS if extra is None else extra
    patches = [_Patch(s, extra, grid) for s in surfs]
    live = [p for p in patches if p.movable]
    if not live:
        return [p.surf for p in patches]
    start = [p.P.copy() for p in live]

    def gaps():
        pts = [p.points() for p in live]
        T = _closest(mesh, np.vstack(pts))
        out, k = [], 0
        for s in pts:
            out.append((T[k:k + len(s)], float(np.linalg.norm(T[k:k + len(s)] - s, axis=1).max())))
            k += len(s)
        return out

    g0 = gaps()                                                     # the plain Coons patches
    g = g0
    for _ in range(rounds):
        for p, (T, _) in zip(live, g):
            p.solve(T)
        g = gaps()
    if SMOOTH_SEAMS:
        seams = _seam_pairs(live)
        for _ in range(rounds):
            _fit_together(live, [T for T, _ in g], seams, SEAM_WEIGHT)
            g = gaps()
    # a patch the fit made worse (a fold, where the nearest mesh points jump between two sides
    # of a thin part) goes back to its plain Coons surface
    for p, P0, (_, d0), (_, d1) in zip(live, start, g0, g):
        if not d1 <= d0:
            p.P = P0
    return [p.apply() if p.movable else p.surf for p in patches]


def build_shape(mesh, QV, QF, tol, samples=7, interior=True):
    """The solid made of one Coons patch per quad. Returns (shape, number of faces)."""
    from tostep.plugins.mesh2step import classic as core
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_Sewing
    from OCP.GeomAPI import GeomAPI_Interpolate
    from OCP.GeomFill import GeomFill_BSplineCurves, GeomFill_FillingStyle
    try:                                         # OCP >= 7.8 / 8.x
        from OCP.collections import HArray1_gp_Pnt as TColgp_HArray1OfPnt
    except ImportError:                          # older OCP
        from OCP.TColgp import TColgp_HArray1OfPnt

    QV = _closest(mesh, np.asarray(QV, float))                    # corners onto the mesh
    edges = {}
    for f in QF:
        for k in range(4):
            a, b = f[k], f[(k + 1) % 4]
            edges.setdefault((a, b) if a < b else (b, a), None)
    keys = list(edges)
    t = np.linspace(0.0, 1.0, samples)
    A = QV[[k[0] for k in keys]]; B = QV[[k[1] for k in keys]]
    P = A[:, None, :] + (B - A)[:, None, :] * t[None, :, None]   # straight edges, then onto the mesh
    inner = P[:, 1:-1].reshape(-1, 3)
    P[:, 1:-1] = _closest(mesh, inner).reshape(len(keys), samples - 2, 3)
    try:
        from OCP.collections import HArray1_double as TColStd_HArray1OfReal
    except ImportError:
        from OCP.TColStd import TColStd_HArray1OfReal
    # every edge curve on the same uniform parameters, so all of them have the same knots:
    # the Coons patches then need no knot merging, and two patches sharing an edge have the
    # very same row of control points along it (needed for the tangent matching)
    par = TColStd_HArray1OfReal(1, samples)
    for i in range(samples):
        par.SetValue(i + 1, i / (samples - 1.0))
    curves = {}
    for key, pts in zip(keys, P):
        arr = TColgp_HArray1OfPnt(1, samples)
        for i, p in enumerate(pts):
            arr.SetValue(i + 1, core._gp(p))
        it = GeomAPI_Interpolate(arr, par, False, 1e-9)
        it.Perform()
        if not it.IsDone():
            raise QuadError("an edge curve could not be drawn")
        curves[key] = it.Curve()
    sew = BRepBuilderAPI_Sewing(max(1e-5, 0.01 * tol), True, True, True, False)
    n = 0
    surfs = []
    for f in QF:
        loop = []
        for k in range(4):
            a, b = f[k], f[(k + 1) % 4]
            c = curves[(a, b) if a < b else (b, a)]
            if a > b:
                c = c.Reversed()
            loop.append(c)
        try:
            fill = GeomFill_BSplineCurves(loop[0], loop[1], loop[2], loop[3], GeomFill_FillingStyle.GeomFill_CoonsStyle)
            surfs.append(fill.Surface())
        except Exception:
            raise QuadError("a quad could not be filled")
    if interior:
        surfs = fit_interiors(surfs, mesh)
    for surf in surfs:
        mf = BRepBuilderAPI_MakeFace(surf, 1e-7)
        if not mf.IsDone():
            raise QuadError("a quad could not be filled")
        sew.Add(mf.Face())
        n += 1
    sew.Perform()
    shape, ns, no = core._shells_to_solid(sew.SewedShape(), lambda *a: None)
    return shape, n


def suitable(ctx):
    """(yes, reason): a closed mesh with no real flat faces and almost no sharp edges (the
    Ladder engine's own test for an organic part, routing signals in ladder.py)."""
    from tostep.plugins.mesh2step import ladder
    if not ctx.m.is_watertight:
        return False, "the mesh is not closed"
    sig = ladder.signals(ctx)
    if sig["flat_sharp"] >= ladder.ORGANIC_FLAT_MAX:
        return False, (f"{100 * sig['flat_sharp']:.0f}% of its surface is flat faces with sharp edges "
                       "(the other engines make those exact; quad patches would round their edges over)")
    if sig["lathe_curved"] >= ladder.LATHE_MIN:
        return False, (f"{100 * sig['lathe_curved']:.0f}% of its curved area turns about one axis: a turned shape "
                       "(the Quick preset makes those as exact surfaces of revolution, far fewer faces)")
    # stricter than the Ladder's organic test (2 %): a quad layout rounds over every sharp edge
    # that does not happen to lie along quad edges (the ridged test knob, 0.8 %: 0.52 mm off)
    if sig["sharp30_len"] >= SHARP_MAX:
        return False, (f"{100 * sig['sharp30_len']:.1f}% of its edge length is sharper than 30° "
                       "(quad patches would round those edges over)")
    return True, ""


# ---------------------------------------------------------------- entry point
def convert(in_path, out_path, prm=None, log=None, progress=None):
    """Same contract as mesh2step.classic.convert: returns its Report."""
    import trimesh
    from tostep.plugins.mesh2step import classic as core
    log = log or (lambda s: None)
    prog = progress or (lambda f: None)
    prm = prm or core.Params()
    rep = core.Report(out_path=str(out_path))
    t0 = time.time()
    name = remesher_name()
    if name is None:
        raise QuadError("No quad remesher: build QuadriFlow (tools/build_quadriflow.py) or "
                        "install Instant Meshes (pip install pynanoinstantmeshes)")
    mesh = core.load_mesh(in_path, prm, log)
    rep.n_input_tris = len(mesh.faces)
    ctx = core.MeshCtx(mesh)
    ok, why = suitable(ctx)
    if not ok:
        raise QuadError("not an organic closed shape: " + why)
    rep.mesh_volume = float(abs(mesh.volume))
    tol = prm.tol if prm.tol > 0 else max(0.01, 5e-4 * ctx.diag)
    target = max(tol, prm.organic_tol or 0.0)
    log(f"Part size {ctx.diag:.2f} mm (diagonal), tolerance {target:.4g} mm")
    log(f"Quad layout by {name}; one Coons patch per quad, refined until within the tolerance")
    budget = float(prm.budget_s or 0) or float(os.environ.get("TOSTEP_BUDGET", "0") or 0)
    ref = trimesh.Trimesh(np.array(mesh.vertices), np.array(mesh.faces), process=False)
    want, best, tried, attempts, ratio = FIRST_FACES, None, [], 0, 1.0
    while True:
        t1 = time.time()
        attempts += 1
        n_req = int(max(8, want / ratio))
        QV, QF = quad_layout(mesh.vertices, mesh.faces, n_req)
        QV, QF = _clean_quads(QV, QF)
        if QF:
            ratio = max(0.2, min(5.0, len(QF) / n_req))          # how many quads the remesher gives per one asked for
        shape, nq = None, len(QF)
        if len(QF) < 6 or not _is_closed(QF):
            log(f"  {len(QF)} quads: the layout came back open or degenerate — trying finer")
        else:
            try:
                shape, nq = build_shape(mesh, QV, QF, target)
            except QuadError as e:
                log(f"  {len(QF)} quads: {e}")
        if shape is not None and core._sealed(shape):
            mean_d, max_d = core.measure_deviation(ref, shape, target)
            worst = core._DEBUG.pop("worst_at", ())
            log(f"  {nq} quads: max deviation {max_d:.4f} mm, mean {mean_d:.4f} mm ({time.time() - t1:.1f} s)")
            tried.append((nq, max_d))
            if best is None or max_d < best[1]:
                best = (shape, max_d, mean_d, worst, nq)
            if max_d <= target:
                break
        elif shape is not None:
            log(f"  {nq} quads: the patches did not close into a solid ({time.time() - t1:.1f} s)")
        prog(min(0.85, 0.1 + 0.75 * attempts / 8))
        # next layout: from the error's measured rate of decrease between the last two good
        # results, otherwise twice as many quads; always at least a third more than the last
        last = max(nq, int(want))
        if len(tried) >= 2 and tried[-1][1] < tried[-2][1] and tried[-1][0] > tried[-2][0]:
            (n1, d1), (n2, d2) = tried[-2], tried[-1]
            rate = max(0.5, min(3.0, np.log(d1 / d2) / max(np.log(n2 / n1), 1e-6)))
            want = n2 * (d2 / target) ** (1.0 / rate) * 1.15
        else:
            want = 2 * last
        want = int(min(MAX_FACES, max(want, 1.33 * last)))
        if last >= 0.9 * MAX_FACES or attempts >= 8 or (budget and time.time() - t0 > budget):
            break
    if best is None:
        raise QuadError("no quad layout gave a closed solid")
    shape, max_d, mean_d, worst, nq = best
    if max_d > target:
        log(f"  stopped at {nq} quads: the closest result is {max_d:.4f} mm from the mesh")
    log("Writing STEP…")
    core.write_step(shape, out_path)
    back = core.read_step(out_path)
    rep.is_solid = core._sealed(back)
    rep.is_valid = True if rep.is_solid else bool(core.BRepCheck_Analyzer(back).IsValid())
    rep.n_faces = core._count_faces(back)
    rep.n_nurbs = rep.n_faces
    rep.step_volume = core._volume(back) if rep.is_solid else 0.0
    rep.mean_dev, rep.max_dev, rep.worst_at, rep.tol = mean_d, max_d, worst, target
    rep.ok = True
    rep.seconds = time.time() - t0
    rep.route = "quad patches (" + name + ")"
    prog(1.0)
    log(rep.summary())
    return rep
