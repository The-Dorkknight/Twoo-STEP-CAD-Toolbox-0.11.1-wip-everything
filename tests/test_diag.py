# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""The diagnostic code: a job's settings and result in one short string, shown in the preview
window as a QR beside the same facts in words (app/diagcode.py).

    python -m unittest discover -s tests -v

The QR picture itself needs the `segno` package; those checks are skipped without it.
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
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tostep.core import diagcode  # noqa: E402
from tostep.plugins.mesh2step import classic as core  # noqa: E402

KNOB = os.path.join(ROOT, "tests", "data", "knob_plain.stl")
try:
    import segno  # noqa: F401
    HAVE_QR = True
except ImportError:
    HAVE_QR = False

PARAMS = dict(units="mm", mode="surface", tol="0.02", engine="ladder", plane_angle="3", crease_angle="30",
              patch_size="30", max_bend="60", primitives=True, memory_guard=True, guess=True, organic_tol="0.25",
              decimate="20000", smooth="0")
PROFILE = dict(name="moderate", threads=4, gpu=False, budget_s=2400, max_fills=60)
RESULT = dict(solid=True, valid=True, route="lathe", tris=2958.0, faces=944.0, planes=45.0, round=1.0, nurbs=7.0,
              faceted=1.0, max_dev=0.2407, mean_dev=0.0071, dvol=0.121, seconds=8.6)


def cli(*args):
    return subprocess.run([sys.executable, os.path.join(ROOT, "run_tostep.py"), *args], capture_output=True, text=True,
                          timeout=600)


