# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""
rough — the "roughing" stage: claim as much of the mesh as possible with exact swept
surfaces before any freeform fitting. This module finds lathe shapes (surfaces of
revolution): an axis plus a profile curve.

How a lathe shape is recognised (Pottmann et al., "line geometry"): on a surface of
revolution every surface normal, extended to a line, meets the axis. For each triangle take
its middle x and normal n and form the six numbers (x × n, n); the axis is the smallest
eigenvector of the 6×6 sum of their outer products. That is one pass over the triangles and
one tiny eigen-solve — far cheaper than fitting patches.

The profile is then read off as (distance from axis, position along axis) of every corner,
ordered along one cut through the axis, and fitted with the smoothest spline that keeps all
corners within tolerance. The STEP face is a true surface of revolution of that profile.

A primitive made here is the tuple ("rev", point_on_axis, axis_direction, Profile, True);
mesh2step.classic treats it like its cylinders and spheres (same trimming, same checks).
"""
from __future__ import annotations

import math
import warnings

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree


_TRACE = None          # debugging: set to a list to record the profile fits tried
STATS = {"direct": 0, "seam": 0, "invalid": 0}     # how lathe faces were built (for testing)


# ---------------------------------------------------------------- profile
class Profile:
    """The profile curve of a lathe shape in (r, z): a cubic spline, densely sampled for
    fast distance queries. `closed` profiles (a torus tube) are periodic."""

    def __init__(self, tck, closed, n_samples=3000):
        from scipy.interpolate import splev
        self.tck = tck
        self.closed = bool(closed)
        self.analytic = None       # ("cyl", r) / ("cone", r0, z0, sin a, cos a) / ("sph", zc, R)
        self.flip = False          # analytic surface's own normal points the other way
        t, c, k = tck
        self.t0, self.t1 = float(t[k]), float(t[len(c[0])])
        ts = np.linspace(self.t0, self.t1, n_samples, endpoint=not self.closed)
        self.ts = ts
        self.rz = np.array(splev(ts, tck)).T
        d = np.array(splev(ts, tck, der=1)).T
        self.tan = d / np.maximum(np.linalg.norm(d, axis=1, keepdims=True), 1e-12)
        self.tree = cKDTree(self.rz)
        seg = np.linalg.norm(np.diff(self.rz, axis=0), axis=1)
        self.length = float(seg.sum())
        cosd = np.clip((self.tan[1:] * self.tan[:-1]).sum(1), -1.0, 1.0)
        self.turning = float(np.arccos(cosd).sum())     # total bend of the curve, in radians

    def nearest(self, rz):
        """(distance, nearest point, sample index) for each query point; exact against the
        sampled polyline (the nearest sample, then its two neighbouring segments)."""
        rz = np.atleast_2d(rz)
        _, i = self.tree.query(rz)
        n = len(self.rz)
        best_d = np.full(len(rz), np.inf)
        best_p = np.zeros_like(rz, dtype=float)
        for off in (-1, 0):
            a_i = i + off
            b_i = a_i + 1
            if self.closed:
                a_i = a_i % n; b_i = b_i % n
            else:
                a_i = np.clip(a_i, 0, n - 1); b_i = np.clip(b_i, 0, n - 1)
            a, b = self.rz[a_i], self.rz[b_i]
            ab = b - a
            den = np.maximum((ab * ab).sum(1), 1e-30)
            s = np.clip(((rz - a) * ab).sum(1) / den, 0.0, 1.0)
            p = a + ab * s[:, None]
            d = np.linalg.norm(rz - p, axis=1)
            upd = d < best_d
            best_d[upd] = d[upd]; best_p[upd] = p[upd]
        return best_d, best_p, i

    def nearest_t(self, rz):
        """Curve parameter of the nearest point on the profile for each query point."""
        rz = np.atleast_2d(rz)
        _, i = self.tree.query(rz)
        n = len(self.rz)
        step = (self.t1 - self.t0) / (n if self.closed else n - 1)
        best_d = np.full(len(rz), np.inf)
        best_t = np.zeros(len(rz))
        for off in (-1, 0):
            a_i = i + off
            if self.closed:
                a_m = a_i % n; b_m = (a_i + 1) % n
            else:
                a_i = np.clip(a_i, 0, n - 2)
                a_m = a_i; b_m = a_i + 1
            a, b = self.rz[a_m], self.rz[b_m]
            ab = b - a
            den = np.maximum((ab * ab).sum(1), 1e-30)
            s = np.clip(((rz - a) * ab).sum(1) / den, 0.0, 1.0)
            d = np.linalg.norm(rz - (a + ab * s[:, None]), axis=1)
            t = self.t0 + (a_i + s) * step
            upd = d < best_d
            best_d[upd] = d[upd]; best_t[upd] = t[upd]
        if self.closed:
            best_t = self.t0 + (best_t - self.t0) % (self.t1 - self.t0)
        return best_t

    def at(self, t):
        """(r, z) of the profile at curve parameter(s) t."""
        from scipy.interpolate import splev
        t = np.asarray(t, float)
        if self.closed:
            t = self.t0 + (t - self.t0) % (self.t1 - self.t0)
        return np.array(splev(np.clip(t, self.t0, self.t1), self.tck)).T

    def control_points(self):
        return len(self.tck[1][0]) - (3 if self.closed else 0)


def _frame(axis):
    """Two unit vectors perpendicular to the axis, right-handed: e1 × e2 = axis."""
    axis = np.asarray(axis, float)
    e1 = np.cross(axis, [1.0, 0, 0] if abs(axis[0]) < 0.9 else [0, 1.0, 0])
    e1 /= np.linalg.norm(e1)
    e2 = np.cross(axis, e1)
    return e1, e2


def _rz(P, p0, axis):
    d = np.atleast_2d(P) - p0
    z = d @ axis
    rad = d - np.outer(z, axis)
    return np.c_[np.linalg.norm(rad, axis=1), z], rad


# ---------------------------------------------------------------- hooks used by mesh2step.classic
SWEPT = ("rev", "ext")     # primitive kinds made here: lathe shapes and extruded profiles


def _xyh(P, o, d):
    """Coordinates of points in an extrusion's own frame: (x, y) across, h along."""
    e1, e2 = _frame(d)
    q = np.atleast_2d(P) - o
    return np.c_[q @ e1, q @ e2], q @ d


def residual(prim, P):
    """Distance of each point from the swept surface."""
    _, p0, axis, prof, _ = prim
    if prim[0] == "ext":
        return prof.nearest(_xyh(P, p0, axis)[0])[0]
    rz, _ = _rz(P, p0, axis)
    return prof.nearest(rz)[0]


def normal_ok(prim, pts, nrm, cos_lim):
    """Do these face normals agree with the surface's own normal there?"""
    _, p0, axis, prof, _ = prim
    if prim[0] == "ext":
        e1, e2 = _frame(axis)
        _, _, i = prof.nearest(_xyh(pts, p0, axis)[0])
        tan = prof.tan[i]
        # natural normal of S(u, v) = C(u) + v·d:  C'(u) × d
        n = np.outer(tan[:, 1], e1) - np.outer(tan[:, 0], e2)
        return np.einsum("ij,ij->i", n, nrm) >= cos_lim
    rz, rad = _rz(pts, p0, axis)
    _, _, i = prof.nearest(rz)
    tan = prof.tan[i]                               # (dr, dz) along the profile
    er = rad / np.maximum(np.linalg.norm(rad, axis=1, keepdims=True), 1e-12)
    # natural normal of S(u, v) = p0 + z(v)·axis + r(v)·e_r(u):  z'·e_r − r'·axis
    n = er * tan[:, 1:2] - np.outer(tan[:, 0], axis)
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
    return np.einsum("ij,ij->i", n, nrm) >= cos_lim


def project(prim, P):
    """Nearest points on the swept surface (same angle about the axis / same height)."""
    _, p0, axis, prof, _ = prim
    P = np.atleast_2d(np.asarray(P, float))
    if prim[0] == "ext":
        e1, e2 = _frame(axis)
        xy, h = _xyh(P, p0, axis)
        _, q, _ = prof.nearest(xy)
        return p0 + np.outer(q[:, 0], e1) + np.outer(q[:, 1], e2) + np.outer(h, axis)
    rz, rad = _rz(P, p0, axis)
    _, q, _ = prof.nearest(rz)
    er = rad / np.maximum(np.linalg.norm(rad, axis=1, keepdims=True), 1e-12)
    return p0 + np.outer(q[:, 1], axis) + er * q[:, 0:1]


