# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""Tests for the Hardpoints and SCAD → STEP tabs and what they sit on.

    python -m unittest discover -s tests -v        (needs the app's requirements)

The .csg tests need no OpenSCAD; the .scad tests are skipped when it is not installed.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
DATA = os.path.join(ROOT, "tests", "data")
CSG = os.path.join(DATA, "cube.csg")
BRACKET = os.path.join(ROOT, "examples", "bracket.scad")
KNOB_HP = os.path.join(DATA, "knob_hardpoint.stl")
KNOB_PLAIN = os.path.join(DATA, "knob_plain.stl")
HAVE_OPENSCAD = bool(shutil.which("openscad"))


def blob(path):
    with open(path, "rb") as f:
        return f.read()


def cli(*args, **kw):
    return subprocess.run([sys.executable, os.path.join(ROOT, "run_tostep.py"), *args], capture_output=True,
                          text=True, timeout=600, **kw)


class Hardpoints(unittest.TestCase):
    def test_inspect(self):
        from tostep.plugins.mesh2step import hardpoints
        a = hardpoints.inspect(KNOB_HP)
        self.assertTrue(a["labelled"] and a["valid"])
        self.assertEqual(a["faces"], 4)
        self.assertEqual(a["kinds"], {"cylinder": 1, "torus": 1, "sphere": 1, "plane": 1})
        b = hardpoints.inspect(KNOB_PLAIN)
        self.assertFalse(b["labelled"])
        self.assertEqual(b["triangles"], a["triangles"])

    def test_edited_file_is_flagged(self):
        from tostep.plugins.mesh2step import hardpoints
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "edited.stl")
            data = bytearray(blob(KNOB_HP))
            data[84 + 8] ^= 0x40                           # change one coordinate byte
            with open(p, "wb") as f:
                f.write(data)
            r = hardpoints.inspect(p)
        self.assertTrue(r["labelled"])
        self.assertFalse(r["valid"])

    def test_labels_give_fewer_faces_than_guessing(self):
        with tempfile.TemporaryDirectory() as d:
            a, b = os.path.join(d, "a.step"), os.path.join(d, "b.step")
            ra = cli(KNOB_HP, a)
            rb = cli(KNOB_HP, b, "--no-labels")
            self.assertEqual(ra.returncode, 0, ra.stdout + ra.stderr)
            self.assertEqual(rb.returncode, 0, rb.stdout + rb.stderr)
            fa = [l for l in ra.stdout.splitlines() if l.startswith("Faces in STEP")][0]
            fb = [l for l in rb.stdout.splitlines() if l.startswith("Faces in STEP")][0]
            n = lambda s: int(s.split(":")[1].split()[0])      # noqa: E731
            self.assertLess(n(fa), n(fb))
            self.assertIn("taken from the file's labels", ra.stdout)

    def test_save_labelled_round_trip(self):
        from tostep.core import hstl
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "k.step")
            r = cli(KNOB_PLAIN, out, "--save-labelled")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            lab = os.path.join(d, "k_hardpoints.stl")
            self.assertTrue(hstl.is_hardpoint_stl(lab))
            # same triangles, byte for byte, as the plain file
            a, b = blob(KNOB_PLAIN), blob(lab)
            self.assertEqual(len(a), len(b))
            self.assertEqual(hstl.geometry_crc(a[84:], (len(a) - 84) // 50), hstl.geometry_crc(b[84:], (len(b) - 84) // 50))


class Scad(unittest.TestCase):
    def test_csg_cli_and_machine_protocol(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "cube.step")
            r = cli("scad", CSG, "-o", out, "--no-verify", "--machine")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("@@DONE " + out, r.stdout)
            self.assertIn("@@PROGRESS", r.stdout)
            self.assertGreater(os.path.getsize(out), 1000)

    def test_missing_file_fails_cleanly(self):
        r = cli("scad", os.path.join(DATA, "nope.scad"), "--machine")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("@@ERROR", r.stdout)

    @unittest.skipUnless(HAVE_OPENSCAD, "OpenSCAD not installed")
    def test_scad_with_define(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "b.step")
            r = cli("scad", BRACKET, "-o", out, "-D", "nut_trap=false")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("Closed solid: yes", r.stdout)

    def test_defines_only_for_changed_values(self):
        from tostep.plugins.scad2step import bridge as scad_bridge
        params = [{"name": "w", "value": "30", "kind": "number"}, {"name": "t", "value": '"Hi"', "kind": "string"},
                  {"name": "b", "value": "true", "kind": "bool"}]
        out = scad_bridge.defines_from({"w": "30", "t": "Yo", "b": "false", "zzz": "1"}, params)
        self.assertEqual(out, {"t": '"Yo"', "b": "false"})
        self.assertEqual(scad_bridge.defines_from({"w": "abc"}, params), {})

    def test_plugin_bridge_runs_scad_job(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "tostep_bridge", os.path.join(ROOT, "tostep", "frontends", "cad_shared", "bridge.py"))
        bridge = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bridge)

        class Local(bridge.Job):
            def command(self):
                return [sys.executable, os.path.join(ROOT, "run_tostep.py")] + list(self.args)
        with tempfile.TemporaryDirectory() as d:
            dst = os.path.join(d, "c.step")
            res = {}
            Local(d, bridge.scad_args(CSG, dst, verify=False), on_finish=lambda ok, r: res.update(ok=ok, r=r)).run_blocking()
            self.assertTrue(res.get("ok"), res)
            self.assertEqual(res["r"], dst)


class Server(unittest.TestCase):
    """The tabs' HTTP endpoints, with a real server and real worker processes."""

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

    def call(self, path, body=None, raw=None):
        req = urllib.request.Request(self.url + path, data=raw if raw is not None else (None if body is None else json.dumps(body).encode()),
                                     headers={"X-Token": self.app.token, "Content-Type": "application/json"},
                                     method="GET" if body is None and raw is None else "POST")
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def wait(self, jid):
        for _ in range(900):
            code, j = self.call("/api/job/" + jid)
            if j["state"] in ("done", "error", "cancelled"):
                return j
            time.sleep(0.2)
        self.fail("job did not finish")

    def test_pages_have_tabs(self):
        req = urllib.request.Request(self.url + "/")
        html = urllib.request.urlopen(req).read().decode()
        for t in ("p-hp", "p-scad", "Hardpoints", "SCAD → STEP"):
            self.assertIn(t, html)
        self.assertNotIn("__VERSION__", html)

    def test_hardpoint_inspect_and_convert(self):
        code, up = self.call("/api/upload?name=knob.stl", raw=blob(KNOB_HP))
        self.assertEqual(code, 200)
        code, info = self.call("/api/hp/inspect", {"file": up["id"]})
        self.assertTrue(info["valid"])
        code, r = self.call("/api/run", {"kind": "convert", "file": up["id"], "params": {"use_labels": True}})
        j = self.wait(r["job"])
        self.assertEqual(j["state"], "done", j.get("error"))
        self.assertIn("Closed solid: yes", j["summary"])

    def test_make_hardpoint_stl(self):
        code, up = self.call("/api/upload?name=plain.stl", raw=blob(KNOB_PLAIN))
        code, r = self.call("/api/run", {"kind": "convert", "file": up["id"], "params": {"make_labelled": True}})
        j = self.wait(r["job"])
        self.assertEqual(j["state"], "done", j.get("error"))
        self.assertTrue(j["labelled_saved"] and os.path.exists(j["labelled_saved"]))
        from tostep.core import hstl
        self.assertTrue(hstl.is_hardpoint_stl(j["labelled_saved"]))

    def test_scad_dropped_file_converts_to_output_folder(self):
        code, up = self.call("/api/scad/upload?name=cube.csg", raw=blob(CSG))
        self.assertEqual(code, 200, up)
        self.assertFalse(up["local"])
        code, r = self.call("/api/run", {"kind": "scad", "file": up["id"], "params": {"verify": False}})
        j = self.wait(r["job"])
        self.assertEqual(j["state"], "done", j.get("error"))
        self.assertTrue(j["saved_to"].startswith(os.path.join(self.tmp, "out")))
        self.assertEqual(j["kind"], "scad")

    def test_scad_wrong_file_type_is_refused(self):
        code, up = self.call("/api/scad/upload?name=part.stl", raw=b"x")
        self.assertEqual(code, 400)

    @unittest.skipUnless(HAVE_OPENSCAD, "OpenSCAD not installed")
    def test_scad_local_file_customizer_and_beside(self):
        d = os.path.join(self.tmp, "model")
        os.makedirs(d)
        scad = os.path.join(d, "bracket.scad")
        shutil.copy(BRACKET, scad)
        code, info = self.call("/api/scad/open-path", {"path": scad})
        self.assertEqual(code, 200, info)
        self.assertTrue(info["local"] and info["params"])
        code, r = self.call("/api/run", {"kind": "scad", "file": info["id"],
                                         "params": {"where": "beside", "verify": True, "values": {"width": "40"}}})
        j = self.wait(r["job"])
        self.assertEqual(j["state"], "done", j.get("error"))
        self.assertEqual(j["saved_to"], os.path.join(d, "bracket.step"))
        self.assertIn("Closed solid: yes", j["summary"])

    def test_scad_status_and_bad_path(self):
        code, st = self.call("/api/scad/status")
        self.assertEqual(code, 200)
        self.assertIn("openscad", st)
        code, e = self.call("/api/scad/open-path", {"path": "/no/such/file.scad"})
        self.assertEqual(code, 400)

    def test_preview_of_scad_job_has_no_original(self):
        code, up = self.call("/api/scad/upload?name=cube2.csg", raw=blob(CSG))
        code, r = self.call("/api/run", {"kind": "scad", "file": up["id"], "params": {"verify": False}})
        self.wait(r["job"])
        code, e = self.call(f"/api/preview3d/{r['job']}?which=mesh")
        self.assertEqual(code, 400)
        code, p = self.call(f"/api/preview3d/{r['job']}?which=result")
        self.assertEqual(code, 200)


class FileManager(unittest.TestCase):
    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux entries")
    def test_install_and_remove_right_click_entries(self):
        with tempfile.TemporaryDirectory() as home:
            env = dict(os.environ, HOME=home, XDG_DATA_HOME=os.path.join(home, ".local", "share"))
            r = cli("install", "files", env=env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            nemo = os.path.join(home, ".local", "share", "nemo", "actions")
            self.assertEqual(len(os.listdir(nemo)), 3)
            body = blob(os.path.join(nemo, "tostep-convert-scad.nemo_action")).decode()
            self.assertIn("scad %F --pause", body)
            r = cli("install", "files", "--remove", env=env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(os.listdir(nemo), [])


if __name__ == "__main__":
    unittest.main()
