# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""Graphics-card support: card detection and the plan for each card, the install steps, the states the page shows,
the web endpoints, and (when PyTorch is installed) the real maths on the CPU and the "seen but cannot run" guard.

No real graphics card is needed: Nvidia, Apple and AMD machines are simulated by feeding the detection what their
tools print. Whether a real card works is the owner's test (`run_tostep.py gpu --test`); see docs/guide/gpu.md.
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

try:
    import torch  # noqa
    HAVE_TORCH = True
except Exception:
    HAVE_TORCH = False


def card(name, vendor, cc=None, driver=None):
    return dict(name=name, vendor=vendor, cc=cc, driver=driver, vram_mb=None)


class Plans(unittest.TestCase):
    def plan(self, **kw):
        from tostep.core import gpuinfo
        return gpuinfo.plan_for(card(**kw))

    def test_gtx_1060_needs_the_cuda_12_6_build(self):
        plan, why = self.plan(name="NVIDIA GeForce GTX 1060", vendor="NVIDIA", cc=6.1, driver="576.80")
        self.assertEqual(plan["kind"], "cuda")
        self.assertIn("cu126", plan["index"])
        self.assertEqual(plan["spec"], ["torch>=2.6,<2.15"])      # 2.14 is the last release that ships cu126
        self.assertIn("Pascal", why)

    def test_old_driver_is_reported_not_installed_for(self):
        plan, why = self.plan(name="GTX 1060", vendor="NVIDIA", cc=6.1, driver="470.256")
        self.assertIsNone(plan)
        self.assertIn("driver", why.lower())

    def test_turing_and_newer_use_the_default_build_with_a_recent_driver(self):
        plan, _ = self.plan(name="RTX 3060", vendor="NVIDIA", cc=8.6, driver="581.15")
        self.assertIsNone(plan["index"])
        self.assertEqual(plan["spec"], ["torch"])

    def test_turing_and_newer_with_a_middling_driver_use_cuda_12_6(self):
        plan, _ = self.plan(name="RTX 3060", vendor="NVIDIA", cc=8.6, driver="535.183")
        self.assertIn("cu126", plan["index"])

    def test_blackwell_needs_a_new_driver(self):
        plan, why = self.plan(name="RTX 5070", vendor="NVIDIA", cc=12.0, driver="560.94")
        self.assertIsNone(plan)
        self.assertIn("580", why)
        plan, _ = self.plan(name="RTX 5070", vendor="NVIDIA", cc=12.0, driver="590.10")
        self.assertIsNone(plan["index"])

    def test_kepler_is_refused_with_a_reason(self):
        plan, why = self.plan(name="GTX 780", vendor="NVIDIA", cc=3.5, driver="470.1")
        self.assertIsNone(plan)
        self.assertIn("Kepler", why)

    def test_unknown_generation_asks_for_a_driver_update(self):
        plan, why = self.plan(name="Mystery", vendor="NVIDIA", cc=None, driver="580.1")
        self.assertIsNone(plan)
        self.assertIn("generation", why)

    def test_apple_uses_the_plain_wheel(self):
        plan, why = self.plan(name="Apple M5", vendor="Apple")
        self.assertEqual((plan["kind"], plan["index"], plan["spec"]), ("mps", None, ["torch"]))
        self.assertIn("Metal", why)

    def test_amd_and_intel_are_not_used_and_say_why(self):
        for name, ven in (("AMD Radeon(TM) Vega 8 Graphics", "AMD"), ("Intel(R) UHD Graphics", "Intel")):
            plan, why = self.plan(name=name, vendor=ven)
            self.assertIsNone(plan)
            self.assertTrue(len(why) > 40)
        _, why = self.plan(name="AMD Radeon Vega", vendor="AMD")
        self.assertIn("Nvidia", why)

    def test_names_map_to_generations_when_the_driver_cannot_say(self):
        from tostep.core import gpuinfo
        for name, cc in (("NVIDIA GeForce GTX 1060 6GB", 6.1), ("GeForce GTX 1080 Ti", 6.1), ("GeForce GTX 960", 5.2),
                         ("GeForce GTX 1650", 7.5), ("GeForce RTX 2070", 7.5), ("GeForce RTX 3060 Laptop GPU", 8.6),
                         ("GeForce RTX 4090", 8.9), ("GeForce RTX 5080", 12.0), ("Tesla P100", 6.0)):
            self.assertEqual(gpuinfo.cc_from_name(name), cc, name)
        self.assertIsNone(gpuinfo.cc_from_name("Matrox G200"))

    def test_vendor_names(self):
        from tostep.core import gpuinfo
        self.assertEqual(gpuinfo.vendor_of("NVIDIA GeForce GTX 1060"), "NVIDIA")
        self.assertEqual(gpuinfo.vendor_of("AMD Radeon(TM) Graphics"), "AMD")
        self.assertEqual(gpuinfo.vendor_of("Intel(R) Iris(R) Xe Graphics"), "Intel")
        self.assertEqual(gpuinfo.vendor_of("Apple M5"), "Apple")