class RevSurf:
    """The OCC surface of revolution for one patch of a lathe primitive, together with what
    is needed to work in its own (u = angle, v = profile parameter) coordinates.

    The seam (where angle 0 meets 360°) is turned to face away from the patch, an open
    profile is cut down to the stretch the patch uses, and a closed profile's own start
    point is moved away from the patch too."""

    def __init__(self, prim, pts=None, towards=None):
        from OCP.Geom import Geom_BSplineCurve, Geom_SurfaceOfRevolution
        from OCP.collections import Array1_gp_Pnt, Array1_double, Array1_int
        from OCP.gp import gp_Pnt, gp_Ax1, gp_Dir
        _, p0, axis, prof, _ = prim
        self.prim, self.prof = prim, prof
        self.p0 = np.asarray(p0, float)
        self.axis = axis = np.asarray(axis, float)
        e1, e2 = _frame(axis)
        xd = e1
        centre = None
        if pts is not None and len(pts):
            centre = np.asarray(pts, float)
        elif towards is not None:
            centre = np.asarray(towards, float)[None]
        if centre is not None:
            d = centre - self.p0
            d = d - np.outer(d @ axis, axis)
            # mean direction of the patch about the axis (a patch may wrap a long way round)
            ang = np.arctan2(d @ e2, d @ e1)
            m = math.atan2(np.sin(ang).mean(), np.cos(ang).mean())
            xd = -(math.cos(m) * e1 + math.sin(m) * e2)
        self.xd = xd
        self.yd = np.cross(axis, xd)                     # angle u runs from xd towards yd
        self.kind = prof.analytic[0] if prof.analytic else "rev"
        self.flip = bool(prof.flip) if prof.analytic else False
        self.period = 0.0
        self.u_period, self.v_period = 2 * np.pi, 0.0   # u is an angle; v repeats only on a closed profile
        self.u_lim = (0.0, 2 * np.pi)                    # a face must stay clear of the seam
        self.u_deg = True
        if prof.analytic:
            from OCP.Geom import Geom_CylindricalSurface, Geom_ConicalSurface, Geom_SphericalSurface
            from OCP.gp import gp_Ax3
            an = prof.analytic
            z_ref = an[2] if self.kind == "cone" else (an[1] if self.kind == "sph" else 0.0)
            self.loc = self.p0 + z_ref * axis
            ax3 = gp_Ax3(gp_Pnt(*map(float, self.loc)), gp_Dir(*map(float, axis)), gp_Dir(*map(float, xd)))
            if self.kind == "cyl":
                self.surf = Geom_CylindricalSurface(ax3, float(an[1]))
            elif self.kind == "cone":
                self.surf = Geom_ConicalSurface(ax3, math.atan2(an[3], an[4]), float(an[1]))
            else:
                self.surf = Geom_SphericalSurface(ax3, float(an[2]))
            self.v0, self.v1 = (-0.5 * np.pi, 0.5 * np.pi) if self.kind == "sph" else (-np.inf, np.inf)
            return
        t, c, k = prof.tck
        n = len(c[0])
        if prof.closed:
            m_ = n - k
            r_, z_ = np.asarray(c[0][:m_]), np.asarray(c[1][:m_])
            knots = np.asarray(t[k:n + 1]); mults = np.ones(len(knots), int)
        else:
            r_, z_ = np.asarray(c[0]), np.asarray(c[1])
            knots, mults = np.unique(t, return_counts=True)
        poles = self.p0 + np.outer(z_, axis) + np.outer(r_, xd)
        ap = Array1_gp_Pnt(1, len(poles))
        for i, q in enumerate(poles):
            ap.SetValue(i + 1, gp_Pnt(float(q[0]), float(q[1]), float(q[2])))
        ak = Array1_double(1, len(knots)); am = Array1_int(1, len(knots))
        for i in range(len(knots)):
            ak.SetValue(i + 1, float(knots[i])); am.SetValue(i + 1, int(mults[i]))
        cur = Geom_BSplineCurve(ap, ak, am, int(k), bool(prof.closed))
        self.period = self.v_period = (prof.t1 - prof.t0) if prof.closed else 0.0
        self.v0, self.v1 = prof.t0, prof.t1
        if centre is not None and len(centre) >= 3:
            rz, _ = _rz(centre, self.p0, axis)
            if prof.closed:
                # start the closed profile as far as possible from the patch
                _, _, i = prof.nearest(rz)
                used = np.zeros(len(prof.ts), bool); used[i] = True
                gaps = np.flatnonzero(~used)
                if len(gaps):
                    far = gaps[np.argmax(cKDTree(prof.rz[used]).query(prof.rz[gaps])[0])]
                    j = int(np.argmin(np.abs(knots[:-1] - prof.ts[far]))) + 1
                    try:
                        cur.SetOrigin(j)
                        self.v0 = float(cur.FirstParameter()); self.v1 = float(cur.LastParameter())
                    except Exception:
                        pass
            else:
                # keep only the stretch of profile this patch lies on (plus a margin): the
                # rest may run into the axis, where a lathe surface folds onto itself
                tv = prof.nearest_t(rz)
                pad = 0.05 * float(tv.max() - tv.min()) + 0.01 * (prof.t1 - prof.t0)
                a_, b_ = max(prof.t0, float(tv.min()) - pad), min(prof.t1, float(tv.max()) + pad)
                if b_ - a_ > 1e-9 and (a_ > prof.t0 or b_ < prof.t1):
                    try:
                        cur.Segment(a_, b_)
                        self.v0, self.v1 = a_, b_
                    except Exception:
                        pass
        self.curve = cur
        self.surf = Geom_SurfaceOfRevolution(cur, gp_Ax1(gp_Pnt(*map(float, self.p0)), gp_Dir(*map(float, axis))))

    def uv(self, P):
        """(u, v) of the nearest surface point for each 3-D point; u in [0, 2π)."""
        rz, rad = _rz(P, self.p0, self.axis)
        u = np.arctan2(rad @ self.yd, rad @ self.xd) % (2 * np.pi)
        an = self.prof.analytic
        if self.kind == "cyl":
            return np.c_[u, rz[:, 1]]
        if self.kind == "cone":
            return np.c_[u, (rz[:, 0] - an[1]) * an[3] + (rz[:, 1] - an[2]) * an[4]]
        if self.kind == "sph":
            return np.c_[u, np.arctan2(rz[:, 1] - an[1], rz[:, 0])]
        v = self.prof.nearest_t(rz)
        if self.period:
            v = self.v0 + (v - self.v0) % self.period
        return np.c_[u, np.clip(v, self.v0, self.v1)]

    def point(self, uv):
        """3-D points of the surface at (u, v)."""
        uv = np.atleast_2d(uv)
        an = self.prof.analytic
        v = uv[:, 1]
        if self.kind == "cyl":
            rz = np.c_[np.full(len(v), an[1]), v]
        elif self.kind == "cone":
            rz = np.c_[an[1] + v * an[3], an[2] + v * an[4]]
        elif self.kind == "sph":
            rz = np.c_[an[2] * np.cos(v), an[1] + an[2] * np.sin(v)]
        else:
            rz = self.prof.at(v)
        er = np.outer(np.cos(uv[:, 0]), self.xd) + np.outer(np.sin(uv[:, 0]), self.yd)
        return self.p0 + np.outer(rz[:, 1], self.axis) + er * rz[:, 0:1]


class ExtSurf:
    """The OCC surface of linear extrusion for one patch of an extruded primitive, with its
    own coordinates: u = position along the profile curve, v = height along the direction.
    An open profile is cut down to the stretch the patch uses; a closed one has its start
    point moved away from the patch."""

    def __init__(self, prim, pts=None, towards=None):
        from OCP.Geom import Geom_BSplineCurve, Geom_SurfaceOfLinearExtrusion
        from OCP.collections import Array1_gp_Pnt, Array1_double, Array1_int
        from OCP.gp import gp_Pnt, gp_Dir
        _, o, d, prof, _ = prim
        self.prim, self.prof = prim, prof
        self.o = np.asarray(o, float)
        self.d = d = np.asarray(d, float)
        self.e1, self.e2 = _frame(d)
        self.kind, self.flip = "ext", False
        self.u_deg = False
        self.v_period = self.period = 0.0
        t, c, k = prof.tck
        n = len(c[0])
        if prof.closed:
            m_ = n - k
            x_, y_ = np.asarray(c[0][:m_]), np.asarray(c[1][:m_])
            knots = np.asarray(t[k:n + 1]); mults = np.ones(len(knots), int)
        else:
            x_, y_ = np.asarray(c[0]), np.asarray(c[1])
            knots, mults = np.unique(t, return_counts=True)
        poles = self.o + np.outer(x_, self.e1) + np.outer(y_, self.e2)
        ap = Array1_gp_Pnt(1, len(poles))
        for i, q in enumerate(poles):
            ap.SetValue(i + 1, gp_Pnt(float(q[0]), float(q[1]), float(q[2])))
        ak = Array1_double(1, len(knots)); am = Array1_int(1, len(knots))
        for i in range(len(knots)):
            ak.SetValue(i + 1, float(knots[i])); am.SetValue(i + 1, int(mults[i]))
        cur = Geom_BSplineCurve(ap, ak, am, int(k), bool(prof.closed))
        self.u_period = (prof.t1 - prof.t0) if prof.closed else 0.0
        self.u0, self.u1 = prof.t0, prof.t1
        centre = np.asarray(pts, float) if pts is not None and len(pts) else None
        if centre is not None and len(centre) >= 3:
            xy, _ = _xyh(centre, self.o, d)
            if prof.closed:
                _, _, i = prof.nearest(xy)
                used = np.zeros(len(prof.ts), bool); used[i] = True
                gaps = np.flatnonzero(~used)
                if len(gaps):
                    far = gaps[np.argmax(cKDTree(prof.rz[used]).query(prof.rz[gaps])[0])]
                    j = int(np.argmin(np.abs(knots[:-1] - prof.ts[far]))) + 1
                    try:
                        cur.SetOrigin(j)
                        self.u0 = float(cur.FirstParameter()); self.u1 = float(cur.LastParameter())
                    except Exception:
                        pass
            else:
                tv = prof.nearest_t(xy)
                pad = 0.05 * float(tv.max() - tv.min()) + 0.01 * (prof.t1 - prof.t0)
                a_, b_ = max(prof.t0, float(tv.min()) - pad), min(prof.t1, float(tv.max()) + pad)
                if b_ - a_ > 1e-9 and (a_ > prof.t0 or b_ < prof.t1):
                    try:
                        cur.Segment(a_, b_)
                        self.u0, self.u1 = a_, b_
                    except Exception:
                        pass
        self.u_lim = (self.u0, self.u1) if prof.closed else None
        self.curve = cur
        self.surf = Geom_SurfaceOfLinearExtrusion(cur, gp_Dir(*map(float, d)))

    def uv(self, P):
        xy, h = _xyh(P, self.o, self.d)
        u = self.prof.nearest_t(xy)
        if self.u_period:
            u = self.u0 + (u - self.u0) % self.u_period
        return np.c_[np.clip(u, self.u0, self.u1), h]

    def point(self, uv):
        uv = np.atleast_2d(uv)
        xy = self.prof.at(uv[:, 0])
        return self.o + np.outer(xy[:, 0], self.e1) + np.outer(xy[:, 1], self.e2) + np.outer(uv[:, 1], self.d)


