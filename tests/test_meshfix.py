# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""app/meshfix.py gives trimesh.repair.fix_normals' answer without its pure-Python graph walk,
and the shape-guessing helpers rewritten in 0.9.1 give the same bits as the numpy calls they replace.

    python -m unittest discover -s tests -v
"""
import os
import sys
import unittest

import numpy as np
import trimesh

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
KNOB = os.path.join(ROOT, "tests", "data", "knob_plain.stl")

from tostep.plugins.mesh2step import meshfix  # noqa: E402


def flipped(V, F, frac, seed):
    F = F.copy()
    fl = np.random.default_rng(seed).random(len(F)) < frac
    F[fl] = F[fl][:, ::-1]
    return V, F


class SameAsTrimesh(unittest.TestCase):
    def both(self, V, F, multibody=False):
        a = trimesh.Trimesh(V, F.copy(), process=False)
        b = trimesh.Trimesh(V, F.copy(), process=False)
        trimesh.repair.fix_normals(a, multibody=multibody)
        meshfix.fix_normals(b, multibody=multibody)
        return a, b

    def test_one_piece_with_flipped_faces(self):
        m = trimesh.load(KNOB, process=True)
        for frac in (0.0, 0.02, 0.3, 0.7):
            for mb in (False, True):
                a, b = self.both(*flipped(m.vertices, m.faces, frac, 1), multibody=mb)
                self.assertTrue(np.array_equal(a.faces, b.faces), (frac, mb))
                self.assertTrue(b.is_winding_consistent)

    def test_with_holes_and_two_equal_bodies(self):
        m = trimesh.load(KNOB, process=True)
        keep = np.random.default_rng(2).random(len(m.faces)) > 0.03
        a, b = self.both(*flipped(m.vertices, m.faces[keep], 0.3, 3))
        self.assertTrue(np.array_equal(a.faces, b.faces))
        V = np.vstack([m.vertices, m.vertices + 500])
        F = np.vstack([m.faces, m.faces[:, ::-1] + len(m.vertices)])      # the second one inside out
        for mb in (False, True):
            a, b = self.both(*flipped(V, F, 0.3, 4), multibody=mb)
            self.assertTrue(np.array_equal(a.faces, b.faces), mb)

    def test_big_mesh_ids_do_not_overflow(self):
        s = trimesh.creation.icosphere(6)                        # 81,920 faces: keys past 2**31
        V = np.vstack([s.vertices, s.vertices + 5]); F = np.vstack([s.faces, s.faces + len(s.vertices)])
        a, b = self.both(*flipped(V, F, 0.4, 5))
        self.assertTrue(b.is_winding_consistent)
        self.assertTrue(np.array_equal(a.faces, b.faces))


class SameBits(unittest.TestCase):
    def test_row_norms_and_hypotheses(self):
        from tostep.plugins.mesh2step import classic as C
        rng = np.random.default_rng(7)
        for _ in range(200):
            x = rng.normal(size=(int(rng.integers(1, 500)), 3)) * 10 ** rng.uniform(-3, 3)
            self.assertTrue(np.array_equal(C._row_norms(x), np.linalg.norm(x, axis=1)))
            self.assertTrue(np.array_equal(C._row_norms(x, keepdims=True), np.linalg.norm(x, axis=1, keepdims=True)))
            a, b = rng.normal(size=3), rng.normal(size=3)
            c = np.array((a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]))
            self.assertTrue(np.array_equal(c, np.cross(a, b)))
            self.assertEqual(np.sqrt(a.dot(a)), np.linalg.norm(a))


if __name__ == "__main__":
    unittest.main()
