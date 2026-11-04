# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
# Copied from the ImageToSketch project by tools/sync_imagetosketch.py - edit it there.
"""Small geometry helpers shared by export, overlay and tests (numpy only)."""
import math

import numpy as np


def arc_from_3pts(p0, pm, p1):
    """Circle through three points. Returns (cx, cy, r, a0, a1, ccw) with angles in radians such
    that going from a0 to a1 in the given direction passes through pm. None if collinear."""
    (x0, y0), (xm, ym), (x1, y1) = p0, pm, p1
    d = 2 * (x0 * (ym - y1) + xm * (y1 - y0) + x1 * (y0 - ym))
    if abs(d) < 1e-12:
        return None
    s0, sm, s1 = x0 * x0 + y0 * y0, xm * xm + ym * ym, x1 * x1 + y1 * y1
    cx = (s0 * (ym - y1) + sm * (y1 - y0) + s1 * (y0 - ym)) / d
    cy = (s0 * (x1 - xm) + sm * (x0 - x1) + s1 * (xm - x0)) / d
    r = math.hypot(x0 - cx, y0 - cy)
    a0 = math.atan2(y0 - cy, x0 - cx)
    am = math.atan2(ym - cy, xm - cx)
    a1 = math.atan2(y1 - cy, x1 - cx)
    two_pi = 2 * math.pi
    ccw = ((am - a0) % two_pi) < ((a1 - a0) % two_pi)
    return cx, cy, r, a0, a1, ccw


def cubic_spline(points, periodic=False, per_seg=12):
    """Sample a C2 cubic spline through the points (chord-length parameter; natural ends, or
    periodic). Used for previews/exports; close to what CAD kernels build from the same points."""
    P = np.asarray(points, float)
    if periodic:
        P = np.vstack([P, P[:1]])
    n = len(P)
    if n < 3:
        return P
    h = np.maximum(np.hypot(*np.diff(P, axis=0).T), 1e-9)
    out = []
    for d in range(2):
        y = P[:, d]
        if periodic:
            m = n - 1                                          # unknowns M_0..M_{m-1}
            A = np.zeros((m, m))
            b = np.zeros(m)
            for i in range(m):
                hp, hn = h[i - 1], h[i]                        # h[-1] wraps correctly
                A[i, (i - 1) % m] += hp
                A[i, i] += 2 * (hp + hn)
                A[i, (i + 1) % m] += hn
                b[i] = 6 * ((y[i + 1] - y[i]) / hn - (y[i] - y[i - 1 if i else m - 1]) / hp)
            M = np.linalg.solve(A, b)
            M = np.concatenate([M, M[:1]])
        else:
            A = np.zeros((n, n))
            b = np.zeros(n)
            A[0, 0] = A[-1, -1] = 1.0
            for i in range(1, n - 1):
                A[i, i - 1], A[i, i], A[i, i + 1] = h[i - 1], 2 * (h[i - 1] + h[i]), h[i]
                b[i] = 6 * ((y[i + 1] - y[i]) / h[i] - (y[i] - y[i - 1]) / h[i - 1])
            M = np.linalg.solve(A, b)
        vals = []
        for i in range(n - 1):
            u = np.linspace(0, h[i], per_seg, endpoint=False)
            a_ = (M[i + 1] - M[i]) / (6 * h[i])
            b_ = M[i] / 2
            c_ = (y[i + 1] - y[i]) / h[i] - h[i] * (2 * M[i] + M[i + 1]) / 6
            vals.append(y[i] + c_ * u + b_ * u ** 2 + a_ * u ** 3)
        vals.append(np.array([y[-1]]))
        out.append(np.concatenate(vals))
    S = np.c_[out[0], out[1]]
    return S[:-1] if periodic else S


def sample_prims(prims, step=1.0):
    """Polyline (Nx2) following a loop's primitives, for drawing and measuring."""
    pts = []
    for p in prims:
        k = p[0]
        if k == "circle":
            n = max(int(2 * math.pi * p[3] / step), 16)
            a = np.linspace(0, 2 * math.pi, n, endpoint=False)
            pts.append(np.c_[p[1] + p[3] * np.cos(a), p[2] + p[3] * np.sin(a)])
        elif k == "ellipse":
            cx, cy, a, b, th = p[1:6]
            n = max(int(2 * math.pi * math.sqrt((a * a + b * b) / 2) / step), 24)
            t = np.linspace(0, 2 * math.pi, n, endpoint=False)
            c, s_ = math.cos(th), math.sin(th)
            pts.append(np.c_[cx + a * np.cos(t) * c - b * np.sin(t) * s_, cy + a * np.cos(t) * s_ + b * np.sin(t) * c])
        elif k == "line":
            L = math.hypot(p[2][0] - p[1][0], p[2][1] - p[1][1])
            n = max(int(L / step), 1)
            u = np.linspace(0, 1, n, endpoint=False)[:, None]
            pts.append(np.array(p[1]) * (1 - u) + np.array(p[2]) * u)
        elif k == "arc":
            g = arc_from_3pts(p[1], p[2], p[3])
            if g is None:
                pts.append(np.array([p[1]]))
                continue
            cx, cy, r, a0, a1, ccw = g
            sweep = (a1 - a0) % (2 * math.pi) if ccw else -((a0 - a1) % (2 * math.pi))
            n = max(int(abs(sweep) * r / step), 2)
            a = a0 + np.linspace(0, sweep, n, endpoint=False)
            pts.append(np.c_[cx + r * np.cos(a), cy + r * np.sin(a)])
        elif k == "spline":
            q = np.asarray(p[1], float)
            gap = float(np.hypot(*np.diff(q, axis=0).T).max()) if len(q) > 1 else 1.0
            S = cubic_spline(p[1], p[2], per_seg=int(min(max(math.ceil(gap / step), 4), 200)))
            pts.append(S if p[2] else S[:-1])
    return np.vstack(pts) if pts else np.zeros((0, 2))


def poly_area(P):
    x, y = P[:, 0], P[:, 1]
    return 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))
