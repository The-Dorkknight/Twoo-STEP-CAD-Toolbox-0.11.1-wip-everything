# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""The FreeCAD workbench and the Fusion add-in, loaded against stand-in modules.

This proves only that the files import, register the commands the manifests promise and call the shared bridge
correctly. It does NOT prove they work inside a real FreeCAD or Fusion: neither has been run (see
docs/KNOWN_LIMITATIONS.md). The stand-ins are MagicMocks, so any call on them succeeds.
"""
import importlib
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tostep.core import registry  # noqa: E402
from tostep.frontends import install  # noqa: E402


class _Base(unittest.TestCase):
    kind = ""
    modules = ()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.pkg = os.path.join(self.tmp.name, "TOSTEP")
        install.build_cad_package(install._plugin_src(self.kind), self.pkg, with_location=True)
        self.saved_path = list(sys.path)
        self.saved_mods = {k: sys.modules.get(k) for k in self.modules + ("tostep_bridge", "hstl", "hardpoint_fc",
                                                                           "hardpoint_fusion", "tostep_fc", "TOSTEP")}
        sys.path.insert(0, self.pkg)

    def tearDown(self):
        sys.path[:] = self.saved_path
        for k, v in self.saved_mods.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
        self.tmp.cleanup()


class FreeCAD(_Base):
    kind = "FreeCAD"
    modules = ("FreeCAD", "FreeCADGui", "PySide", "PySide.QtCore", "PySide.QtGui", "PySide.QtWidgets")

    def test_registers_every_command_the_manifests_promise(self):
        added = {}
        gui = mock.MagicMock()
        gui.addCommand.side_effect = lambda name, cmd: added.__setitem__(name, cmd)
        pyside = types.ModuleType("PySide")
        for sub in ("QtCore", "QtGui", "QtWidgets"):
            m = mock.MagicMock()
            setattr(pyside, sub, m)
            sys.modules["PySide." + sub] = m
        with mock.patch.dict(sys.modules, {"FreeCAD": mock.MagicMock(), "FreeCADGui": gui, "PySide": pyside}):
            fc = importlib.import_module("tostep_fc")
            fc.register()
        reg = registry.discover()
        promised = {"TOSTEP_" + c.id.capitalize() for _, c in reg.cad_commands("freecad")} | {"TOSTEP_Settings"}
        self.assertEqual(set(added), promised)

    def test_the_bridge_finds_the_folder_the_installer_wrote_down(self):
        bridge = importlib.import_module("tostep_bridge")
        self.assertEqual(bridge.find_folder(None), install.ROOT)
        self.assertTrue(bridge.is_toolbox_folder(install.ROOT))
        self.assertFalse(bridge.is_toolbox_folder(self.tmp.name))


class Fusion(_Base):
    kind = "Fusion"
    modules = ("adsk", "adsk.core", "adsk.fusion")

    def test_the_add_in_imports_and_defines_every_command(self):
        adsk = types.ModuleType("adsk")
        adsk.core = mock.MagicMock()
        adsk.fusion = mock.MagicMock()
        # the add-in subclasses these handler types, so they must be real classes
        for name in ("CustomEventHandler", "CommandCreatedEventHandler", "InputChangedEventHandler",
                     "CommandEventHandler"):
            setattr(adsk.core, name, type(name, (), {}))
        with mock.patch.dict(sys.modules, {"adsk": adsk, "adsk.core": adsk.core, "adsk.fusion": adsk.fusion}):
            mod = importlib.import_module("TOSTEP")
        reg = registry.discover()
        promised = {"TOSTEP_" + c.id.capitalize() for _, c in reg.cad_commands("fusion")}
        self.assertEqual(set(mod.CMDS), promised)
        self.assertTrue(callable(mod.run) and callable(mod.stop))

    def test_the_manifest_matches_the_folder(self):
        import json
        with open(os.path.join(self.pkg, "TOSTEP.manifest"), encoding="utf-8") as f:
            m = json.load(f)
        self.assertEqual(m["type"], "addin")
        self.assertTrue(os.path.isfile(os.path.join(self.pkg, "TOSTEP.py")))
        self.assertEqual(m["version"], "0.11.0")


if __name__ == "__main__":
    unittest.main()
