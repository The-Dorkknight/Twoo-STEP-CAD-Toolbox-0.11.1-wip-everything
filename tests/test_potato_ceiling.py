# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""The Potato profile reduces big meshes first; no other profile does."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tostep.core import perf  # noqa: E402

HW = {"logical": 4, "physical": 4, "ram_gb": 16.0, "apple_silicon": False, "gpu_backend": None}


class PotatoCeiling(unittest.TestCase):
    def test_only_potato_has_a_ceiling(self):
        self.assertEqual(perf.settings("potato", HW)["tri_ceiling"], 30000)
        for name in ("moderate", "extreme"):
            self.assertFalse(perf.settings(name, HW).get("tri_ceiling", 0))

    def test_worker_env_carries_it(self):
        self.assertEqual(perf.worker_env(perf.settings("potato", HW))["TOSTEP_TRI_CEILING"], "30000")
        self.assertEqual(perf.worker_env(perf.settings("extreme", HW))["TOSTEP_TRI_CEILING"], "0")

    def test_loader_reduces_only_when_asked(self):
        import trimesh
        import tempfile
        from tostep.plugins.mesh2step import classic
        m = trimesh.creation.icosphere(subdivisions=5)          # 20,480 triangles
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s.stl"); m.export(p)
            for ceil, expect in (("10000", 10000), ("0", 0)):
                os.environ["TOSTEP_TRI_CEILING"] = ceil
                try:
                    prm = classic.Params(); msgs = []
                    classic.load_mesh(p, prm, msgs.append)
                finally:
                    os.environ.pop("TOSTEP_TRI_CEILING", None)
                self.assertEqual(prm.decimate, expect)


if __name__ == "__main__":
    unittest.main()
