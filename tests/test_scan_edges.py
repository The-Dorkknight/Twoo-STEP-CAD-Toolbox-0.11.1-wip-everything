# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""0.11.4: edge-keeping smoothing for scans (edgesmooth.py), the scan flag and its route rules, and the
prepared mesh kept for the second engine.

    python -m unittest tests.test_scan_edges -v
"""
import os
import sys
import tempfile
import types
import unittest

import numpy as np
import trimesh

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import json  # noqa: E402
import shutil  # noqa: E402
import threading  # noqa: E402
import urllib.request  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402

from tostep.plugins.mesh2step import classic, edgesmooth, ladder, scandetect  # noqa: E402


def noisy(m, sigma, seed=1):
    m = m.copy()
    m.vertices = m.vertices + np.random.default_rng(seed).normal(0, sigma, m.vertices.shape)
    return m


def dense_box(n=40):
    """A 20 mm box made of many small triangles (a scan-like density)."""
    b = trimesh.creation.box((20, 20, 20))
    for _ in range(4):
        v, f = trimesh.remesh.subdivide(b.vertices, b.faces)
        b = trimesh.Trimesh(v, f, process=True)
    return b


class EdgeSmooth(unittest.TestCase):
    def test_noise_goes_but_the_box_keeps_its_edges(self):
        box = dense_box()
        nz = noisy(box, 0.05)
        V = edgesmooth.smooth(nz.vertices, nz.faces)
        out = trimesh.Trimesh(V, nz.faces, process=False)
        ang = np.degrees(out.face_adjacency_angles)
        # the box's 12 edges stay sharp: about as many edges above 60 degrees as the clean box has
        clean_sharp = int((np.degrees(box.face_adjacency_angles) > 60).sum())
        self.assertGreater(int((ang > 60).sum()), 0.8 * clean_sharp)
        # and the faces are flat again: most neighbours now within 2 degrees
        flat = ang[ang < 30]
        before = float(np.median(np.degrees(nz.face_adjacency_angles)))
        self.assertLess(float(np.median(flat)), min(2.0, 0.5 * before))
        # closer to the true box than the noisy mesh was
        d_noisy = np.abs(trimesh.proximity.signed_distance(box, nz.vertices[:2000]))
        d_out = np.abs(trimesh.proximity.signed_distance(box, V[:2000]))
        self.assertLess(float(d_out.mean()), 0.7 * float(d_noisy.mean()))

    def test_same_triangles_still_closed_nothing_turned_over(self):
        s = noisy(trimesh.creation.icosphere(4, radius=10), 0.05)
        V = edgesmooth.smooth(s.vertices, s.faces)
        out = trimesh.Trimesh(V, s.faces, process=False)
        self.assertTrue(out.is_watertight)
        n0 = s.face_normals; n1 = out.face_normals
        self.assertTrue(bool(np.all(np.einsum("ij,ij->i", n0, n1) >= 0)))

    def test_a_clean_mesh_hardly_moves(self):
        box = dense_box()
        V = edgesmooth.smooth(box.vertices, box.faces)
        self.assertLess(float(np.abs(V - box.vertices).max()), 1e-6)


class ScanRules(unittest.TestCase):
    def prm(self, scan):
        p = classic.Params()
        p.ladder = True
        p.scan = scan
        return p

    def fake_choose(self, scan, lathe=0.9):
        p = self.prm(scan)
        sig = {"flat_sharp": 0.0, "perp": 0.0, "lathe_curved": lathe, "sharp30_len": 0.05}
        old = ladder.signals, ladder.crease_from_histogram
        ladder.signals = lambda ctx: sig
        ladder.crease_from_histogram = lambda ctx: 12.0
        try:
            ctx = types.SimpleNamespace(nF=150000)
            r, _ = ladder.choose(ctx, p, lambda *a: None)
        finally:
            ladder.signals, ladder.crease_from_histogram = old
        return r, p

    def test_a_turned_scan_is_guesstimated_and_keeps_the_crease(self):
        r, p = self.fake_choose(scan=True)
        self.assertEqual(r, "lathe")
        self.assertGreater(p.organic_tol, 0)                 # problem areas guesstimated, as on other scans
        self.assertEqual(p.crease_angle, classic.Params.crease_angle)   # no histogram crease on a scan

    def test_a_turned_cad_part_is_unchanged(self):
        r, p = self.fake_choose(scan=False)
        self.assertEqual(r, "lathe")
        self.assertEqual(p.organic_tol, 0.0)
        self.assertEqual(p.crease_angle, 12.0)

    def test_scan_prep_flags_a_noisy_scan_and_smooths_it(self):
        s = noisy(trimesh.creation.icosphere(7, radius=40), 0.06)        # 327,680 triangles
        s = trimesh.util.concatenate([s, trimesh.creation.icosphere(1, radius=0.3).apply_translation((90, 0, 0))])
        lines = []
        p = classic.Params()
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "scan.stl")
            s.export(path)
            m = classic.load_mesh(path, p, lines.append)
        self.assertTrue(m.metadata.get("scan_noisy"))
        self.assertTrue(any("keeping real edges" in ln for ln in lines))
        self.assertTrue(m.is_watertight)


class Detect(unittest.TestCase):
    """The scan call: dense and randomly tilted. Measured on clean and scanned meshes in docs/research/SCAN_EDGES.md."""

    def test_clean_dense_meshes_are_not_scans(self):
        self.assertFalse(scandetect.assess(dense_box())["likely"])                        # 24,576 triangles: too few anyway
        big = trimesh.creation.icosphere(7, radius=30)                                    # 327,680 clean triangles
        a = scandetect.assess(big)
        self.assertFalse(a["likely"], a)
        self.assertLess(a["jitter"], 0.5)
        cyl = trimesh.creation.cylinder(10, 40, sections=400)
        v, f = cyl.vertices, cyl.faces
        for _ in range(3):
            v, f = trimesh.remesh.subdivide(v, f)
        self.assertFalse(scandetect.assess(trimesh.Trimesh(v, f, process=True))["likely"])

    def test_a_noisy_dense_mesh_is_a_scan(self):
        a = scandetect.assess(noisy(trimesh.creation.icosphere(7, radius=30), 0.04))
        self.assertTrue(a["likely"], a)
        self.assertGreater(a["jitter"], 2.0)
        self.assertIn("tilted", a["why"])

    def test_a_dense_mesh_the_cleanup_had_to_fix_is_a_scan_even_when_smooth(self):
        a = scandetect.assess(trimesh.creation.icosphere(7, radius=30), scan_cleaned=True)
        self.assertTrue(a["likely"])
        self.assertIn("clean-up", a["why"])

    def test_scan_mode_overrides_the_verdict(self):
        s = noisy(trimesh.creation.icosphere(7, radius=40), 0.06)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "s.stl")
            s.export(path)
            lines = []
            p = classic.Params(); p.scan_mode = "cad"
            m = classic.load_mesh(path, p, lines.append)
            self.assertFalse(m.metadata.get("scan_noisy"))
            self.assertFalse(any("keeping real edges" in ln or "Denoised" in ln for ln in lines))
            lines = []
            p = classic.Params(); p.scan_mode = "scan"
            m = classic.load_mesh(path, p, lines.append)
            self.assertTrue(m.metadata.get("scan_noisy"))
            lines = []
            m = classic.load_mesh(path, classic.Params(), lines.append)                   # auto
            self.assertTrue(m.metadata.get("scan_noisy"))
            self.assertTrue(any("Looks like a 3D scan" in ln for ln in lines))

    def test_a_clean_dense_mesh_comes_through_untouched_in_auto(self):
        big = trimesh.creation.icosphere(7, radius=30)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "clean.stl")
            big.export(path)
            lines = []
            m = classic.load_mesh(path, classic.Params(), lines.append)
        self.assertFalse(m.metadata.get("scan_noisy"))
        self.assertFalse(any("Denoised" in ln or "keeping real edges" in ln for ln in lines))

    def test_pinned_corners_stay_put(self):
        box = dense_box()
        V = edgesmooth.smooth(noisy(box, 0.02).vertices, box.faces)
        corners = np.array([[sx * 10, sy * 10, sz * 10] for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)], float)
        nearest = np.array([np.abs(np.linalg.norm(box.vertices - c, axis=1)).argmin() for c in corners])
        # a smoothed (noisy) box keeps its corners within the noise it started with, not rounded off
        self.assertLess(float(np.linalg.norm(V[nearest] - corners, axis=1).max()), 0.2)


class ScanCheckRoute(unittest.TestCase):
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
        cls.httpd.shutdown(); cls.httpd.server_close(); shutil.rmtree(cls.tmp, ignore_errors=True)

    def call(self, path, body=None, raw=None):
        req = urllib.request.Request(self.url + path, data=raw if raw is not None else json.dumps(body).encode(),
                                     headers={"X-Token": self.app.token, "Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.loads(r.read() or b"{}")

    def upload(self, mesh, name):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, name); mesh.export(p)
            with open(p, "rb") as f:
                return self.call("/api/upload?name=" + name, raw=f.read())["id"]

    def test_small_and_clean_files_are_not_asked_about(self):
        self.assertFalse(self.call("/api/scancheck", {"file": self.upload(trimesh.creation.icosphere(3), "small.stl")})["likely"])
        self.assertFalse(self.call("/api/scancheck", {"file": self.upload(trimesh.creation.icosphere(7, radius=30), "clean.stl")})["likely"])
        self.assertFalse(self.call("/api/scancheck", {"file": "nope"})["likely"])

    def test_upload_lands_in_the_files_folder_once(self):
        m = trimesh.creation.icosphere(3)
        a = self.upload(m, "same.stl")
        b = self.upload(m, "same.stl")                       # byte for byte the same: no second copy
        pa, pb = self.app.files[a]["path"], self.app.files[b]["path"]
        self.assertEqual(pa, pb)
        self.assertEqual(os.path.dirname(pa), os.path.join(self.app.out, "same"))     # a folder per project
        c = self.upload(trimesh.creation.icosphere(2), "same.stl")   # same name, different file: its own folder
        self.assertEqual(os.path.dirname(self.app.files[c]["path"]), os.path.join(self.app.out, "same (2)"))
        self.assertEqual(os.listdir(self.app.work), [])      # nothing left behind in the scratch folder

    def test_a_scan_is_asked_about(self):
        r = self.call("/api/scancheck", {"file": self.upload(noisy(trimesh.creation.icosphere(7, radius=30), 0.04), "scan.stl")})
        self.assertTrue(r["likely"], r)
        self.assertIn("tilted", r["why"])
        page = urllib.request.urlopen(self.url + "/?t=" + self.app.token, timeout=30).read().decode()
        for bit in ('id="scanq"', "scanAnswer(", "/api/scancheck", "Looking at the mesh"):
            self.assertIn(bit, page)


class LoadedOnce(unittest.TestCase):
    def test_second_load_of_the_same_file_reuses_the_prepared_mesh(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "part.stl")
            trimesh.creation.icosphere(3).export(path)
            a, b = [], []
            m1 = classic.load_mesh(path, classic.Params(), a.append)
            m2 = classic.load_mesh(path, classic.Params(), b.append)
            self.assertTrue(any("Loaded" in ln for ln in a))
            self.assertTrue(any("prepared a moment ago" in ln for ln in b))
            self.assertEqual(len(m1.faces), len(m2.faces))
            self.assertIsNot(m1, m2)
            m2.vertices[0] += 5.0                         # a copy: changing it doesn't change the kept one
            m3 = classic.load_mesh(path, classic.Params(), b.append)
            self.assertLess(float(np.abs(m3.vertices[0] - m1.vertices[0]).max()), 1e-9)


if __name__ == "__main__":
    unittest.main()


class JobLogSaved(unittest.TestCase):
    """0.11.4: every job's whole log is written beside its result (the page's console only keeps the last lines)."""

    def test_log_file_written(self):
        import tempfile
        from tostep.frontends.web import server
        d = tempfile.mkdtemp()
        job = {"id": "abc", "plugin": "mesh2step", "state": "done", "dest_dir": d, "log": ["one", "two"]}
        server.App._save_log(types.SimpleNamespace(out=d), job, "part.step")
        txt = open(os.path.join(d, "part.log.txt"), encoding="utf-8").read()
        self.assertIn("one\ntwo", txt)
        self.assertEqual(job["log_saved"], os.path.join(d, "part.log.txt"))
