# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""The EXPERIMENTAL Ladder engine: routing signals, route choice, lathe-first claim, caps."""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import numpy as np  # noqa: E402
import trimesh  # noqa: E402

from tostep.plugins.mesh2step import engines  # noqa: E402
from tostep.plugins.mesh2step import ladder  # noqa: E402
from tostep.plugins.mesh2step import classic as core  # noqa: E402

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
KNOB = os.path.join(DATA, "knob_plain.stl")


def bracket():
    """A plate with a round hole: flats bounded by sharp edges, walls square to z."""
    m = trimesh.creation.box(extents=(40, 24, 6))
    hole = trimesh.creation.cylinder(radius=4.0, height=10.0, sections=48)
    try:
        d = m.difference(hole)
        if d is not None and len(d.faces) and d.is_watertight:
            return d
    except BaseException:
        pass
    return m


def blob():
    m = trimesh.creation.icosphere(subdivisions=4, radius=15.0)
    v = m.vertices
    rng = np.random.default_rng(3)
    d = np.zeros(len(v))
    for _ in range(6):
        w = rng.normal(size=3); w /= np.linalg.norm(w)
        d += rng.uniform(1, 3) * np.sin(rng.uniform(0.08, 0.25) * (v @ w) + rng.uniform(0, 6.28))
    return trimesh.Trimesh(v * (1 + 0.12 * d)[:, None], m.faces, process=True)


def vase():
    """A closed turned part: a wavy profile revolved, with flat ends (the profile runs
    from the axis out and back, so trimesh closes it)."""
    z = np.linspace(0, 40, 30)
    r = 12 + 4 * np.sin(z / 8.0)
    pts = np.vstack([[0.0, 0.0], np.c_[r, z], [0.0, 40.0]])
    m = trimesh.creation.revolve(pts, sections=72)
    m.merge_vertices(); m.fix_normals()
    return m


class Signals(unittest.TestCase):
    def test_routes(self):
        for mesh, want in ((bracket(), "prismatic"), (blob(), "organic"), (vase(), "lathe"),
                           (trimesh.load(KNOB, force="mesh"), "lathe")):
            ctx = core.MeshCtx(mesh)
            sig = ladder.signals(ctx)
            r, why = ladder.route(sig)
            self.assertEqual(r, want, (why, {k: v for k, v in sig.items() if "axis" not in k}))
            self.assertTrue(why)

    def test_crease_valley_sits_above_the_exporter_step(self):
        ctx = core.MeshCtx(trimesh.load(KNOB, force="mesh"))     # 48-gon body: 7.5° facet steps
        c = ladder.crease_from_histogram(ctx)
        self.assertIsNotNone(c)
        self.assertGreater(c, 7.5)
        self.assertLess(c, 30.0)

    def test_choose_sets_the_route_up(self):
        prm = core.Params(ladder=True)
        r, _ = ladder.choose(core.MeshCtx(blob()), prm, lambda s: None)
        self.assertEqual(r, "organic")
        self.assertTrue(prm.skip_planes)
        self.assertFalse(prm.fit_primitives)
        self.assertGreater(prm.organic_tol, 0)
        prm = core.Params(ladder=True, ladder_guess=False)
        ladder.choose(core.MeshCtx(blob()), prm, lambda s: None)
        self.assertEqual(prm.organic_tol, 0.0)
        prm = core.Params(ladder=True, route="mixed")
        r, _ = ladder.choose(core.MeshCtx(blob()), prm, lambda s: None)
        self.assertEqual(r, "mixed")
        self.assertFalse(prm.skip_planes)


    def test_prismatic_route_claims_extruded_outlines_first(self):
        """0.10.0: prismatic parts rough first (Staged) unless told not to."""
        prm = core.Params(ladder=True)
        r, _ = ladder.choose(core.MeshCtx(bracket()), prm, lambda s: None)
        self.assertEqual(r, "prismatic")
        self.assertTrue(prm.rough)
        prm = core.Params(ladder=True, ladder_rough=False)
        ladder.choose(core.MeshCtx(bracket()), prm, lambda s: None)
        self.assertFalse(prm.rough)

    def test_mixed_route_roughs_only_with_a_lathe_share_and_keeps_released_flat_rules(self):
        real = ladder.signals
        base = dict(zero_len=0.2, sharp30_len=0.1, flat_sharp=0.1, n_flats=3, perp=0.2,
                    perp_axis=np.array([0, 0, 1.0]), lathe_axis=np.array([0, 0, 1.0]))
        try:
            for share, want in ((0.73, True), (0.30, False)):
                ladder.signals = lambda ctx, share=share: dict(base, lathe_curved=share)
                prm = core.Params(ladder=True)
                r, _ = ladder.choose(core.MeshCtx(blob()), prm, lambda s: None)
                self.assertEqual(r, "mixed")
                self.assertEqual(prm.rough, want)
                self.assertEqual(prm.strip_rule, "classic")      # the CAD-only flat tweaks stay off
                self.assertEqual(prm.crease_angle, core.Params.crease_angle)
        finally:
            ladder.signals = real
        prm = core.Params(ladder=True)
        ladder.choose(core.MeshCtx(bracket()), prm, lambda s: None)
        self.assertEqual(prm.strip_rule, "sag")

    def test_ceiling_lookup_is_light(self):
        from tostep.core import perf
        had = "torch" in sys.modules
        budget, fills = perf.auto_limits()
        self.assertGreater(budget, 0)
        self.assertGreater(fills, 0)
        self.assertEqual("torch" in sys.modules, had)       # no graphics-card probing


