# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
# Copied from the ImageToSketch project by tools/sync_imagetosketch.py - edit it there.
"""Transition mode: neighbouring colour areas use one and the same curve for the edge they share.

Each colour area's outline is fitted on its own, so the edge two neighbours share comes out
twice, a few hundredths of a millimetre apart. For pieces lying side by side that does not
matter; for anything that joins them - Part Design padding one area after another into the same
body, fusing the STEP solids - those near-misses make slivers, and FreeCAD 1.0 lost volume and
failed with "Removing splitter failed" on them (docs/TRANSITIONS.md).

Here the outlines become a network of shared edges:

  * Outlines are taken one at a time, outer outlines before holes, longest first. Each one is
    compared with the outlines of other colour areas already taken ("fixed").
  * Where it runs along a fixed neighbour for long enough (a "run"), the run's two ends are
    junctions. On the neighbour, a junction becomes a vertex: an existing one if one is close,
    otherwise the neighbour's edge is split there. Junctions are shared out through a list of
    junction points, so where three areas meet all three use the same point.
  * The run is then replaced by the neighbour's own edges, walked the other way. What is left of
    the outline keeps its own curves, with its ends moved onto the junctions.
  * Splitting an edge splits it for every outline that uses it, so an edge that is shared stays
    one edge.

Every outline is checked afterwards (closed, end to end; area not changed by more than the
junction moves can explain). One that fails keeps its own curves, and is counted.

Primitives are in pixels of the working picture: ("line", p0, p1), ("arc", p0, pm, p1),
("circle", cx, cy, r), ("ellipse", cx, cy, a, b, angle), ("spline", [points], periodic).
"""
import math

import numpy as np

from .geom import arc_from_3pts, cubic_spline, poly_area

TWO_PI = 2.0 * math.pi


# ------------------------------------------------------------------ primitives --
def _pt(q):
    return (float(q[0]), float(q[1]))


def _norm(p):
    """The same primitive with plain float tuples everywhere (exact comparison of end points)."""
    k = p[0]
    if k == "line":
        return ("line", _pt(p[1]), _pt(p[2]))
    if k == "arc":
        return ("arc", _pt(p[1]), _pt(p[2]), _pt(p[3]))
    if k == "circle":
        return ("circle", float(p[1]), float(p[2]), float(p[3]))
    if k == "ellipse":
        return ("ellipse",) + tuple(float(v) for v in p[1:6])
    return ("spline", [_pt(q) for q in p[1]], bool(p[2]))


def is_closed(p):
    return p[0] in ("circle", "ellipse") or (p[0] == "spline" and bool(p[2]))


def start(p):
    return p[1] if p[0] in ("line", "arc") else p[1][0]


def end(p):
    return p[2] if p[0] == "line" else p[3] if p[0] == "arc" else p[1][-1]


def reverse(p):
    if p[0] == "line":
        return ("line", p[2], p[1])
    if p[0] == "arc":
        return ("arc", p[3], p[2], p[1])
    if p[0] == "spline" and not p[2]:
        return ("spline", list(reversed(p[1])), False)
    return p                                                   # closed: no direction of its own


def with_start(p, P):
    if p[0] == "line":
        return ("line", P, p[2])
    if p[0] == "arc":
        return ("arc", P, p[2], p[3])
    return ("spline", [P] + list(p[1][1:]), False)


def with_end(p, P):
    if p[0] == "line":
        return ("line", p[1], P)
    if p[0] == "arc":
        return ("arc", p[1], p[2], P)
    return ("spline", list(p[1][:-1]) + [P], False)


def _arc(p):
    g = arc_from_3pts(p[1], p[2], p[3])
    if g is None:
        return None
    cx, cy, r, a0, a1, ccw = g
    sweep = (a1 - a0) % TWO_PI if ccw else -((a0 - a1) % TWO_PI)
    return cx, cy, r, a0, sweep


def _spline_dense(pts, step):
    q = np.asarray(pts, float)
    gap = float(np.hypot(*np.diff(q, axis=0).T).max()) if len(q) > 1 else 1.0
    k = int(min(max(math.ceil(gap / step), 4), 200))
    return cubic_spline(pts, False, per_seg=k), k


