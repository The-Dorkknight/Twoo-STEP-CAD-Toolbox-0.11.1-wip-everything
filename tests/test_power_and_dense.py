# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""0.11.3: the power check, the sign-of-life ticker with its stage timings, and the dense-mesh prep.

    python -m unittest tests.test_power_and_dense -v
"""
import os
import sys
import time
import types
import unittest

import numpy as np
import trimesh

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tostep.core import powerinfo  # noqa: E402
from tostep.plugins.mesh2step import classic  # noqa: E402


class Power(unittest.TestCase):
    def snap(self, **kw):
        s = {"platform": "linux", "on_battery": False, "mhz": None, "max_mhz": None, "driver": "intel_pstate",
             "governor": "powersave", "epp": "balance_performance", "turbo_off": False, "max_perf_pct": 100,
             "profile": "balanced"}
        s.update(kw)
        return s

    def test_a_healthy_laptop_gets_no_advice(self):
        # intel_pstate's 'powersave' governor is its normal dynamic mode, not a stuck clock
        self.assertEqual(powerinfo.advice(self.snap()), [])

    def test_battery_is_named(self):
        self.assertTrue(any("battery" in a for a in powerinfo.advice(self.snap(on_battery=True))))

    def test_power_saver_profile_says_how_to_fix_it_per_system(self):
        self.assertTrue(any("powerprofilesctl" in a for a in powerinfo.advice(self.snap(profile="power-saver"))))
        self.assertTrue(any("Windows" in a for a in powerinfo.advice(self.snap(platform="win32", profile="Power saver"))))
        self.assertTrue(any("Low Power Mode" in a for a in powerinfo.advice(
            {"platform": "darwin", "low_power": True})))

    def test_epp_turbo_and_cap(self):
        self.assertTrue(any("favour saving power" in a for a in powerinfo.advice(self.snap(epp="power"))))
        self.assertTrue(any("Turbo boost" in a for a in powerinfo.advice(self.snap(turbo_off=True))))
        self.assertTrue(any("capped at 40 %" in a for a in powerinfo.advice(self.snap(max_perf_pct=40))))

    def test_old_style_powersave_governor_is_flagged_but_only_there(self):
        self.assertTrue(powerinfo.advice(self.snap(driver="acpi-cpufreq")))

    def test_a_low_clock_only_counts_when_the_processor_is_busy(self):
        s = self.snap(mhz=800.0, max_mhz=4100.0)
        self.assertEqual(powerinfo.advice(s), [])                 # idle: low clocks are normal
        self.assertTrue(any("0.8 of 4.1 GHz" in a for a in powerinfo.advice(s, busy=True)))

    def test_snapshot_never_raises(self):
        s = powerinfo.snapshot()
        self.assertIn("platform", s)
        self.assertIsInstance(powerinfo.advice(s), list)
        self.assertIsInstance(powerinfo.clock_text(), str)


class Ticker(unittest.TestCase):
    def test_stage_times_and_report(self):
        L = classic._Log(lambda s: None)
        L.stage = "alpha"
        L._spent["alpha"] = 40.0                     # pretend: 40 s in one stage, 2 s in another
        L._spent["beta"] = 2.0
        rep = L.time_report()
        self.assertIsNotNone(rep)
        self.assertIn("alpha 0:40", rep)
        self.assertNotIn("beta", rep)                # under 5 % of the time: left out
        self.assertIsNone(classic._Log(lambda s: None).time_report())      # a short run needs no report

    def test_ticker_speaks_after_a_quiet_spell_and_names_the_stage(self):
        lines = []
        L = classic._Log(lines.append)
        L.TICK_S, L.POLL_S = 0.3, 0.1
        L.start_ticker()
        L.stage = "building faces"
        L.detail = "round 2 of up to 6"
        time.sleep(0.9)
        L.stop_ticker()
        said = [x for x in lines if "still building faces" in x]
        self.assertTrue(said, lines)
        self.assertIn("round 2 of up to 6", said[0])

    def test_ticker_stays_quiet_while_the_log_is_busy(self):
        lines = []
        L = classic._Log(lines.append)
        L.TICK_S, L.POLL_S = 0.5, 0.1
        L.start_ticker()
        for _ in range(8):
            L("real message")
            time.sleep(0.1)
        L.stop_ticker()
        self.assertFalse([x for x in lines if "still" in x], lines)


def noisy_sphere(noise):
    m = trimesh.creation.icosphere(7, radius=40)           # 327,680 triangles
    if noise:
        rng = np.random.default_rng(0)
        n = m.vertex_normals
        m.vertices = m.vertices + n * rng.normal(0, noise, (len(m.vertices), 1))
    return m


class DensePrep(unittest.TestCase):
    prm = types.SimpleNamespace(scan_cleanup="auto", tol=0.0)

    def test_a_noisy_dense_closed_mesh_is_denoised_and_reduced_and_stays_closed(self):
        m = noisy_sphere(0.05)
        out = []
        r = classic.scan_prep(m, self.prm, False, out.append)
        self.assertEqual(len(r.faces) <= classic.SCAN_PREREDUCE * 2, True)
        self.assertLess(len(r.faces), len(m.faces))
        self.assertTrue(r.is_watertight)
        self.assertTrue(any("scanner noise" in x for x in out), out)
        self.assertTrue(any(x.startswith("Reduced plainly") for x in out), out)

    def test_a_clean_dense_mesh_is_not_denoised(self):
        m = noisy_sphere(0.0)
        v0 = np.array(m.vertices)
        out = []
        r = classic.scan_prep(m, self.prm, False, out.append)
        self.assertFalse(any("Denoised" in x for x in out), out)
        self.assertTrue(r.is_watertight)
        # the reduction is guarded to half the tolerance: nothing moved further than that
        _, d, _ = trimesh.proximity.closest_point(r, v0[::400])
        self.assertLess(float(d.max()), 0.5 * 5e-4 * float(np.linalg.norm(m.extents)) + 1e-9)

    def test_off_leaves_it_alone(self):
        m = noisy_sphere(0.05)
        n = len(m.faces)
        out = []
        r = classic.scan_prep(m, types.SimpleNamespace(scan_cleanup="off", tol=0.0), False, out.append)
        self.assertEqual(len(r.faces), n)
        self.assertEqual(out, [])

    def test_a_small_mesh_is_not_a_scan(self):
        m = trimesh.creation.icosphere(4, radius=10)
        out = []
        self.assertIs(classic.scan_prep(m, self.prm, False, out.append), m)
        self.assertEqual(out, [])

    def test_plain_reduce_refuses_to_move_the_surface_further_than_allowed(self):
        m = trimesh.creation.icosphere(6, radius=20)
        out = []
        self.assertIsNone(classic.plain_reduce(m, 200, out.append, max_move=0.001))
        self.assertTrue(any("not used" in x for x in out), out)


class StatusFile(unittest.TestCase):
    """The worker writes where it is; the server reads it back while the worker is stuck in OpenCASCADE."""

    def test_log_writes_stage_and_detail_and_the_server_reads_them(self):
        import json
        import tempfile
        path = os.path.join(tempfile.mkdtemp(), "j.status.json")
        old = os.environ.get("TOSTEP_STATUS_FILE")
        os.environ["TOSTEP_STATUS_FILE"] = path
        try:
            L = classic._Log(lambda s: None)
            L.stage = "building faces and sewing them into a solid"
            L.detail = "round 3 of up to 6"
            time.sleep(1.1)
            L.detail = "round 4 of up to 6"            # throttled to one write a second, so this one lands
            with open(path, encoding="utf-8") as fh:
                st = json.load(fh)
            self.assertEqual(st["stage"], "building faces and sewing them into a solid")
            self.assertIn("round", st["detail"])
            from tostep.frontends.web import server
            live = server._live({"state": "running", "status_file": path, "started": time.time() - 5})
            self.assertEqual(live["stage"], st["stage"])
            self.assertGreaterEqual(live["stage_for"], 0)
            self.assertIn("clock", live)
            self.assertEqual(server._live({"state": "done"}), {})          # nothing for a finished job
        finally:
            if old is None:
                os.environ.pop("TOSTEP_STATUS_FILE", None)
            else:
                os.environ["TOSTEP_STATUS_FILE"] = old

    def test_a_missing_status_file_is_not_an_error(self):
        from tostep.frontends.web import server
        live = server._live({"state": "running", "status_file": "/nonexistent/x.json", "started": time.time()})
        self.assertNotIn("stage", live)


if __name__ == "__main__":
    unittest.main()
