# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""The EXPERIMENTAL Coons gap filler (classic.Builder.nurbs_face_coons, paper D18): the corner
and Coons helpers, the setting on every way in, and a whole conversion where it is used.

    python -m unittest discover -s tests -v
"""
import os
import sys
import tempfile
import unittest

import numpy as np
import trimesh

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tostep.plugins.mesh2step import engines  # noqa: E402
from tostep.plugins.mesh2step import classic as core  # noqa: E402


def square_outline(n=400, side=10.0):
    """A closed square outline, evenly sampled, starting half-way along a side."""
    c = np.array([[0, 0, 0], [side, 0, 0], [side, side, 0], [0, side, 0]], float)
    t = np.linspace(0, 4, n, endpoint=False) + 0.5
    k = np.floor(t).astype(int) % 4
    f = (t - np.floor(t))[:, None]
    return c[k] + f * (c[(k + 1) % 4] - c[k])


def bumpy_blob():
    """The coarse blob of the measurements (5,120 triangles): the least-squares fit misses many
    of its patches, so the gap filler runs."""
    m = trimesh.creation.icosphere(subdivisions=4)
    v = m.vertices.copy()
    f = np.sin(3 * v[:, 0]) * np.cos(2 * v[:, 1]) * 0.15 + 0.1 * np.sin(5 * v[:, 2] + v[:, 0])
    v = v * (1 + f[:, None]) * 30
    v[:, 2] *= 1.4
    return trimesh.Trimesh(v, m.faces)


class Helpers(unittest.TestCase):
    def test_square_corners_are_among_the_candidates(self):
        P = square_outline()
        cands = core.Builder._corner_candidates(P)
        corners = np.array([[0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0]], float)
        best = min(cands, key=lambda c4: max(np.linalg.norm(P[list(c4)][:, None] - corners[None], axis=2).min(1)))
        self.assertLess(max(np.linalg.norm(P[list(best)][:, None] - corners[None], axis=2).min(1)), 0.2)

    def test_coons_grid_of_a_flat_square_is_the_square(self):
        P = square_outline()
        cands = core.Builder._corner_candidates(P)
        sides = core.Builder._sides(P, cands[0], 21)
        G = core.Builder._coons_grid(sides).reshape(-1, 3)
        self.assertLess(np.abs(G[:, 2]).max(), 1e-9)                       # stays in the plane
        self.assertGreater(G[:, 0].min(), -0.3)
        self.assertLess(G[:, 0].max(), 10.3)

    def test_sides_join_end_to_end(self):
        P = square_outline()
        sides = core.Builder._sides(P, core.Builder._corner_candidates(P)[0], 9)
        for k in range(4):
            self.assertLess(np.linalg.norm(sides[k][-1] - sides[(k + 1) % 4][0]), 1e-9)


class Setting(unittest.TestCase):
    def test_default_is_the_plate_filler(self):
        self.assertEqual(core.Params().gap_fill, "plate")

    def test_manifest_cli_and_plugin_know_the_setting(self):
        from tostep.core import manifest
        from tostep.plugins.mesh2step import build_params
        here = os.path.join(ROOT, "tostep", "plugins", "mesh2step")
        mf = manifest.load(os.path.join(here, "manifest.toml"), "tostep.plugins.mesh2step")
        p = {x.name: x for x in mf.params}["gap_fill"]
        self.assertTrue(p.advanced)
        self.assertEqual(p.default, "plate")
        self.assertIn("coons", p.choices)
        prm = dict(mf.defaults() if hasattr(mf, "defaults") else {x.name: x.default for x in mf.params})
        prm["gap_fill"] = "coons+plate"
        self.assertEqual(build_params(prm, 1.0).gap_fill, "coons+plate")
        with open(os.path.join(here, "cli.py")) as fh:
            self.assertIn("--gap-fill", fh.read())


class Conversion(unittest.TestCase):
    def test_blob_with_coons_fill_is_a_valid_solid_and_uses_it(self):
        d = tempfile.mkdtemp()
        src = os.path.join(d, "blob.stl")
        bumpy_blob().export(src)
        lines = []
        rep = engines.convert(src, os.path.join(d, "blob.step"), {"gap_fill": "coons"}, "classic",
                              log=lines.append)
        self.assertTrue(rep.is_solid and rep.is_valid)
        used = [l for l in lines if "gap fills: Coons" in l]
        self.assertTrue(used, "no gap-fill line in the log")
        n_used = int(used[0].split("Coons ")[1].split("/")[0])
        self.assertGreater(n_used, 0)
        # measured (D18): 0.145 mm against the plate filler's 0.141 mm, tolerance 0.063 mm
        self.assertLess(rep.max_dev, 0.2)


if __name__ == "__main__":
    unittest.main()