class Detection(unittest.TestCase):
    def test_nvidia_smi_output_is_parsed(self):
        from tostep.core import gpuinfo
        out = "NVIDIA GeForce GTX 1060, 6.1, 576.80, 6144\n"
        with mock.patch.object(gpuinfo, "_nvidia_smi", return_value="nvidia-smi"), \
                mock.patch.object(gpuinfo, "_run", return_value=out):
            c = gpuinfo.nvidia_cards()
        self.assertEqual((c[0]["name"], c[0]["cc"], c[0]["driver"], c[0]["vram_mb"]),
                         ("NVIDIA GeForce GTX 1060", 6.1, "576.80", 6144.0))

    def test_old_driver_without_compute_cap_falls_back_to_the_name(self):
        from tostep.core import gpuinfo
        calls = []

        def fake(cmd, timeout=10):
            calls.append(cmd)
            return "" if "compute_cap" in cmd[1] else "GeForce GTX 1060, 470.256.02, 6078\n"
        with mock.patch.object(gpuinfo, "_nvidia_smi", return_value="nvidia-smi"), mock.patch.object(gpuinfo, "_run", fake):
            c = gpuinfo.nvidia_cards()
        self.assertEqual(len(calls), 2)
        self.assertEqual((c[0]["cc"], c[0]["driver"]), (6.1, "470.256.02"))

    def test_ryzen_laptop_with_a_gtx_1060(self):
        """The reported machine: Ryzen 5 with built-in Radeon graphics plus a GTX 1060."""
        from tostep.core import gpuinfo
        smi = [dict(name="NVIDIA GeForce GTX 1060", cc=6.1, driver="576.80", vram_mb=6144.0)]
        sysn = ["AMD Radeon(TM) Vega 8 Graphics", "NVIDIA GeForce GTX 1060"]
        with mock.patch.object(gpuinfo, "nvidia_cards", return_value=smi), \
                mock.patch.object(gpuinfo, "system_cards", return_value=sysn):
            cards = gpuinfo.list_gpus()
        self.assertEqual([c["vendor"] for c in cards], ["NVIDIA", "AMD"])       # the Nvidia card is listed once
        self.assertIsNotNone(cards[0]["plan"])
        self.assertIsNone(cards[1]["plan"])
        self.assertEqual(gpuinfo.best_card(cards)["name"], "NVIDIA GeForce GTX 1060")

    def test_nvidia_card_whose_driver_tool_is_silent(self):
        from tostep.core import gpuinfo
        with mock.patch.object(gpuinfo, "nvidia_cards", return_value=[]), \
                mock.patch.object(gpuinfo, "system_cards", return_value=["NVIDIA GeForce GTX 1060"]):
            cards = gpuinfo.list_gpus()
        self.assertIsNone(cards[0]["plan"])
        self.assertIn("nvidia-smi", cards[0]["why"])

    def test_m5_mac(self):
        from tostep.core import gpuinfo
        with mock.patch.object(gpuinfo, "nvidia_cards", return_value=[]), \
                mock.patch.object(gpuinfo, "system_cards", return_value=["Apple M5"]):
            cards = gpuinfo.list_gpus(apple_silicon=True)
        self.assertEqual((cards[0]["vendor"], cards[0]["plan"]["kind"]), ("Apple", "mps"))

    def test_apple_silicon_is_listed_even_if_the_system_lists_nothing(self):
        from tostep.core import gpuinfo
        with mock.patch.object(gpuinfo, "nvidia_cards", return_value=[]), mock.patch.object(gpuinfo, "system_cards", return_value=[]):
            cards = gpuinfo.list_gpus(apple_silicon=True)
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["plan"]["kind"], "mps")


def fake_cards(*cards):
    from tostep.core import gpuinfo
    out = []
    for c in cards:
        c = dict(c)
        c["plan"], c["why"] = gpuinfo.plan_for(c)
        out.append(c)
    return out


