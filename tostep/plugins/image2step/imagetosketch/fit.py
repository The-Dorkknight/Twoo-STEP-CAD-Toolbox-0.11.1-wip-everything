# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
# Copied from the ImageToSketch project by tools/sync_imagetosketch.py - edit it there.
"""Stage 5: turn a refined closed outline into the fewest CAD primitives that fit it.

Primitives (pixel coordinates, y down - the pipeline converts to mm):
    ("circle", cx, cy, r)
    ("line",   (x0, y0), (x1, y1))
    ("arc",    (x0, y0), (xm, ym), (x1, y1))      three-point arc
    ("spline", [(x, y), ...], periodic)

Method: Douglas-Peucker gives candidate break points; a shortest-path search over those break
points picks the cheapest cover by lines (cost 1), arcs (1.6) and splines (2.5 + 0.3/chord), so
the simplest description within tolerance wins. Neighbouring primitives are then joined at exact
intersections / tangent points, which restores sharp corners that blur had rounded.
"""
import math

import numpy as np

LINE_COST, ARC_COST, SPL_COST, SPL_PER = 1.0, 1.6, 1.5, 0.5
RDP_FACTOR = 1.0            # break-point density: Douglas-Peucker epsilon as a multiple of tol


# ----------------------------------------------------------------- basics --
def resample_closed(pts, step=1.0):
    closed = np.vstack([pts, pts[:1]])
    seg = np.hypot(*np.diff(closed, axis=0).T)
    total = float(seg.sum())
    n = max(int(round(total / step)), 8)
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    t = np.linspace(0, total, n, endpoint=False)
    return np.c_[np.interp(t, cum, closed[:, 0]), np.interp(t, cum, closed[:, 1])]


def fit_circle(pts):
    """Taubin circle fit (far less biased than the simple algebraic fit on short arcs), in
    Chernov's moment form: a handful of sums and a Newton iteration, no matrix decomposition.
    Returns cx, cy, r, residuals."""
    n = len(pts)
    mx, my = float(pts[:, 0].sum()) / n, float(pts[:, 1].sum()) / n
    x, y = pts[:, 0] - mx, pts[:, 1] - my
    z = x * x + y * y
    mxx, myy, mxy = float(x @ x) / n, float(y @ y) / n, float(x @ y) / n
    mxz, myz, mzz = float(x @ z) / n, float(y @ z) / n, float(z @ z) / n
    mz = mxx + myy
    if mz < 1e-12:
        return mx, my, 0.0, np.zeros(n)
    cov = mxx * myy - mxy * mxy
    var = mzz - mz * mz
    a3 = 4.0 * mz
    a2 = -3.0 * mz * mz - mzz
    a1 = var * mz + 4.0 * cov * mz - mxz * mxz - myz * myz
    a0 = mxz * (mxz * myy - myz * mxy) + myz * (myz * mxx - mxz * mxy) - var * cov
    xn, yn = 0.0, a0
    for _ in range(40):
        dy = a1 + xn * (2 * a2 + 3 * a3 * xn)
        if dy == 0:
            break
        xnew = xn - yn / dy
        if xnew == xn or not math.isfinite(xnew):
            break
        ynew = a0 + xnew * (a1 + xnew * (a2 + xnew * a3))
        if abs(ynew) >= abs(yn):
            break
        xn, yn = xnew, ynew
    det = xn * xn - xn * mz + cov
    if abs(det) < 1e-12 * max(mz * mz, 1e-12):
        return mx, my, float("inf"), np.full(n, np.inf)        # the points are on a line
    cx = (mxz * (myy - xn) - myz * mxy) / det / 2.0
    cy = (myz * (mxx - xn) - mxz * mxy) / det / 2.0
    r = math.sqrt(cx * cx + cy * cy + mz)
    res = np.hypot(x - cx, y - cy) - r
    return cx + mx, cy + my, r, res


