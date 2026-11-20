# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""Edge-keeping smoothing for scans (0.11.4; paper D25, built).

Scanner noise tilts every small triangle a little. At the half-millimetre triangle size the converter
works at, 0.04 mm of noise is about 10 degrees between neighbouring triangles, and 11 % of the edges of
the test scan looked sharper than the 30-degree crease angle. Every one of those cut the surface into
smaller "smooth" areas, and the patch layout then split the rest until no patch bent more than its limit:
1,500 to 2,500 freeform patches of about 15 triangles each, where a soldering iron needs tens. That was
most of the time of a scan conversion, and most of its facets.

Plain smoothing (Taubin, Laplacian) rounds real edges off along with the noise. This smooths the
triangles' directions instead, each one only with neighbours that already point nearly the same way
(bilateral normal filtering), so a real edge, where the directions differ a lot, is left alone. Then the
points are moved so the triangles take up those directions (the vertex update of Sun et al.).

    Zheng, Fu, Au, Tai: "Bilateral Normal Filtering for Mesh Denoising", IEEE TVCG 17 (2011).
    Sun, Rosin, Martin, Langbein: "Fast and Effective Feature-Preserving Mesh Denoising", IEEE TVCG 13 (2007).

Measured on the stand-in scan (150,000 triangles, against the noise-free shape it was made from):
neighbouring triangles 9.8 -> 1.1 degrees apart (median), distance to the true shape 0.013 -> 0.007 mm
(mean), 2.4 s. Stronger settings (a wider normal window, more passes) smoothed more but moved the surface
away from the true shape (0.027 mm at sigma 0.5, 10 passes), so the defaults are the gentle ones.
docs/research/SCAN_EDGES.md has the table.

Only topology-preserving point moves: a closed mesh stays closed. A point that would flip a triangle over
is put back where it was.
"""
from __future__ import annotations

import numpy as np
import scipy.sparse as sp

SIGMA_R = 0.25        # how different two directions may be and still be averaged (unit-normal distance)
PIN_ANGLE = 40.0      # a point where triangles meet at more than this many degrees (in the input) is a real edge or
                      # corner of the part and is never moved (a smoothed clean box moved its corners by 2.4 tolerances)
NORMAL_PASSES = 5
POINT_PASSES = 15


def _normals(V, F):
    n = np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]])
    a = np.linalg.norm(n, axis=1)
    return n / np.maximum(a, 1e-30)[:, None], 0.5 * a


def _face_pairs(F, nV):
    """Every pair of triangles that share a point (both ways round)."""
    nF = len(F)
    VF = sp.csr_matrix((np.ones(3 * nF, np.int8), (F.ravel(), np.repeat(np.arange(nF), 3))), shape=(nV, nF))
    FF = (VF.T @ VF).tocoo()
    keep = FF.row != FF.col
    return FF.row[keep], FF.col[keep]


def smooth(V, F, sigma_r=SIGMA_R, normal_passes=NORMAL_PASSES, point_passes=POINT_PASSES):
    """New point positions (same order, same triangles). V: (n, 3) floats, F: (m, 3) ints."""
    V = np.array(V, dtype=np.float64)
    V_in = V.copy()
    F = np.asarray(F, dtype=np.int64)
    nF = len(F)
    if nF < 4:
        return V
    i, j = _face_pairs(F, len(V))
    N0, A = _normals(V, F)
    N = N0.copy()
    Cc = V[F].mean(1)
    d2 = np.sum((Cc[i] - Cc[j]) ** 2, 1)
    sigma_s2 = max(float(np.mean(d2)), 1e-18)        # the neighbourhood's own size
    ws = np.exp(-d2 / (2.0 * sigma_s2)) * A[j]
    for _ in range(normal_passes):
        wr = np.exp(-np.sum((N[i] - N[j]) ** 2, 1) / (2.0 * sigma_r ** 2))
        W = sp.csr_matrix((ws * wr, (i, j)), shape=(nF, nF))
        Nn = W @ N + N * A[:, None]
        N = Nn / np.maximum(np.linalg.norm(Nn, axis=1, keepdims=True), 1e-30)
    # pinned points: any point whose triangles still disagree by more than PIN_ANGLE once the noise is filtered
    # out of their directions (judged on the input's own directions, noise alone pinned a tenth of a scan's points)
    cosmin = np.full(len(V), 1.0)
    rows = F.ravel()
    fidx = np.repeat(np.arange(nF), 3)
    mean_n = np.zeros_like(V)
    np.add.at(mean_n, rows, N[fidx])
    mean_n /= np.maximum(np.linalg.norm(mean_n, axis=1, keepdims=True), 1e-30)
    np.minimum.at(cosmin, rows, np.einsum("ij,ij->i", N[fidx], mean_n[rows]))
    pinned = cosmin < np.cos(np.radians(PIN_ANGLE / 2.0))
    # (neighbours of a pinned point on the same triangle keep moving: only the point itself stays)
    cnt = np.maximum(np.bincount(rows, minlength=len(V)), 1).astype(np.float64)[:, None]
    Nf = N[fidx]
    for _ in range(point_passes):
        Cc = V[F].mean(1)
        d = np.einsum("ij,ij->i", Nf, Cc[fidx] - V[rows])
        step = np.zeros_like(V)
        np.add.at(step, rows, Nf * d[:, None])
        step /= cnt
        step[pinned] = 0.0
        V += step
    # a triangle turned over (its direction now against the original) is a fold: put its points back
    for _ in range(3):
        N1, _ = _normals(V, F)
        flipped = np.einsum("ij,ij->i", N1, N0) < 0.0
        if not flipped.any():
            break
        back = np.unique(F[flipped])
        V[back] = V_in[back]
    return V
