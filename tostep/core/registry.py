# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""
registry — finds the plugins, reads their manifests, and loads their code only when asked.

Built-in plugins are the folders under tostep/plugins/ that hold a manifest.toml. More can be
named in TOSTEP_PLUGIN_PATH (folders that contain plugin folders, separated like PATH). A plugin
added that way is Python code that runs with your rights: install only ones you trust. A built-in
plugin of the same id always wins.

A broken plugin never takes the others down: its error is kept (registry.errors) and shown by
`tostep plugins`, and everything else works.
"""
from __future__ import annotations

import importlib
import os
import sys

from . import manifest as mf

BUILTIN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugins")


class Plugin:
    def __init__(self, m: mf.Manifest, builtin: bool):
        self.m = m
        self.builtin = builtin
        self.problem = ""                 # why it cannot be used (missing requirement, ...)

    @property
    def id(self):
        return self.m.id

    def _import(self, dotted):
        if not self.builtin and self.m.folder:
            root = os.path.dirname(self.m.folder)
            if root not in sys.path:
                sys.path.insert(0, root)
        return importlib.import_module(dotted)

    def module(self, dotted):
        """Import one of the plugin's modules (its cli hooks, its web routes) by dotted name."""
        return self._import(dotted)

    def run_function(self):
        if not self.m.entry:
            raise RuntimeError(f"the plugin '{self.id}' runs no jobs of its own")
        mod, _, fn = self.m.entry.partition(":")
        return getattr(self._import(mod), fn)

    def asset(self, rel):
        """Absolute path of a file inside the plugin's folder (a page fragment, an icon)."""
        return os.path.join(self.m.folder, rel) if rel else ""


class Registry:
    def __init__(self):
        self.plugins: dict[str, Plugin] = {}
        self.errors: list[tuple[str, str]] = []      # (folder name, message)

    # ---- lookups
    def get(self, pid):
        return self.plugins[pid]

    def for_kind(self, kind):
        for p in self.plugins.values():
            if p.m.kind and p.m.kind == kind and not p.problem:
                return p
        return None

    def for_command(self, command):
        for p in self.plugins.values():
            if p.m.cli_command == command and not p.problem:
                return p
        return None

    def usable(self):
        """Plugins that load, in dependency order (a plugin comes after everything it requires)."""
        done, out = set(), []

        def visit(p):
            if p.id in done or p.problem:
                return
            done.add(p.id)
            for r in p.m.requires:
                if r in self.plugins:
                    visit(self.plugins[r])
            out.append(p)
        for pid in sorted(self.plugins):
            visit(self.plugins[pid])
        return out

    def tabs(self):
        return sorted((p for p in self.usable() if p.m.web_tab), key=lambda p: (p.m.web_order, p.id))

    def cad_commands(self, frontend):
        out = []
        for p in sorted(self.usable(), key=lambda p: (p.m.web_order, p.id)):
            for c in p.m.cad:
                if frontend in c.frontends:
                    out.append((p, c))
        return out

    # ---- building
    def _add(self, m, builtin, folder_name):
        if m.id in self.plugins:
            self.errors.append((folder_name, f"id {m.id!r} is already taken by "
                                f"{'a built-in' if self.plugins[m.id].builtin else 'another'} plugin"))
            return
        self.plugins[m.id] = Plugin(m, builtin)

    def scan(self, root, builtin, package_prefix=""):
        if not os.path.isdir(root):
            return
        for name in sorted(os.listdir(root)):
            mp = os.path.join(root, name, "manifest.toml")
            if not os.path.isfile(mp):
                continue
            try:
                m = mf.load(mp, package=(package_prefix + name) if package_prefix else name)
            except mf.ManifestError as e:
                self.errors.append((name, str(e)))
                continue
            self._add(m, builtin, name)

    def check(self):
        """Mark plugins whose requirements are missing, unusable or circular."""
        changed = True
        while changed:
            changed = False
            for p in self.plugins.values():
                if p.problem:
                    continue
                for r in p.m.requires:
                    if r not in self.plugins:
                        p.problem = f"needs the plugin '{r}', which is not installed"
                    elif self.plugins[r].problem:
                        p.problem = f"needs the plugin '{r}', which cannot be used"
                    if p.problem:
                        changed = True
                        break
        for p in self.plugins.values():       # circular requirements
            seen, stack = set(), [p.id]
            while stack:
                cur = stack.pop()
                for r in self.plugins[cur].m.requires:
                    if r == p.id:
                        p.problem = "its requirements go round in a circle"
                    elif r in self.plugins and r not in seen:
                        seen.add(r)
                        stack.append(r)
        kinds, cmds, tabs = {}, {}, {}
        for p in self.usable():                # one owner for every kind, command and tab
            for table, key, what in ((kinds, p.m.kind, "job kind"), (cmds, p.m.cli_command, "command"),
                                      (tabs, p.m.web_tab, "tab")):
                if not key:
                    continue
                if key in table:
                    p.problem = f"its {what} '{key}' is already used by '{table[key]}'"
                else:
                    table[key] = p.id


_cache = None


def discover(fresh=False):
    """The registry for this process: built-ins plus TOSTEP_PLUGIN_PATH."""
    global _cache
    if _cache is not None and not fresh:
        return _cache
    reg = Registry()
    reg.scan(BUILTIN, True, "tostep.plugins.")
    for root in filter(None, os.environ.get("TOSTEP_PLUGIN_PATH", "").split(os.pathsep)):
        reg.scan(root, False)
    reg.check()
    _cache = reg
    return reg
