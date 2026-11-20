# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""How far 3 Taubin passes move the points of a mesh: median, 99 %, 99.9 %, largest. Noise moves every point;
a clean mesh's flat areas do not move at all, so the median tells them apart (docs/research/SLOW_SCAN_2.md).

    python docs/research/scripts/scan_prep/move_stats.py scan.stl      (a clean dense box is measured as well)
"""
import sys

import numpy as np
import trimesh


def stats(m, label):
    v0 = np.array(m.vertices)
    trimesh.smoothing.filter_taubin(m, iterations=3)
    d = np.linalg.norm(np.asarray(m.vertices) - v0, axis=1)
    tol = 5e-4 * float(np.linalg.norm(m.extents))
    q = np.percentile(d, [50, 99, 99.9, 100])
    print(f"{label:28s} {len(m.faces):>9,} triangles  tolerance {tol:.3f} mm  move median {q[0]:.4f}  "
          f"99 % {q[1]:.3f}  99.9 % {q[2]:.3f}  largest {q[3]:.3f}")


stats(trimesh.load(sys.argv[1]), "the scan")
stats(trimesh.creation.box((60, 40, 20)).subdivide_to_size(0.25), "a clean dense box")