def samples(p, step=1.0):
    """Points along an open primitive with their parameter t (0 at the start, 1 at the end)."""
    k = p[0]
    if k == "line":
        L = math.hypot(p[2][0] - p[1][0], p[2][1] - p[1][1])
        t = np.linspace(0.0, 1.0, max(int(L / step), 1) + 1)
        a, b = np.array(p[1]), np.array(p[2])
        return t, a[None, :] * (1 - t[:, None]) + b[None, :] * t[:, None]
    if k == "arc":
        g = _arc(p)
        if g is None:
            return samples(("line", p[1], p[3]), step)
        cx, cy, r, a0, sw = g
        t = np.linspace(0.0, 1.0, max(int(abs(sw) * r / step), 2) + 1)
        a = a0 + sw * t
        return t, np.c_[cx + r * np.cos(a), cy + r * np.sin(a)]
    D, _ = _spline_dense(p[1], step)
    return np.linspace(0.0, 1.0, len(D)), np.asarray(D, float)


def point_at(p, t):
    if p[0] == "line":
        return ((1 - t) * p[1][0] + t * p[2][0], (1 - t) * p[1][1] + t * p[2][1])
    if p[0] == "arc":
        g = _arc(p)
        if g is None:
            return point_at(("line", p[1], p[3]), t)
        cx, cy, r, a0, sw = g
        return (cx + r * math.cos(a0 + sw * t), cy + r * math.sin(a0 + sw * t))
    D, _ = _spline_dense(p[1], 0.5)
    return _pt(D[int(round(t * (len(D) - 1)))])


def _spline_or_line(pts):
    pts = [q for i, q in enumerate(pts) if i == 0 or q != pts[i - 1]]
    if len(pts) < 2:
        return None
    if len(pts) == 2:
        return ("line", pts[0], pts[1])
    return ("spline", pts, False)


def split(p, t):
    """(first part, second part) of an open primitive, cut at parameter t. They meet exactly."""
    if p[0] == "line":
        X = point_at(p, t)
        return ("line", p[1], X), ("line", X, p[2])
    if p[0] == "arc":
        g = _arc(p)
        if g is None:
            return split(("line", p[1], p[3]), t)
        X = point_at(p, t)
        return ("arc", p[1], point_at(p, t / 2.0), X), ("arc", X, point_at(p, (1.0 + t) / 2.0), p[3])
    pts = list(p[1])
    D, k = _spline_dense(pts, 0.5)
    idx = int(round(t * (len(D) - 1)))
    idx = min(max(idx, 1), len(D) - 2)
    j, rem = divmod(idx, k)
    if rem == 0:                                               # right on a point it passes through
        a, b = pts[:j + 1], pts[j:]
    else:
        X = _pt(D[idx])
        a, b = pts[:j + 1] + [X], [X] + pts[j + 1:]
    return _spline_or_line(a), _spline_or_line(b)


def open_up(p, P):
    """A closed primitive as open pieces starting (and ending) at its point nearest P."""
    if p[0] == "circle":
        cx, cy, r = p[1:4]
        f = math.atan2(P[1] - cy, P[0] - cx)
        pt = lambda a: (cx + r * math.cos(a), cy + r * math.sin(a))    # noqa: E731
        A, B = pt(f), pt(f + math.pi)
        return [("arc", A, pt(f + math.pi / 2), B), ("arc", B, pt(f + 3 * math.pi / 2), A)]
    if p[0] == "ellipse":
        cx, cy, a, b, th = p[1:6]
        c, s = math.cos(th), math.sin(th)
        t = np.linspace(0, TWO_PI, 48, endpoint=False)
        Q = np.c_[cx + a * np.cos(t) * c - b * np.sin(t) * s, cy + a * np.cos(t) * s + b * np.sin(t) * c]
    else:
        Q = np.asarray(p[1], float)
    i = int(np.argmin(np.hypot(Q[:, 0] - P[0], Q[:, 1] - P[1])))
    Q = np.roll(Q, -i, axis=0)
    pts = [_pt(q) for q in Q] + [_pt(Q[0])]
    h = len(pts) // 2
    return [x for x in (_spline_or_line(pts[:h + 1]), _spline_or_line(pts[h:])) if x]