class Code(unittest.TestCase):
    def build(self, **kw):
        a = dict(kind="convert", params=PARAMS, state="done", result=RESULT, log=["Checkpoint written: x.step"],
                 version="0.5.2-wip", job_id="5f3a9c001122", profile=PROFILE, when=diagcode.EPOCH + 60 * 403000,
                 platform="linux")
        a.update(kw)
        return diagcode.build(**a)

    def test_settings_and_result_come_back(self):
        code = self.build()
        d = diagcode.decode(code)
        self.assertIs(d["ok"], True)
        h, c, r = d["records"]["H"], d["records"]["C"], d["records"]["R"]
        self.assertEqual((h["major"], h["minor"], h["patch"], h["wip"]), (0, 5, 2, True))
        self.assertEqual((h["kind"], h["os"], h["state"], h["time"]), ("convert", "linux", "done", 403000))
        self.assertEqual(h["tag"], int("5f3a", 16) & 0x7FFF)
        self.assertEqual((c["mode"], c["engine"], c["units"], c["profile"]), ("surface", "ladder", "mm", "moderate"))
        self.assertTrue(c["guess"] and c["primitives"] and c["memory_guard"] and not c["gpu"])
        for key, want in (("tol", 0.02), ("organic_tol", 0.25), ("plane_angle", 3.0), ("crease_angle", 30.0),
                          ("patch_size", 30.0), ("max_bend", 60.0), ("decimate_k", 20.0), ("threads", 4.0),
                          ("budget_min", 40.0), ("max_fills", 60.0)):
            self.assertAlmostEqual(c[key], want, places=6, msg=key)
        self.assertTrue(r["solid"] and r["valid"] and r["checkpoint"] and not r["budget"])
        self.assertEqual(r["route"], "lathe")
        for key, want, step in (("tris", 2958, 1), ("faces", 944, 1), ("planes", 45, 1), ("nurbs", 7, 1),
                                ("max_dev", 0.2407, 0.001), ("mean_dev", 0.0071, 0.0001), ("dvol", 0.121, 0.001),
                                ("seconds", 8.6, 1)):
            self.assertLessEqual(abs(r[key] - want), step / 2 + 1e-9, key)

    def test_it_is_short_and_made_of_qr_alphanumerics(self):
        code = self.build()
        self.assertRegex(code, r"^S2S1(-[A-Z][0-9A-HJKMNP-TV-Z]*)+$")      # Crockford base32: no I, L, O, U
        self.assertLessEqual(len(code), 90)                    # fits a 33 x 33 QR at medium error correction
        loft = self.build(params=dict(PARAMS, mode="loft", axis="y", max_slices="9", sensitivity="0.05"))
        self.assertLessEqual(len(loft), 122)
        x = diagcode.decode(loft)["records"]["X"]
        self.assertEqual((x["axis"], x["max_slices"]), ("y", 9.0))
        self.assertAlmostEqual(x["sensitivity"], 0.05)

    def test_in_plain_words(self):
        text = "\n".join(diagcode.text(self.build()))
        for words in ("Twoo STEP CAD Toolbox 0.5.2-wip", "UTC", "Linux", "Mode: Smooth surfaces",
                      "Surface fitter: Ladder (experimental)", "tolerance 0.020 mm", "guesstimate ON",
                      "organic areas within 0.25 mm", "Moderate profile", "4 thread(s)", "time budget 40 min",
                      "closed solid: yes", "geometry check: valid", "route taken: lathe", "faces 944",
                      "deviation max 0.241 mm", "volume +0.121 %", "checkpoint written"):
            self.assertIn(words, text)
        plain = "\n".join(diagcode.text(self.build(params=dict(PARAMS, engine="classic", guess=False, tol="0"),
                                                   log=[])))
        self.assertIn("Surface fitter: Classic", plain)
        self.assertIn("tolerance auto", plain)
        self.assertIn("guesstimate off", plain)
        self.assertIn("Limits hit: none", plain)

    def test_a_misread_is_noticed_and_look_alike_letters_are_forgiven(self):
        code = self.build()
        typed = code.lower().replace("0", "o", 3).replace("1", "l", 1)
        self.assertEqual(diagcode.decode(typed)["records"], diagcode.decode(code)["records"])
        self.assertIs(diagcode.decode("  " + typed + "\n")["ok"], True)
        i = code.index("-C") + 4
        wrong = code[:i] + ("7" if code[i] != "7" else "8") + code[i + 1:]
        self.assertIs(diagcode.decode(wrong)["ok"], False)
        self.assertIn("CHECK FAILED", "\n".join(diagcode.text(wrong)))
        cut = code.rsplit("-K", 1)[0]
        self.assertIsNone(diagcode.decode(cut)["ok"])
        for junk in ("hello", "", "EC1-0C1XYBC4-600M", "S2S1"):
            with self.assertRaises(ValueError):
                diagcode.decode(junk)

    def test_later_versions_may_add_records(self):
        body = self.build().rsplit("-K", 1)[0] + "-Z0123"
        code = body + "-K" + diagcode._check(body)
        d = diagcode.decode(code)
        self.assertIs(d["ok"], True)
        self.assertEqual(d["unknown"], ["Z"])
        self.assertIn("R", d["records"])
        self.assertIn("does not know: Z", "\n".join(diagcode.text(code)))

    def test_what_was_not_measured_stays_unknown(self):
        res = dict(RESULT, max_dev=float("nan"), mean_dev=None, dvol=None, planes=None, round=None, nurbs=None,
                   faceted=None)
        r = diagcode.decode(self.build(result=res))["records"]["R"]
        self.assertIsNone(r["max_dev"])
        self.assertIsNone(r["mean_dev"])
        self.assertIsNone(r["dvol"])
        self.assertIsNone(r["planes"])
        self.assertNotIn("deviation", "\n".join(diagcode.text(self.build(result=res))))
        big = diagcode.decode(self.build(result=dict(RESULT, tris=5e9, dvol=-99.0, seconds=1e9)))["records"]["R"]
        self.assertGreater(big["tris"], 1.6e7)                 # numbers too big for their field stop at its top
        self.assertLess(big["dvol"], -30.0)

    def test_failures(self):
        code = self.build(state="error", result=None, error="No triangles found in the file")
        d = diagcode.decode(code)
        self.assertNotIn("R", d["records"])
        self.assertEqual(d["records"]["E"]["error"], "no_triangles")
        self.assertEqual(d["records"]["H"]["state"], "error")
        self.assertIn("Error: no triangles in the file", "\n".join(diagcode.text(code)))
        same = diagcode.decode(self.build(state="error", result=None, error="No triangles found in the file  "))
        other = diagcode.decode(self.build(state="error", result=None, error="No triangles found in part 2"))
        self.assertEqual(same["records"]["E"]["hash"], d["records"]["E"]["hash"])
        self.assertNotEqual(other["records"]["E"]["hash"], d["records"]["E"]["hash"])
        for text, want in (("the worker stopped unexpectedly (exit code -9) — this usually means the computer ran "
                            "out of memory", "crash_or_memory"), ("No scale token found in the picture", "no_scale"),
                           ("OpenSCAD was not found on this computer", "missing_program"),
                           ("something nobody thought of", "other"), ("", "none")):
            self.assertEqual(diagcode.error_class(text), want, text)

    def test_limits_are_read_from_the_log_and_the_words_are_still_in_the_source(self):
        log = ["Checkpoint written: a.step (3 planes)", "  time budget of 60 s reached — patches still outside…",
               "  (memory is tight: skipping the plate filler for this patch)", "Guesstimate: reduced 9 → 3 triangles",
               "  2 face(s) failed the geometry check — keeping them as exact facets"]
        r = diagcode.decode(self.build(log=log))["records"]["R"]
        self.assertTrue(r["checkpoint"] and r["budget"] and r["memory_tight"] and r["guesstimated"] and r["kept_facets"])
        self.assertFalse(r["checkpoint_kept"] or r["memory_reduced"] or r["v2_failed"])
        source = "".join(open(os.path.join(ROOT, "tostep", "plugins", "mesh2step", n), encoding="utf-8").read()
                         for n in ("classic.py", "engines.py", "ladder.py"))
        for name, words, _ in diagcode.FLAGS:                  # if a log line is reworded, this must follow
            for w in ((words,) if isinstance(words, str) else words):
                self.assertIn(w, source, name)

    def test_facts_from_every_kind_of_report(self):
        rep = core.Report(ok=True, n_input_tris=100, n_faces=6, n_planes=6, is_solid=True, is_valid=True,
                          mesh_volume=1000.0, step_volume=1001.0, max_dev=0.01, mean_dev=0.001, seconds=2.0,
                          route="prismatic")
        f = diagcode.facts(rep)
        self.assertEqual((f["faces"], f["planes"], f["route"], f["solid"]), (6.0, 6.0, "prismatic", True))
        self.assertAlmostEqual(f["dvol"], 0.1)
        self.assertIsNone(diagcode.facts(core.Report())["max_dev"])           # NaN in the report
        from tostep.plugins.jetwash import jetwash
        from tostep.plugins.scad2step import scad2step_core
        from tostep.plugins.image2step import image2step
        w = diagcode.facts(jetwash.WashReport(faces_after=12, solids_after=1, valid_after=True, volume_before=10.0,
                                              volume_after=9.9, seconds=1.0))
        self.assertEqual((w["faces"], w["solid"], w["valid"]), (12.0, True, True))
        self.assertAlmostEqual(w["dvol"], -1.0)
        s = diagcode.facts(scad2step_core.ScadReport(n_faces=8, is_solid=True, is_valid=True))
        self.assertEqual((s["faces"], s["solid"]), (8.0, True))
        i = diagcode.facts(image2step.ImageReport(ok=True, is_solid=True, outlines=3, seconds=0.5))
        self.assertTrue(i["solid"])
        for kind in ("wash", "scad", "image"):                 # no settings record for those yet
            d = diagcode.decode(self.build(kind=kind, result=w))
            self.assertEqual(sorted(d["records"]), ["H", "R"])
            self.assertEqual(d["records"]["H"]["kind"], kind)

    @unittest.skipUnless(HAVE_QR, "the QR picture needs the segno package")
    def test_the_qr_reads_back(self):
        import cv2
        import numpy as np
        code = self.build()
        rows = diagcode.qr_rows(code)
        self.assertEqual(len(rows), len(rows[0]))
        self.assertLessEqual(len(rows), 37)
        m = np.array([[int(v) for v in row] for row in rows], np.uint8)
        img = np.kron(1 - np.pad(m, 4), np.ones((4, 4), np.uint8)) * 255       # 4 px a module, as on screen
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        shot = np.full((700, 1100, 3), 236, np.uint8)                           # a "screenshot" with the QR in it
        shot[120:120 + img.shape[0], 760:760 + img.shape[1]] = img[:, :, None]
        path = os.path.join(d, "shot.png")
        cv2.imwrite(path, shot)
        self.assertEqual(diagcode.read_image(path), [code])
        blank = os.path.join(d, "blank.png")
        cv2.imwrite(blank, np.full((300, 300, 3), 200, np.uint8))
        self.assertEqual(diagcode.read_image(blank), [])
        r = cli("diag", path)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Surface fitter: Ladder", r.stdout)
        self.assertEqual(cli("diag", blank).returncode, 2)

    def test_command_line(self):
        code = self.build()
        r = cli("diag", code)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Mode: Smooth surfaces", r.stdout)
        self.assertIn("closed solid: yes", r.stdout)
        self.assertEqual(cli("diag", "not-a-code").returncode, 2)
        self.assertEqual(cli("diag", code[:-1] + ("0" if code[-1] != "0" else "1")).returncode, 1)   # check failed


