# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""
manifest — what a plugin says about itself (manifest.toml), and the checks that keep that honest.

This is contract 1 (docs/PLUGIN_CONTRACT.md). A plugin is a folder holding a manifest.toml and a
Python package with a `run(job)` function. The core reads the manifest without importing the
plugin's code, so listing plugins, building the command line, the web page and the CAD buttons
never costs the second it takes to load NumPy and OpenCascade.

    from tostep.core import manifest
    m = manifest.load("tostep/plugins/jetwash/manifest.toml")

Everything here is plain standard library (tomllib, 3.11+).
"""
from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field

CONTRACT = 1                      # the version of the plugin contract this core speaks
ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
KIND_RE = re.compile(r"^[a-z][a-z0-9_]{0,15}$")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+([.-][0-9A-Za-z.-]+)?$")
STATUSES = ("stable", "experimental")
PARAM_TYPES = ("float", "int", "bool", "str", "choice", "path")
CAD_INPUTS = ("mesh", "step", "scad", "shape", "picture", "none")
CAD_FRONTENDS = ("freecad", "fusion")


class ManifestError(ValueError):
    """The manifest breaks the contract. The message says which plugin, which field, and why."""


@dataclass
class Param:
    name: str
    type: str
    default: object = None
    help: str = ""
    choices: tuple = ()
    min: float | None = None
    max: float | None = None
    group: str = ""
    advanced: bool = False
    nullable: bool = False            # a blank value means "none" (None), not the default


@dataclass
class CadCommand:
    id: str
    label: str
    tooltip: str
    input: str
    frontends: tuple = CAD_FRONTENDS


@dataclass
class Manifest:
    id: str
    name: str
    version: str
    status: str
    summary: str
    entry: str
    kind: str = ""                    # job kind; "" = this plugin runs no jobs of its own
    requires: tuple = ()
    inputs: tuple = ()
    output: str = "{stem}.step"
    reject: str = ""
    mesh_preview: bool = True         # the web preview can show the original input beside the result
    preview: bool = True              # the result is a STEP the 3D preview can show (False: no preview, no diagnostic panel)
    raw_params: bool = False          # the plugin validates its own parameters (its library already does)
    chain: bool = False               # its result is offered as the input of the next tool ("last converted STEP")
    cli_command: str = ""
    cli_hooks: str = ""
    web_tab: str = ""
    web_title: str = ""
    web_order: int = 100
    web_fragment: str = ""
    web_script: str = ""
    web_style: str = ""
    web_routes: str = ""
    cad: tuple = ()
    params: tuple = ()
    folder: str = ""
    package: str = ""                 # dotted package the plugin's code lives in
    raw: dict = field(default_factory=dict, repr=False)

    def param(self, name):
        for p in self.params:
            if p.name == name:
                return p
        raise KeyError(name)

    def defaults(self):
        return {p.name: p.default for p in self.params}


def _need(d, key, typ, where, plugin):
    if key not in d:
        raise ManifestError(f"{plugin}: {where} needs '{key}'")
    v = d[key]
    if not isinstance(v, typ):
        raise ManifestError(f"{plugin}: {where} '{key}' must be {typ.__name__}, got {type(v).__name__}")
    return v


def parse(data, folder="", package=""):
    """A dict (what tomllib gives) -> Manifest, or ManifestError."""
    pid = data.get("id", "?")
    if data.get("contract") != CONTRACT:
        raise ManifestError(f"{pid}: contract must be {CONTRACT} (this core speaks contract {CONTRACT}), "
                            f"got {data.get('contract')!r}")
    if not isinstance(pid, str) or not ID_RE.match(pid):
        raise ManifestError(f"{pid}: id must be lower-case letters, digits and _ (2 to 32 characters)")
    for key in ("name", "version", "status", "summary"):
        _need(data, key, str, "manifest", pid)
    if data.get("kind") and "entry" not in data:
        raise ManifestError(f"{pid}: a plugin that runs jobs (it has a kind) needs an entry")
    data = dict(data)
    data.setdefault("entry", "")
    if not VERSION_RE.match(data["version"]):
        raise ManifestError(f"{pid}: version {data['version']!r} is not x.y.z")
    if data["status"] not in STATUSES:
        raise ManifestError(f"{pid}: status must be one of {STATUSES}")
    if data["entry"] and ":" not in data["entry"]:
        raise ManifestError(f"{pid}: entry must look like 'package.module:function'")
    kind = data.get("kind", "")
    if kind and not KIND_RE.match(kind):
        raise ManifestError(f"{pid}: kind {kind!r} must be lower-case letters/digits/_ (1 to 16 characters)")
    requires = tuple(data.get("requires", ()))
    for r in requires:
        if not isinstance(r, str) or not ID_RE.match(r):
            raise ManifestError(f"{pid}: requires has a bad plugin id {r!r}")
    io = data.get("io", {})
    inputs = tuple(x.lower() for x in io.get("inputs", ()))
    for x in inputs:
        if not x.startswith("."):
            raise ManifestError(f"{pid}: io.inputs entries start with a dot, got {x!r}")
    output = io.get("output", "{stem}.step")
    if "{stem}" not in output:
        raise ManifestError(f"{pid}: io.output must contain {{stem}}")
    cli = data.get("cli", {})
    web = data.get("web", {})
    if web:
        for key in ("tab", "title", "fragment"):
            _need(web, key, str, "[web]", pid)
        if not re.match(r"^[a-z][a-z0-9]{0,11}$", web["tab"]):
            raise ManifestError(f"{pid}: web.tab must be 1 to 12 lower-case letters/digits")
    params = []
    seen = set()
    for i, p in enumerate(data.get("params", ())):
        name = _need(p, "name", str, f"params[{i}]", pid)
        if name in seen:
            raise ManifestError(f"{pid}: parameter {name!r} is declared twice")
        seen.add(name)
        typ = _need(p, "type", str, f"param {name}", pid)
        if typ not in PARAM_TYPES:
            raise ManifestError(f"{pid}: param {name} type must be one of {PARAM_TYPES}")
        choices = tuple(p.get("choices", ()))
        if typ == "choice" and not choices:
            raise ManifestError(f"{pid}: param {name} is a choice but lists no choices")
        if "default" not in p:
            raise ManifestError(f"{pid}: param {name} needs a default")
        dflt = p["default"]
        ok = {"float": (int, float), "int": (int,), "bool": (bool,), "str": (str,), "path": (str,),
              "choice": (str,)}[typ]
        if dflt is not None and (not isinstance(dflt, ok) or (typ != "bool" and isinstance(dflt, bool))):
            raise ManifestError(f"{pid}: param {name} default {dflt!r} is not a {typ}")
        if typ == "choice" and dflt not in choices:
            raise ManifestError(f"{pid}: param {name} default {dflt!r} is not one of its choices")
        if not p.get("help"):
            raise ManifestError(f"{pid}: param {name} needs a help sentence (it is the documentation)")
        params.append(Param(name=name, type=typ, default=dflt, help=p["help"], choices=choices,
                            min=p.get("min"), max=p.get("max"), group=p.get("group", ""),
                            advanced=bool(p.get("advanced", False)), nullable=bool(p.get("nullable", False))))
    cads = []
    for i, c in enumerate(data.get("cad", ())):
        cid = _need(c, "id", str, f"cad[{i}]", pid)
        inp = _need(c, "input", str, f"cad {cid}", pid)
        if inp not in CAD_INPUTS:
            raise ManifestError(f"{pid}: cad {cid} input must be one of {CAD_INPUTS}")
        fe = tuple(c.get("frontends", CAD_FRONTENDS))
        for f in fe:
            if f not in CAD_FRONTENDS:
                raise ManifestError(f"{pid}: cad {cid} names an unknown front end {f!r}")
        cads.append(CadCommand(id=cid, label=_need(c, "label", str, f"cad {cid}", pid),
                               tooltip=_need(c, "tooltip", str, f"cad {cid}", pid), input=inp, frontends=fe))
    return Manifest(
        id=pid, name=data["name"], version=data["version"], status=data["status"], summary=data["summary"],
        entry=data["entry"], kind=kind, requires=requires, inputs=inputs, output=output,
        reject=io.get("reject", ""),
        mesh_preview=bool(io.get("mesh_preview", True)), preview=bool(io.get("preview", True)),
        chain=bool(io.get("chain", False)),
        raw_params=bool(io.get("raw_params", False)),
        cli_command=cli.get("command", ""), cli_hooks=cli.get("hooks", ""),
        web_tab=web.get("tab", ""), web_title=web.get("title", ""), web_order=int(web.get("order", 100)),
        web_fragment=web.get("fragment", ""), web_script=web.get("script", ""), web_style=web.get("style", ""),
        web_routes=web.get("routes", ""), cad=tuple(cads), params=tuple(params), folder=folder,
        package=package, raw=data)


def load(path, package=""):
    folder = os.path.dirname(os.path.abspath(path))
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as e:
        raise ManifestError(f"{os.path.basename(folder)}: manifest.toml is not valid TOML ({e})")
    return parse(data, folder=folder, package=package)