def _surf(prim, pts=None, towards=None):
    return ExtSurf(prim, pts, towards) if prim[0] == "ext" else RevSurf(prim, pts, towards)


def _unwrap(a, period, axis=-1):
    """One continuous branch of a coordinate that repeats every `period` (0 = it doesn't)."""
    if not period:
        return a
    k = 2 * np.pi / period
    return np.unwrap(a * k, axis=axis) / k


def geom(prim, towards=None, pts=None):
    """OCC surface for this swept primitive and whether faces on it must be flipped."""
    S = _surf(prim, pts, towards)
    return S.surf, S.flip


def _edge_pcurve(edge, S):
    """This edge's curve in the lathe surface's own (u, v) coordinates, worked out directly
    rather than by OCC's general projection (which goes wrong near the axis and on wavy
    profiles). Returns (2-D curve, largest distance between the edge and the surface)."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GeomAbs import GeomAbs_Line
    from OCP.Geom2d import Geom2d_BSplineCurve
    from OCP.Geom2dAPI import Geom2dAPI_Interpolate
    from OCP.collections import Array1_gp_Pnt2d, Array1_double, Array1_int, HArray1_gp_Pnt2d, HArray1_double
    from OCP.gp import gp_Pnt2d
    c = BRepAdaptor_Curve(edge)
    f, l = c.FirstParameter(), c.LastParameter()
    straight = c.GetType() == GeomAbs_Line

    def pts3(ts):
        if straight:
            a, b = c.Value(f), c.Value(l)
            A = np.array([a.X(), a.Y(), a.Z()]); Bp = np.array([b.X(), b.Y(), b.Z()])
            w = ((np.asarray(ts) - f) / max(l - f, 1e-300))[:, None]
            return A + (Bp - A) * w
        out = np.empty((len(ts), 3))
        for i, t in enumerate(ts):
            p = c.Value(float(t)); out[i] = (p.X(), p.Y(), p.Z())
        return out

    ends = pts3(np.array([f, l]))
    uv_e = S.uv(ends)
    du = abs(uv_e[1, 0] - uv_e[0, 0])
    if S.u_period:
        du = min(du, S.u_period - du)
    length = float(np.linalg.norm(ends[1] - ends[0]))
    n = 2 + (int(math.ceil(math.degrees(du) / 3.0)) if S.u_deg else 0) + \
        int(math.ceil(length / max(S.prof.length / 100.0, 1e-9)))
    if not straight:
        n += 4
    n = int(min(max(n, 3), 60))
    ts = np.linspace(f, l, n)
    P = pts3(ts)
    uv = S.uv(P)
    # one continuous branch of each repeating coordinate along the edge
    uv[:, 0] = _unwrap(uv[:, 0], S.u_period)
    uv[:, 1] = _unwrap(uv[:, 1], S.v_period)
    pc = None
    step = np.linalg.norm(np.diff(uv, axis=0), axis=1)
    if n > 2 and step.min() > 1e-10:
        try:
            hp = HArray1_gp_Pnt2d(1, n); hk = HArray1_double(1, n)
            for i in range(n):
                hp.SetValue(i + 1, gp_Pnt2d(float(uv[i, 0]), float(uv[i, 1]))); hk.SetValue(i + 1, float(ts[i]))
            it = Geom2dAPI_Interpolate(hp, hk, False, 1e-10)
            it.Perform()
            if it.IsDone():
                pc = it.Curve()
        except Exception:
            pc = None
    if pc is None:
        ap = Array1_gp_Pnt2d(1, 2); ak = Array1_double(1, 2); am = Array1_int(1, 2)
        for i, j in ((1, 0), (2, n - 1)):
            ap.SetValue(i, gp_Pnt2d(float(uv[j, 0]), float(uv[j, 1])))
            ak.SetValue(i, float(ts[j])); am.SetValue(i, 2)
        pc = Geom2d_BSplineCurve(ap, ak, am, 1)
    # how far apart the edge and its curve on the surface really are (also between samples)
    tm = np.unique(np.r_[ts, 0.5 * (ts[:-1] + ts[1:]), np.linspace(f, l, 23)])   # (OCC checks at 23)
    Q = pts3(tm)
    uvm = np.empty((len(tm), 2))
    for i, t in enumerate(tm):
        q = pc.Value(float(t)); uvm[i] = (q.X(), q.Y())
    dev = float(np.linalg.norm(S.point(uvm) - Q, axis=1).max())
    return pc, dev, uv


def _outline(B, r, info):
    """Every outline edge of a lathe patch with its curve in (u, v): a list per loop of
    (chain index, reversed, [(edge, 2-D curve, deviation, uv samples), ...])."""
    ctx = B.ctx
    S = info.get("_rev")
    if S is None:
        S = info["_rev"] = _surf(info["prim"], ctx.V[np.unique(ctx.F[info["faces"]])])
    cache = B.__dict__.setdefault("_rev_pc", {})
    out = []
    for lp in B.region_loops.get(r, []):
        loop = []
        for ci, rev in lp:
            key = (r, ci)
            if key not in cache or cache[key][0] is not B.edges[ci]:
                ch, es = B.chains[ci], B.edges[ci]
                items = None
                if ch.get("kind") == "polyline" and len(es) == len(ch["verts"]) - 1:
                    items = _polyline_pcurves(es, ctx.V[ch["verts"]], S)
                if items is None:
                    items = [(e,) + _edge_pcurve(e, S) for e in es]
                cache[key] = (es, items)
            loop.append((ci, rev, cache[key][1]))
        out.append(loop)
    return S, out


def _polyline_pcurves(edges, P, S):
    """(u, v) curves for a run of straight edges between the points P, all at once. A short
    straight edge maps to a straight line in (u, v) to well within its own distance from the
    surface; edges that swing more than 5° round the axis take the general route."""
    from OCP.Geom2d import Geom2d_BSplineCurve
    from OCP.collections import Array1_gp_Pnt2d, Array1_double, Array1_int
    from OCP.gp import gp_Pnt2d
    m = len(edges)
    nf = 23                                                                    # (where OCC checks)
    fr = np.linspace(0.0, 1.0, nf)
    A, Bp = P[:-1], P[1:]
    pts = A[:, None, :] + (Bp - A)[:, None, :] * fr[None, :, None]            # m × 23 × 3
    uv = S.uv(pts.reshape(-1, 3)).reshape(m, nf, 2)
    uv[:, :, 0] = _unwrap(uv[:, :, 0], S.u_period, axis=1)
    uv[:, :, 1] = _unwrap(uv[:, :, 1], S.v_period, axis=1)
    lin = uv[:, :1, :] + (uv[:, -1:, :] - uv[:, :1, :]) * fr[None, :, None]
    dev = np.linalg.norm(S.point(lin.reshape(-1, 2)).reshape(m, nf, 3) - pts, axis=2).max(1)
    # the edge's own distance from the surface: the straight (u, v) line must not add much to it
    own = np.linalg.norm(S.point(uv.reshape(-1, 2)).reshape(m, nf, 3) - pts, axis=2).max(1)
    length = np.linalg.norm(Bp - A, axis=1)
    simple = (length > 1e-12) & (dev <= 1.5 * own + 1e-4)
    if S.u_deg:
        simple &= np.abs(uv[:, -1, 0] - uv[:, 0, 0]) <= math.radians(5.0)
    am = Array1_int(1, 2); am.SetValue(1, 2); am.SetValue(2, 2)
    items = []
    for i, e in enumerate(edges):
        if not simple[i]:
            items.append((e,) + _edge_pcurve(e, S))
            continue
        ap = Array1_gp_Pnt2d(1, 2); ak = Array1_double(1, 2)
        ap.SetValue(1, gp_Pnt2d(float(uv[i, 0, 0]), float(uv[i, 0, 1])))
        ap.SetValue(2, gp_Pnt2d(float(uv[i, -1, 0]), float(uv[i, -1, 1])))
        ak.SetValue(1, 0.0); ak.SetValue(2, float(length[i]))
        items.append((e, Geom2d_BSplineCurve(ap, ak, am, 1), float(dev[i]), lin[i][[0, -1]]))
    return items


def _crossing_chains(outline):
    """Chains of a lathe patch whose (u, v) curves cross another outline edge (or themselves)
    anywhere but at a shared end point."""
    from shapely.geometry import LineString
    from shapely.strtree import STRtree
    lines, owner, ends = [], [], []
    for li, loop in enumerate(outline):
        for ci, rev, items in loop:
            for e, pc, dev, uv in items:
                if len(uv) < 2 or np.linalg.norm(np.ptp(uv, axis=0)) < 1e-12:
                    continue
                lines.append(LineString(uv)); owner.append(ci); ends.append((uv[0], uv[-1]))
    bad = set()
    if len(lines) < 2:
        return bad
    for i, ln in enumerate(lines):
        if not ln.is_simple:
            bad.add(owner[i])
    tree = STRtree(lines)
    span = max(float(np.ptp([p for l in lines for p in l.coords], axis=0).max()), 1e-9)
    eps = 1e-7 * span
    for i, ln in enumerate(lines):
        for j in tree.query(ln):
            j = int(j)
            if j <= i or not ln.intersects(lines[j]):
                continue
            inter = ln.intersection(lines[j])
            # touching only at a common end point is how neighbouring edges are meant to meet
            common = [p for p in ends[i] if min(np.linalg.norm(p - q) for q in ends[j]) <= eps]
            if inter.geom_type == "Point" and common and \
                    min(np.linalg.norm(np.array(inter.coords[0]) - p) for p in common) <= eps:
                continue
            if inter.geom_type == "MultiPoint" and len(common) == 2 and len(inter.geoms) == 2 and \
                    all(min(np.linalg.norm(np.array(g.coords[0]) - p) for p in common) <= eps for g in inter.geoms):
                continue                                 # two edges that close a loop between them
            bad.add(owner[i]); bad.add(owner[j])
    return bad


def fix_outlines(B):
    """Before any face is built: if the fitted boundary curves of a lathe patch cross each
    other in the patch's own (u, v) coordinates, put the offending chains back to straight
    segments between their mesh corners (a mesh outline never crosses itself)."""
    n_fixed = 0
    for ri, r in enumerate(B.regions):
        if r.get("type") != "prim" or r.get("prim") is None or r["prim"][0] not in SWEPT:
            continue
        for _ in range(3):
            try:
                S, outline = _outline(B, ri, r)
                bad = _crossing_chains(outline)
            except Exception:
                break
            changed = [ci for ci in bad if B.demote_chain(ci)]
            n_fixed += len(changed)
            if not changed:
                break
    STATS["demoted"] = STATS.get("demoted", 0) + n_fixed
    return n_fixed


def trimmed_face(B, r, info):
    """Build the STEP face for one lathe patch: the surface of revolution, cut by the
    patch's outline. Every outline edge gets its curve in the surface's (u, v) coordinates
    computed here, so the face needs no repair pass. Returns None if that doesn't work out
    (the caller then falls back to the general route)."""
    from OCP.BRep import BRep_Builder
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.TopoDS import TopoDS_Face, TopoDS_Vertex, TopoDS_Wire
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopExp import TopExp
    loops = B.region_loops.get(r, [])
    if not loops:
        return None
    S, outline = _outline(B, r, info)
    bb = BRep_Builder()
    loc = TopLoc_Location()
    face = TopoDS_Face()
    bb.MakeFace(face, S.surf, 1e-7)
    u_lo, u_hi = np.inf, -np.inf
    worst = 0.0
    for lp, loop in zip(loops, outline):
        for ci, rev, items in loop:
            for e, pc, dev, uv in items:
                u_lo = min(u_lo, float(uv[:, 0].min())); u_hi = max(u_hi, float(uv[:, 0].max()))
                tol_e = 1.25 * dev + 1e-6
                bb.UpdateEdge(e, pc, S.surf, loc, tol_e)
                v1, v2 = TopoDS_Vertex(), TopoDS_Vertex()
                TopExp.Vertices_s(e, v1, v2)
                for v in (v1, v2):
                    if not v.IsNull():
                        bb.UpdateVertex(v, tol_e)
                worst = max(worst, dev)
        # the wire is put together by hand: the usual wire maker merges corners that lie
        # within tolerance of each other, which scrambles outlines with very short edges
        w = TopoDS_Wire()
        bb.MakeWire(w)
        if S.flip:         # the surface's own normal points into the part: build it mirrored
            lp = [(ci, not rev) for ci, rev in reversed(lp)]
        for ci, rev in lp:
            es = B.edges[ci]
            for e in (reversed(es) if rev else es):
                bb.Add(w, e.Reversed() if rev else e)
        w.Closed(True)
        bb.Add(face, w)
    info["_edge_dev"] = worst
    if S.u_lim is not None:
        gap = 0.02 if S.u_deg else 0.003 * (S.u_lim[1] - S.u_lim[0])
        if u_lo < S.u_lim[0] + gap or u_hi > S.u_lim[1] - gap:
            STATS["seam"] += 1
            return None                                  # reaches the seam: not handled here
    if not BRepCheck_Analyzer(face).IsValid():
        STATS["invalid"] += 1
        return None
    STATS["direct"] += 1
    STATS[S.kind] = STATS.get(S.kind, 0) + 1
    return face.Reversed() if S.flip else face


# ---------------------------------------------------------------- detection
def line_geometry(C, N, w, iters=4):
    """Best rotation/screw axis for a set of surface samples (middle C, normal N, weight w).
    Returns (axis direction, point on axis, pitch, per-sample misfit, scale) or None.
    The misfit is unitless: about the sine of the angle by which a normal line misses the axis."""
    wsum = float(w.sum())
    if wsum <= 0 or len(C) < 12:
        return None
    ctr = (C * w[:, None]).sum(0) / wsum
    X = C - ctr
    scale = math.sqrt(float((w * (X * X).sum(1)).sum() / wsum)) or 1.0
    X = X / scale
    Lm = np.c_[np.cross(X, N), N]                      # (moment, direction) of each normal line
    rho = np.ones(len(C))
    sol = None
    for _ in range(iters):
        ww = w * rho
        M = (Lm * ww[:, None]).T @ Lm / max(float(ww.sum()), 1e-30)
        A11, A12, A22 = M[:3, :3], M[:3, 3:], M[3:, 3:]
        # minimise x^T M x over x = (c, cbar) with |c| = 1: eliminate cbar
        A22i = np.linalg.pinv(A22)
        S = A11 - A12 @ A22i @ A12.T
        ev, evec = np.linalg.eigh(S)
        c = evec[:, 0]
        cbar = -A22i @ A12.T @ c
        f = np.abs(Lm @ np.r_[c, cbar])
        sol = (c, cbar, f, ev)
        rho = 1.0 / (1.0 + 400.0 * f * f)                # down-weight what doesn't share the motion
    c, cbar, f, ev = sol
    pitch = float(c @ cbar)                              # |c| = 1
    axis = c
    pt = np.cross(c, cbar - pitch * c)                   # point on the axis (normalised coords)
    return axis, pt * scale + ctr, pitch * scale, f, scale


def _cut_chain(V, F, s, keep=None):
    """Cut triangles F with the plane where the per-corner value s changes sign; return the
    longest connected polyline of the cut (3-D points in order) and whether it closes on
    itself. `keep(point)` may reject cut points (e.g. the wrong half of the plane)."""
    sf = s[F]
    cross = (sf.min(1) < 0) & (sf.max(1) >= 0)
    tris = F[cross]
    if len(tris) < 3:
        return None, False
    segs = {}
    pts = {}
    for tri in tris:
        ends = []
        for a, b in ((tri[0], tri[1]), (tri[1], tri[2]), (tri[2], tri[0])):
            sa, sb = s[a], s[b]
            if (sa < 0) != (sb < 0):
                key = (min(a, b), max(a, b))
                if key not in pts:
                    t = sa / (sa - sb)
                    pts[key] = V[a] + (V[b] - V[a]) * t
                ends.append(key)
        if len(ends) == 2:
            segs.setdefault(ends[0], []).append(ends[1])
            segs.setdefault(ends[1], []).append(ends[0])
    if keep is not None:
        ok = {k for k, p in pts.items() if keep(p)}
        segs = {k: [x for x in v if x in ok] for k, v in segs.items() if k in ok}
    best, best_closed = None, False
    seen = set()
    for start in list(segs):
        if start in seen:
            continue
        # walk to one end first
        comp = [start]; seen.add(start)
        stack = [start]
        while stack:
            k = stack.pop()
            for x in segs.get(k, []):
                if x not in seen:
                    seen.add(x); comp.append(x); stack.append(x)
        ends = [k for k in comp if len(segs.get(k, [])) == 1]
        cur = ends[0] if ends else comp[0]
        order = [cur]; used = {cur}
        while True:
            nxt = [x for x in segs.get(cur, []) if x not in used]
            if not nxt:
                break
            cur = nxt[0]; order.append(cur); used.add(cur)
        closed = (not ends) and len(order) > 2 and order[0] in segs.get(order[-1], [])
        if best is None or len(order) > len(best):
            best, best_closed = order, closed
    if best is None or len(best) < 3:
        return None, False
    return np.array([pts[k] for k in best]), best_closed


def _meridian_chain(V, F, p0, axis, angle):
    """Cut the triangles with the half-plane through the axis at `angle`; return the longest
    connected polyline of the cut as (r, z) points in order, and whether it closes on itself."""
    e1, e2 = _frame(axis)
    u = math.cos(angle) * e1 + math.sin(angle) * e2      # in-plane radial direction
    m = np.cross(axis, u)                                # plane normal
    d = V - p0
    side = d @ u
    F = F[side[F].max(1) > 0]
    P, closed = _cut_chain(V, F, d @ m, keep=lambda p: (p - p0) @ u > 0)
    if P is None:
        return None, False
    return _rz(P, p0, axis)[0], closed


def _fit_profile(rz_pts, chain, closed, tol, slack=0.0, extend=0.0, allow_negative=False):
    """Spline profile through the corners' (r, z), ordered by where each falls along the cut
    `chain`. The smoothest spline that keeps (nearly) every corner within tol/2."""
    from scipy.interpolate import splprep
    # arc-length parameter of the chain, then each data point's parameter by projection
    q = np.vstack([chain, chain[:1]]) if closed else chain
    seg = np.linalg.norm(np.diff(q, axis=0), axis=1)
    keep = np.r_[True, seg > 1e-12]
    q = q[keep]
    if not closed and extend > 0 and len(q) >= 3:
        # The cut stops at the region's edge at one height; corners at other heights can sit
        # a little further along. Carry the cut on straight at both ends so those corners
        # get a place along the profile instead of all piling up at its end.
        d0 = q[0] - q[1]; d1 = q[-1] - q[-2]
        d0 /= max(np.linalg.norm(d0), 1e-12); d1 /= max(np.linalg.norm(d1), 1e-12)
        a_, b_ = q[0] + extend * d0, q[-1] + extend * d1
        if allow_negative or a_[0] > 0:                  # (a lathe profile must not cross its axis)
            q = np.vstack([a_, q])
        if allow_negative or b_[0] > 0:
            q = np.vstack([q, b_])
    seg = np.linalg.norm(np.diff(q, axis=0), axis=1)
    s = np.r_[0.0, np.cumsum(seg)]
    total = float(s[-1])
    if total <= 0 or len(q) < 4:
        return None
    # dense resample of the chain for nearest-parameter lookup
    n_d = int(min(20000, max(400, 8 * len(q))))
    sd = np.linspace(0, total, n_d)
    qd = np.c_[np.interp(sd, s, q[:, 0]), np.interp(sd, s, q[:, 1])]
    dist, j = cKDTree(qd).query(rz_pts)
    # corners that the cut didn't reach (it was interrupted by a hole) would all pile up at
    # its ends: leave them out, the surface simply won't claim them
    near = dist <= 4.0 * tol + slack
    if near.sum() < 12:
        return None
    rz_pts = rz_pts[near]
    t = sd[j[near]]
    # thin: average the data in small steps along the profile
    n_bins = int(min(1500, max(40, len(rz_pts) // 6)))
    edges = np.linspace(0, total, n_bins + 1)
    b = np.clip(np.searchsorted(edges, t, side="right") - 1, 0, n_bins - 1)
    cnt = np.bincount(b, minlength=n_bins)
    ok = cnt > 0
    if ok.sum() < 6:
        return None
    tb = np.bincount(b, t, minlength=n_bins)[ok] / cnt[ok]
    rb = np.bincount(b, rz_pts[:, 0], minlength=n_bins)[ok] / cnt[ok]
    zb = np.bincount(b, rz_pts[:, 1], minlength=n_bins)[ok] / cnt[ok]
    data = np.c_[rb, zb]
    if closed:
        data = np.vstack([data, data[:1]]); tb = np.r_[tb, tb[0] + total]
    if np.any(np.diff(tb) <= 0):
        o = np.argsort(tb); tb = tb[o]; data = data[o]
        good = np.r_[True, np.diff(tb) > 0]
        tb, data = tb[good], data[good]
    if len(tb) < 6:
        return None
    # Smoothest spline that explains about as many corners as any spline can. Corners that
    # don't belong (neighbouring features caught by the loose first test, mesh noise) must
    # not drag the curve, so they are dropped and the fit repeated.
    def fits(d_t, d_pts):
        trial = []
        for sfac in np.geomspace(1.0, 0.003, 14) * tol:
            try:
                with warnings.catch_warnings():          # (the tight end of the ladder often can't
                    warnings.simplefilter("ignore")      #  be reached: that is expected, not news)
                    tck, _ = splprep(d_pts.T, u=d_t, per=closed, s=len(d_t) * sfac ** 2, k=3, quiet=2)
            except Exception:
                continue
            prof = Profile(tck, closed)
            d = prof.nearest(rz_pts)[0]
            inl = d <= 0.8 * tol
            rms = float(np.sqrt((d[inl] ** 2).mean())) if inl.any() else float("inf")
            trial.append((float(inl.mean()), prof, d, rms))
        return trial

    def choose(trial):
        """Among the splines about as tight as the tightest, the one with the best balance of
        corners explained against wiggle. Clean CAD data gets a tight curve; noisy data a
        smooth one (a spline that chases the noise would make a rippled surface)."""
        top = max(x[0] for x in trial)
        tight = min(x[3] for x in trial if x[0] >= top - 0.01)
        okay = [x for x in trial if x[3] <= max(2.0 * tight, 0.05 * tol) and x[0] >= top - 0.05]
        # a curve that bends far more than the smoothest acceptable one is chasing noise: it
        # would be a rippled surface that is slow to draw and meshes into tens of thousands
        # of triangles
        calm = min(x[1].turning for x in okay)
        okay = [x for x in okay if x[1].turning <= 1.5 * calm + 0.35]
        return min(okay, key=lambda x: (1.0 - x[0]) + 0.02 * x[1].control_points() / max(len(chain), 1))

    trial = fits(tb, data)
    if _TRACE is not None:
        _TRACE.append([(x[0], x[3], x[1].control_points(), float(np.percentile(x[2], 99))) for x in trial])
    if not trial:
        return None
    if max(x[0] for x in trial) < 0.5:
        return None
    frac, prof, d, _ = choose(trial)
    for _ in range(2):
        inl = d <= tol
        if inl.all() or inl.sum() < 12:
            break
        bi = b[inl]
        cnt2 = np.bincount(bi, minlength=n_bins)
        ok2 = cnt2 > 0
        if ok2.sum() < 6:
            break
        tb2 = np.bincount(bi, t[inl], minlength=n_bins)[ok2] / cnt2[ok2]
        data2 = np.c_[np.bincount(bi, rz_pts[inl, 0], minlength=n_bins)[ok2] / cnt2[ok2],
                      np.bincount(bi, rz_pts[inl, 1], minlength=n_bins)[ok2] / cnt2[ok2]]
        if closed:
            data2 = np.vstack([data2, data2[:1]]); tb2 = np.r_[tb2, tb2[0] + total]
        if np.any(np.diff(tb2) <= 0):
            break
        trial2 = fits(tb2, data2)
        if not trial2:
            break
        cand = choose(trial2)
        if cand[0] + 1e-9 < frac:
            break
        frac, prof, d, _ = cand
    return prof


def _off_axis(tri, p0, axis, rmax):
    """Which triangles (n×3×3 corners) keep clear of the axis: none of their corners close
    to it, and they don't swing more than 40° round it."""
    rz_c, rad_c = _rz(tri.reshape(-1, 3), p0, axis)
    r_face_min = rz_c[:, 0].reshape(-1, 3).min(1)
    er = (rad_c / np.maximum(rz_c[:, 0:1], 1e-12)).reshape(-1, 3, 3)
    cos_span = np.minimum(np.minimum((er[:, 0] * er[:, 1]).sum(1), (er[:, 1] * er[:, 2]).sum(1)),
                          (er[:, 2] * er[:, 0]).sum(1))
    return (r_face_min > 0.01 * rmax) & (cos_span > math.cos(math.radians(40.0)))