def fit_ellipse(pts):
    """Least-squares ellipse (via OpenCV's direct fit). Returns cx, cy, a, b, angle (radians,
    direction of the major axis, a >= b) and the approximate distance of each point from it."""
    import cv2
    (cx, cy), (d1, d2), ang = cv2.fitEllipseDirect(pts.astype(np.float32).reshape(-1, 1, 2))
    a, b, th = d1 / 2.0, d2 / 2.0, math.radians(ang)
    if b > a:
        a, b, th = b, a, th + math.pi / 2
    if not (np.isfinite(a) and np.isfinite(b)) or b < 1.0:
        return cx, cy, a, b, th, np.full(len(pts), np.inf)
    c, s_ = math.cos(th), math.sin(th)
    u = (pts[:, 0] - cx) * c + (pts[:, 1] - cy) * s_
    v = -(pts[:, 0] - cx) * s_ + (pts[:, 1] - cy) * c
    g = np.sqrt((u / a) ** 2 + (v / b) ** 2)
    grad = np.sqrt((u / a ** 2) ** 2 + (v / b ** 2) ** 2) / np.maximum(g, 1e-9)
    return cx, cy, a, b, th, (g - 1.0) / np.maximum(grad, 1e-9)


def fit_line(pts):
    """Total least squares line. Returns point on line, unit direction, residuals."""
    n = len(pts)
    mx, my = float(pts[:, 0].sum()) / n, float(pts[:, 1].sum()) / n
    x, y = pts[:, 0] - mx, pts[:, 1] - my
    th = 0.5 * math.atan2(2.0 * float(x @ y), float(x @ x) - float(y @ y))
    ux, uy = math.cos(th), math.sin(th)
    if ux * (pts[-1, 0] - pts[0, 0]) + uy * (pts[-1, 1] - pts[0, 1]) < 0:
        ux, uy = -ux, -uy
    res = y * ux - x * uy
    return np.array([mx, my]), np.array([ux, uy]), res


def _rdp(P, a, b, eps, out):
    stack = [(a, b)]
    while stack:
        a, b = stack.pop()
        if b - a < 2:
            continue
        seg = P[a + 1:b]
        d = P[b] - P[a]
        L = math.hypot(d[0], d[1])
        if L < 1e-9:
            dist = np.hypot(seg[:, 0] - P[a, 0], seg[:, 1] - P[a, 1])
        else:
            dist = np.abs((seg[:, 0] - P[a, 0]) * d[1] - (seg[:, 1] - P[a, 1]) * d[0]) / L
        i = int(dist.argmax())
        if dist[i] > eps:
            k = a + 1 + i
            out.append(k)
            stack.append((a, k))
            stack.append((k, b))


def rdp_closed(P, eps):
    """Douglas-Peucker on a closed polyline; returns sorted vertex indices."""
    n = len(P)
    far = int(np.hypot(*(P - P[0]).T).argmax())
    out = [0, far]
    _rdp(P, 0, far, eps, out)
    Q = np.vstack([P, P[:1]])
    _rdp(Q, far, n, eps, out)
    return sorted(set(i % n for i in out))


