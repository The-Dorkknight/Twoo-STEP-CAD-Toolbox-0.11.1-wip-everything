# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""The quality presets of the STL → STEP tab (app/presets.py): one table for the page and the command line.

    python -m unittest discover -s tests -v
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
KNOB = os.path.join(ROOT, "tests", "data", "knob_plain.stl")

from tostep.plugins.mesh2step import presets  # noqa: E402


def cli(*args):
    return subprocess.run([sys.executable, os.path.join(ROOT, "run_tostep.py"), *args], capture_output=True, text=True,
                          timeout=900)


class Table(unittest.TestCase):
    def test_four_presets_and_standard_picks_by_shape(self):
        self.assertEqual([p["id"] for p in presets.PRESETS], ["draft", "quick", "standard", "fine"])
        self.assertEqual(presets.DEFAULT, "standard")
        # 0.10.2: Standard is the Auto engine and changes nothing else; Classic stays the base
        self.assertEqual(presets.settings("standard"), dict(presets.BASE, engine="auto"))
        self.assertEqual(presets.BASE["engine"], "classic")

    def test_every_setting_is_one_the_page_sends(self):
        from tostep.frontends.web import server as webui
        sent = set(re.findall(r"(\w+):\$\('", webui.build_page().decode().split("function convParams(){", 1)[1].split("}}", 1)[0]))
        sent |= {"mode", "engine"}
        for p in presets.PRESETS:
            for key in presets.settings(p["id"]):
                self.assertIn(key, sent, (p["id"], key))

    def test_the_page_carries_the_table(self):
        from tostep.frontends.web import server as webui
        m = re.search(r"const PRESETS=(\{.*?\});\n", webui.build_page().decode())
        self.assertIsNotNone(m)
        self.assertEqual(json.loads(m.group(1)), presets.for_page())
        self.assertIn('<button id="t-conv" class="on" onclick="tab(\'conv\')">STL → STEP</button>', webui.build_page().decode())

    def test_cli_defaults_match_the_page_values(self):
        d = presets.cli_defaults("draft")
        self.assertEqual((d["engine"], d["guess"], d["decimate"], d["budget"]), ("ladder", 0.5, 50000, 60.0))
        self.assertAlmostEqual(d["patch_size"], 0.30)
        self.assertEqual(presets.cli_defaults("fine")["engine"], "best")
        with self.assertRaises(ValueError):
            presets.settings("ultra")


class CommandLine(unittest.TestCase):
    def test_quick_preset_uses_the_ladder_and_an_option_still_wins(self):
        with tempfile.TemporaryDirectory() as d:
            r = cli(KNOB, os.path.join(d, "a.step"), "--preset", "quick")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("Route:", r.stdout)                     # the Ladder engine ran
            r = cli(KNOB, os.path.join(d, "b.step"), "--preset", "quick", "--engine", "classic")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertNotIn("Route:", r.stdout)                  # the explicit option won
            self.assertIn("Tip: this looks like a turned part", r.stdout)

    def test_unknown_preset_is_refused(self):
        r = cli(KNOB, "--preset", "ultra")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("invalid choice", r.stderr)


if __name__ == "__main__":
    unittest.main()