def _one_revolve(ctx, ids, tol, min_faces=40, grow_tol=None):
    C, N, w = ctx.C[ids], ctx.N[ids], ctx.area[ids]
    lg = line_geometry(C, N, w)
    if lg is None:
        return None
    axis, p0, pitch, f, scale = lg
    if abs(pitch) > 0.05 * scale:
        return None                                     # a screw motion, not a lathe shape
    cand = f < 0.05
    if cand.sum() < min_faces or w[cand].sum() < 0.15 * w.sum():
        return None
    cids = ids[cand]
    Fc = ctx.F[cids]
    vids = np.unique(Fc)
    V = ctx.V
    rz_all, _ = _rz(V[vids], p0, axis)
    rmax = float(rz_all[:, 0].max())
    if rmax <= 0:
        return None
    # the cut that gives the longest unbroken profile
    best = None
    for ang in np.linspace(0, 2 * np.pi, 12, endpoint=False):
        chain, closed = _meridian_chain(V, Fc, p0, axis, ang)
        if chain is None:
            continue
        ln = float(np.linalg.norm(np.diff(chain, axis=0), axis=1).sum())
        if best is None or ln > best[0]:
            best = (ln, chain, closed)
    if best is None:
        return None
    _, chain, closed = best
    tri_all = V[ctx.F[ids]]
    edge = float(np.median(np.linalg.norm(tri_all[:, [1, 2, 0]] - tri_all, axis=2).max(1)))
    prof = _fit_profile(rz_all, chain, closed, tol, slack=0.25 * edge, extend=edge + 2.0 * tol)
    if prof is None:
        return None
    prim = ("rev", p0, axis, prof, True)
    # Stay off the axis: a surface of revolution is singular where its profile touches it,
    # and a triangle that swings a long way round the axis has edges that cut far inside
    # the surface. (Coarse CAD cylinders with 9+ sides still pass.)
    got = _claim(ctx, ids, prim, tol, _off_axis(tri_all, p0, axis, rmax), min_faces, grow_tol)
    if got is None:
        return None
    ok, prim = got
    return ids[ok], prim