# --------------------------------------------------------------------- network --
class _Net:
    """Edges in a table, outlines as lists of (edge id, reversed?)."""

    def __init__(self):
        self.E = {}
        self.loops = []
        self._n = 0

    def add(self, p):
        self._n += 1
        self.E[self._n] = p
        return self._n

    def prim(self, ref):
        p = self.E[ref[0]]
        return reverse(p) if ref[1] else p

    def replace(self, eid, pieces):
        """Edge eid becomes the edges `pieces` (in its own direction) in every outline."""
        ids = [self.add(q) for q in pieces]
        for li, refs in enumerate(self.loops):
            if refs is None:
                continue
            out = []
            for r in refs:
                if r[0] != eid:
                    out.append(r)
                elif not r[1]:
                    out += [(i, False) for i in ids]
                else:
                    out += [(i, True) for i in reversed(ids)]
            self.loops[li] = out
        del self.E[eid]
        return ids

    def drop_degenerate(self):
        """Pieces whose two ends are one point (left when two junctions were merged) go: they
        would be a dot - or, for an arc, a whole circle - where nothing is."""
        bad = [eid for eid, p in self.E.items() if not is_closed(p) and start(p) == end(p)]
        for eid in bad:
            for li, refs in enumerate(self.loops):
                if refs is not None and any(r[0] == eid for r in refs):
                    self.loops[li] = [r for r in refs if r[0] != eid]
            del self.E[eid]
        return len(bad)

    def move_vertex(self, old, new):
        if old == new:
            return
        for eid, p in list(self.E.items()):
            if is_closed(p):
                continue
            if start(p) == old:
                p = with_start(p, new)
            if end(p) == old:
                p = with_end(p, new)
            self.E[eid] = p

    def sample(self, li, step):
        """Points along outline li: (xy N x 2, index of the ref in the outline, t on that ref)."""
        xy, ri, tt = [], [], []
        for k, ref in enumerate(self.loops[li]):
            p = self.prim(ref)
            if is_closed(p):
                from .geom import sample_prims
                P = sample_prims([p], step)
                t = np.linspace(0, 1, len(P), endpoint=False)
            else:
                t, P = samples(p, step)
                t, P = t[:-1], P[:-1]                          # the end is the next one's start
            xy.append(P)
            ri.append(np.full(len(P), k))
            tt.append(t)
        if not xy:
            return np.zeros((0, 2)), np.zeros(0, int), np.zeros(0)
        return np.vstack(xy), np.concatenate(ri), np.concatenate(tt)

    def area(self, li, step=1.0):
        P = np.vstack([self.sample(li, step)[0]])
        return poly_area(P) if len(P) >= 3 else 0.0

    def continuous(self, li):
        refs = self.loops[li]
        if len(refs) == 1 and is_closed(self.prim(refs[0])):
            return True
        ps = [self.prim(r) for r in refs]
        if any(is_closed(p) for p in ps):
            return False
        return all(end(ps[i]) == start(ps[(i + 1) % len(ps)]) for i in range(len(ps)))


def _nearest(B, A, chunk=1024):
    """For every point of B: distance to the nearest point of A, and that point's index."""
    d = np.empty(len(B))
    k = np.empty(len(B), int)
    for i in range(0, len(B), chunk):
        D = np.hypot(B[i:i + chunk, None, 0] - A[None, :, 0], B[i:i + chunk, None, 1] - A[None, :, 1])
        k[i:i + chunk] = D.argmin(axis=1)
        d[i:i + chunk] = D[np.arange(len(D)), k[i:i + chunk]]
    return d, k


