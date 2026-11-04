# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""The plug-in contract (version 1) and the seams between the core, the plug-ins and the front ends.

These tests are the reason the toolbox can be maintained by someone who did not write it: a change that
breaks a plug-in manifest, a CAD command, the page or the command line fails here, not on a user's
computer. They need no heavy conversion, so they run in seconds.

    python -m pytest tests/test_contract.py
"""
import ast
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tostep.core import manifest as mf  # noqa: E402
from tostep.core import params as params_mod  # noqa: E402
from tostep.core import registry  # noqa: E402

BUILTIN_IDS = {"mesh2step", "jetwash", "hardpoint", "scad2step", "image2step"}
FRONTEND_DIRS = {"freecad": os.path.join(ROOT, "tostep", "frontends", "freecad", "TOSTEP"),
                 "fusion": os.path.join(ROOT, "tostep", "frontends", "fusion", "TOSTEP")}


def run(*args, env=None):
    return subprocess.run([sys.executable, os.path.join(ROOT, "run_tostep.py"), *args], capture_output=True,
                          text=True, timeout=300, env=env)


def write_plugin(root, name, toml, code="def run(job):\n    return job.out\n"):
    d = os.path.join(root, name)
    os.makedirs(d)
    with open(os.path.join(d, "manifest.toml"), "w", encoding="utf-8") as f:
        f.write(toml)
    with open(os.path.join(d, "__init__.py"), "w", encoding="utf-8") as f:
        f.write(code)
    return d


class Registry(unittest.TestCase):
    def test_the_five_built_in_plugins_load_cleanly(self):
        reg = registry.discover(fresh=True)
        self.assertEqual(reg.errors, [], reg.errors)
        self.assertEqual(set(reg.plugins), BUILTIN_IDS)
        for p in reg.plugins.values():
            self.assertEqual(p.problem, "", (p.id, p.problem))
            self.assertEqual(p.m.raw.get("contract"), 1, p.id)

    def test_every_plugin_with_a_kind_has_a_working_entry_point(self):
        reg = registry.discover()
        for p in reg.usable():
            if p.m.kind:
                self.assertTrue(callable(p.run_function()), p.id)

    def test_job_kinds_are_unique_and_frozen(self):
        # the kind appears inside diagnostic codes people have already saved: never rename one
        reg = registry.discover()
        kinds = {p.id: p.m.kind for p in reg.plugins.values() if p.m.kind}
        self.assertEqual(kinds, {"mesh2step": "convert", "jetwash": "wash", "scad2step": "scad",
                                 "image2step": "image"})

    def test_cli_commands_and_tabs_are_unique(self):
        reg = registry.discover()
        cmds = [p.m.cli_command for p in reg.plugins.values() if p.m.cli_command]
        tabs = [p.m.web_tab for p in reg.plugins.values() if p.m.web_tab]
        self.assertEqual(len(cmds), len(set(cmds)))
        self.assertEqual(len(tabs), len(set(tabs)))

    def test_a_broken_plugin_never_takes_the_others_down(self):
        with tempfile.TemporaryDirectory() as extra:
            write_plugin(extra, "broken", "this is = not [valid toml")
            write_plugin(extra, "needy", textwrap.dedent('''
                contract = 1
                id = "needy"
                name = "Needy"
                version = "1.0.0"
                status = "experimental"
                summary = "wants a plug-in that is not there"
                kind = "needy"
                requires = ["nothinglikethis"]
                entry = "needy:run"
                '''))
            old = os.environ.get("TOSTEP_PLUGIN_PATH")
            os.environ["TOSTEP_PLUGIN_PATH"] = extra
            try:
                reg = registry.discover(fresh=True)
                self.assertTrue(BUILTIN_IDS <= {p.id for p in reg.usable()})
                self.assertIn("broken", [n for n, _ in reg.errors])
                self.assertNotIn("needy", [p.id for p in reg.usable()])
                self.assertIn("nothinglikethis", reg.plugins["needy"].problem)
            finally:
                if old is None:
                    os.environ.pop("TOSTEP_PLUGIN_PATH", None)
                else:
                    os.environ["TOSTEP_PLUGIN_PATH"] = old
                registry.discover(fresh=True)

    def test_a_plugin_cannot_steal_a_built_in_id_or_command(self):
        with tempfile.TemporaryDirectory() as extra:
            write_plugin(extra, "imposter", textwrap.dedent('''
                contract = 1
                id = "jetwash"
                name = "Imposter"
                version = "9.9.9"
                status = "stable"
                summary = "same id as a built-in"
                '''))
            old = os.environ.get("TOSTEP_PLUGIN_PATH")
            os.environ["TOSTEP_PLUGIN_PATH"] = extra
            try:
                reg = registry.discover(fresh=True)
                self.assertEqual(reg.plugins["jetwash"].m.name, "Jetwash")
                self.assertTrue(any("already taken" in msg for _, msg in reg.errors), reg.errors)
            finally:
                if old is None:
                    os.environ.pop("TOSTEP_PLUGIN_PATH", None)
                else:
                    os.environ["TOSTEP_PLUGIN_PATH"] = old
                registry.discover(fresh=True)


class Manifests(unittest.TestCase):
    def test_a_kind_without_an_entry_is_refused(self):
        with self.assertRaises(mf.ManifestError):
            mf.parse({"contract": 1, "id": "x", "name": "X", "version": "1", "status": "stable", "summary": "s",
                      "kind": "x"}, "x", "x")

    def test_an_unknown_contract_version_is_refused(self):
        with self.assertRaises(mf.ManifestError):
            mf.parse({"contract": 2, "id": "x", "name": "X", "version": "1", "status": "stable", "summary": "s"},
                     "x", "x")

    def test_parameter_types_are_checked(self):
        reg = registry.discover()
        for p in reg.plugins.values():
            for q in p.m.params:
                self.assertIn(q.type, ("float", "int", "bool", "str", "choice", "path"), (p.id, q.name))
                if q.type == "choice":
                    self.assertTrue(q.choices, (p.id, q.name))

    def test_mesh2step_defaults_match_the_converter(self):
        """The manifest is what the page and the command line show; classic.Params is what runs. They agree."""
        from tostep.plugins import mesh2step
        from tostep.plugins.mesh2step import classic
        reg = registry.discover()
        m = reg.plugins["mesh2step"].m
        typed = params_mod.coerce(m, {})
        built = mesh2step.build_params(typed, 1.0)
        default = classic.Params()
        drift = []
        for name in built.__dataclass_fields__:
            a, b = getattr(built, name), getattr(default, name)
            if isinstance(a, float) and isinstance(b, float):
                same = abs(a - b) < 1e-9
            else:
                same = a == b
            if not same:
                drift.append((name, a, b))
        # these are set by the front end on purpose (the job decides them), not by the converter's own default
        expected = {"tol", "guess", "save_labelled"}
        self.assertEqual([d for d in drift if d[0] not in expected], [], drift)


class Coercion(unittest.TestCase):
    def setUp(self):
        self.m = registry.discover().plugins["jetwash"].m

    def test_blank_is_the_default(self):
        self.assertEqual(params_mod.coerce(self.m, {"threshold": ""})["threshold"], 0.05)
        self.assertEqual(params_mod.coerce(self.m, {})["coarseness"], "2")

    def test_numbers_are_typed_and_bad_ones_name_the_parameter(self):
        self.assertEqual(params_mod.coerce(self.m, {"threshold": "0.2"})["threshold"], 0.2)
        with self.assertRaises(ValueError) as e:
            params_mod.coerce(self.m, {"threshold": "lots"})
        self.assertIn("threshold", str(e.exception))
        with self.assertRaises(ValueError):
            params_mod.coerce(self.m, {"threshold": "nan"})

    def test_a_bad_choice_falls_back_to_the_default_and_extras_pass_through(self):
        out = params_mod.coerce(self.m, {"coarseness": "9", "something_else": 7})
        self.assertEqual(out["coarseness"], "2")
        self.assertEqual(out["something_else"], 7)


class CadFrontends(unittest.TestCase):
    """The FreeCAD and Fusion plug-ins are hand-written adapters. These tests keep them honest."""

    def test_every_cad_command_in_a_manifest_exists_in_both_adapters(self):
        reg = registry.discover()
        for frontend, folder in FRONTEND_DIRS.items():
            src = ""
            for fn in os.listdir(folder):
                if fn.endswith(".py"):
                    with open(os.path.join(folder, fn), encoding="utf-8") as f:
                        src += f.read()
            for p, c in reg.cad_commands(frontend):
                cid = "TOSTEP_" + c.id.capitalize()
                self.assertIn(cid, src, f"{frontend} has no {cid} (plugin {p.id})")

    def test_the_adapters_use_no_old_names(self):
        for folder in FRONTEND_DIRS.values():
            for fn in os.listdir(folder):
                if fn.endswith((".py", ".xml", ".manifest")):
                    with open(os.path.join(folder, fn), encoding="utf-8") as f:
                        text = f.read()
                    self.assertNotRegex(text, r"STL2STEP|stl2step|EVERYTHING to solid", fn)

    def test_the_adapters_only_use_command_line_flags_that_exist(self):
        """The bridge builds command lines for run_tostep.py. Every flag it uses must be accepted by it."""
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "tostep_bridge", os.path.join(ROOT, "tostep", "frontends", "cad_shared", "bridge.py"))
        bridge = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bridge)
        with tempfile.TemporaryDirectory() as d:
            cases = {
                "convert": bridge.convert_args("a.stl", "b.step", "surface", "mm", 0.05, 1000, "z"),
                "convert-loft": bridge.convert_args("a.stl", "b.step", "loft", "in", 0, 0, "x"),
                "scad": bridge.scad_args("a.scad", "b.step", "mm", False),
                "wash": bridge.wash_args("a.step", "b.step", 0.05, 2, "mm"),
            }
            for name, args in cases.items():
                cmd = args[0] if args[0] in ("scad", "wash") else "convert"
                rest = args[1:] if args[0] in ("scad", "wash") else args
                # Parse only: ask the real parser to read the arguments with a missing input file; an unknown
                # flag is "unrecognized arguments" (exit 2), a missing file is an ordinary error (exit 1).
                r = run(cmd, *[os.path.join(d, a) if a.endswith((".stl", ".scad", ".step")) else a for a in rest])
                self.assertNotIn("unrecognized arguments", r.stderr, (name, r.stderr))
                self.assertNotIn("invalid choice", r.stderr, (name, r.stderr))
                self.assertNotIn("expected one argument", r.stderr, (name, r.stderr))

    def test_the_cad_packages_are_built_from_one_shared_source(self):
        from tostep.frontends import install
        with tempfile.TemporaryDirectory() as d:
            for kind in ("FreeCAD", "Fusion"):
                dest = os.path.join(d, kind)
                install.build_cad_package(install._plugin_src(kind), dest, with_location=True)
                for name in ("tostep_bridge.py", "hstl.py", install.LOCATION_FILE):
                    self.assertTrue(os.path.isfile(os.path.join(dest, name)), (kind, name))
                with open(os.path.join(dest, install.LOCATION_FILE), encoding="utf-8") as f:
                    self.assertEqual(f.read().strip(), install.ROOT)


class Page(unittest.TestCase):
    def test_the_page_is_assembled_from_the_plugins(self):
        from tostep.frontends.web import server
        reg = registry.discover()
        html = server.build_page(reg).decode("utf-8")
        left = set(re.findall(r"__[A-Z][A-Z_]+__", html))
        self.assertLessEqual(left, {"__OUT__", "__TOKEN__"}, left)      # filled in per request / at serve time
        for p in reg.tabs():
            self.assertIn(f'id="t-{p.m.web_tab}"', html, p.id)
            self.assertIn(f'id="p-{p.m.web_tab}"', html, p.id)
        self.assertIn("WORK IN PROGRESS", html)

    def test_page_scripts_are_valid_javascript(self):
        node = subprocess.run(["which", "node"], capture_output=True, text=True).stdout.strip()
        if not node:
            self.skipTest("node is not installed")
        from tostep.frontends.web import server
        html = server.build_page().decode("utf-8")
        scripts = re.findall(r"<script[^>]*>(.*?)</script>", html, re.S)
        self.assertTrue(scripts)
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
            f.write("\n;\n".join(scripts).replace("__TOKEN__", "x").replace("__OUT__", '""'))
            path = f.name
        try:
            r = subprocess.run([node, "--check", path], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
        finally:
            os.remove(path)


class CommandLine(unittest.TestCase):
    def test_version_and_plugin_list(self):
        r = run("--version")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Twoo STEP CAD Toolbox", r.stdout)
        self.assertIn("WORK IN PROGRESS", r.stdout)
        r = run("plugins")
        self.assertEqual(r.returncode, 0, r.stderr)
        for pid in BUILTIN_IDS:
            self.assertIn(pid, r.stdout)

    def test_every_command_has_help(self):
        reg = registry.discover()
        for p in reg.usable():
            if p.m.cli_command:
                r = run(p.m.cli_command, "--help")
                self.assertEqual(r.returncode, 0, (p.id, r.stderr))
                self.assertIn("usage:", r.stdout, p.id)

    def test_the_old_environment_variables_are_gone(self):
        """STL2STEP_* variables were renamed TOSTEP_* with the new name; nothing may still read the old ones."""
        for dirpath, _, files in os.walk(os.path.join(ROOT, "tostep")):
            for fn in files:
                if fn.endswith((".py", ".js", ".html", ".toml")):
                    with open(os.path.join(dirpath, fn), encoding="utf-8") as f:
                        self.assertNotIn("STL2STEP_", f.read(), os.path.join(dirpath, fn))


class Layers(unittest.TestCase):
    """ARCHITECTURE.md's rules 1 and 2, checked by reading the imports."""

    def imports(self, path):
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read(), path)
        names = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names += [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.append(node.module)
                names += [node.module + "." + a.name for a in node.names]
        return names

    def py_files(self, top):
        for dirpath, dirs, files in os.walk(os.path.join(ROOT, *top.split("/"))):
            dirs[:] = [d for d in dirs if d not in ("__pycache__", "bin")]
            for fn in files:
                if fn.endswith(".py"):
                    yield os.path.join(dirpath, fn)

    def test_plugin_run_code_never_imports_front_ends(self):
        """Only a plug-in's own web.py (its page adapter) may use the web server's public helpers."""
        bad = [(os.path.relpath(p, ROOT), n) for p in self.py_files("tostep/plugins")
               for n in self.imports(p) if n.startswith("tostep.frontends")
               and os.path.basename(p) != "web.py"]
        self.assertEqual(bad, [])

    def test_web_adapters_use_only_public_server_names(self):
        bad = []
        for p in self.py_files("tostep/plugins"):
            if os.path.basename(p) == "web.py":
                bad += [(os.path.relpath(p, ROOT), n) for n in self.imports(p)
                        if n.startswith("tostep.frontends") and n.rsplit(".", 1)[-1].startswith("_")]
        self.assertEqual(bad, [])

    def test_the_core_names_a_plugin_only_where_listed(self):
        allowed = {"tostep/core/preview3d.py": "tostep.plugins.mesh2step"}     # known coupling, ARCHITECTURE.md
        bad = []
        for p in self.py_files("tostep/core"):
            rel = os.path.relpath(p, ROOT).replace(os.sep, "/")
            for n in self.imports(p):
                if n.startswith("tostep.plugins") and not n.startswith(allowed.get(rel, "\0")):
                    bad.append((rel, n))
        self.assertEqual(bad, [])

    def test_the_core_never_imports_a_front_end(self):
        bad = [(os.path.relpath(p, ROOT), n) for p in self.py_files("tostep/core")
               for n in self.imports(p) if n.startswith("tostep.frontends")]
        self.assertEqual(bad, [])

    def test_plugins_only_import_plugins_they_require(self):
        reg = registry.discover()
        allowed = {(p.id, r) for p in reg.plugins.values() for r in p.m.requires}
        bad = []
        for p in self.py_files("tostep/plugins"):
            mine = os.path.relpath(p, os.path.join(ROOT, "tostep", "plugins")).split(os.sep)[0]
            for n in self.imports(p):
                parts = n.split(".")
                if n.startswith("tostep.plugins.") and len(parts) > 2 and parts[2] != mine \
                        and (mine, parts[2]) not in allowed:
                    bad.append((os.path.relpath(p, ROOT), n))
        self.assertEqual(bad, [])


class Headers(unittest.TestCase):
    NOTICE = "Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)"

    def test_every_source_file_carries_the_notice(self):
        missing = []
        for top in ("tostep", "tests", "tools", "launch"):
            for dirpath, dirs, files in os.walk(os.path.join(ROOT, top)):
                dirs[:] = [d for d in dirs if d not in ("__pycache__", "bin", "data")]
                for fn in files:
                    if fn.endswith((".py", ".sh")):
                        path = os.path.join(dirpath, fn)
                        with open(path, encoding="utf-8") as f:
                            head = f.read(600)
                        if self.NOTICE not in head:
                            missing.append(os.path.relpath(path, ROOT))
        self.assertEqual(missing, [], "files without the Required Notice line")

    def test_every_python_file_parses(self):
        for dirpath, dirs, files in os.walk(os.path.join(ROOT, "tostep")):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for fn in files:
                if fn.endswith(".py"):
                    path = os.path.join(dirpath, fn)
                    with open(path, encoding="utf-8") as f:
                        ast.parse(f.read(), path)


if __name__ == "__main__":
    unittest.main()
