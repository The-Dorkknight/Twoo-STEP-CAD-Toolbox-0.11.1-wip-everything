"""Clean (noise-free) dense meshes that must NOT be treated as scans or must come through unharmed."""
import numpy as np, trimesh
from skimage import measure

def sub_to(m, n):
    while len(m.faces) < n:
        v, f = trimesh.remesh.subdivide(m.vertices, m.faces)
        m = trimesh.Trimesh(v, f, process=True)
    return m

# 1 box, exactly flat faces, uniform dense triangles
sub_to(trimesh.creation.box((40, 30, 20)), 300000).export("box_dense.stl")
# 2 box + cylinder boss + hole (CAD-like), dense by subdivision
b = trimesh.creation.box((40, 30, 12)); c = trimesh.creation.cylinder(8, 20, sections=96).apply_translation((0, 0, 6))
u = b.union(c) if False else trimesh.util.concatenate([b, c])
sub_to(trimesh.creation.cylinder(10, 40, sections=128), 300000).export("cyl_dense.stl")
# 3 marching-cubes rounded box (voxel stair-steps = scan-like roughness, but no noise)
h = 0.25
x = np.arange(-14, 14, h); y = np.arange(-11, 11, h); z = np.arange(-9, 9, h)
X, Y, Z = np.meshgrid(x, y, z, indexing="ij")
q = np.stack([np.abs(X) - 10, np.abs(Y) - 7, np.abs(Z) - 5], -1)
d = np.linalg.norm(np.maximum(q, 0), axis=-1) + np.minimum(np.max(q, -1), 0) - 1.5     # rounded box SDF
v, f, _, _ = measure.marching_cubes(d, 0, spacing=(h, h, h)); v += np.array([x[0], y[0], z[0]])
mc = trimesh.Trimesh(v, f, process=True); mc.export("box_mc.stl"); print("box_mc", len(mc.faces))
# 4 ghost: sheet-like body, wavy hem, two eye tunnels - marching cubes of an SDF (156k-ish triangles)
h = 0.45
x = np.arange(-40, 40, h); y = np.arange(-40, 40, h); z = np.arange(-5, 90, h)
X, Y, Z = np.meshgrid(x, y, z, indexing="ij")
ang = np.arctan2(Y, X)
r = 28 * np.clip(1 - ((Z - 20) / 70) ** 2, 0, 1) ** 0.5 + 6
hem = 2.5 * np.sin(6 * ang)
body = np.hypot(X, Y) - (r + hem * (Z < 25))
body = np.maximum(body, -(Z - 2 - hem * 0.6)) if False else body
body = np.maximum(body, -Z + 0.0 + 1.2 * np.sin(6 * ang))          # wavy cut hem
for ex in (-8, 8):
    body = np.maximum(body, -(np.hypot(X - ex, Z - 55) - 4) * (Y > 0))   # eye tunnels in front
v, f, _, _ = measure.marching_cubes(body, 0, spacing=(h, h, h)); v += np.array([x[0], y[0], z[0]])
g = trimesh.Trimesh(v, f, process=True); g.export("ghost_mc.stl"); print("ghost_mc", len(g.faces), g.is_watertight)
