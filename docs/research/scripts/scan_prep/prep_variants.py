# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""Plain quadric reduction of a dense, noisy, closed scan to 150,000 triangles, with and without a denoise first
(docs/research/SLOW_SCAN_2.md).

    python docs/research/scripts/scan_prep/prep_variants.py scan.stl
"""
import os
import sys
import time

import numpy as np
import trimesh

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
sys.path.insert(0, ROOT)
from tostep.plugins.mesh2step import classic  # noqa: E402

src = trimesh.load(sys.argv[1])
print(f"{len(src.faces):,} triangles, closed: {src.is_watertight}")


def variant(name, passes, cap):
    m = src.copy()
    t = time.time()
    if passes:
        v0 = np.array(m.vertices)
        trimesh.smoothing.filter_taubin(m, iterations=passes)
        if cap:                                        # no point moves further than `cap` from where it was
            d = np.asarray(m.vertices) - v0
            n = np.linalg.norm(d, axis=1)
            m.vertices = v0 + d * np.minimum(1.0, cap / np.maximum(n, 1e-12))[:, None]
    out = []
    r = classic.plain_reduce(m, 150000, out.append)
    print(f"{name:30s} {'closed' if r is not None else 'NOT closed':10s} {time.time() - t:5.1f} s  "
          f"{out[-1][:110] if out else ''}")


variant("no denoise", 0, 0)
variant("3 Taubin passes", 3, 0)
variant("3 passes, move capped 0.043", 3, 0.043)
variant("3 passes, move capped 0.09", 3, 0.09)