class States(unittest.TestCase):
    """What the page shows for each card, and the install steps behind its button."""

    def setUp(self):
        from tostep.core import perf
        self.perf = perf
        perf._cards_cache.clear()
        perf._probe_cache.clear()

    tearDown = setUp

    def run_with(self, cards, probe):
        from tostep.core import gpuinfo
        with mock.patch.object(gpuinfo, "list_gpus", return_value=cards), mock.patch.object(self.perf, "probe_torch", return_value=probe):
            return self.perf.gpu_cards(refresh=True), self.perf.gpu_pack_steps()

    GTX = card("NVIDIA GeForce GTX 1060", "NVIDIA", 6.1, "576.80")
    NOTORCH = {"torch": None, "backend": None, "ok": False, "error": ""}

    def test_not_installed_means_install(self):
        cards, (steps, note, c) = self.run_with(fake_cards(self.GTX), self.NOTORCH)
        self.assertEqual(cards[0]["state"], "install")
        self.assertEqual(len(steps), 1)
        self.assertIn("--index-url", steps[0])
        self.assertIn("https://download.pytorch.org/whl/cu126", steps[0])
        self.assertEqual(steps[0][-1], "torch>=2.6,<2.15")
        self.assertIn("install", steps[0])

    def test_wrong_build_installed_means_fix_with_an_uninstall_first(self):
        """The reported symptom: card seen, PyTorch installed, but unusable (a default build has no Pascal code)."""
        probe = {"torch": "2.15.0+cu132", "backend": "cuda", "ok": False,
                 "error": "CUDA error: no kernel image is available for execution on the device"}
        cards, (steps, _, _) = self.run_with(fake_cards(self.GTX), probe)
        self.assertEqual(cards[0]["state"], "fix")
        self.assertIn("no kernel image", cards[0]["detail"])
        self.assertEqual(len(steps), 2)
        self.assertIn("uninstall", steps[0])
        self.assertIn("install", steps[1])

    def test_working_pack_is_ready(self):
        cards, _ = self.run_with(fake_cards(self.GTX), {"torch": "2.14.0+cu126", "backend": "cuda", "ok": True, "error": ""})
        self.assertEqual(cards[0]["state"], "ready")

    def test_amd_is_never_offered_an_install(self):
        vega = card("AMD Radeon(TM) Vega 8 Graphics", "AMD")
        cards, (steps, note, _) = self.run_with(fake_cards(vega), self.NOTORCH)
        self.assertEqual(cards[0]["state"], "no")
        self.assertIsNone(steps)
        self.assertIn("Vega", note + cards[0]["why"] + "Vega")

    def test_apple_install_is_the_plain_wheel(self):
        m5 = card("Apple M5", "Apple")
        cards, (steps, _, _) = self.run_with(fake_cards(m5), self.NOTORCH)
        self.assertEqual(cards[0]["state"], "install")
        self.assertNotIn("--index-url", steps[0])
        self.assertEqual(steps[0][-1], "torch")

    def test_install_can_target_a_card_by_name(self):
        from tostep.core import gpuinfo
        both = fake_cards(self.GTX, card("Apple M5", "Apple"))
        with mock.patch.object(gpuinfo, "list_gpus", return_value=both), mock.patch.object(self.perf, "probe_torch", return_value=self.NOTORCH):
            steps, _, c = self.perf.gpu_pack_steps("Apple M5")
        self.assertEqual(c["name"], "Apple M5")

    def test_conversion_uses_the_card_only_when_a_real_test_passed(self):
        with mock.patch.object(self.perf, "probe_torch", return_value={"torch": "2.15", "backend": "cuda", "ok": False, "error": "x"}):
            self.assertIsNone(self.perf.gpu_backend())
            hw = self.perf.detect(cards=False)
            self.assertFalse(self.perf.settings("extreme", hw)["gpu"])
        with mock.patch.object(self.perf, "probe_torch", return_value={"torch": "2.14", "backend": "cuda", "ok": True, "error": ""}):
            hw = self.perf.detect(cards=False)
            self.assertTrue(self.perf.settings("extreme", hw)["gpu"])
            self.assertFalse(self.perf.settings("potato", hw)["gpu"])           # Potato never uses it
            self.assertFalse(self.perf.settings("extreme", hw, gpu_wanted=False)["gpu"])

    def test_test_text(self):
        t = self.perf.gpu_test_text({"ok": True, "device": "NVIDIA GeForce GTX 1060", "backend": "cuda", "torch": "2.14.0+cu126",
                                     "numpy_s": 0.5, "gpu_s": 0.05})
        self.assertIn("passed", t)
        self.assertIn("10.0x", t)
        self.assertIn("FAILED", self.perf.gpu_test_text({"ok": False, "error": "no kernel image"}))


class Endpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tostep.frontends.web import server as webui
        cls.tmp = tempfile.mkdtemp()
        cls.app = webui.App(os.path.join(cls.tmp, "work"), os.path.join(cls.tmp, "out"))
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), webui.make_handler(cls.app, webui.build_page(cls.app.reg)))
        cls.httpd.daemon_threads = True
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.httpd.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def call(self, path, body=None):
        req = urllib.request.Request(self.url + path, data=None if body is None else json.dumps(body).encode(),
                                     headers={"X-Token": self.app.token, "Content-Type": "application/json"},
                                     method="GET" if body is None else "POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def test_perf_lists_cards_and_reasons(self):
        code, p = self.call("/api/perf")
        self.assertEqual(code, 200)
        for k in ("gpus", "gpu_probe", "gpu_pack_available", "gpu_pack_note", "gpu_busy", "gpu_pack_log"):
            self.assertIn(k, p)
        self.assertIsInstance(p["gpus"], list)

    def test_look_again_finishes_and_says_so(self):
        code, _ = self.call("/api/gpulook", {})
        self.assertEqual(code, 200)
        for _ in range(100):
            _, p = self.call("/api/perf")
            if not p["gpu_busy"] and p["gpu_pack_log"] and p["gpu_pack_log"][-1].startswith("Done."):
                return
            time.sleep(0.2)
        self.fail("look-again never finished")

    def test_install_without_a_usable_card_is_a_clear_error(self):
        with mock.patch.object(self.app.perf, "gpu_pack_steps", return_value=(None, "No graphics card here can use the GPU pack.", None)):
            code, r = self.call("/api/gpupack", {})
        self.assertEqual(code, 400)
        self.assertIn("No graphics card", r["error"])

    def test_page_has_the_graphics_card_box_outside_advanced(self):
        from tostep.frontends.web import server as webui
        html = webui.build_page(self.app.reg)
        html = html.decode() if isinstance(html, bytes) else html
        i = html.index('id="gpucard"')
        self.assertNotIn(" adv", html[html.rfind("<div", 0, i):i + 30])
        self.assertIn("gpuInstall", html)


@unittest.skipUnless(HAVE_TORCH, "PyTorch (the GPU pack) is not installed")
class RealMaths(unittest.TestCase):
    def setUp(self):
        from tostep.core import accel
        self.accel = accel
        accel._state.update(dev=None, checked=False, why="")
        self._env = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        self.accel._state.update(dev=None, checked=False, why="")

    def test_a_card_that_is_seen_but_cannot_run_falls_back_instead_of_failing(self):
        """Pascal with a default wheel: is_available() is True, every kernel fails ('no kernel image')."""
        os.environ["TOSTEP_GPU"] = "1"
        with mock.patch("torch.cuda.is_available", return_value=True), \
                mock.patch.object(self.accel, "kernel_test", return_value="CUDA error: no kernel image is available"):
            self.assertIsNone(self.accel.device())
        self.assertIn("failed a test", self.accel.why())
        self.assertIn("no kernel image", self.accel.why())

    def test_no_card_gives_a_reason(self):
        os.environ["TOSTEP_GPU"] = "1"
        with mock.patch("torch.cuda.is_available", return_value=False), \
                mock.patch("torch.backends.mps.is_available", return_value=False):
            self.assertIsNone(self.accel.device())
        self.assertIn("no usable graphics card", self.accel.why())

    def test_the_test_runs_real_operations(self):
        import torch
        self.assertEqual(self.accel.kernel_test(torch.device("cpu")), "")

    def test_selftest_agrees_with_numpy(self):
        os.environ["TOSTEP_GPU"] = "cpu-test"
        r = self.accel.selftest()
        self.assertTrue(r["ok"], r)
        self.assertLess(r["max_difference"], 1e-6)

    def test_float32_only_path_matches(self):
        """A card without float64 (Apple MPS) runs everything in float32: same answers, within rounding."""
        import numpy as np
        import torch
        rng = np.random.default_rng(5)
        S, H = 300, 40
        sV = rng.normal(size=(S, 3, 3)); sN = rng.normal(size=(S, 3)); sN /= np.linalg.norm(sN, axis=1)[:, None]
        sC, sA, ctol = sV.mean(1), rng.uniform(0.5, 2, S), np.full(S, 0.4)
        u = rng.normal(size=(H, 3)); u /= np.linalg.norm(u, axis=1)[:, None]
        rows = {"pln": np.hstack([rng.normal(size=(H, 3)), u]),
                "cyl": np.hstack([rng.normal(size=(H, 3)), u, rng.uniform(1, 3, (H, 1)), np.ones((H, 1))]),
                "sph": np.hstack([rng.normal(size=(H, 3)), rng.uniform(1, 3, (H, 1)), np.ones((H, 1))])}
        for kind, blk in rows.items():
            ref = self.accel.numpy_scores(kind, blk, sV, sN, sC, sA, 0.8, ctol, 0.5, 0.5)
            f32 = self.accel.score_blocks(kind, blk, sV, sN, sC, sA, 0.8, ctol, 0.5, 0.5, torch.device("cpu"), torch.float32)
            f64 = self.accel.score_blocks(kind, blk, sV, sN, sC, sA, 0.8, ctol, 0.5, 0.5, torch.device("cpu"))
            self.assertTrue(np.allclose(f64, ref, atol=1e-9), kind)
            self.assertLess(np.abs(f32 - ref).max(), 0.05 * max(1.0, ref.max()), kind)

    def test_nothing_asks_the_device_for_float64(self):
        """Regression (found on an M5 MacBook, 2026-10-29): `.double()` on a device tensor fails on Apple's MPS backend,
        which has no float64. The answer must be brought back to the processor first, and inputs converted before moving."""
        import inspect
        src = inspect.getsource(self.accel.score_blocks)
        self.assertNotIn(".double().cpu()", src)
        self.assertIn(".cpu().double()", src)
        self.assertIn(").to(dev)", src)

    def test_the_card_test_runs_the_real_scoring(self):
        """Ready must mean the scoring works, not just that matrix multiplication does."""
        import torch
        calls = []
        real = self.accel.score_blocks

        def spy(kind, *a, **k):
            calls.append(kind)
            return real(kind, *a, **k)
        with mock.patch.object(self.accel, "score_blocks", spy):
            self.assertEqual(self.accel.kernel_test(torch.device("cpu")), "")
        self.assertEqual(sorted(calls), ["cyl", "pln", "sph"])
        with mock.patch.object(self.accel, "score_blocks", side_effect=RuntimeError("Cannot convert a MPS Tensor to float64 dtype")):
            self.assertIn("float64", self.accel.kernel_test(torch.device("cpu")))

    def test_v2_scoring_on_torch_equals_numpy_for_planes_cylinders_and_spheres(self):
        """The same maths, two ways: _batch_hypotheses through the card path and through plain NumPy."""
        import numpy as np
        import torch
        from tostep.plugins.mesh2step import v2
        rng = np.random.default_rng(3)
        F = 300
        th = rng.uniform(0, 2 * np.pi, F)
        z = rng.uniform(0, 20, F)
        C = np.c_[10 * np.cos(th), 10 * np.sin(th), z]                    # a cylinder of radius 10 about z
        N = np.c_[np.cos(th), np.sin(th), np.zeros(F)]
        ctx = SimpleNamespace(C=C, N=N, diag=40.0)
        pairs = [(int(a), int(b)) for a, b in rng.integers(0, F, (80, 2)) if a != b]
        S = 120
        idx = rng.choice(F, S, replace=False)
        sC, sN = C[idx], N[idx]
        sV = sC[:, None, :] + rng.normal(scale=0.2, size=(S, 3, 3))
        sA = rng.uniform(0.5, 2.0, S)

        def run(dev):
            self.accel._state.update(dev=dev, checked=True, why="")
            return v2._batch_hypotheses(ctx, pairs, sV, sN, sC, sA, 0.5, 0.9, 0.98)
        a = run(None)
        b = run(torch.device("cpu"))
        self.assertEqual(len(a), len(b))
        self.assertGreater(len(a), 80)
        self.assertGreater(max(x[0] for x in a), 0)                        # something actually scored
        for x, y in zip(a, b):
            self.assertEqual(x[1][0], y[1][0])
            self.assertAlmostEqual(x[0], y[0], places=6)


if __name__ == "__main__":
    unittest.main()
