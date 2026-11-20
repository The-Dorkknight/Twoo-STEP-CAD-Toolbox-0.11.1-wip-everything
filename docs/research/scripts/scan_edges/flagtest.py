"""What does the scan test say about each mesh, and how much does the edge-keeping smoothing change a clean one?"""
import os, sys, numpy as np, trimesh
sys.path.insert(0, os.environ.get("TOSTEP_ROOT", "."))
from tostep.plugins.mesh2step import classic as C, edgesmooth
for f in sys.argv[1:]:
    lines = []
    m = C.load_mesh(f, C.Params(), lines.append)
    flag = bool(m.metadata.get("scan_noisy"))
    diag = float(np.linalg.norm(m.extents)); tol = max(0.01, 5e-4 * diag)
    # what the smoothing would do to this mesh, regardless of the flag
    V = edgesmooth.smooth(m.vertices, m.faces)
    mv = np.linalg.norm(V - m.vertices, axis=1)
    a0 = np.degrees(m.face_adjacency_angles); r = trimesh.Trimesh(V, m.faces, process=False); a1 = np.degrees(r.face_adjacency_angles)
    print(f"{f:14s} {len(m.faces):7d} tris  flagged scan: {flag!s:5}  tol {tol:.3f}  smoothing would move: median {np.median(mv)/tol:.2f} tol, p99 {np.percentile(mv,99)/tol:.2f} tol, max {mv.max()/tol:.2f} tol | sharp edges >45°: {(a0>45).sum()} -> {(a1>45).sum()}", flush=True)
    for ln in lines:
        if "noise" in ln or "Denoised" in ln or "Smoothed" in ln: print("      ", ln[:170])
