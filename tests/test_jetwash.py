# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""Jetwash on a made-up part: a box with small bosses that are all below the threshold (removed
in batches, 0.10.0) and one big boss that must stay.

    python -m unittest discover -s tests -v
"""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tostep.plugins.jetwash import jetwash  # noqa: E402
from tostep.plugins.mesh2step import classic as core  # noqa: E402


def box_with_bosses(path, n_small=24, big=True):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
    from OCP.gp import gp_Ax2, gp_Pnt, gp_Dir
    shape = BRepPrimAPI_MakeBox(60.0, 40.0, 10.0).Shape()
    for i in range(n_small):
        x = 5 + (i % 8) * 7; y = 8 + (i // 8) * 12
        cyl = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(x, y, 10.0), gp_Dir(0, 0, 1)), 0.3, 0.4).Shape()
        shape = BRepAlgoAPI_Fuse(shape, cyl).Shape()
    if big:
        cyl = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(30.0, 20.0, 0.0), gp_Dir(0, 0, -1)), 6.0, 5.0).Shape()
        shape = BRepAlgoAPI_Fuse(shape, cyl).Shape()
    core.write_step(shape, path)
    return path


class Bosses(unittest.TestCase):
    def test_small_bosses_go_in_batches_and_the_big_one_stays(self):
        with tempfile.TemporaryDirectory() as d:
            src = box_with_bosses(os.path.join(d, "bosses.step"))
            log = []
            rep = jetwash.jetwash(src, os.path.join(d, "washed.step"), threshold=1.0, log=log.append)
            self.assertTrue(rep.ok and rep.valid_after)
            self.assertEqual(rep.features_removed, 24)
            self.assertEqual(rep.features_kept, 0)
            self.assertEqual(rep.faces_after, 6 + 2)                # the box and the big boss (wall + end)
            self.assertAlmostEqual(rep.volume_after, 60 * 40 * 10 + 3.141592653589793 * 36 * 5, delta=0.01)
            self.assertLess(rep.seconds, 60)

    def test_batches_fall_back_to_single_removals(self):
        """FAST_FACES bounds a batch; a part with more boss faces than that is still fully washed."""
        self.assertGreaterEqual(jetwash.FAST_FACES, 8)
        with tempfile.TemporaryDirectory() as d:
            src = box_with_bosses(os.path.join(d, "bosses.step"), n_small=16, big=False)
            rep = jetwash.jetwash(src, os.path.join(d, "washed.step"), threshold=1.0, log=lambda s: None)
            self.assertEqual((rep.features_removed, rep.faces_after), (16, 6))
            self.assertAlmostEqual(rep.volume_after, 24000.0, delta=0.01)


if __name__ == "__main__":
    unittest.main()