class Convert(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.addCleanup(self.d.cleanup)

    def run_one(self, mesh, **kw):
        src = os.path.join(self.d.name, "in.stl"); out = os.path.join(self.d.name, "out.step")
        mesh.export(src)
        log = []
        rep = engines.convert(src, out, dict(core.Params().__dict__, **kw), "ladder", log=log.append)
        return rep, "\n".join(log)

    def test_prismatic_part_is_exact_and_quick(self):
        rep, log = self.run_one(bracket())
        self.assertIn("Route: prismatic", log)
        self.assertTrue(rep.is_solid and rep.is_valid)
        self.assertEqual(rep.route, "prismatic")
        self.assertEqual(rep.n_nurbs, 0)
        self.assertLess(rep.max_dev, 0.05)

    def test_rounded_extrusion_is_roughed_and_the_plain_fitter_is_the_fallback(self):
        from shapely.geometry import box as sbox
        m = trimesh.creation.extrude_polygon(sbox(-20, -12, 20, 12).buffer(5, join_style=1, resolution=24), 8.0)
        m.merge_vertices(); m.fix_normals()
        rep, log = self.run_one(m)
        self.assertIn("extruded outlines are claimed first (roughing)", log)
        self.assertTrue(rep.is_solid and rep.is_valid)
        self.assertLessEqual(rep.n_faces, 12)                      # 2 flats + 4 walls + 4 rounds
        self.assertLess(rep.max_dev, rep.tol)
        rep2, log2 = self.run_one(m, ladder_rough=False)
        self.assertNotIn("roughing", log2)
        self.assertTrue(rep2.is_solid and rep2.is_valid)

    def test_lathe_part_claims_revolves_before_the_flat_search(self):
        rep, log = self.run_one(vase())
        self.assertIn("Route: lathe", log)
        self.assertIn("claimed first", log)
        self.assertTrue(rep.is_solid and rep.is_valid)
        self.assertLessEqual(rep.n_faces, 12)

    def test_organic_part_skips_dead_end_searches_and_guesstimates(self):
        rep, log = self.run_one(blob())
        self.assertIn("Route: organic", log)
        self.assertIn("Flat-face search skipped", log)
        self.assertIn("guesstimated", log)
        self.assertNotIn("cylinder/sphere surface(s) recognised", log)
        self.assertTrue(rep.is_solid and rep.is_valid)

    def test_budget_and_fill_cap_are_honoured(self):
        # a budget that is already spent when fitting starts: the loop must still finish
        # with a closed solid, demoting what is outside tolerance instead of refining
        rep, log = self.run_one(blob(), route="mixed", ladder_guess=False, budget_s=0.001, max_fills=1)
        self.assertIn("time budget", log)
        self.assertTrue(rep.is_solid)

    def test_checkpoint_is_written_before_the_slow_part_and_survives_a_crash(self):
        src = os.path.join(self.d.name, "in.stl"); out = os.path.join(self.d.name, "out.step")
        blob().export(src)
        log = []
        rep = engines.convert(src, out, dict(core.Params().__dict__, checkpoint="on"), "classic", log=log.append)
        self.assertIn("Checkpoint written", "\n".join(log))
        self.assertTrue(rep.is_solid and rep.is_valid)
        # now make the freeform fitter die after the checkpoint: the file written earlier must be kept
        real = core.Builder.nurbs_face

        def boom(self, r, info):
            raise MemoryError("simulated")
        core.Builder.nurbs_face = boom
        try:
            log = []
            rep = engines.convert(src, out, dict(core.Params().__dict__, checkpoint="on"), "classic", log=log.append)
        finally:
            core.Builder.nurbs_face = real
        text = "\n".join(log)
        self.assertIn("The surface fitter stopped (MemoryError", text)
        self.assertIn("keeping the checkpoint", text)
        self.assertTrue(rep.ok and rep.is_solid)
        self.assertTrue(os.path.exists(out))

    def test_profile_budget_reaches_the_converter(self):
        os.environ["TOSTEP_BUDGET"] = "0.001"
        try:
            rep, log = self.run_one(blob(), route="mixed", ladder_guess=False)
        finally:
            del os.environ["TOSTEP_BUDGET"]
        self.assertIn("time budget", log)
        self.assertTrue(rep.is_solid)

    def test_classic_is_untouched(self):
        src = os.path.join(self.d.name, "in.stl"); out = os.path.join(self.d.name, "out.step")
        bracket().export(src)
        log = []
        rep = engines.convert(src, out, dict(core.Params().__dict__), "classic", log=log.append)
        self.assertNotIn("Route:", "\n".join(log))
        self.assertEqual(rep.route, "")
        self.assertTrue(rep.is_solid and rep.is_valid)


if __name__ == "__main__":
    unittest.main()
