import sys, time, numpy as np, trimesh
sys.path.insert(0, ".")
import bnf
m = trimesh.load("m150.ply", process=False)
truth = trimesh.load("truth.stl", process=False)
rng = np.random.default_rng(0)
def report(name, V):
    mm = trimesh.Trimesh(V, m.faces, process=False)
    ang = np.degrees(mm.face_adjacency_angles)
    S, _ = trimesh.sample.sample_surface(mm, 20000, seed=2)
    _, d1, _ = trimesh.proximity.closest_point(truth, S)
    T, _ = trimesh.sample.sample_surface(truth, 20000, seed=3)
    _, d2, _ = trimesh.proximity.closest_point(mm, T)
    mv = np.linalg.norm(V - m.vertices, axis=1)
    print(f"{name:28s} angle50/90/99 {np.percentile(ang,[50,90,99]).round(1)} >30°:{(ang>30).mean():.4f} | to truth mean {d1.mean():.4f} p99 {np.percentile(d1,99):.3f} max {d1.max():.3f} | truth->mesh p99 {np.percentile(d2,99):.3f} max {d2.max():.3f} | moved med {np.median(mv):.3f} max {mv.max():.3f}", flush=True)
report("as fitted now (150k)", np.asarray(m.vertices))
pairs = bnf.face_pairs(np.asarray(m.faces), len(m.vertices))
for sr in (0.25, 0.35, 0.5):
    for ni in (5, 10, 20):
        t = time.time()
        V = bnf.bnf(m.vertices, m.faces, n_iter=ni, v_iter=15, sigma_r=sr, pairs=pairs)
        report(f"bnf sr={sr} it={ni} ({time.time()-t:.1f}s)", V)