def _distance_to(B, A, margin):
    """For every point of B: about how far it is from outline A (a closed polyline), to a
    half a pixel or so; inf where it is further than `margin`. Measured on a small raster that
    covers only where the two outlines can be near each other, so it costs little however long
    they are."""
    import cv2
    lo = np.maximum(B.min(axis=0), A.min(axis=0)) - margin - 2
    hi = np.minimum(B.max(axis=0), A.max(axis=0)) + margin + 2
    d = np.full(len(B), np.inf)
    if (hi <= lo).any():
        return d
    f = 2.0                                                    # half pixels
    W, H = int(math.ceil((hi[0] - lo[0]) * f)) + 1, int(math.ceil((hi[1] - lo[1]) * f)) + 1
    canvas = np.full((H, W), 255, np.uint8)
    q = np.round((np.vstack([A, A[:1]]) - lo) * f * 16).astype(np.int32).reshape(-1, 1, 2)
    cv2.polylines(canvas, [q], False, 0, 1, cv2.LINE_8, 4)
    dt = cv2.distanceTransform(canvas, cv2.DIST_L2, 3) / f
    inside = (B[:, 0] >= lo[0]) & (B[:, 1] >= lo[1]) & (B[:, 0] <= hi[0]) & (B[:, 1] <= hi[1])
    if inside.any():
        P = np.round((B[inside] - lo) * f).astype(int)
        P[:, 0] = np.clip(P[:, 0], 0, W - 1)
        P[:, 1] = np.clip(P[:, 1], 0, H - 1)
        d[inside] = dt[P[:, 1], P[:, 0]]
    d[d > margin] = np.inf
    return d


def _runs(lab):
    """Cyclic runs of equal labels: [(label, first index, last index)]; the list starts at a
    label change (or is one run covering everything)."""
    n = len(lab)
    ch = [i for i in range(n) if lab[i] != lab[i - 1]]
    if not ch:
        return [(lab[0], 0, n - 1)]
    out = []
    for j, s in enumerate(ch):
        e = ch[(j + 1) % len(ch)] - 1
        out.append((lab[s], s, e % n))
    return out


def _run_len(s, e, n):
    return (e - s) % n + 1


# ------------------------------------------------------------------------ weld --
def weld(loops, groups, holes, reach, step=1.0):
    """loops: list of closed primitive lists (pixels). groups: colour area of each. holes: is it
    a hole. reach: how far apart two outlines may be and still be one edge (pixels).
    Returns (new primitive lists, report dict)."""
    net = _Net()
    for prims in loops:
        net.loops.append([(net.add(_norm(p)), False) for p in prims])
    n = len(loops)
    rep = dict(shared_runs=0, outlines_welded=0, failed=0, whole=0)
    if n < 2:
        return [list(p) for p in loops], rep
    snap = max(2.0 * reach, 3.0 * step)                         # scraps shorter than this go
    near = max(1.0, 0.6 * reach)                               # one junction, seen from two outlines
    min_run = max(3.0 * reach, 6.0 * step)
    canon = []                                                 # junction points everybody uses

    def canon_near(P, r=near):
        best, bd = None, r
        for Q in canon:
            d = math.hypot(Q[0] - P[0], Q[1] - P[1])
            if d <= bd:
                best, bd = Q, d
        return best

    lengths = []
    for i in range(n):
        P = net.sample(i, step)[0]
        lengths.append(float(np.hypot(*np.diff(np.vstack([P, P[:1]]), axis=0).T).sum()) if len(P) > 1 else 0.0)
    order = sorted(range(n), key=lambda i: (bool(holes[i]), -lengths[i]))
    fixed = []
    for b in order:
        others = [a for a in fixed if groups[a] != groups[b]]
        if others:
            before = list(net.loops[b])
            area0 = net.area(b, step)
            try:
                changed = _weld_one(net, b, others, reach, snap, near, min_run, step, canon, canon_near, rep)
            except Exception:
                changed, net.loops[b] = False, before
                rep["failed"] += 1
            if changed:
                net.drop_degenerate()
                area1 = net.area(b, step)
                tol_area = max(0.03 * area0, 0.6 * snap * lengths[b])
                if not net.continuous(b) or abs(area1 - area0) > tol_area or _spiky(net, b) \
                        or _crosses(net, b, canon, 4.0 * snap):
                    net.loops[b] = before
                    rep["failed"] += 1
                else:
                    rep["outlines_welded"] += 1
        fixed.append(b)
    out = []
    for i in range(n):
        out.append([net.prim(r) for r in net.loops[i]])
    return out, rep


