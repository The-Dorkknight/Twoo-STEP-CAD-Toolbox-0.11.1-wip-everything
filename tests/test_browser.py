# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""The generated page in a real (headless) browser: no script errors, every tab present, a file dropped on a
tab runs a job and shows its result. Includes the example plug-in from docs/ADDING_A_PLUGIN.md, so that guide
cannot go stale.

Skipped when Playwright or its Chromium is not installed (the start files do not install them).
"""
import os
import shutil
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

try:
    from playwright.sync_api import sync_playwright
except ImportError:                                    # pragma: no cover
    sync_playwright = None

EXAMPLES = os.path.join(ROOT, "examples", "plugin_template")


@unittest.skipUnless(sync_playwright, "Playwright is not installed")
class Page(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["TOSTEP_PLUGIN_PATH"] = EXAMPLES
        from tostep.core import registry
        from tostep.frontends.web import server
        reg = registry.discover(fresh=True)
        cls.tmp = tempfile.mkdtemp()
        cls.app = server.App(os.path.join(cls.tmp, "work"), os.path.join(cls.tmp, "out"), reg)
        page = server.build_page(reg).replace(b"__OUT__", b'""')
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(cls.app, page))
        cls.httpd.daemon_threads = True
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.httpd.server_address[1]}/"
        try:
            cls.pw = sync_playwright().start()
            cls.browser = cls.pw.chromium.launch()
        except Exception as e:                         # no browser binary
            cls.httpd.shutdown()
            os.environ.pop("TOSTEP_PLUGIN_PATH", None)
            raise unittest.SkipTest(f"Chromium is not available: {e}")

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.httpd.shutdown()
        shutil.rmtree(cls.tmp, ignore_errors=True)
        os.environ.pop("TOSTEP_PLUGIN_PATH", None)
        from tostep.core import registry
        registry.discover(fresh=True)

    def open(self):
        ctx = self.browser.new_context()
        ctx.add_init_script("try{localStorage.setItem('tostep-agreed','1')}catch(e){}")   # the first-start notice
        page = ctx.new_page()
        self.errors = []
        page.on("pageerror", lambda e: self.errors.append(str(e)))
        page.on("console", lambda m: self.errors.append(m.text) if m.type == "error" else None)
        page.goto(self.url)
        return page

    def drop(self, page, tab, path):
        with page.expect_file_chooser() as fc:
            page.click(f"#d-{tab}")
        fc.value.set_files(path)

    def test_every_tab_is_there_and_no_script_errors(self):
        page = self.open()
        page.wait_for_timeout(500)
        for t in ("conv", "hp", "scad", "img", "wash", "plug", "hello"):
            self.assertEqual(page.locator(f"#t-{t}").count(), 1, t)
            page.click(f"#t-{t}")
            self.assertTrue(page.locator(f"#p-{t}").is_visible(), t)
        self.assertEqual(self.errors, [])

    def test_the_example_plugin_runs_from_its_tab(self):
        path = os.path.join(self.tmp, "notes.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write("one\n\ntwo\nthree\n")
        page = self.open()
        page.click("#t-hello")
        self.drop(page, "hello", path)
        page.wait_for_selector("#b-hello:not([disabled])")
        page.check("#hello-skip")
        page.click("#b-hello")
        page.wait_for_selector("#res-hello b", timeout=60000)
        self.assertIn("3 line(s) counted", page.inner_text("#res-hello"))
        self.assertEqual(self.errors, [])

    def test_a_conversion_from_the_convert_tab(self):
        import trimesh
        path = os.path.join(self.tmp, "block.stl")
        trimesh.creation.box(extents=[20, 10, 5]).export(path)
        page = self.open()
        self.drop(page, "conv", path)
        page.wait_for_selector("#b-conv:not([disabled])")
        page.click("#b-conv")
        page.wait_for_selector("#res-conv b", timeout=120000)
        self.assertIn("Done", page.inner_text("#res-conv"))
        self.assertIn("Closed solid: yes", page.inner_text("#res-conv"))
        self.assertEqual([e for e in self.errors if "WebGL" not in e], [])


if __name__ == "__main__":
    unittest.main()