def _face_sag(ctx):
    """How far each flat triangle sits from the smooth surface it approximates (its chord
    sag), estimated per edge from the bend across that edge and the triangle's width square
    to it. (Using the edge's own length instead would overstate it for long thin triangles.)"""
    got = getattr(ctx, "_rough_sag", None)
    if got is not None and len(got) == ctx.nF:
        return got
    e = ctx.V[ctx.adj_edges[:, 0]] - ctx.V[ctx.adj_edges[:, 1]]
    L = np.maximum(np.linalg.norm(e, axis=1), 1e-12)
    ang = ctx.adj_angles()
    th = np.radians(np.where(ang < 30.0, ang, 0.0))       # (a sharp crease is an edge, not a bend)
    sag = np.zeros(ctx.nF)
    for side in (0, 1):
        f = ctx.adj[:, side]
        np.maximum.at(sag, f, (2.0 * ctx.area[f] / L) * th / 8.0)
    ctx._rough_sag = sag
    return sag


def _reversed(prim):
    """The same swept primitive with its profile running the other way (exact: the knot
    vector and the coefficients are mirrored)."""
    kind, p0, axis, prof, flag = prim
    t, c, k = prof.tck
    tk = np.asarray(t); tr = (tk[0] + tk[-1]) - tk[::-1]
    cr = [np.asarray(ci)[::-1].copy() for ci in c]
    return (kind, p0, axis, Profile((tr, cr, k), prof.closed), flag)


