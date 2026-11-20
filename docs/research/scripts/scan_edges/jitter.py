import sys, numpy as np, trimesh
for f in sys.argv[1:]:
    m = trimesh.load(f, process=True, force="mesh")
    a = np.degrees(m.face_adjacency_angles)
    flat = a[a < 30]
    edge = float(np.median(m.edges_unique_length)); diag = float(np.linalg.norm(m.extents))
    # fraction of triangles whose normal differs > 5 deg from the mean of its neighbours (jitter), measured on faces
    print(f"{f.split('/')[-1]:16s} {len(m.faces):7d} tris edge/diag {edge/diag:.4f} | adj angle median(all) {np.median(a):5.2f} median(<30°) {np.median(flat):5.2f} p90(<30°) {np.percentile(flat,90):5.2f} frac>5° {(flat>5).mean():.3f}")