def _vertex_on(net, a, P, snap, canon, canon_near, corner_r=None):
    V = _vertex_on_(net, a, P, snap, canon, canon_near, corner_r)
    Q = canon_near(V, snap)                                    # two junctions this close are one
    if Q is not None and Q != V:
        net.move_vertex(V, Q)
        for i, R in enumerate(canon):
            if R == V:
                canon[i] = Q
        V = Q
    return V


def _vertex_on_(net, a, P, snap, canon, canon_near, corner_r=None):
    """Make sure outline a has a vertex for the junction at P (a corner of the outline being
    welded, or failing that the end of its run along a): an existing vertex of a if one is that
    close, otherwise a's edge is split where P falls on it. Returns the vertex. `snap` here is
    the junction radius; `corner_r` how far apart two fits' versions of one corner may be."""
    corner_r = corner_r if corner_r is not None else snap
    Q = canon_near(P)
    target = Q if Q is not None else P
    xy, ri, tt = net.sample(a, 0.5)
    k = int(np.argmin(np.hypot(xy[:, 0] - target[0], xy[:, 1] - target[1])))
    refs = net.loops[a]
    ref = refs[ri[k]]
    p = net.prim(ref)
    if is_closed(p):                                           # open the circle (etc.) up right here
        ids = net.replace(ref[0], open_up(net.E[ref[0]], tuple(xy[k])))
        V = start(net.E[ids[0]])
        if Q is not None and math.hypot(V[0] - Q[0], V[1] - Q[1]) <= snap:
            net.move_vertex(V, Q)
            V = Q
        if Q is None:
            canon.append(V)
        return V
    # An existing vertex close by? A corner of a (where it turns) is the same corner as the one
    # being welded even a few pixels away - two fits seldom put a corner in exactly the same
    # place. A vertex where a just runs on (a point where it was split before) only counts if it
    # is really close; otherwise a is split right here.
    ps = [net.prim(r) for r in refs]
    best, bd = None, None
    for i, p in enumerate(ps):
        V = start(p)
        d = math.hypot(V[0] - target[0], V[1] - target[1])
        a_, b_ = _direction(ps[i - 1], True), _direction(p, False)
        turn = math.degrees(math.acos(max(-1.0, min(1.0, a_[0] * b_[0] + a_[1] * b_[1])))) if a_ and b_ else 0.0
        reach_v = corner_r if turn > 15.0 else snap
        if d <= reach_v and (bd is None or d < bd):
            best, bd = V, d
    if best is not None:
        if Q is not None and best != Q:
            net.move_vertex(best, Q)
            best = Q
        if Q is None and canon_near(best, 1e-9) is None:
            canon.append(best)
        return best
    t = float(tt[k])
    if ref[1]:
        t = 1.0 - t
    e = net.E[ref[0]]
    if t <= 1e-3 or t >= 1 - 1e-3:
        V = start(e) if t <= 0.5 else end(e)
    else:
        p1, p2 = split(e, t)
        if p1 is None or p2 is None:
            V = start(e) if t <= 0.5 else end(e)
        else:
            net.replace(ref[0], [p1, p2])
            V = end(p1)
    if Q is not None and V != Q and math.hypot(V[0] - Q[0], V[1] - Q[1]) <= snap:
        net.move_vertex(V, Q)
        V = Q
    if canon_near(V, 1e-9) is None:
        canon.append(V)
    return V


def _average(net, a, path, run_pts, reach, canon):
    """Both neighbours traced the edge they share; until now it was the one fixed first that
    counted. For straight pieces, meet halfway: move the line by half the mean distance from it
    to the other outline's own points along it. The ends move with it (for every outline that
    uses them), so everything stays joined. Returns the path's new ends and the path."""
    for ref in path:
        e = net.E[ref[0]]
        if e[0] != "line":
            continue
        (x0, y0), (x1, y1) = e[1], e[2]
        L = math.hypot(x1 - x0, y1 - y0)
        if L < 4.0 * reach:
            continue
        ux, uy = (x1 - x0) / L, (y1 - y0) / L
        rel = run_pts - (x0, y0)
        along = rel[:, 0] * ux + rel[:, 1] * uy
        off = rel[:, 0] * -uy + rel[:, 1] * ux                 # signed distance from the line
        m = (along > 0.15 * L) & (along < 0.85 * L) & (np.abs(off) <= reach)
        if m.sum() < 5:
            continue
        h = 0.5 * float(np.median(off[m]))
        if abs(h) < 1e-3:
            continue
        for old in ((x0, y0), (x1, y1)):
            new = (old[0] - uy * h, old[1] + ux * h)
            net.move_vertex(old, new)
            for i, Q in enumerate(canon):
                if Q == old:
                    canon[i] = new
    ps = [net.prim(r) for r in path]
    return start(ps[0]), end(ps[-1]), path