class Job(unittest.TestCase):
    """A real conversion through the app's server: the worker hands the numbers over, the server
    builds the code, the page has the panel."""

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
        cls.httpd.server_close()
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

    def run_job(self, data, name, params):
        code, up = self.call("/api/upload?name=" + urllib.parse.quote(name), raw=data)
        self.assertEqual(code, 200, up)
        code, j = self.call("/api/run", {"kind": "convert", "file": up["id"], "params": params})
        self.assertEqual(code, 200, j)
        jid = j["id"] if "id" in j else j["job"]
        for _ in range(1500):
            code, st = self.call("/api/job/" + jid)
            if st["state"] in ("done", "error", "cancelled"):
                return jid, st
            time.sleep(0.2)
        self.fail("job did not finish")

    def test_a_conversion_carries_its_settings_and_result(self):
        with open(KNOB, "rb") as f:
            jid, st = self.run_job(f.read(), "knob.stl", dict(PARAMS, decimate="0"))
        self.assertEqual(st["state"], "done", st)
        code, d = self.call("/api/diag/" + jid)
        self.assertEqual(code, 200, d)
        self.assertTrue(d["has_settings"])
        self.assertEqual(d["name"], "knob.stl")
        self.assertNotIn("knob", d["code"].lower())            # the file's name is not in the code
        got = diagcode.decode(d["code"])
        self.assertIs(got["ok"], True)
        c, r, h = got["records"]["C"], got["records"]["R"], got["records"]["H"]
        self.assertEqual((c["mode"], c["engine"], c["guess"]), ("surface", "ladder", True))
        self.assertAlmostEqual(c["tol"], 0.02)
        self.assertAlmostEqual(c["organic_tol"], 0.25)
        self.assertEqual(c["profile"], self.app.settings()["name"])
        self.assertGreater(c["threads"], 0)
        self.assertTrue(r["solid"])
        self.assertEqual(r["route"], "lathe")
        self.assertGreater(r["faces"], 0)
        self.assertGreater(r["tris"], 1000)
        self.assertIsNotNone(r["max_dev"])
        self.assertEqual(h["state"], "done")
        self.assertLess(abs(h["time"] - (time.time() - diagcode.EPOCH) / 60), 10)
        words = " ".join(v for _, v in d["rows"])
        self.assertIn("Ladder (experimental)", words)
        self.assertIn("closed solid: yes", words)
        self.assertEqual(d["code"], self.call("/api/diag/" + jid)[1]["code"])     # the same every time it is asked
        if HAVE_QR:
            self.assertEqual(len(d["qr"]), len(d["qr"][0]))
        else:
            self.assertIsNone(d["qr"])

    def test_a_failed_job_has_one_too(self):
        jid, st = self.run_job(b"solid nothing\nendsolid nothing\n", "empty.stl", dict(PARAMS, engine="classic"))
        self.assertEqual(st["state"], "error", st)
        code, d = self.call("/api/diag/" + jid)
        self.assertEqual(code, 200, d)
        got = diagcode.decode(d["code"])["records"]
        self.assertEqual(got["H"]["state"], "error")
        self.assertEqual(got["E"]["error"], "no_triangles")
        self.assertNotIn("R", got)
        self.assertIn("No triangles", d["error"])

    def test_page_and_unknown_job(self):
        with urllib.request.urlopen(self.url + "/?t=" + self.app.token, timeout=30) as r:
            page = r.read().decode("utf-8")
        for bit in ('id="v-diag"', 'id="v-qr"', "function diagLoad", "viewDiag(", "/api/diag/"):
            self.assertIn(bit, page)
        self.assertEqual(self.call("/api/diag/0000")[0], 404)


if __name__ == "__main__":
    unittest.main()
