# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""The EXPERIMENTAL Quad patches engine (app/quadfit.py): which parts it takes, the quad helpers,
a whole conversion when a quad remesher is there, and the hand-over to Classic.

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
from tostep.plugins.mesh2step import quadfit  # noqa: E402
from tostep.plugins.mesh2step import classic as core  # noqa: E402

KNOB = os.path.join(ROOT, "tests", "data", "knob_plain.stl")


def blob(sub=4, amp=0.12):
    m = trimesh.creation.icosphere(subdivisions=sub, radius=15.0)
    v = m.vertices
    rng = np.random.default_rng(3)
    d = np.zeros(len(v))
    for _ in range(6):
        w = rng.normal(size=3); w /= np.linalg.norm(w)
        d += rng.uniform(1, 3) * np.sin(rng.uniform(0.08, 0.25) * (v @ w) + rng.uniform(0, 6.28))
    return trimesh.Trimesh(v * (1 + amp * d)[:, None], m.faces, process=True)


class Helpers(unittest.TestCase):
    def test_split_to_quads_keeps_a_closed_mesh_closed(self):
        cube = trimesh.creation.box(extents=(2, 2, 2))
        V, F = quadfit.split_to_quads(cube.vertices, cube.faces.tolist())
        self.assertEqual(len(F), 3 * len(cube.faces))
        self.assertTrue(all(len(f) == 4 for f in F))
        self.assertTrue(quadfit._is_closed(F))
        self.assertFalse(quadfit._is_closed(F[:-1]))

    def test_clean_quads_drops_degenerate_faces(self):
        V = np.zeros((6, 3))
        QV, QF = quadfit._clean_quads(V, [[0, 1, 2, 3], [0, 0, 1, 2], [2, 3, 4]])
        self.assertEqual(QF, [[0, 1, 2, 3]])
        self.assertEqual(len(QV), 4)

    def test_closest_matches_the_exact_search(self):
        m = blob(3)
        P = m.vertices[::7] + np.random.default_rng(1).normal(scale=0.3, size=(len(m.vertices[::7]), 3))
        exact = trimesh.proximity.closest_point(m, P)[0]
        fast = quadfit._closest(m, P)
        self.assertLess(np.abs(fast - exact).max(), 1e-6)


class WhichParts(unittest.TestCase):
    def test_organic_yes_cad_and_turned_no(self):
        self.assertTrue(quadfit.suitable(core.MeshCtx(blob()))[0])
        ok, why = quadfit.suitable(core.MeshCtx(core.load_mesh(KNOB, core.Params(), lambda s: None)))
        self.assertFalse(ok)
        box = trimesh.creation.box(extents=(30, 20, 10))
        ok, why = quadfit.suitable(core.MeshCtx(box))
        self.assertFalse(ok)
        self.assertIn("flat faces", why)
        ok, why = quadfit.suitable(core.MeshCtx(trimesh.creation.icosphere(4, 10.0)))
        self.assertFalse(ok)
        self.assertIn("turned shape", why)

    def test_a_cad_part_goes_to_classic(self):
        with tempfile.TemporaryDirectory() as d:
            log = []
            rep = engines.convert(KNOB, os.path.join(d, "k.step"), {}, "quad", log=log.append)
            text = "\n".join(log)
            self.assertIn("converting with Classic instead", text)
            self.assertTrue(rep.is_solid and rep.is_valid)


@unittest.skipUnless(quadfit.remesher_name(), "no quad remesher here (QuadriFlow or Instant Meshes)")
class Conversion(unittest.TestCase):
    def test_blob_becomes_a_closed_solid_of_untrimmed_patches_within_tolerance(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "blob.stl")
            blob(4, 0.04).export(src)                  # a gentle blob: a few hundred quads at most
            log = []
            rep = engines.convert(src, os.path.join(d, "blob.step"), {}, "quad", log=log.append)
            self.assertTrue(rep.is_solid and rep.is_valid, "\n".join(log))
            self.assertIn("quad patches", rep.route)
            self.assertEqual(rep.n_faces, rep.n_nurbs)
            self.assertEqual(rep.n_faceted_regions, 0)
            self.assertLessEqual(rep.max_dev, rep.tol)
            self.assertAlmostEqual(rep.step_volume / rep.mesh_volume, 1.0, delta=0.002)
            self.assertLess(rep.n_faces, 1500)


@unittest.skipUnless(quadfit.have_instant_meshes(), "Instant Meshes (pynanoinstantmeshes) not installed")
class InstantMeshes(unittest.TestCase):
    """The layout a Mac or Windows machine uses (no QuadriFlow program there): quiet, all quads."""
    def test_layout_is_closed_quads_and_prints_nothing(self):
        m = blob(3, 0.04)
        with tempfile.TemporaryFile() as out:     # what the compiled code writes to file 1
            sys.stdout.flush()
            saved = os.dup(1)
            os.dup2(out.fileno(), 1)
            try:
                V, F = quadfit._instant_meshes(m.vertices, m.faces, 200)
            finally:
                os.dup2(saved, 1)
                os.close(saved)
            out.seek(0)
            printed = out.read()
        self.assertTrue(all(len(f) == 4 for f in F))
        self.assertTrue(quadfit._is_closed(F))
        self.assertEqual(printed, b"")


if __name__ == "__main__":
    unittest.main()
