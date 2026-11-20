# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""Scan clean-up (scanclean.py), the faster patch-layout sub-graph, and the checkpoint size cap.

    python -m unittest tests.test_scan_cleanup -v
"""
import os
import sys
import unittest

import numpy as np
import trimesh
from scipy.sparse import coo_matrix

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tostep.plugins.mesh2step import classic, scanclean  # noqa: E402


def body():
    return trimesh.creation.cylinder(radius=10, height=80, sections=96)


def with_junk():
    """A scan-like mesh: the body with a hole, scanner spray (open specks), a small closed part
    (a screw of an assembly) and a big open turntable plate."""
    b = body()
    b = b.subdivide().subdivide()
    keep = np.linalg.norm(b.triangles_center - np.array([0, 0, 40]), axis=1) > 4.0    # a hole in the top
    b.update_faces(keep)
    b.remove_unreferenced_vertices()
    rng = np.random.default_rng(0)
    spray = []
    for _ in range(25):
        s = trimesh.creation.icosphere(1, radius=0.4)
        s.update_faces(np.arange(len(s.faces)) % 3 != 0)            # open, like a scanner fragment
        s.remove_unreferenced_vertices()
        spray.append(s.apply_translation(rng.uniform([-30, -30, -40], [30, 30, 40])))
    screw = trimesh.creation.cylinder(radius=1.2, height=6, sections=24).apply_translation((40, 0, 0))
    plate = trimesh.creation.box((80, 80, 1)).subdivide().subdivide().apply_translation((0, 0, -46))
    plate.update_faces(np.arange(len(plate.faces)) != 0)            # an open plate
    return trimesh.util.concatenate([b, screw, plate] + spray), b, screw


class Cleanup(unittest.TestCase):
    def setUp(self):
        self.log = []

    def L(self, s):
        self.log.append(s)

    def test_one_closed_piece_is_left_alone(self):
        m = body()
        out = scanclean.clean(m, "auto", self.L)
        self.assertIs(out, m)
        self.assertEqual(self.log, [])

    def test_auto_drops_spray_keeps_small_closed_parts_and_fills_the_hole(self):
        m, b, screw = with_junk()
        out = scanclean.clean(m, "auto", self.L)
        parts = out.split(only_watertight=False)
        sizes = sorted(len(p.faces) for p in parts)
        self.assertEqual(len(parts), 3, sizes)                     # body, screw, plate
        self.assertTrue(any("loose piece" in s for s in self.log), self.log)
        self.assertTrue(any("filled" in s for s in self.log), self.log)
        big = max(parts, key=lambda p: p.area)
        self.assertTrue(any(p.is_watertight and abs(p.volume - body().volume) < 0.03 * body().volume for p in parts))
        self.assertGreater(big.area, 0)

    def test_on_drops_every_small_piece(self):
        m, _, _ = with_junk()
        out = scanclean.clean(m, "on", self.L)
        self.assertEqual(len(out.split(only_watertight=False)), 2)   # body and plate: the screw goes too

    def test_largest_keeps_one_piece(self):
        m, _, _ = with_junk()
        out = scanclean.clean(m, "largest", self.L)
        self.assertEqual(len(out.split(only_watertight=False)), 1)

    def test_off_changes_nothing(self):
        m, _, _ = with_junk()
        n = len(m.faces)
        self.assertEqual(len(scanclean.clean(m, "off", self.L).faces), n)

    def test_non_manifold_edge_is_cut(self):
        b = body()
        v = b.vertices
        e = b.edges_unique[0]
        fin = trimesh.Trimesh(np.vstack([v, [[30, 30, 30]]]), np.vstack([b.faces, [[e[0], e[1], len(v)]]]),
                              process=False)
        out = scanclean.cut_nonmanifold(fin, self.L)
        _, cnt = np.unique(out.edges_sorted, axis=0, return_counts=True)
        self.assertLessEqual(cnt.max(), 2)
        self.assertTrue(any("non-manifold" in s for s in self.log))

    def test_load_mesh_runs_it_and_logs(self):
        import tempfile
        m, _, _ = with_junk()
        p = os.path.join(tempfile.mkdtemp(), "scan.stl")
        m.export(p)
        lines = []
        out = classic.load_mesh(p, classic.Params(), lines.append)
        self.assertTrue(any(s.startswith("Scan clean-up:") for s in lines), lines)
        self.assertLess(len(out.split(only_watertight=False)), 5)
        lines2 = []
        out2 = classic.load_mesh(p, classic.Params(scan_cleanup="off"), lines2.append)
        self.assertFalse(any(s.startswith("Scan clean-up:") for s in lines2))
        self.assertGreater(len(out2.split(only_watertight=False)), 20)


class Faster(unittest.TestCase):
    def test_sub_graph_is_unchanged(self):
        m = trimesh.creation.icosphere(4)
        ctx = classic.MeshCtx(m)
        g = classic._Graph(ctx)
        rng = np.random.default_rng(1)
        for _ in range(10):
            ids = rng.choice(ctx.nF, rng.integers(5, ctx.nF), replace=False)
            loc = -np.ones(ctx.nF, np.int64); loc[ids] = np.arange(len(ids))
            mm = (loc[g.a] >= 0) & (loc[g.b] >= 0)
            old = coo_matrix((g.w[mm], (loc[g.a[mm]], loc[g.b[mm]])), shape=(len(ids), len(ids))).tocsr()
            self.assertEqual(abs(old - g.sub(ids, ctx.nF)).max() if old.nnz else 0, 0)

    def test_limits_are_set(self):
        self.assertEqual(classic.CKPT_MAX_TRIS, 60000)
        self.assertEqual(classic.SCAN_PREREDUCE, 150000)

    def test_plain_reduce_stays_closed_and_close(self):
        m = trimesh.creation.icosphere(6, radius=20)          # 81,920 triangles
        lines = []
        r = classic.plain_reduce(m, 20000, lines.append)
        self.assertIsNotNone(r)
        self.assertTrue(r.is_watertight)
        self.assertLess(len(r.faces), 21000)
        self.assertTrue(any(s.startswith("Reduced plainly") for s in lines), lines)
        _, d, _ = trimesh.proximity.closest_point(r, m.vertices[::50])
        self.assertLess(float(d.max()), 0.05)

    def test_quick_reduces_big_meshes(self):
        from tostep.plugins.mesh2step import presets
        self.assertEqual(presets.BY_ID["quick"]["set"].get("decimate"), "60000")


if __name__ == "__main__":
    unittest.main()