def _claim(ctx, ids, prim, tol, allowed, min_faces, grow_tol):
    """Which triangles of `ids` lie on the swept surface: corners within tolerance, middle
    not far off, facing the same way. Returns (mask over ids, primitive — its profile turned
    round if needed so the surface's own normal matches the mesh), or None."""
    C, N, w = ctx.C[ids], ctx.N[ids], ctx.area[ids]
    tri = ctx.V[ctx.F[ids]]
    res = np.array([residual(prim, tri[:, k]) for k in range(3)]).max(0)
    # Between its corners a flat triangle may sit off the true surface by its own chord sag
    # (a coarse mesh of a round shape) — but not by more: a surface that bows away from the
    # triangle's middle and edge middles further than that was fitted to too few points.
    sag = _face_sag(ctx)[ids]
    nxt = tri[:, [1, 2, 0]]
    mids = np.concatenate([tri + (nxt - tri) * f for f in (0.25, 0.5, 0.75)], axis=1)      # n × 9 × 3
    inner = np.maximum(residual(prim, C), residual(prim, mids.reshape(-1, 3)).reshape(-1, 9).max(1))
    inner_ok = inner <= tol + 2.0 * sag
    ok = (res <= tol) & inner_ok & allowed
    if ok.sum() < min_faces:
        return None
    agree = normal_ok(prim, C[ok], N[ok], 0.0)
    if (w[ok] * agree).sum() < 0.5 * w[ok].sum():
        prim = _reversed(prim)
    ok[ok] = normal_ok(prim, C[ok], N[ok], math.cos(math.radians(30.0)))
    if ok.sum() < min_faces:
        return None
    # Hysteresis: triangles that only just miss (mesh noise) but touch accepted ones and
    # still face the right way belong to the surface too. Without this the region is
    # peppered with pin-holes and ragged at its border, and every hole costs a patch.
    grow_tol = max(1.5 * tol, grow_tol or 0.0)
    near = (res <= grow_tol) & (inner <= grow_tol + 2.0 * sag) & allowed & ~ok
    if near.any():
        near[near] = normal_ok(prim, C[near], N[near], math.cos(math.radians(30.0)))
    if near.any():
        loc = -np.ones(ctx.nF, np.int64); loc[ids] = np.arange(len(ids))
        a0, a1 = loc[ctx.adj[:, 0]], loc[ctx.adj[:, 1]]
        both = (a0 >= 0) & (a1 >= 0)
        a0, a1 = a0[both], a1[both]
        cand = ok | near
        k = cand[a0] & cand[a1]
        g = coo_matrix((np.ones(k.sum()), (a0[k], a1[k])), shape=(len(ids), len(ids)))
        _, lab = connected_components(g, directed=False)
        good = np.zeros(lab.max() + 1, bool); good[np.unique(lab[ok])] = True
        ok = cand & good[lab]
    return ok, prim


def _one_extrude(ctx, ids, tol, min_faces=40, grow_tol=None):
    """An extruded profile in this set of triangles: a direction that (nearly) every normal
    is square to, plus the profile curve seen along that direction. Returns (faces, prim)."""
    C, N, w = ctx.C[ids], ctx.N[ids], ctx.area[ids]
    wsum = float(w.sum())
    if wsum <= 0 or len(ids) < min_faces:
        return None
    rho = np.ones(len(ids))
    d = None
    for _ in range(4):
        ww = w * rho
        M = (N * ww[:, None]).T @ N / max(float(ww.sum()), 1e-30)
        ev, evec = np.linalg.eigh(M)
        d = evec[:, 0]
        f = np.abs(N @ d)
        rho = 1.0 / (1.0 + 400.0 * f * f)
    if ev[1] < 3.0 * max(ev[0], 0.0) + 5e-4:
        return None                                     # (nearly) flat: no direction stands out
    cand = f < 0.05
    # most of the piece must take part: on any curved surface a thin band of triangles is
    # square to any chosen direction, and that band is not an extrusion
    if cand.sum() < min_faces or w[cand].sum() < 0.35 * wsum:
        return None
    cids = ids[cand]
    Fc = ctx.F[cids]
    vids = np.unique(Fc)
    V = ctx.V
    o = (C[cand] * w[cand, None]).sum(0) / w[cand].sum()
    xy_all, h_all = _xyh(V[vids], o, d)
    e1, e2 = _frame(d)
    # the cross-section that gives the longest unbroken profile
    best = None
    hv = (V - o) @ d
    for q in np.linspace(0.08, 0.92, 9):
        h = float(np.quantile(h_all, q))
        P, closed = _cut_chain(V, Fc, hv - h)
        if P is None:
            continue
        ln = float(np.linalg.norm(np.diff(P, axis=0), axis=1).sum())
        if best is None or ln > best[0]:
            best = (ln, P, closed)
    if best is None:
        return None
    _, P, closed = best
    chain = _xyh(P, o, d)[0]
    tri_all = V[ctx.F[ids]]
    edge = float(np.median(np.linalg.norm(tri_all[:, [1, 2, 0]] - tri_all, axis=2).max(1)))
    prof = _fit_profile(xy_all, chain, closed, tol, slack=0.25 * edge, extend=edge + 2.0 * tol, allow_negative=True)
    if prof is None:
        return None
    prim = ("ext", o, d, prof, True)
    got = _claim(ctx, ids, prim, tol, np.ones(len(ids), bool), min_faces, grow_tol)
    if got is None:
        return None
    ok, prim = got
    if w[ok].sum() < 0.25 * wsum:
        return None
    return ids[ok], prim


def _simplify_ext(ctx, faces, prim, tol, lim=None):
    """The simple cases of an extruded profile: a straight line makes a flat face and a
    circular arc makes a cylinder. Those go out as a true plane and a true cylinder."""
    _, o, d, prof, _ = prim
    lim = max(tol, lim or 0.0)
    e1, e2 = _frame(d)
    pts = ctx.V[np.unique(ctx.F[faces])]
    xy, h = _xyh(pts, o, d)
    C, N, w = ctx.C[faces], ctx.N[faces], ctx.area[faces]
    if not prof.closed:
        tv = prof.nearest_t(xy)
        if float(tv.max() - tv.min()) <= 1e-9:
            return prim
        rz = prof.at(np.linspace(float(tv.min()), float(tv.max()), 200))
    else:
        rz = prof.rz
    ctr = rz.mean(0)
    _, _, vt = np.linalg.svd(rz - ctr)
    if not prof.closed and np.abs((rz - ctr) @ np.array([-vt[0][1], vt[0][0]])).max() <= 0.25 * tol:
        c2 = xy.mean(0)
        _, _, vt = np.linalg.svd(xy - c2)
        nrm2 = np.array([-vt[0][1], vt[0][0]])
        if np.abs((xy - c2) @ nrm2).max() <= lim:
            n3 = nrm2[0] * e1 + nrm2[1] * e2
            if float(((N @ n3) * w).sum()) < 0:
                n3 = -n3
            o3 = np.asarray(o, float) + c2[0] * e1 + c2[1] * e2 + float(h.mean()) * np.asarray(d, float)
            return ("pln", o3, n3)
    # circle: x² + y² = 2·cx·x + 2·cy·y + (R² − cx² − cy²)
    def circle(q):
        A = np.c_[2 * q[:, 0], 2 * q[:, 1], np.ones(len(q))]
        sol, *_ = np.linalg.lstsq(A, (q ** 2).sum(1), rcond=None)
        r2 = float(sol[2] + sol[0] ** 2 + sol[1] ** 2)
        return (np.array([sol[0], sol[1]]), math.sqrt(r2)) if r2 > 0 else (None, 0.0)
    cc, R = circle(rz)
    if cc is not None and np.abs(np.linalg.norm(rz - cc, axis=1) - R).max() <= 0.25 * tol:
        cc, R = circle(xy)
        if cc is not None and R < 1e4 and np.abs(np.linalg.norm(xy - cc, axis=1) - R).max() <= lim:
            p0 = np.asarray(o, float) + cc[0] * e1 + cc[1] * e2
            axis = np.asarray(d, float)
            rad = C - p0; rad -= np.outer(rad @ axis, axis)
            outward = float((np.einsum("ij,ij->i", rad, N) * w).sum()) > 0
            pad = 0.03 * float(h.max() - h.min()) + 2.0 * tol
            za, zb = float(h.min()) - pad, float(h.max()) + pad
            newp = _exact_profile("line", (R, za), (R, zb)) if outward else _exact_profile("line", (R, zb), (R, za))
            newp.analytic = ("cyl", float(R))
            newp.flip = not outward
            return ("rev", p0, axis, newp, True)
    return prim


