"""An organic stand-in: a soldering-iron-like body with a bent axis, an oval moulded grip with finger
dips, a flat window and sharp end caps. truth.stl (no noise) and scan.stl (0.04 mm noise, as the S99 stand-in)."""
import numpy as np, trimesh
from skimage import measure
rng = np.random.default_rng(7)
h = 0.2
x = np.arange(-16, 16, h); y = np.arange(-16, 16, h); z = np.arange(-3, 172, h)
X, Y, Z = np.meshgrid(x, y, z, indexing="ij")
bend = 0.0006 * (Z - 90) ** 2                       # the axis curves sideways by up to ~5 mm
Xb = X - bend
r = np.interp(Z, [0, 22, 26, 32, 40, 90, 100, 170], [0.8, 1.6, 4.5, 7.0, 9.5, 9.5, 10.5, 10.5])
grip = (Z > 36) & (Z < 92)
r = r + np.where(grip, 0.9 * np.sin((Z - 36) / 56 * np.pi) ** 2 - 0.6 * (np.cos((Z - 36) / 56 * 6 * np.pi) > 0.7), 0)
oval = np.where(grip, np.hypot(Xb / 1.12, Y / 0.92), np.hypot(Xb, Y))
d = oval - r
d = np.where((Z > 108) & (Z < 150), np.maximum(d, Xb - 7.5), d)
d = np.maximum(d, Z - 168); d = np.maximum(d, -Z)
v, f, _, _ = measure.marching_cubes(d, 0, spacing=(h, h, h))
v += np.array([x[0], y[0], z[0]])
m = trimesh.Trimesh(v, f, process=True)
m.export("truth.stl")
m.vertices = m.vertices + rng.normal(0, 0.04, m.vertices.shape)
m.export("scan.stl")
print(len(m.faces), "triangles, closed", m.is_watertight)