def _path(net, a, V0, V1, mid):
    """Outline a's edges from vertex V0 to vertex V1, the way that passes nearer `mid`, as refs
    walked from V0 to V1."""
    refs = net.loops[a]
    m = len(refs)
    ps = [net.prim(r) for r in refs]
    i0 = [i for i in range(m) if start(ps[i]) == V0]
    i1 = [i for i in range(m) if end(ps[i]) == V1]
    if not i0 or not i1:
        return None
    i0, i1 = i0[0], i1[0]
    fwd = [refs[(i0 + j) % m] for j in range((i1 - i0) % m + 1)]
    j0 = [i for i in range(m) if start(ps[i]) == V1][0]
    j1 = [i for i in range(m) if end(ps[i]) == V0][0]
    back = [refs[(j0 + j) % m] for j in range((j1 - j0) % m + 1)]
    back = [(r[0], not r[1]) for r in reversed(back)]

    def dist(path):
        P = np.vstack([samples(net.prim(r), 1.0)[1] for r in path])
        return float(np.hypot(P[:, 0] - mid[0], P[:, 1] - mid[1]).min())
    return fwd if dist(fwd) <= dist(back) else back


def _piece(p, t0, t1):
    """The part of an open primitive between parameters t0 < t1."""
    q = p
    if t1 < 1 - 1e-6:
        q = split(q, t1)[0]
        if q is None:
            return None
        t0 = t0 / t1 if t1 > 0 else 0.0
    if t0 > 1e-6:
        q = split(q, t0)[1]
    return q


def _own(net, b, ri, tt, k0, k1, n):
    """Outline b's own primitives from sample k0 forward to sample k1."""
    refs = net.loops[b]
    m = len(refs)
    ra, ta = int(ri[k0]), float(tt[k0])
    rb, tb = int(ri[k1]), float(tt[k1])
    count = (rb - ra) % m
    if count == 0 and tb < ta:
        count = m                                              # all the way round
    if count == 0:
        q = _piece(net.prim(refs[ra]), ta, tb) if tb > ta + 1e-6 else None
        return [q] if q is not None else []
    out = []
    q = _piece(net.prim(refs[ra]), ta, 1.0) if ta < 1 - 1e-6 else None
    if q is not None:
        out.append(q)
    for j in range(1, count):
        out.append(net.prim(refs[(ra + j) % m]))
    q = _piece(net.prim(refs[rb]), 0.0, tb) if tb > 1e-6 else None
    if q is not None:
        out.append(q)
    return out