def _exact_profile(kind, rz_a, rz_b, closed=False):
    """A Profile that is exactly a straight line (kind "line") from rz_a to rz_b, or a circular
    arc (kind = (zc, R, v_a, v_b), angles measured from the +r direction)."""
    from scipy.interpolate import splprep
    if kind == "line":
        n = 8
        w = np.linspace(0.0, 1.0, n)[:, None]
        P = np.asarray(rz_a) * (1 - w) + np.asarray(rz_b) * w
        u = np.linspace(0.0, float(np.linalg.norm(np.asarray(rz_b) - np.asarray(rz_a))), n)
    else:
        zc, R, va, vb = kind
        n = int(min(400, max(40, abs(vb - va) / math.radians(1.5))))
        v = np.linspace(va, vb, n)
        P = np.c_[R * np.cos(v), zc + R * np.sin(v)]
        u = R * np.abs(v - va)
    tck, _ = splprep(P.T, u=u, s=0, k=3, quiet=2)
    return Profile(tck, False)


def _simplify(ctx, faces, prim, tol, lim=None):
    """Recognise the simple cases: a profile that is a straight line makes a cylinder or a
    cone, and a circle centred on the axis makes a sphere. Those are written to the STEP file
    as true cylinders, cones and spheres (CAD programs treat them as such: hole features,
    radius measurements), and the profile is replaced by the exact line or arc."""
    _, p0, axis, prof, _ = prim
    if prof.closed:
        return prim
    lim = max(tol, lim or 0.0)
    pts = ctx.V[np.unique(ctx.F[faces])]
    rz_p, _ = _rz(pts, p0, axis)
    tv = prof.nearest_t(rz_p)
    ta, tb = float(tv.min()), float(tv.max())
    if tb - ta <= 1e-9:
        return prim
    rz = prof.at(np.linspace(ta, tb, 200))                # the stretch of profile actually used
    fwd = rz[-1] - rz[0]
    # --- straight line (total least squares through the data points themselves)
    ctr = rz.mean(0)
    _, _, vt = np.linalg.svd(rz - ctr)
    d = vt[0] if vt[0] @ fwd >= 0 else -vt[0]
    off = (rz - ctr) @ np.array([-d[1], d[0]])
    if np.abs(off).max() <= 0.25 * tol:
        ctr = rz_p.mean(0)
        _, _, vt = np.linalg.svd(rz_p - ctr)
        d = vt[0] if vt[0] @ fwd >= 0 else -vt[0]
        s_p = (rz_p - ctr) @ d
        span = float(s_p.max() - s_p.min())
        if abs(d[0]) * span <= 0.25 * tol:               # radius doesn't change: a cylinder
            d = np.array([0.0, 1.0 if d[1] >= 0 else -1.0])
            s_p = (rz_p - ctr) @ d
        off_p = (rz_p - ctr) @ np.array([-d[1], d[0]])
        if np.abs(off_p).max() <= lim and abs(d[1]) > 0.05:
            pad = 0.03 * span + 2.0 * tol
            a_ = ctr + d * (float(s_p.min()) - pad); b_ = ctr + d * (float(s_p.max()) + pad)
            if d[0] != 0.0 and min(a_[0], b_[0]) <= 0:   # a cone's tip: stay on the near side
                return prim
            newp = _exact_profile("line", a_, b_)
            if d[0] == 0.0:
                newp.analytic = ("cyl", float(ctr[0]))
            else:
                sgn = 1.0 if d[1] > 0 else -1.0          # OCC's cone runs up the axis
                newp.analytic = ("cone", float(ctr[0]), float(ctr[1]), float(sgn * d[0]), float(sgn * d[1]))
            newp.flip = bool(d[1] < 0)
            return ("rev", p0, axis, newp, True)
    # --- circle centred on the axis: (r, z) on  r² + (z − zc)² = R²
    A = np.c_[2 * rz[:, 1], np.ones(len(rz))]
    sol, *_ = np.linalg.lstsq(A, (rz ** 2).sum(1), rcond=None)
    zc = float(sol[0]); R2 = float(sol[1] + zc * zc)
    if R2 > 0:
        R = math.sqrt(R2)
        if np.abs(np.hypot(rz[:, 0], rz[:, 1] - zc) - R).max() <= 0.25 * tol:
            A = np.c_[2 * rz_p[:, 1], np.ones(len(rz_p))]
            sol, *_ = np.linalg.lstsq(A, (rz_p ** 2).sum(1), rcond=None)
            zc = float(sol[0]); R2 = float(sol[1] + zc * zc)
            if R2 > 0:
                R = math.sqrt(R2)
                if np.abs(np.hypot(rz_p[:, 0], rz_p[:, 1] - zc) - R).max() <= lim:
                    v = np.arctan2(rz[:, 1] - zc, rz[:, 0])
                    va, vb = float(v[0]), float(v[-1])
                    pad = math.radians(2.0)
                    lo, hi = -0.5 * np.pi + 1e-3, 0.5 * np.pi - 1e-3
                    if vb >= va:
                        va, vb = max(lo, va - pad), min(hi, vb + pad)
                    else:
                        va, vb = min(hi, va + pad), max(lo, vb - pad)
                    newp = _exact_profile((zc, R, va, vb), None, None)
                    newp.analytic = ("sph", zc, R)
                    newp.flip = bool(vb < va)
                    return ("rev", p0, axis, newp, True)
    return prim


def _fill_islands(ctx, faces, prim, ids, tol, fill_tol):
    """Absorb small islands of left-over triangles that are enclosed by the lathe region and
    still close to its surface (noise in the mesh, not a different feature)."""
    _, p0, axis, prof, _ = prim
    rmax = float(_rz(ctx.V[np.unique(ctx.F[faces])], p0, axis)[0][:, 0].max()) if prim[0] == "rev" else 0.0
    inreg = np.zeros(ctx.nF, bool); inreg[faces] = True
    incomp = np.zeros(ctx.nF, bool); incomp[ids] = True
    left = incomp & ~inreg
    if not left.any():
        return faces
    adj = ctx.adj
    k = left[adj[:, 0]] & left[adj[:, 1]]
    g = coo_matrix((np.ones(k.sum()), (adj[k, 0], adj[k, 1])), shape=(ctx.nF, ctx.nF))
    _, lab = connected_components(g, directed=False)
    # which islands touch anything other than the lathe region (or the component's own border)?
    touch_other = np.zeros(lab.max() + 1, bool)
    cross = left[adj[:, 0]] ^ left[adj[:, 1]]
    for a, b in adj[cross]:
        isl, oth = (a, b) if left[a] else (b, a)
        if not inreg[oth]:
            touch_other[lab[isl]] = True
    total = float(ctx.area[faces].sum())
    add = []
    for isl in np.unique(lab[left]):
        if touch_other[isl]:
            continue
        f = np.flatnonzero(left & (lab == isl))
        # an island on the component's outer border has free edges to other components
        nb_count = np.zeros(ctx.nF, np.int64)
        np.add.at(nb_count, adj[:, 0], incomp[adj[:, 1]]); np.add.at(nb_count, adj[:, 1], incomp[adj[:, 0]])
        if np.any(nb_count[f] < 3):
            continue
        if ctx.area[f].sum() > 0.02 * total:
            continue
        if prim[0] == "rev" and not _off_axis(ctx.V[ctx.F[f]], p0, axis, rmax).all():
            continue                                     # the cap over the axis stays out
        if residual(prim, ctx.V[np.unique(ctx.F[f])]).max() <= fill_tol:
            add.append(f)
    return np.concatenate([faces] + add) if add else faces


