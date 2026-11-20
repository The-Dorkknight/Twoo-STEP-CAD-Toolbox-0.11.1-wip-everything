# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""Is this dense mesh a 3D scan? (0.11.4)

A scan is rough in a way a CAD export or a marching-cubes mesh is not: its small triangles are tilted a few
degrees at random. Measured here as the typical angle between neighbouring triangles, among the edges that are
not real creases (under 30 degrees):

    clean box, 786,000 triangles              0.00 degrees     soldering-iron stand-in, 0.04 mm noise   13.2
    clean cylinder, 524,000 triangles         0.00             organic stand-in, 0.04 mm noise          13.2
    clean ghost (marching cubes), 313,000     0.09             real-looking scan of the first             13.1
    clean organic stand-in, 634,000           0.06

Over 100,000 triangles and a typical angle of 2 degrees or more is a scan; nothing clean came near 0.2. The
earlier test (how far three smoothing passes move the points) called two clean organic meshes scans because a
smoothing pass shrinks any curved surface a little.

`assess()` is cheap (the adjacency of the mesh, a few seconds at 650,000 triangles) and is also what the web page
asks, before a conversion starts, so the person can say "yes, a scan" or "no, treat it as CAD".
"""
from __future__ import annotations

import numpy as np

MIN_TRIANGLES = 100_000
JITTER_DEG = 2.0


def jitter(m) -> float:
    """Typical angle (degrees) between neighbouring triangles, ignoring real creases (30 degrees and up)."""
    a = np.degrees(m.face_adjacency_angles)
    a = a[a < 30.0]
    return float(np.median(a)) if len(a) else 0.0


def assess(m, scan_cleaned: bool = False) -> dict:
    """{'likely': bool, 'jitter': degrees, 'triangles': n, 'why': one plain sentence}."""
    n = len(m.faces)
    j = jitter(m) if n > MIN_TRIANGLES or scan_cleaned else 0.0
    if scan_cleaned and n > MIN_TRIANGLES:
        why = (f"{n:,} triangles, and the clean-up had to remove loose pieces, bad edges or holes, "
               "which scanners leave and CAD programs don't")
    elif n > MIN_TRIANGLES and j >= JITTER_DEG:
        why = (f"{n:,} triangles, and neighbouring triangles are tilted about {j:.0f}° from each other at random "
               "(a CAD export or a clean model is flat or smooth to a fraction of a degree)")
    else:
        why = ""
    return {"likely": bool(why), "jitter": j, "triangles": n, "why": why}