def _weld_one(net, b, others, reach, snap, near, min_run, step, canon, canon_near, rep):
    xy, ri, tt = net.sample(b, step)
    n = len(xy)
    if n < 8:
        return False
    lab = np.full(n, -1)
    best = np.full(n, np.inf)
    lo, hi = xy.min(axis=0) - snap, xy.max(axis=0) + snap
    for a in others:
        A = net.sample(a, step)[0]
        if not len(A) or (A.max(axis=0) < lo).any() or (A.min(axis=0) > hi).any():
            continue
        d = _distance_to(xy, A, reach + 1.0)
        m = (d <= reach) & (d < best)
        lab[m], best[m] = a, d[m]
    if (lab < 0).all():
        return False
    # tidy the labels: runs that are too short do not count; short gaps inside a run are closed
    for _ in range(2):
        for L, s, e in _runs(list(lab)):
            ln = _run_len(s, e, n) * step
            if L >= 0 and ln < min_run:
                lab[np.arange(s, s + _run_len(s, e, n)) % n] = -1
        rs = _runs(list(lab))
        for j, (L, s, e) in enumerate(rs):
            if L < 0 and _run_len(s, e, n) * step <= reach and len(rs) > 2:
                prev, nxt = rs[j - 1][0], rs[(j + 1) % len(rs)][0]
                if prev == nxt and prev >= 0:
                    lab[np.arange(s, s + _run_len(s, e, n)) % n] = prev
    rs = _runs(list(lab))
    if all(L < 0 for L, _, _ in rs):
        return False
    if len(rs) == 1:                                           # all of it lies on one neighbour
        a = rs[0][0]
        A = net.sample(a, step)[0]
        d = _distance_to(A, xy, reach + 1.0)
        if (d <= reach).all():                                 # ... and that neighbour on it: one loop
            refs = list(net.loops[a])
            sa = np.sign(_signed(net, a)) or 1.0
            sb = np.sign(_signed(net, b)) or 1.0
            net.loops[b] = refs if sa == sb else [(r[0], not r[1]) for r in reversed(refs)]
            rep["whole"] += 1
            rep["shared_runs"] += 1
            return True
        return False
    # a closed primitive (a circle as the whole outline) has no ends to cut at: open it up first
    refs = net.loops[b]
    if len(refs) == 1 and is_closed(net.prim(refs[0])):
        s0 = rs[0][1]
        net.loops[b] = [(net.add(q), False) for q in open_up(net.prim(refs[0]), tuple(xy[s0]))]
        return _weld_one(net, b, others, reach, snap, near, min_run, step, canon, canon_near, rep)
    # Junctions. Where an outline leaves its neighbour it has a corner of its own (the fitter puts
    # corners where two edges really meet), while the end of the run - the last point within
    # reach - lies beyond it by up to reach / sin(angle). So the corner is the junction, if
    # there is one near the run's end.
    corner = [k for k in range(n) if ri[k] != ri[k - 1]] or [0]
    win = int(3.0 * reach / step) + 2

    def at_corner(k):
        best, bd = k, win + 1
        for c in corner:
            dk = min((c - k) % n, (k - c) % n)
            if dk < bd:
                best, bd = c, dk
        return best
    shared = []
    for L, s, e in rs:
        if L < 0:
            continue
        s, e = at_corner(s), at_corner(e)
        if _run_len(s, e, n) * step < min_run / 2:
            continue
        V0 = _vertex_on(net, L, tuple(xy[s]), near, canon, canon_near, snap)
        V1 = _vertex_on(net, L, tuple(xy[e]), near, canon, canon_near, snap)
        if V0 == V1:
            continue
        mid = tuple(xy[(s + _run_len(s, e, n) // 2) % n])
        path = _path(net, L, V0, V1, mid)
        if not path:
            continue
        run_pts = xy[np.arange(s, s + _run_len(s, e, n)) % n]
        V0, V1, path = _average(net, L, path, run_pts, reach, canon)
        shared.append((s, e, V0, V1, path))
    if not shared:
        return False
    # the new outline: own pieces between the shared runs, ends moved onto the junctions
    new = []
    # (ends are read again here: meeting halfway on a later run may have moved an earlier one's)
    ends = lambda path: (start(net.prim(path[0])), end(net.prim(path[-1])))    # noqa: E731
    for j, (s, e, _, _, path) in enumerate(shared):
        V0, V1 = ends(path)
        new += path
        s_next, _, _, _, path_next = shared[(j + 1) % len(shared)]
        V0_next = ends(path_next)[0]
        gap = (s_next - e) % n
        if V1 == V0_next:
            continue
        if math.hypot(V1[0] - V0_next[0], V1[1] - V0_next[1]) < near:
            net.move_vertex(V0_next, V1)                       # one junction, not two a hair apart
            for i, R in enumerate(canon):
                if R == V0_next:
                    canon[i] = V1
            continue
        if gap <= 1:
            new.append((net.add(("line", V1, V0_next)), False))
            continue
        own = _own(net, b, ri, tt, e, s_next, n)
        # a scrap left over between the end of a run and the next corner of the outline goes:
        # the junction itself is the corner now
        while own and _length(own[0]) < snap and len(own) > 1:
            own.pop(0)
        while own and _length(own[-1]) < snap and len(own) > 1:
            own.pop()
        if not own:
            new.append((net.add(("line", V1, V0_next)), False))
            continue
        if len(own) == 1 and _length(own[0]) < snap:
            own = [("line", V1, V0_next)]
        own[0] = with_start(own[0], V1)
        own[-1] = with_end(own[-1], V0_next)
        new += [(net.add(q), False) for q in own]
    net.loops[b] = new
    rep["shared_runs"] += len(shared)
    return True


def _crosses(net, li, centres, R, step=0.5):
    """True if outline li crosses itself near any of the junction points (where welding changed
    it): a loop like that makes no valid face. Checked on a fine polyline, near the junctions
    only, so it stays cheap."""
    P = net.sample(li, step)[0]
    if len(P) < 4 or not centres:
        return False
    C = np.asarray(centres, float)
    near = np.zeros(len(P), bool)
    for i in range(0, len(C), 64):
        D = np.hypot(P[:, None, 0] - C[None, i:i + 64, 0], P[:, None, 1] - C[None, i:i + 64, 1])
        near |= (D <= R).any(axis=1)
    idx = np.nonzero(near)[0]
    if len(idx) < 3:
        return False
    a = P[idx]
    b = P[(idx + 1) % len(P)]
    m = len(idx)
    for i in range(m):
        j = np.arange(i + 2, m)
        j = j[(idx[j] != (idx[i] - 1) % len(P)) & (idx[j] != (idx[i] + 1) % len(P))]
        if not len(j):
            continue
        p0, p1 = a[i], b[i]
        q0, q1 = a[j], b[j]
        d1 = (p1[0] - p0[0]) * (q0[:, 1] - p0[1]) - (p1[1] - p0[1]) * (q0[:, 0] - p0[0])
        d2 = (p1[0] - p0[0]) * (q1[:, 1] - p0[1]) - (p1[1] - p0[1]) * (q1[:, 0] - p0[0])
        d3 = (q1[:, 0] - q0[:, 0]) * (p0[1] - q0[:, 1]) - (q1[:, 1] - q0[:, 1]) * (p0[0] - q0[:, 0])
        d4 = (q1[:, 0] - q0[:, 0]) * (p1[1] - q0[:, 1]) - (q1[:, 1] - q0[:, 1]) * (p1[0] - q0[:, 0])
        if ((d1 * d2 < 0) & (d3 * d4 < 0)).any():
            return True
    return False


def _direction(p, at_end):
    t, P = samples(p, 0.25)
    if len(P) < 2:
        return None
    v = (P[-1] - P[-2]) if at_end else (P[1] - P[0])
    n = math.hypot(v[0], v[1])
    return None if n < 1e-12 else (v[0] / n, v[1] / n)


def _spiky(net, li):
    """True if the outline doubles back on itself anywhere (a turn of more than 175 degrees from
    one piece to the next, a piece of no length, or a corner it passes through twice - out along
    one piece and back along another): such an outline makes no valid face."""
    ps = [net.prim(r) for r in net.loops[li]]
    if len(ps) == 1 and is_closed(ps[0]):
        return False
    corners = [start(p) for p in ps if not is_closed(p)]
    if len(set(corners)) != len(corners):                      # passes through one point twice: a spur
        return True
    for i, p in enumerate(ps):
        if _length(p) < 1e-6:
            return True
        a, b = _direction(ps[i - 1], True), _direction(p, False)
        if a is not None and b is not None and a[0] * b[0] + a[1] * b[1] < -0.996:
            return True
    return False


def _length(p):
    if p[0] == "line":
        return math.hypot(p[2][0] - p[1][0], p[2][1] - p[1][1])
    P = samples(p, 1.0)[1]
    return float(np.hypot(*np.diff(P, axis=0).T).sum()) if len(P) > 1 else 0.0


def _signed(net, li):
    P = net.sample(li, 1.0)[0]
    if len(P) < 3:
        return 0.0
    x, y = P[:, 0], P[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))