# ------------------------------------------------------------ segmentation --
def _thin(c, cap=128):
    """Decimate a run of 1-px-spaced points to about `cap`, but keep every point of the first
    and last 8 px: what happens at the ends decides whether a primitive may stop there.
    Returns (points, px represented by each sample)."""
    n = len(c)
    k = max(1, n // cap)
    if k == 1:
        return c, np.ones(n)
    e = min(8, n // 4)
    idx = np.unique(np.concatenate([np.arange(e), np.arange(e, n - e, k), np.arange(n - e, n)]))
    w = np.diff(np.concatenate([idx, [n]])).astype(float)
    return c[idx], w


def _robust(kind, c, tol, bump, cap=128, ta=0, tb=0, tol_end=None):
    """Fit a line or circle to the run of points c.

    Short runs of outliers (dirt, burrs, a hair across the edge) up to `bump` px long are
    tolerated in the middle. The first `ta` and last `tb` px lie next to a real corner, which
    blur has rounded: they do not take part in the fit and only have to stay within `tol_end`
    of the result - close enough to be corner rounding, too far to hide a different feature.
    Returns (model, ok)."""
    n = len(c)
    pts, w = _thin(c, cap)
    pos = np.cumsum(w) - w                                     # index of each sample along the run
    core = (pos >= ta) & (pos < n - tb)
    if core.sum() < (2 if kind == "line" else 6):
        core = np.ones(len(pts), bool)                         # too short to set the ends aside
        tol = tol_end if tol_end is not None else tol
    fit = fit_line if kind == "line" else fit_circle

    def residual(model):
        if kind == "line":
            m, u = model[0], model[1]
            return np.abs((pts - m) @ np.array([-u[1], u[0]]))
        return np.abs(np.hypot(pts[:, 0] - model[0], pts[:, 1] - model[1]) - model[2])

    out = fit(pts[core])
    if kind == "arc" and not (np.isfinite(out[2]) and out[2] >= 3.0):
        return None, False
    res = residual(out)
    inl = res <= tol
    if not inl[core].all():
        if bump <= 0 or core.sum() < 12:
            return None, False
        use = core & (res <= 3 * tol)
        for _ in range(2):                                     # refit on the inliers
            if use.sum() < max(6, 0.6 * core.sum()):
                return None, False
            out = fit(pts[use])
            if kind == "arc" and not (np.isfinite(out[2]) and out[2] >= 3.0):
                return None, False
            res = residual(out)
            inl = res <= tol
            use = core & inl
        ci = np.flatnonzero(core)
        if w[use].sum() < 0.75 * w[core].sum() or not inl[ci[:3]].all() or not inl[ci[-3:]].all():
            return None, False
        bad = np.flatnonzero(core & ~inl)
        if len(bad):
            runs = np.split(bad, np.flatnonzero(np.diff(bad) > 1) + 1)
            if max(w[r].sum() for r in runs) > min(bump, 0.2 * n):
                return None, False
    if not core.all() and tol_end is not None and res[~core].max() > tol_end:
        return None, False
    return out[:-1] if kind == "line" else out[:3], True


def _arc_ok(c, model):
    cx, cy, r = model
    if not (3.0 <= r <= 1e5):
        return False
    pts = c[::max(1, len(c) // 64)]
    ang = np.unwrap(np.arctan2(pts[:, 1] - cy, pts[:, 0] - cx))
    sweep = ang[-1] - ang[0]
    if abs(sweep) > math.radians(345) or abs(sweep) < 1e-6:
        return False
    d = np.diff(ang) * np.sign(sweep)
    return bool(np.mean(d > -0.05) > 0.9)                      # keeps turning the same way


def _turns(P, verts, trim):
    """How far (radians) the outline turns within +-trim px of each break point."""
    n = len(P)
    v = np.asarray(verts)
    w = max(int(trim), 3)
    a = P[v] - P[(v - w) % n]
    b = P[(v + w) % n] - P[v]
    return np.abs(np.arctan2(a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0], (a * b).sum(1)))


def _corners(P, verts, trim):
    """True for break points that sit on a corner: the outline turns by more than 35 degrees
    within +-trim px. A large fillet or a gentle curve does not. (A fillet of only a few pixels
    radius does - at that size it cannot be told from a sharp corner that blur has rounded.)"""
    return _turns(P, verts, trim) > math.radians(35)


def _ends(a, b, trim, corner_a, corner_b):
    """How many px at each end of the run a..b lie in a corner's rounded zone."""
    lim = (b - a) // 2
    return (min(trim, lim) if corner_a else 0), (min(trim, lim) if corner_b else 0)


def _segment(P, verts, tol, trim, bump, tol_end, max_span=80):
    """Shortest-path cover of the closed chain by line / arc / spline pieces.
    P is rotated so verts[0] == 0. Returns list of (kind, i, j) and the vertex table.

    How far a line or arc can run from a break point is found by galloping + bisection (a fit
    that works up to j works for anything shorter), then a short look-ahead tries to jump a
    bump. That keeps the number of fits per break point around a dozen."""
    n = len(P)
    M = len(verts)
    vv = list(verts) + [n]                                     # vertex M == vertex 0, one lap on
    Q = np.vstack([P, P[:1]])
    INF = float("inf")
    best = [INF] * (M + 1)
    back = [None] * (M + 1)
    best[0] = 0.0
    patience = 2.5 * bump + 6
    sharp = _corners(P, verts, trim)

    def test(kind, i, j):
        a, b = vv[i], vv[j]
        ta, tb = _ends(a, b, trim, sharp[i], sharp[j % M])
        c = Q[a:b + 1]
        if len(c) < (2 if kind == "line" else 6):
            return False
        model, ok = _robust(kind, c, tol, bump, ta=ta, tb=tb, tol_end=tol_end)
        return ok and (kind == "line" or _arc_ok(c, model))

    def reach(kind, i):
        """All j this primitive can end at, as a list of inclusive (lo, hi) ranges."""
        j0 = i + (1 if kind == "line" else 2)
        jmax = min(i + max_span, M, i + M - 1)                 # never swallow the whole loop
        ranges = []
        start = j0
        while start <= jmax:
            if not test(kind, i, start):
                lo = None
            else:
                lo, step = start, 1
                while lo + step <= jmax and test(kind, i, lo + step):
                    lo += step
                    step *= 2
                hi = min(lo + step, jmax + 1)                  # first known failure (or past end)
                while hi - lo > 1:
                    mid = (lo + hi) // 2
                    if test(kind, i, mid):
                        lo = mid
                    else:
                        hi = mid
                ranges.append((start, lo))
            # look a little further for a place to land beyond a bump
            base = vv[lo] if lo is not None else vv[i]
            k = (lo + 1 if lo is not None else start) + 1
            nxt = None
            while bump > 0 and k <= jmax and vv[k] - base <= patience:
                if test(kind, i, k):
                    nxt = k
                    break
                k += 1
            if nxt is None:
                break
            start = nxt
        return ranges

    for i in range(M):
        if best[i] == INF:
            continue
        for kind, cost in (("line", LINE_COST), ("arc", ARC_COST)):
            for lo, hi in reach(kind, i):
                for j in range(lo, hi + 1):
                    if best[i] + cost < best[j]:
                        best[j], back[j] = best[i] + cost, (kind, i, j)
        for j in range(i + 1, min(i + max_span, M) + 1):
            # a spline is priced by the points it will need, and may not run through a corner
            cost = best[i] + SPL_COST + SPL_PER * max(j - i, (vv[j] - vv[i]) / 20.0)
            if cost < best[j]:
                best[j], back[j] = cost, ("spline", i, j)
            if j < M and sharp[j]:
                break
    out = []
    k = M
    while k > 0:
        out.append(back[k])
        k = back[k][1]
    return out[::-1], vv


# ------------------------------------------------------------------ joints --
def _line_line(m1, u1, m2, u2, v):
    cr = u1[0] * u2[1] - u1[1] * u2[0]
    if abs(cr) < math.sin(math.radians(3)):
        p1 = m1 + u1 * np.dot(v - m1, u1)
        p2 = m2 + u2 * np.dot(v - m2, u2)
        return (p1 + p2) / 2
    d = m2 - m1
    t = (d[0] * u2[1] - d[1] * u2[0]) / cr
    return m1 + u1 * t


def _line_circle(m, u, c, r, v, tol):
    """(point, tangent?) where a line meets a circle, nearest to v."""
    foot = m + u * np.dot(c - m, u)
    d = float(np.hypot(*(c - foot)))
    if abs(d - r) <= max(tol, 0.01 * r):                       # tangent: a fillet
        return foot, True
    if d > r:
        return foot, False
    h = math.sqrt(max(r * r - d * d, 0.0))
    a, b = foot + u * h, foot - u * h
    return (a if np.hypot(*(a - v)) < np.hypot(*(b - v)) else b), False


def _circle_circle(c1, r1, c2, r2, v, tol):
    """(point, tangent?) where two circles meet, nearest to v."""
    d = float(np.hypot(*(c2 - c1)))
    if d < 2 * tol and abs(r1 - r2) < 2 * tol:                # two pieces of the same circle
        return _project("arc", (c1[0], c1[1], r1), v), True
    e = (c2 - c1) / d
    if abs(d - (r1 + r2)) <= tol:                              # touching from outside
        return c1 + e * (r1 + (d - r1 - r2) / 2), True
    if abs(d - abs(r1 - r2)) <= tol:                           # touching from inside
        return (c1 + e * r1 if r1 > r2 else c2 - e * r2), True
    if d > r1 + r2 or d < abs(r1 - r2):
        return v, False
    a = (r1 * r1 - r2 * r2 + d * d) / (2 * d)
    h = math.sqrt(max(r1 * r1 - a * a, 0.0))
    p = c1 + e * a
    n = np.array([-e[1], e[0]])
    q1, q2 = p + n * h, p - n * h
    return (q1 if np.hypot(*(q1 - v)) < np.hypot(*(q2 - v)) else q2), False


def _project(kind, model, v):
    if kind == "line":
        m, u = model
        return m + u * np.dot(v - m, u)
    if kind == "arc":
        c = np.array(model[:2])
        d = v - c
        return c + d / max(np.hypot(*d), 1e-9) * model[2]
    return v


def _joint(A, B, v, tol, limit, corner):
    """Meeting point of two neighbouring primitives near break point v.
    At a real corner, or where the two are tangent (a fillet), the exact intersection / tangent
    point is used. Where they merely meet at a shallow angle the intersection is ill-defined and
    can lie far away, so the break point itself (pulled onto both) is used instead."""
    j, tangent = _joint_raw(A, B, v, tol)
    j = np.asarray(j, float)
    lim = limit if (corner or tangent) else max(2.0, 4.0 * tol)
    if not np.all(np.isfinite(j)) or np.hypot(*(j - v)) > lim:
        j = (np.asarray(_project(A[0], A[3], v), float) + np.asarray(_project(B[0], B[3], v), float)) / 2
    return j


def _joint_raw(A, B, v, tol):
    ka, ma = A[0], A[3]
    kb, mb = B[0], B[3]
    if ka == "line" and kb == "line":
        return _line_line(ma[0], ma[1], mb[0], mb[1], v), False
    if ka == "line" and kb == "arc":
        return _line_circle(ma[0], ma[1], np.array(mb[:2]), mb[2], v, tol)
    if ka == "arc" and kb == "line":
        return _line_circle(mb[0], mb[1], np.array(ma[:2]), ma[2], v, tol)
    if ka == "arc" and kb == "arc":
        return _circle_circle(np.array(ma[:2]), ma[2], np.array(mb[:2]), mb[2], v, tol)
    if ka == "spline" and kb != "spline":
        return _project(kb, mb, v), True
    if kb == "spline" and ka != "spline":
        return _project(ka, ma, v), True
    return v, True


# -------------------------------------------------------------------- main --
def _smooth_run(seg, sd):
    """Gaussian-smooth a run of ~1 px spaced points along its length (sd in points). A closed
    run (first point repeated at the end) is smoothed round the seam; an open run keeps its two
    end points exactly."""
    n = len(seg)
    if sd <= 0 or n < 8:
        return seg
    h = int(math.ceil(3 * sd))
    closed = float(np.hypot(*(seg[0] - seg[-1]))) < 1e-9
    if closed:
        body = seg[:-1]
        m = len(body)
        idx = np.arange(-h, m + h) % m
        ext = body[idx]
    else:
        ext = np.vstack([2 * seg[0] - seg[h:0:-1], seg, 2 * seg[-1] - seg[-2:-h - 2:-1]]) if n > h + 1 \
            else seg
        if ext is seg:
            return seg
    k = np.exp(-0.5 * (np.arange(-h, h + 1) / float(sd)) ** 2)
    k /= k.sum()
    out = np.c_[np.convolve(ext[:, 0], k, mode="valid"), np.convolve(ext[:, 1], k, mode="valid")]
    if closed:
        return np.vstack([out, out[:1]])
    out[0], out[-1] = seg[0], seg[-1]
    return out


def _spline_points(Q, a, b, tol, smooth=0.0):
    seg = Q[a:b + 1]
    if len(seg) <= 4:
        return [tuple(p) for p in seg]
    seg = _smooth_run(np.asarray(seg, float), smooth)
    keep = [0, len(seg) - 1]
    _rdp(seg, 0, len(seg) - 1, 0.5 * tol, keep)
    keep = sorted(set(keep))
    dense = [keep[0]]
    far = max(24.0, 8.0 * smooth)                             # (longer on an enlarged picture)
    for k in keep[1:]:                                        # never leave long unsupported gaps
        gap = k - dense[-1]
        if gap > far:
            parts = int(math.ceil(gap / far))
            base = dense[-1]
            dense.extend([base + int(round(gap * q / parts)) for q in range(1, parts)])
        dense.append(k)
    # An interpolating spline swings wide where a long gap sits next to a cluster of close
    # points (CAD kernels more so than the preview). Even the spacing out: split any gap much
    # longer than a neighbour.
    closed = len(seg) > 8 and float(np.hypot(*(seg[0] - seg[-1]))) < 1e-9
    for _ in range(6):
        dense = sorted(set(dense))
        gaps = np.diff(dense)
        m = len(gaps)
        if m < 2:
            break
        add = []
        for i in range(m):
            nb = []
            if i > 0 or closed:
                nb.append(gaps[i - 1])
            if i < m - 1 or closed:
                nb.append(gaps[(i + 1) % m])
            if gaps[i] >= 4 and nb and gaps[i] > 2.5 * min(nb):
                add.append(dense[i] + int(gaps[i]) // 2)
        if not add:
            break
        dense.extend(add)
    return [tuple(seg[i]) for i in sorted(set(dense))]


def fit_loop(pts, tol=0.5, edge_sigma=1.1, detect_circles=True, bump=14.0, smooth=0.0):
    """Fit one closed outline (see _fit_loop), then check the result still encloses the same
    area as the outline it came from. If bridging went wrong, retry without it; if that fails
    too, fall back to a smooth spline through the outline."""
    from .geom import sample_prims, poly_area
    P = np.asarray(pts, float)
    A = poly_area(P)
    per = float(np.hypot(*np.diff(np.vstack([P, P[:1]]), axis=0).T).sum())
    for b in ([bump, 0.0] if bump > 0 else [0.0]):
        prims = _fit_loop(P, tol, edge_sigma, detect_circles, min(b, per / 12.0), smooth)
        S = sample_prims(prims, 1.0)
        if len(S) >= 3 and abs(poly_area(S) - A) <= 0.02 * A + 0.5 * b * math.sqrt(max(A, 1.0)) * 0.2 + per * tol:
            return prims
    R = resample_closed(P, 1.0)
    return [("spline", _spline_points(np.vstack([R, R[:1]]), 0, len(R), tol, smooth)[:-1], True)]


def _fit_loop(pts, tol, edge_sigma, detect_circles, bump, smooth=0.0):
    """Fit one closed outline. tol = largest allowed deviation in px.
    edge_sigma = blur of the edges in px (from detection); used to undo the small inward pull
    that blur exerts on curved edges (radius reads sigma^2 / 2r too small).
    bump = longest stretch (px) of an edge that may be treated as dirt and bridged over by the
    line or arc running through it; 0 keeps every wiggle."""
    P = resample_closed(np.asarray(pts, float), 1.0)
    n = len(P)
    s2 = edge_sigma ** 2

    def unbias(r):
        return r + s2 / (2.0 * r) if r > 2 * edge_sigma else r

    if detect_circles and n >= 12:
        cx, cy, r, res = fit_circle(P)
        if np.isfinite(r) and np.abs(res).max() <= 1.5 * tol and math.sqrt(np.mean(res ** 2)) <= 0.6 * tol:
            return [("circle", float(cx), float(cy), float(unbias(r)))]
        if np.isfinite(r) and bump > 0 and np.mean(np.abs(res) <= 3 * tol) > 0.8:
            inl = np.abs(res) <= 3 * tol                       # round, apart from a speck on the rim
            for _ in range(2):
                cx, cy, r, _ = fit_circle(P[inl])
                res = np.hypot(P[:, 0] - cx, P[:, 1] - cy) - r
                inl = np.abs(res) <= 1.5 * tol
                if inl.sum() < 0.8 * n:
                    break
            else:
                bad = np.flatnonzero(~inl)
                runs = np.split(bad, np.flatnonzero(np.diff(bad) > 1) + 1) if len(bad) else []
                longest = max([len(q) for q in runs] or [0])
                if len(runs) > 1 and bad[0] == 0 and bad[-1] == n - 1:
                    longest = max(longest, len(runs[0]) + len(runs[-1]))
                if longest <= bump and math.sqrt(np.mean(res[inl] ** 2)) <= 0.6 * tol:
                    return [("circle", float(cx), float(cy), float(unbias(r)))]

    if detect_circles and n >= 40:                             # a whole ellipse
        cx, cy, a, b, th, res = fit_ellipse(P)
        if np.all(np.isfinite(res)) and a / b > 1.02 and b > 4 * edge_sigma + 3 \
                and np.abs(res).max() <= 1.5 * tol and math.sqrt(np.mean(res ** 2)) <= 0.6 * tol:
            # blur pulls each end inward by sigma^2 * curvature / 2
            return [("ellipse", float(cx), float(cy), float(a + s2 * a / (2 * b * b)),
                     float(b + s2 * b / (2 * a * a)), float(th))]

    verts = rdp_closed(P, RDP_FACTOR * tol)
    if len(verts) < 3:
        sp = _spline_points(np.vstack([P, P[:1]]), 0, n, tol, smooth)[:-1]
        return [("spline", sp, True)]

    trim = int(round(2.5 * edge_sigma + 3.5))             # how far blur rounds a corner, px
    tol_end = tol + 1.2 * edge_sigma + 1.4                # how far it pulls the outline there

    # Where to cut the closed chain open: at the sharpest corner if there is one, otherwise at
    # the start of the longest straight chord (a line end), so no primitive straddles the seam.
    turn = _turns(P, verts, trim)
    if float(turn.max()) >= math.radians(35):
        s = verts[int(turn.argmax())]
    else:
        V = P[verts]
        s = verts[int(np.hypot(*(np.roll(V, -1, 0) - V).T).argmax())]
    P = np.roll(P, -s, axis=0)
    verts = sorted((v - s) % n for v in verts)

    segs, vv = _segment(P, verts, tol, trim, bump, tol_end)
    if len(segs) >= 3 and segs[0][0] == segs[-1][0] and segs[0][0] != "spline":
        # Did the seam cut one primitive in two? If the last and first pieces fit as a single
        # line / arc, start again from a break point in the middle of the chain.
        M0 = len(verts)
        cn = _corners(P, verts, trim)
        i0, j0 = segs[-1][1], segs[0][2]
        both = np.vstack([P[vv[i0]:], P[:vv[j0] + 1]])
        ta, tb = _ends(0, len(both) - 1, trim, cn[i0 % M0], cn[j0 % M0])
        model, ok = _robust(segs[0][0], both, tol, bump, ta=ta, tb=tb, tol_end=tol_end)
        if ok and (segs[0][0] == "line" or _arc_ok(both, model)):
            s2nd = vv[segs[len(segs) // 2][1]]
            P = np.roll(P, -s2nd, axis=0)
            verts = sorted((v - s2nd) % n for v in verts)
            segs, vv = _segment(P, verts, tol, trim, bump, tol_end)
    Q = np.vstack([P, P[:1]])
    M = len(verts)
    corner = _corners(P, verts, trim)

    # A sliver of a primitive wedged between two proper ones (typically a few px of "line" where
    # a straight edge runs into a fillet) is noise in the break points, not geometry: let a
    # neighbour take it over if it can do so within a slightly looser tolerance.
    def feasible(kind, i, j):
        ta, tb = _ends(vv[i], vv[j], trim, corner[i % M], corner[j % M])
        c_ = Q[vv[i]:vv[j] + 1]
        model, ok = _robust(kind, c_, 1.6 * tol, bump, ta=ta, tb=tb, tol_end=tol_end)
        return ok and (kind == "line" or _arc_ok(c_, model))

    def sliver(seg):
        return vv[seg[2]] - vv[seg[1]] <= max(20, 3 * trim)

    if len(segs) >= 4 and (sliver(segs[0]) or sliver(segs[-1])):
        # move the seam away so the sliver has a neighbour on both sides within the chain
        m = len(segs) // 2
        v0 = segs[m][1]
        P = np.roll(P, -vv[v0], axis=0)
        verts = sorted((v - vv[v0]) % n for v in verts)
        segs = [(k, (i - v0) % M, ((j - v0 - 1) % M) + 1) for k, i, j in segs[m:] + segs[:m]]
        vv = list(verts) + [n]
        Q = np.vstack([P, P[:1]])
        corner = _corners(P, verts, trim)

    q = 0
    while len(segs) >= 3 and q < len(segs):
        k, i, j = segs[q]
        if sliver(segs[q]) and 0 < q < len(segs) - 1:
            (ka, ia, ja), (kb, ib, jb) = segs[q - 1], segs[q + 1]
            if ka != "spline" and vv[ja] - vv[ia] > 3 * (vv[j] - vv[i]) and feasible(ka, ia, j):
                segs[q - 1:q + 1] = [(ka, ia, j)]
                continue
            if kb != "spline" and vv[jb] - vv[ib] > 3 * (vv[j] - vv[i]) and feasible(kb, i, jb):
                segs[q:q + 2] = [(kb, i, jb)]
                continue
        q += 1

    # An organic stretch comes out as many small arcs with the odd short line where it happens to
    # run straight: make it one smooth spline instead. A designed shape (fillets joining long
    # straight edges) is recognised by being mostly line, and left alone.
    def organic(run):
        curved = sum(1 for k, _, _ in run if k != "line")
        straight = sum(vv[j] - vv[i] for k, i, j in run if k == "line")
        return curved >= 4 and straight < 0.35 * (vv[run[-1][2]] - vv[run[0][1]])

    if not corner.any() and organic(segs):
        segs = [("spline", 0, M)]
    else:
        merged, run = [], []

        def flush():
            if run and organic(run):
                merged.append(("spline", run[0][1], run[-1][2]))
            else:
                merged.extend(run)
            del run[:]

        for seg in segs:                                       # runs end at corners (and the seam)
            if run and corner[seg[1] % M]:
                flush()
            run.append(seg)
        flush()
        segs = merged

    if len(segs) == 1 and segs[0][0] == "spline":
        sp = _spline_points(Q, 0, n, tol, smooth)[:-1]
        return [("spline", sp, True)]

    full = []
    for k, i, j in segs:                                       # final models from every point
        m = None
        if k != "spline":
            ta, tb = _ends(vv[i], vv[j], trim, corner[i], corner[j % M])
            c_ = Q[vv[i]:vv[j] + 1]
            m, ok = _robust(k, c_, tol, bump, cap=10 ** 9, ta=ta, tb=tb, tol_end=tol_end)
            if not ok:
                m, ok = _robust(k, c_, 1.5 * tol, bump, ta=ta, tb=tb, tol_end=1.5 * tol_end)
            if not ok:
                k, m = "spline", None
            elif k == "arc":
                m = (m[0], m[1], unbias(m[2]))
        full.append((k, i, j, m))
    segs = full
    K = len(segs)
    joints = []
    for q in range(K):                                        # joint at the START of segment q
        A, B = segs[q - 1], segs[q]
        span = min(vv[A[2]] - vv[A[1]], vv[B[2]] - vv[B[1]])
        joints.append(_joint(A, B, Q[vv[B[1]]], tol, max(6.0, 2.0 * trim, 0.35 * span),
                             bool(corner[B[1] % M])))

    prims = []
    for q, (kind, i, j, model) in enumerate(segs):
        p0, p1 = joints[q], joints[(q + 1) % K]
        a_, b_ = vv[i], vv[j]
        if kind == "line":
            if np.hypot(*(p1 - p0)) > 1e-6:
                prims.append(("line", tuple(p0), tuple(p1)))
        elif kind == "arc":
            c = np.array(model[:2])
            mid = Q[(a_ + b_) // 2] - c
            pm = c + mid / max(np.hypot(*mid), 1e-9) * model[2]
            prims.append(("arc", tuple(p0), tuple(pm), tuple(p1)))
        else:
            sp = _spline_points(Q, a_, b_, tol, smooth)
            sp[0], sp[-1] = tuple(p0), tuple(p1)
            if len(sp) == 2:
                prims.append(("line", sp[0], sp[1]))
            else:
                prims.append(("spline", sp, False))
    return prims


def prim_counts(prims):
    out = {}
    for p in prims:
        out[p[0]] = out.get(p[0], 0) + 1
    return out


def fit_quick(pts, tol=0.5, edge_sigma=1.1, smooth=0.0):
    """Cheap fit for borderline outlines (they only become construction geometry): a circle if it
    is one, otherwise a smooth closed spline. No line/arc search."""
    P = resample_closed(np.asarray(pts, float), 1.0)
    n = len(P)
    if n >= 12:
        cx, cy, r, res = fit_circle(P)
        if np.isfinite(r) and np.abs(res).max() <= 3.0 * tol:
            r = r + edge_sigma ** 2 / (2.0 * r) if r > 2 * edge_sigma else r
            return [("circle", float(cx), float(cy), float(r))]
    sp = _spline_points(np.vstack([P, P[:1]]), 0, n, 2.0 * tol, smooth)[:-1]
    if len(sp) < 3:
        return []
    return [("spline", sp, True)]
