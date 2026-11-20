import sys, pickle, numpy as np, trimesh
d = pickle.load(open(sys.argv[1], "rb"))
m = trimesh.Trimesh(d["V"], d["F"], process=False)
ang = np.degrees(m.face_adjacency_angles)
print("tris", len(m.faces), "adjacent-angle pct 50/90/99/99.9:", np.percentile(ang, [50, 90, 99, 99.9]).round(1), "frac >30:", (ang > 30).mean().round(4))
# local normal deviation: each face vs area-weighted average over vertex-neighbour faces
vf = m.vertex_faces
N = m.face_normals; A = m.area_faces
acc = np.zeros((len(m.vertices), 3))
np.add.at(acc, m.faces.ravel(), np.repeat(N * A[:, None], 3, axis=0))
vn = acc / np.maximum(np.linalg.norm(acc, axis=1, keepdims=True), 1e-12)
fn = vn[m.faces].sum(1); fn /= np.maximum(np.linalg.norm(fn, axis=1, keepdims=True), 1e-12)
dev = np.degrees(np.arccos(np.clip((fn * N).sum(1), -1, 1)))
print("face normal vs smoothed pct 50/90/99/99.9:", np.percentile(dev, [50, 90, 99, 99.9]).round(1), "frac>60:", (dev > 60).mean().round(4))
q = m.edges_unique_length; print("edge length pct 5/50/95:", np.percentile(q, [5, 50, 95]).round(3))
# triangle quality: min angle
tri = m.vertices[m.faces]
def angs(t):
    a = np.linalg.norm(t[:,1]-t[:,2],axis=1); b=np.linalg.norm(t[:,0]-t[:,2],axis=1); c=np.linalg.norm(t[:,0]-t[:,1],axis=1)
    A0=np.degrees(np.arccos(np.clip((b*b+c*c-a*a)/(2*b*c+1e-30),-1,1))); B0=np.degrees(np.arccos(np.clip((a*a+c*c-b*b)/(2*a*c+1e-30),-1,1)))
    return np.minimum(np.minimum(A0,B0),180-A0-B0)
mn = angs(tri); print("min angle pct 1/5/50:", np.percentile(mn, [1, 5, 50]).round(2))
