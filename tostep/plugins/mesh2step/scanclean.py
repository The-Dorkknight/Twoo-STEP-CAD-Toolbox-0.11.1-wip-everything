# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""Scan clean-up (0.11.2): what a 3D scan brings that a CAD export doesn't, removed before converting.

  * loose pieces: scanner spray, bits of the turntable, single stray triangles. Each becomes its own
    "smooth curved component" later, and one stand-in scan had 973 of them.
  * non-manifold edges (three or more triangles on one edge): no solid can be built through them.
  * holes the scanner couldn't see: filled per hole (flat triangulation in the hole's own best plane,
    or a fan from its middle when that isn't a simple outline).

Modes (setting `scan_cleanup`):
  auto     (default) only when the mesh is not one closed piece. Drops open pieces smaller than 2 % of
           the largest piece's area and any piece under 0.1 % (so small closed parts of an assembly,
           such as screws, stay), cuts non-manifold edges, fills holes up to a quarter of the part's size across.
  on       the same, but drops every piece under 2 % of the largest, closed or not.
  largest  keeps only the largest piece (a scan stood on a turntable plate, a brim, a support).
  off      none of it.

Nothing here rounds or moves the surface; denoising stays the separate "Denoise iterations" setting.
Measured on the stand-in scan in docs/research/SCAN_CLEANUP.md.
"""
from __future__ import annotations

import time

import numpy as np
import trimesh

MODES = ("auto", "on", "largest", "off")


def _pieces(m):
    return m.split(only_watertight=False)


def drop_loose(m, mode, log):
    parts = _pieces(m)
    if len(parts) <= 1:
        return m
    areas = np.array([p.area for p in parts])
    big = float(areas.max())
    if mode == "largest":
        keep = [parts[int(np.argmax(areas))]]
    else:
        keep = []
        for p, a in zip(parts, areas):
            # specks, whatever they are: tiny in area, or a handful of triangles AND small next to the biggest piece.
            # (0.11.4: a handful of triangles alone was enough, so a CAD part of two boxes of 12 triangles each lost
            # both of them and the conversion failed with an IndexError; the golden gate's "stepped" case found it)
            if a < 0.001 * big or (len(p.faces) < 20 and a < 0.02 * big):
                continue
            if a < 0.02 * big and (mode == "on" or not p.is_watertight):
                continue                                  # small open fragments (or anything small, in "on")
            keep.append(p)
    if not keep:                                          # never end with nothing: the biggest piece stays
        keep = [parts[int(np.argmax(areas))]]
    gone = len(parts) - len(keep)
    if not gone:
        return m
    kept_area = sum(p.area for p in keep)
    log(f"Scan clean-up: removed {gone:,} loose piece(s) ({100 * (1 - kept_area / areas.sum()):.1f} % of the "
        f"surface), kept {len(keep)}")
    return trimesh.util.concatenate(keep) if len(keep) > 1 else keep[0]


def cut_nonmanifold(m, log):
    e = m.edges_sorted
    _, inv, cnt = np.unique(e, axis=0, return_inverse=True, return_counts=True)
    bad = (cnt[inv.ravel()] > 2).reshape(-1, 3).any(1)
    n = int(bad.sum())
    if n:
        m.update_faces(~bad)
        m.remove_unreferenced_vertices()
        log(f"Scan clean-up: cut {n:,} triangle(s) at non-manifold edges (three or more triangles on one edge)")
    return m


def _boundary_loops(m):
    e = m.edges_sorted
    _, inv, cnt = np.unique(e, axis=0, return_inverse=True, return_counts=True)
    raw = m.edges[cnt[inv.ravel()] == 1]          # as they run in their own triangle
    nxt = {}
    for a, b in raw:
        nxt.setdefault(int(a), []).append(int(b))
    loops, used = [], set()
    for a0 in list(nxt):
        for b0 in nxt[a0]:
            if (a0, b0) in used:
                continue
            loop, a, b, ok = [a0], a0, b0, True
            while True:
                used.add((a, b))
                if b == a0:
                    break
                loop.append(b)
                nx = [c for c in nxt.get(b, []) if (b, c) not in used]
                if not nx or len(loop) > 500000:
                    ok = False
                    break
                a, b = b, nx[0]
            if ok and len(loop) >= 3:
                loops.append(loop)
    return loops


def _patch(V, loop):
    """Triangles closing one hole: flat in the hole's best plane if its outline is simple there,
    else (None) a fan from a new middle point."""
    try:
        import mapbox_earcut as earcut
    except ImportError:
        return None
    P = V[loop]
    c = P.mean(0)
    _, _, vt = np.linalg.svd(P - c, full_matrices=False)
    uv = (P - c) @ vt[:2].T
    try:
        tri = np.asarray(earcut.triangulate_float64(uv, np.array([len(uv)], np.uint32))).reshape(-1, 3)
    except Exception:
        return None
    if len(tri) != len(loop) - 2:
        return None
    return np.asarray(loop)[tri]


def fill_holes(m, log, max_frac=0.25):
    loops = _boundary_loops(m)
    if not loops:
        return m
    V = np.asarray(m.vertices)
    diag = float(np.linalg.norm(m.extents))
    newF, newV, filled, big = [], [], 0, 0
    for lp in loops:
        P = V[lp]
        # size by the outline's extent, not its length: a scanner hole's edge zig-zags (a test hole
        # 10 mm across had a 480 mm outline)
        if float(np.linalg.norm(np.ptp(P, axis=0))) > max_frac * diag:
            big += 1                                  # that big an opening is the shape, not a scanner gap
            continue
        tris = _patch(V, lp)
        if tris is None:
            ci = len(V) + len(newV)
            newV.append(P.mean(0))
            tris = np.array([[lp[i], lp[(i + 1) % len(lp)], ci] for i in range(len(lp))])
        newF.append(tris[:, ::-1])                    # the hole's edges run the other way in the patch
        filled += 1
    if newF:
        V2 = np.vstack([V] + ([np.asarray(newV)] if newV else []))
        m = trimesh.Trimesh(V2, np.vstack([m.faces] + newF), process=True)
    log(f"Scan clean-up: filled {filled} hole(s)" + (f"; {big} opening(s) too big to be a scanner gap left open" if big else ""))
    return m


def clean(m, mode, log):
    """The clean-up for one mesh, by mode. Returns the mesh (the same one if nothing was done)."""
    mode = mode if mode in MODES else "auto"
    if mode == "off":
        return m
    t = time.time()
    n0 = len(m.faces)
    one_closed = m.is_watertight and m.body_count == 1
    if mode == "auto" and one_closed:
        return m
    m = drop_loose(m, mode, log)
    if not m.is_watertight:
        m = cut_nonmanifold(m, log)
        m = fill_holes(m, log)
    m.metadata["scan_cleaned"] = True                  # load_mesh then denoises it lightly when dense
    if len(m.faces) != n0 or time.time() - t > 2:
        log(f"Scan clean-up: {n0:,} → {len(m.faces):,} triangles, "
            f"{'closed' if m.is_watertight else 'still open'}, {time.time() - t:.1f} s")
    return m
