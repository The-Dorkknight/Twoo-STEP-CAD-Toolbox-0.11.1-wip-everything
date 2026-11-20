"""Bilateral normal filtering + vertex update (Zheng et al. 2011, Sun et al. 2007), numpy/scipy."""
import numpy as np, scipy.sparse as sp, time

def face_pairs(F, nV):
    """Pairs of faces sharing a vertex (i != j), as two index arrays."""
    nF = len(F)
    rows = np.repeat(np.arange(nF), 3); cols = F.ravel()
    VF = sp.csr_matrix((np.ones(len(rows), np.int8), (cols, rows)), shape=(nV, nF))
    FF = (VF.T @ VF).tocoo()
    m = FF.row != FF.col
    return FF.row[m], FF.col[m]

def normals(V, F):
    n = np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]])
    a = np.linalg.norm(n, axis=1)
    return n / np.maximum(a, 1e-30)[:, None], 0.5 * a

def bnf(V, F, n_iter=8, v_iter=12, sigma_r=0.35, sigma_s=None, pairs=None, fixed=None):
    V = np.array(V, float); F = np.asarray(F)
    i, j = pairs if pairs is not None else face_pairs(F, len(V))
    N, A = normals(V, F)
    C = V[F].mean(1)
    if sigma_s is None:
        sigma_s = float(np.mean(np.linalg.norm(C[i] - C[j], axis=1)))
    ws = np.exp(-np.sum((C[i] - C[j]) ** 2, 1) / (2 * sigma_s ** 2)) * A[j]
    nF = len(F)
    for _ in range(n_iter):
        wr = np.exp(-np.sum((N[i] - N[j]) ** 2, 1) / (2 * sigma_r ** 2))
        W = sp.csr_matrix((ws * wr, (i, j)), shape=(nF, nF))
        Nn = W @ N + N * A[:, None]
        N = Nn / np.maximum(np.linalg.norm(Nn, axis=1, keepdims=True), 1e-30)
    # vertex update: move each vertex to agree with the filtered normals of its faces
    rows = F.ravel(); fidx = np.repeat(np.arange(nF), 3)
    cnt = np.bincount(rows, minlength=len(V)).astype(float)
    for _ in range(v_iter):
        C = V[F].mean(1)
        d = np.einsum("ij,ij->i", N[fidx], C[fidx] - V[rows])
        step = np.zeros_like(V)
        np.add.at(step, rows, N[fidx] * d[:, None])
        step /= np.maximum(cnt, 1)[:, None]
        if fixed is not None:
            step[fixed] = 0
        V += step
    return V