def sectors(ctx, faces, prim, max_deg=120.0):
    """Split a swept region into pieces that don't wrap past the seam of their surface: a
    lathe region into pieces of at most max_deg around the axis, an extruded region with a
    closed profile into three along the profile. Returns a list of face-id arrays."""
    _, p0, axis, prof, _ = prim
    e1, e2 = _frame(axis)
    if prim[0] == "ext":
        if not prof.closed:
            ang = ang_v = None
        else:
            k = 2 * np.pi / (prof.t1 - prof.t0)
            ang = (prof.nearest_t(_xyh(ctx.C[faces], p0, axis)[0]) - prof.t0) * k
            ang_v = ((prof.nearest_t(_xyh(ctx.V[ctx.F[faces]].reshape(-1, 3), p0, axis)[0]) - prof.t0) * k).reshape(-1, 3)
            max_deg = 400.0                              # only a full ring needs cutting
    else:
        d = ctx.C[faces] - p0
        ang = np.arctan2(d @ e2, d @ e1)
        dv = ctx.V[ctx.F[faces]] - p0                    # n × 3 × 3
        ang_v = np.arctan2(dv @ e2, dv @ e1)
    if ang is None:
        return _pieces(ctx, faces, np.zeros(len(faces), int))
    order = np.sort(ang)
    gaps = np.diff(np.r_[order, order[0] + 2 * np.pi])
    gi = int(np.argmax(gaps))
    start = order[(gi + 1) % len(order)]                 # just after the widest empty arc
    ring = gaps[gi] < math.radians(20.0)                 # goes (almost) all the way round

    def spans(start):
        """Each triangle's own stretch of angle, measured from `start`."""
        rel_c = (ang - start) % (2 * np.pi)
        rv = (ang_v - start - rel_c[:, None] + np.pi) % (2 * np.pi) - np.pi + rel_c[:, None]
        return rv.min(1), rv.max(1)

    def best_cut(lo, hi, theta, half):
        """The angle near theta that the fewest triangles straddle. On a mesh made by a CAD
        program this lands on a row of edges that runs straight up the surface, so the cut
        is clean (and the pieces can later be merged back into one face)."""
        cand = np.unique(np.r_[lo, hi])
        cand = cand[(cand >= theta - half) & (cand <= theta + half)]
        if len(cand) > 96:
            cand = cand[np.linspace(0, len(cand) - 1, 96).astype(int)]
        cand = np.r_[theta, cand]
        cross = ((lo[None, :] < cand[:, None] - 1e-5) & (hi[None, :] > cand[:, None] + 1e-5)).sum(1)
        best = np.flatnonzero(cross == cross.min())
        return float(cand[best[np.argmin(np.abs(cand[best] - theta))]])

    if ring:
        lo, hi = spans(start)
        start = start + best_cut(lo, hi, 0.0, math.radians(15.0))
    lo, hi = spans(start)
    rel = 0.5 * (lo + hi)
    if ring:
        rel = rel % (2 * np.pi)
    extent = 2 * np.pi if ring else float(hi.max())
    n = max(1, int(math.ceil(math.degrees(extent) / max_deg - 1e-9)))
    if ring:
        n = max(n, 3)
    cuts = [best_cut(lo, hi, k * extent / n, 0.25 * extent / n) for k in range(1, n)]
    b = np.searchsorted(np.array(cuts), rel, side="right") if cuts else np.zeros(len(faces), int)
    if prof.closed and prim[0] == "rev":
        # a ring-shaped profile (e.g. a torus tube) must not wrap all the way round either
        rz, _ = _rz(ctx.C[faces], p0, axis)
        tv = prof.nearest_t(rz)
        span = prof.t1 - prof.t0
        used = np.sort(tv)
        g2 = np.diff(np.r_[used, used[0] + span])
        j = int(np.argmax(g2))
        rel_v = (tv - used[(j + 1) % len(used)]) % span
        ext_v = float(rel_v.max())
        nv = 3 if g2[j] < 0.06 * span else max(1, int(math.ceil(ext_v / (0.4 * span) - 1e-9)))
        bv = np.minimum((rel_v / (ext_v / nv + 1e-12)).astype(int), nv - 1)
        b = b * nv + bv
        n = n * nv
    return _pieces(ctx, faces, b)


def _pieces(ctx, faces, b):
    """Connected pieces of each slice b; slivers cut off by a slice line go to the piece next door."""
    loc = -np.ones(ctx.nF, np.int64); loc[faces] = np.arange(len(faces))
    a0, a1 = loc[ctx.adj[:, 0]], loc[ctx.adj[:, 1]]
    both = (a0 >= 0) & (a1 >= 0)
    a0, a1 = a0[both], a1[both]
    area = ctx.area[faces]
    total = float(area.sum())
    for _ in range(6):
        same = b[a0] == b[a1]
        g = coo_matrix((np.ones(same.sum()), (a0[same], a1[same])), shape=(len(faces), len(faces)))
        nc, lab = connected_components(g, directed=False)
        size = np.bincount(lab, weights=area, minlength=nc)
        count = np.bincount(lab, minlength=nc)
        small = (size < 0.02 * total) | (count < min(12, max(2, len(faces) // 20)))
        if not small.any() or nc == 1:
            break
        # biggest neighbouring piece of each small piece
        cross = lab[a0] != lab[a1]
        best = {}
        for x, y in zip(np.r_[a0[cross], a1[cross]], np.r_[a1[cross], a0[cross]]):
            lx, ly = lab[x], lab[y]
            if small[lx] and (lx not in best or size[ly] > size[best[lx][0]]):
                best[lx] = (ly, b[y])
        if not best:
            break
        newb = b.copy()
        for lx, (ly, by) in best.items():
            if not small[ly] or size[ly] > size[lx]:
                newb[lab == lx] = by
        if np.array_equal(newb, b):
            break
        b = newb
    same = b[a0] == b[a1]
    g = coo_matrix((np.ones(same.sum()), (a0[same], a1[same])), shape=(len(faces), len(faces)))
    _, lab = connected_components(g, directed=False)
    return [faces[lab == c] for c in np.unique(lab)]


def loop_order(prim, loops_pts):
    """Index of the outer loop of a lathe patch: the one enclosing the most area in the
    surface's own (angle, profile position) coordinates."""
    _, p0, axis, prof, _ = prim
    if prim[0] == "ext":
        return int(np.argmax([float(np.linalg.norm(np.ptp(P, axis=0))) for P in loops_pts]))
    e1, e2 = _frame(axis)
    allp = np.vstack(loops_pts)
    d = allp - p0
    a_all = np.arctan2(d @ e2, d @ e1)
    mid = math.atan2(np.sin(a_all).mean(), np.cos(a_all).mean())
    best, best_a = 0, -1.0
    for i, P in enumerate(loops_pts):
        rz, rad = _rz(P, p0, axis)
        _, _, j = prof.nearest(rz)
        u = (np.arctan2(rad @ e2, rad @ e1) - mid + np.pi) % (2 * np.pi) - np.pi
        x = u * float(rz[:, 0].mean()); y = prof.ts[j] * (prof.length / max(prof.t1 - prof.t0, 1e-12))
        a = abs(0.5 * np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y))
        if a > best_a:
            best, best_a = i, a
    return best


def find_revolves(ctx, ids, tol, max_shapes=3, log=None, grow_tol=None):
    """Lathe shapes inside one smooth component.
    Returns ([(face ids, primitive), ...], remaining face ids)."""
    ids = np.asarray(ids, dtype=np.int64)
    total = float(ctx.area[ids].sum()) or 1.0
    out = []
    rest = ids
    for _ in range(max_shapes):
        if len(rest) < 40:
            break
        got = _one_revolve(ctx, rest, tol, grow_tol=grow_tol)
        if got is None:
            break
        faces, prim = got
        # keep connected pieces that are big enough to be worth a face of their own
        mask = np.zeros(ctx.nF, bool); mask[faces] = True
        adj = ctx.adj
        k = mask[adj[:, 0]] & mask[adj[:, 1]]
        g = coo_matrix((np.ones(k.sum()), (adj[k, 0], adj[k, 1])), shape=(ctx.nF, ctx.nF))
        nc, lab = connected_components(g, directed=False)
        taken = []
        for comp in np.unique(lab[faces]):
            piece = faces[lab[faces] == comp]
            if len(piece) >= 30 and ctx.area[piece].sum() >= 0.01 * total:
                taken.append(piece)
        if not taken:
            break
        fill_tol = max(1.5 * tol, grow_tol or 0.0)
        taken = [_fill_islands(ctx, piece, prim, rest, tol, fill_tol) for piece in taken]
        for piece in taken:
            out.append((piece, _simplify(ctx, piece, prim, tol, fill_tol)))
        rest = np.setdiff1d(rest, np.concatenate(taken))
    return out, rest


def _connected(ctx, faces):
    """Connected pieces of a set of triangles (list of face-id arrays, biggest first)."""
    loc = -np.ones(ctx.nF, np.int64); loc[faces] = np.arange(len(faces))
    a0, a1 = loc[ctx.adj[:, 0]], loc[ctx.adj[:, 1]]
    both = (a0 >= 0) & (a1 >= 0)
    g = coo_matrix((np.ones(both.sum()), (a0[both], a1[both])), shape=(len(faces), len(faces)))
    _, lab = connected_components(g, directed=False)
    out = [faces[lab == c] for c in np.unique(lab)]
    out.sort(key=lambda f: -float(ctx.area[f].sum()))
    return out


def find_extrudes(ctx, ids, tol, max_shapes=2, grow_tol=None, total=None):
    """Extruded profiles inside a set of triangles, looked for in each connected piece.
    Returns ([(face ids, primitive), ...], remaining face ids). A primitive here may also be
    a plane ("pln", point, normal) or a lathe-style cylinder, when the profile is that simple."""
    ids = np.asarray(ids, dtype=np.int64)
    total = total or float(ctx.area[ids].sum()) or 1.0
    out = []
    left = []
    for piece in _connected(ctx, ids):
        rest = piece
        for _ in range(max_shapes):
            if len(rest) < 40:
                break
            got = _one_extrude(ctx, rest, tol, grow_tol=grow_tol)
            if got is None:
                break
            faces, prim = got
            fill_tol = max(1.5 * tol, grow_tol or 0.0)
            taken = []
            for part in _connected(ctx, faces):
                if len(part) >= 30 and ctx.area[part].sum() >= 0.01 * total:
                    taken.append(_fill_islands(ctx, part, prim, rest, tol, fill_tol))
            if not taken:
                break
            for part in taken:
                out.append((part, _simplify_ext(ctx, part, prim, tol, fill_tol)))
            rest = np.setdiff1d(rest, np.concatenate(taken))
            STATS["ext_found"] = STATS.get("ext_found", 0) + len(taken)
        left.append(rest)
    return out, (np.concatenate(left) if left else ids[:0])


def find_swept(ctx, ids, tol, grow_tol=None):
    """The roughing search for one smooth component: lathe shapes first, then extruded
    profiles in what is left. Returns ([(face ids, primitive), ...], remaining face ids)."""
    ids = np.asarray(ids, dtype=np.int64)
    total = float(ctx.area[ids].sum()) or 1.0
    out, rest = find_revolves(ctx, ids, tol, grow_tol=grow_tol)
    if len(rest) >= 40:
        more, rest = find_extrudes(ctx, rest, tol, grow_tol=grow_tol, total=total)
        out.extend(more)
    return out, rest
