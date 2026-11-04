# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""0.10.2: the Standard preset's Auto engine, and the checks on the finished solid (a region its
face doesn't cover; the checks must not mesh the shape itself).

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
sys.path.insert(0, os.path.join(ROOT, "tests"))

from tostep.plugins.mesh2step import engines  # noqa: E402
from tostep.plugins.mesh2step import presets  # noqa: E402
from tostep.plugins.mesh2step import quadfit  # noqa: E402
from tostep.plugins.mesh2step import classic as core  # noqa: E402

KNOB = os.path.join(ROOT, "tests", "data", "knob_plain.stl")


def _box_solid(ext):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt
    return BRepPrimAPI_MakeBox(gp_Pnt(*(-0.5 * np.asarray(ext))), *ext).Shape()


def _has_triangles(shape):
    from OCP.BRep import BRep_Tool
    from OCP.TopLoc import TopLoc_Location
    for f in core._explore(shape, core.TopAbs_FACE):
        if core._S(BRep_Tool, "Triangulation")(core._cast("Face", f), TopLoc_Location()) is not None:
            return True
    return False


class Uncovered(unittest.TestCase):
    def test_finds_mesh_with_nothing_over_it_and_leaves_the_shape_unmeshed(self):
        mesh = trimesh.creation.box(extents=(20, 20, 20)).subdivide().subdivide()
        ctx = core.MeshCtx(mesh)
        same = _box_solid((20, 20, 20))
        self.assertEqual(len(core._uncovered(same, ctx, 0.05, 0.05)), 0)
        self.assertFalse(_has_triangles(same), "the check must mesh a copy, not the shape")
        short = _box_solid((20, 20, 14))        # 3 mm short at the top and the bottom
        far = core._uncovered(short, ctx, 0.05, 0.05)
        self.assertGreater(len(far), 0)
        self.assertTrue(np.all(np.abs(far[:, 2]) > 7.0 - 1e-6))

    def test_faces_not_drawn_is_empty_for_a_good_solid(self):
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
        cyl = BRepPrimAPI_MakeCylinder(5.0, 10.0).Shape()
        self.assertEqual(len(core._faces_not_drawn(cyl, 0.05)), 0)
        self.assertFalse(_has_triangles(cyl))


class StandardPreset(unittest.TestCase):
    def test_standard_is_auto_and_fine_starts_from_it(self):
        self.assertEqual(presets.settings("standard")["engine"], "auto")
        self.assertEqual(presets.settings("fine")["engine"], "best")
        self.assertIn("auto", engines.ENGINES)

    def test_a_cad_part_goes_the_quick_way(self):
        with tempfile.TemporaryDirectory() as d:
            log = []
            rep = engines.convert(KNOB, os.path.join(d, "k.step"), {}, "auto", log=log.append)
            self.assertIn("converting with the Ladder engine", "\n".join(log))
            self.assertTrue(rep.is_solid and rep.is_valid)

    @unittest.skipUnless(quadfit.remesher_name(), "no quad remesher here")
    def test_a_smooth_organic_part_becomes_quad_patches(self):
        from test_quadfit import blob
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "blob.stl")
            blob(4, 0.04).export(src)
            rep = engines.convert(src, os.path.join(d, "blob.step"), {}, "auto", log=lambda s: None)
            self.assertIn("quad patches", rep.route)
            self.assertEqual(rep.n_faceted_regions, 0)
            self.assertLessEqual(rep.max_dev, rep.tol)


if __name__ == "__main__":
    unittest.main()
