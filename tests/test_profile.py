# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""Extrude / Loft features that came over from the first STL2STEP (the earlier name) trace-and-extrude tool:
symmetric extrusion, your own loft slice heights, and the one-call library wrapper."""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import trimesh  # noqa: E402

from tostep.plugins.mesh2step import profile2d  # noqa: E402
from tostep.plugins.mesh2step import classic as core  # noqa: E402


def z_range(step):
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    bb = Bnd_Box()
    BRepBndLib.Add_s(core.read_step(step), bb)
    return bb.CornerMin().Z(), bb.CornerMax().Z()


class Profile(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.addCleanup(self.d.cleanup)
        plate = trimesh.creation.box(extents=(40, 20, 6))
        plate.apply_translation((0, 0, 10))                  # spans z 7 .. 13
        self.plate = os.path.join(self.d.name, "plate.stl")
        plate.export(self.plate)
        self.cone = os.path.join(self.d.name, "cone.stl")
        trimesh.creation.cone(radius=10, height=30, sections=64).export(self.cone)

    def test_extrude_up_from_the_bottom_by_default(self):
        out = os.path.join(self.d.name, "a.step")
        _, rep = profile2d.convert_stl_to_step(self.plate, out, mode="silhouette", extrude_height=10)
        self.assertTrue(rep.is_solid)
        lo, hi = z_range(out)
        self.assertAlmostEqual(lo, 7.0, places=2)
        self.assertAlmostEqual(hi, 17.0, places=2)

    def test_symmetric_is_centred_on_the_part(self):
        out = os.path.join(self.d.name, "b.step")
        profile2d.convert_stl_to_step(self.plate, out, mode="silhouette", extrude_height=10, symmetric=True)
        lo, hi = z_range(out)
        self.assertAlmostEqual(lo, 5.0, places=2)
        self.assertAlmostEqual(hi, 15.0, places=2)

    def test_symmetric_slice_is_centred_on_the_cross_section(self):
        out = os.path.join(self.d.name, "c.step")
        profile2d.convert_stl_to_step(self.cone, out, mode="slice", slice_height=15, extrude_height=4, symmetric=True)
        lo, hi = z_range(out)
        self.assertAlmostEqual(lo, 13.0, places=2)
        self.assertAlmostEqual(hi, 17.0, places=2)

    def test_loft_with_your_own_heights(self):
        out = os.path.join(self.d.name, "d.step")
        _, rep = profile2d.convert_stl_to_step(self.cone, out, mode="loft", manual_heights=[1, 10, 20, 29])
        self.assertTrue(rep.is_solid)

    def test_heights_text_parsing(self):
        from tostep.plugins import mesh2step
        self.assertEqual(mesh2step.heights("2, 10.5;30"), (2.0, 10.5, 30.0))
        self.assertIsNone(mesh2step.heights(""))
        with self.assertRaises(ValueError):
            mesh2step.heights("a,b")


if __name__ == "__main__":
    unittest.main()
