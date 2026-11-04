# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""The 0.10.0 fixes that make results repeatable and the Draft preset finish on dense flat-sided
meshes: flat-face retriangulation (no zero-area triangles, the outline kept exactly), the plate
filler's boundary handed over as an ordered wire, and the time budget surviving a mesh reduction.

    python -m unittest discover -s tests -v
"""
import os
import sys
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tostep.plugins.mesh2step import classic as core  # noqa: E402


def square_with_collinear_outline(step=1.0, size=10.0):
    """A flat square whose outline carries a point every `step` mm (as a CAD export does), fanned
    into triangles so that two straight stretches meet at one triangle in every corner."""
    n = int(round(size / step))
    ring = []
    for i in range(n):
        ring.append((i * step, 0.0))
    for i in range(n):
        ring.append((size, i * step))
    for i in range(n):
        ring.append((size - i * step, size))
    for i in range(n):
        ring.append((0.0, size - i * step))
    pts = np.array(ring + [(size / 2, size / 2)], float)
    v = np.c_[pts, np.zeros(len(pts))]
    c = len(pts) - 1
    f = np.array([(i, (i + 1) % len(ring), c) for i in range(len(ring))], np.int64)
    return v, f


class Retriangulation(unittest.TestCase):
    def test_outline_kept_and_no_zero_area_triangles(self):
        v, f = square_with_collinear_outline()
        tri = core._retriangulate_flat(v, f, np.array([0.0, 0.0, 1.0]))
        self.assertIsNotNone(tri)
        self.assertLess(len(tri), len(f))                           # fewer triangles than the fan
        cr = np.cross(v[tri[:, 1]] - v[tri[:, 0]], v[tri[:, 2]] - v[tri[:, 0]])
        self.assertTrue(np.all(0.5 * np.abs(cr[:, 2]) > 1e-9))      # none of them degenerate
        self.assertAlmostEqual(0.5 * np.abs(cr[:, 2]).sum(), 100.0, places=6)

        def border(ff):
            e = np.sort(np.vstack([ff[:, [0, 1]], ff[:, [1, 2]], ff[:, [2, 0]]]), axis=1)
            u, cnt = np.unique(e, axis=0, return_counts=True)
            return set(map(tuple, u[cnt == 1]))
        self.assertEqual(border(tri), border(f))                   # exactly the outline edges

    def test_reduction_of_a_dense_flat_sided_mesh_stays_closed(self):
        import trimesh
        box = trimesh.creation.box(extents=(30, 20, 8))
        dense = box.subdivide().subdivide().subdivide().subdivide()
        dense.merge_vertices()
        self.assertTrue(dense.is_watertight)
        prm = core.Params(decimate=200)
        ctx = core.MeshCtx(dense)
        tol = max(0.01, 5e-4 * ctx.diag)
        core._auto_crease(ctx, prm, lambda s: None)
        label, planes = core.find_planes(ctx, prm, tol, lambda s: None)
        core.snap_to_planes(ctx, label, planes, tol)
        small = core.simplify_keep_features(ctx, label, planes, prm, tol, 200, lambda s: None)
        self.assertIsNotNone(small)
        self.assertTrue(small.is_watertight)
        self.assertLess(len(small.faces), len(dense.faces) / 4)
        self.assertTrue(np.all(small.area_faces > 1e-9))
        self.assertAlmostEqual(small.volume, dense.volume, places=6)


class WireOrder(unittest.TestCase):
    def test_edges_come_back_chained_with_their_own_orientation(self):
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeVertex
        P = [(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0)]
        V = [BRepBuilderAPI_MakeVertex(core._gp(p)).Vertex() for p in P]
        E = [BRepBuilderAPI_MakeEdge(V[i], V[(i + 1) % 4]).Edge() for i in range(4)]
        jumbled = [E[2], E[0], E[3], E[1]]
        out = core._wire_order(jumbled)
        self.assertEqual(len(out), 4)
        for e in out:                                               # the very same edge objects
            self.assertTrue(any(e.IsEqual(x) for x in E))
        # consecutive edges share an end point: a chain, whichever edge it starts from
        from OCP.BRep import BRep_Tool
        from OCP.TopAbs import TopAbs_VERTEX

        def ends(e):
            return {tuple(round(c, 6) for c in (q.X(), q.Y(), q.Z())) for q in
                    (BRep_Tool.Pnt_s(core._cast("Vertex", x)) for x in core._explore(e, TopAbs_VERTEX))}
        chain = [ends(e) for e in out]
        for a, b in zip(chain, chain[1:] + chain[:1]):
            self.assertTrue(a & b)
        self.assertEqual(core._edge_length(E[0]), 10.0)
        tiny = BRepBuilderAPI_MakeEdge(V[0], BRepBuilderAPI_MakeVertex(core._gp((1e-6, 0, 0))).Vertex()).Edge()
        self.assertLess(core._edge_length(tiny), 1e-4)              # what the filler now leaves out


class Budget(unittest.TestCase):
    def test_start_time_survives_the_reduction(self):
        """The 60 s cap of the Draft preset counts from the start, also after the mesh is reduced."""
        src = open(os.path.join(ROOT, "tostep", "plugins", "mesh2step", "classic.py"), encoding="utf-8").read()
        body = src[src.index("def convert(in_path, out_path"):]
        body = body[:body.index("\ndef ", 10)]
        rebuilt = body.count("ctx = MeshCtx(")
        self.assertGreaterEqual(rebuilt, 3)
        self.assertEqual(body.count("ctx.t0 = t0"), rebuilt)


if __name__ == "__main__":
    unittest.main()
