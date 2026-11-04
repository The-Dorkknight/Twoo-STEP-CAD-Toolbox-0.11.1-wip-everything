---
name: dropin-plugins
description: Add drop-in plug-ins (independently updated add-ons that plug into declared hooks) to a Python app, or build, pack and test a drop-in for an app that has them, e.g. STL2STEP.
---

# Drop-in plug-ins (pluggy + plugkit)

> **Resumed (14 Oct 2026)** in EVERYTHING to solid 0.9.0-wip via dropin-kit's `plugkit.py` + `dropin_glue.py`, drop-ins in
> `app/dropins/`, pluggy bundled in `app/_vendor`, built-in features kept as the fallback. The three dependency to-dos
> below are **still open**.
>
> **Paused (8 Oct 2026).** Taken out of STL2STEP 0.7.0-wip. Before using this again, add: plug-ins ask the main app
> to download their dependencies; one dependency folder shared by the app and all plug-ins; on deleting a plug-in the
> app asks whether to delete its dependencies, offering only ones nothing else uses; keep plug-ins inside the folder
> users copy (copying `app/` alone left `dropins/` behind and broke the Image tab).

A pattern for Python apps whose add-ons are **updated on their own**: the user drops a folder or zip into
the app's `dropins/` folder (or onto its Plugins page), and it plugs into **hooks the app declares**. Built
for Matthew Armstrong's apps (STL2STEP first; ImageToSketch is its first drop-in) but app-neutral.

Use this skill when asked to: add a plug-in / add-on / extension system to an app; make part of an app
separately updatable; write, pack, update or debug a drop-in; add a hook to an app that already has drop-ins.

## The decision already made (don't re-litigate unless asked)

- **Hooks: pluggy** (MIT, pytest-dev; pure Python, no dependencies). The host declares hook *specs*,
  drop-ins mark *impls* with `pluggy.HookimplMarker("<host name>")`.
- **Drop-in handling: `plugkit.py`** (below, ~500 lines, app-neutral; copy it unchanged into each app).
  pluggy does no discovery, versioning, install or update; plugkit does.
- Rejected: entry points / stevedore (need pip-installing into the app's Python, which private runtimes
  rebuild), Yapsy / pluginbase (subclass coupling or import-isolation only; little recent activity),
  hand-rolled registries (re-invent call order, signature checks, typo detection).
- If the app is in a project with licence rules (e.g. PolyForm Noncommercial, no GPL/AGPL absorbed): pluggy is
  MIT, installed from requirements, listed in THIRD_PARTY.md, never copied in.

## Layout

```
app/plugkit.py        generic loader (copy verbatim, below)
app/hooks.py          the app's own: NAME, API, FOLDER, hookspecs, host() singleton
dropins/<id>/         one folder per drop-in
    plugin.json       manifest
    plugin.py         @hookimpl functions (the "entry")
    <anything>/       its own packages; the folder goes first on sys.path
dropins/state.json    which are switched off            (gitignore)
dropins/.previous/    replaced/removed copies, 2 kept   (gitignore)
dropins/.incoming-*   temporary unpack folders          (gitignore)
tools/dropin_template a working example drop-in to copy
```

## Manifest (`plugin.json`)

```json
{
  "id": "hello-dropin",                    // required; ^[a-z0-9][a-z0-9_-]{1,47}$ ; also the URL/CLI key
  "name": "Hello drop-in",                 // required
  "version": "0.1.0",                      // required; X.Y.Z with optional -wip/-beta/-rc (pre-release < release)
  "entry": "plugin.py",                    // required; .py inside the folder
  "hosts": {"stl2step": [1, 1]},           // required; host name -> [lowest, highest] hook API version
  "python": ">=3.9",                       // optional; only ">=X.Y" is checked
  "requires": ["cv2", "numpy"],            // optional; importable MODULE names that must already exist
  "description": "...", "author": "...", "licence": "...", "homepage": "..."
}
```
(JSON has no comments — the `//` notes above are explanation only.)

## Hook-version rules (the contract)

- `API` in hooks.py is an integer. **Adding** a hook, or an optional argument impls may ignore, keeps it.
- **Changing** what a hook receives or must return raises it. Drop-ins whose `hosts` range excludes the new
  number are set aside with a plain message instead of failing mid-job.
- Every hookspec's docstring is the contract: arguments, return shape, firstresult or not, what None means.
- Prefer returning plain data (dicts, lists, small objects with documented attributes) over app classes.

## Adding drop-ins to an app (host side)

1. `pip`/requirements: `pluggy>=1.3`, **and** ship an unchanged copy of pluggy (with its MIT LICENSE) in
   `app/_vendor/pluggy`, appended to `sys.path` in hooks.py only if `import pluggy` fails (learned the hard way:
   STL2STEP 0.6.0 lost its whole Image tab on a machine where pluggy wasn't installed). Copy `plugkit.py` unchanged.
   When a feature finds no plug-in, its error must say WHY (host.problem, folder missing, off, incompatible, broken,
   last hook error) — a generic "none is loaded" can't be diagnosed from a screenshot.
2. Write `hooks.py`:

```python
import os
NAME = "myapp"          # pluggy project name AND the key in manifests' "hosts"
API = 1
FOLDER = os.environ.get("MYAPP_DROPINS") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dropins")
try:
    import pluggy
except ImportError:                     # fall back to the bundled copy; installed one always wins
    import sys
    sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "_vendor"))
    try:
        import pluggy
    except ImportError:
        pluggy = None
if pluggy is not None:
    hookspec = pluggy.HookspecMarker(NAME)
else:                                   # app must still start without pluggy (plugkit reports it)
    def hookspec(function=None, **_):
        return function if function is not None else (lambda f: f)

class Specs:
    @hookspec(firstresult=True)
    def myapp_tracer(self):
        """Return an object with ... or None. First answer wins."""
    @hookspec
    def myapp_tabs(self):
        """[{"id", "title", "html", "js"}] extra page tabs."""
    @hookspec(firstresult=True)
    def myapp_api(self, path, body, app):
        """Answer /api/x/<id>/<path>; None = 404; raise ValueError for 400."""
    @hookspec
    def myapp_cli(self):
        """{"name": (help, fn(argv) -> exit code)}"""
    @hookspec
    def myapp_job_finished(self, job):
        """Plain dict about a finished job. Return ignored."""

_host = None
def host():
    global _host
    if _host is None:
        import plugkit
        _host = plugkit.Host(NAME, API, FOLDER, Specs)
        _host.load()
    return _host
def reset():
    global _host
    _host = None
```

3. Call hooks only through the host: `hooks.host().first("myapp_tracer")`,
   `hooks.host().call("myapp_tabs")` → `[(plugin_id, result), ...]`. Never call `pm.hook.x()` directly:
   plugkit's call isolates exceptions per drop-in (and SystemExit), records them for display, and skips None.
4. Replace direct imports of the add-on with the hook; give a plain error when nothing answers
   ("The X tracer is missing: install or switch on the X drop-in on the Plugins tab").
5. Web UI (if any) — endpoints, all behind the app's session token:
   `GET /api/plugins` (host.report() + pending changes + can_restart + busy);
   `POST /api/plugins/install` (raw zip body, `?downgrade=1`), `/enable {id,on}`, `/remove {id}`,
   `/rollback {id[,version]}`; `GET|POST /api/x/<id>/<path>` routed to **only that drop-in's** impl
   (pick from `pm.hook.myapp_api.get_hookimpls()` by `plugin_name`); `POST /api/restart`.
   Page: a drop zone + list (name, version, status chip, message, hooks, Switch on/off, Roll back to X,
   Remove) + a "changes waiting for a restart" box. Inject drop-in tabs server-side: a nav button and a
   `<section data-plugin>` per tab; wrap each tab's js in `try{(function(){...})()}catch(e){console.error(...)}`.
   Make sure the waiting box is actually visible (don't reuse a CSS class that is display:none by default).
6. Restart: Python can't unload modules; changes apply at next load. On POSIX: refuse while jobs run, shut the
   server down, then `os.execv(sys.executable, [sys.executable, *kept_interpreter_flags, script, "ui", "--port",
   str(port), "--no-browser"])` with the session token passed via an env var that the server **pops** on start;
   the page polls `/api/plugins` and reloads. Keep `-E -s -u -I` flags from `sys.orig_argv`. Windows: tell the
   user to quit and restart. Work done in fresh worker processes picks up new drop-ins per job anyway.
7. CLI: `app dropins [list | install ZIP [--downgrade] | on|off|remove|rollback ID | pack FOLDER [ZIP]]`, and
   dispatch unknown bare-word commands to `myapp_cli` results (built-ins always win).
8. Job-finished: call it in the job's `finally`, wrapped so it can never spoil the job.
9. Docs: README section (install/update/rollback/restart/trust), SECURITY.md (drop-ins are code with the
   user's rights; no sandbox; zips are path-checked; nothing downloaded by the app), THIRD_PARTY row for
   pluggy, CHANGELOG, `.gitignore` entries above, a research/design note if the project keeps them.

## Writing a drop-in (plug-in side)

```python
# plugin.py   (keep the project's licence header lines at the top of every source file)
import pluggy
hookimpl = pluggy.HookimplMarker("stl2step")     # the HOST's name
ID = "hello-dropin"                               # = plugin.json id

@hookimpl
def stl2step_tabs():
    return [{"id": "hello", "title": "Hello", "html": "<div class=card>...<button id=hello-go>Ping</button>"
             "<span id=hello-out></span></div>",
             "js": "document.getElementById('hello-go').onclick=async()=>{const r=await api('/api/x/%s/ping',{});"
                   "document.getElementById('hello-out').textContent=r.message}" % ID}]

@hookimpl
def stl2step_api(path, body, app):
    if path == "ping":
        return {"message": "pong"}
    return None                                    # 404

@hookimpl
def stl2step_cli():
    return {"hello": ("say hello", lambda argv: print("hello") or 0)}
```

- Implement only the hooks you need; impl argument names must match the spec's (you may take fewer).
- A misspelt hook name makes the drop-in fail to load with a message (check_pending) — that's intended.
- Ship your own packages inside the folder; list in `requires` only modules the host already provides.
- Unique top-level package names: two drop-ins shipping the same package name share whichever is first on
  sys.path (known limit).
- Pack: `python app/stl2step.py dropins pack my-folder` (or `plugkit.pack(folder, zip)`): zip top folder =
  id, no `__pycache__`, `.pyc`, `.git`, dot-folders.
- Version every release; an install must be newer unless downgrade is asked; pre-release < release.

### STL2STEP hooks (API 1, `app/hooks.py`)

| Hook | Kind | Contract |
|---|---|---|
| `stl2step_image_tracer()` | firstresult | object with `service` (ImageToSketch front door: options, trace_file, export, preview_png, data_url, guess, token_pdf, token_stl, sheet_pdf, number, flag, SCALE_NAMES), `version` (str), `describe_step(report)` |
| `stl2step_tabs()` | all | list of `{"id","title","html","js"}`; ids `[a-z0-9_-]{1,32}`; js may call `api(path, body)` |
| `stl2step_api(path, body, app)` | only the addressed drop-in | for `/api/x/<id>/<path>`; JSON-able return; None → 404; ValueError → 400 |
| `stl2step_cli()` | all | `{"name": (help, fn(argv) -> int)}`; built-in commands win |
| `stl2step_job_finished(job)` | all | dict: id, kind, state, name, saved_to, summary, error, seconds, diag; keep quick |

Test locally: `STL2STEP_DROPINS=/tmp/dz python app/stl2step.py dropins install my.zip && ... dropins list`.
ImageToSketch drop-in from its checkout: `python tools/sync_imagetosketch.py CHECKOUT --zip out.zip`.

## Testing checklist (write these as unittest tests in the host)

- Loader: good drop-in loads and answers; one raising in a hook, syntax error, misspelt hook, failing import,
  `sys.exit()` at import → each `error`/message, others still answer; wrong host, API out of range, Python too
  new, missing `requires`, switched off → `incompatible`/`off`; bad manifests; nested-one-level-too-deep
  folder gets its hint; two copies → newer loads, other `shadowed`; version ordering incl. pre-release.
- Install: new, update, same/older refused, downgrade on request, rollback undoes latest change (incl. after
  remove), rollback to named version, KEEP_PREVIOUS honoured, `.incoming-*` cleaned; zip-slip, absolute path,
  symlink, no manifest, incompatible, not-a-zip all refused **and nothing written**; pack round-trip.
- Host: feature works through the hook; plain error when the providing drop-in is off (point FOLDER at a temp
  copy, `hooks.reset()`).
- Server: list, upload install, refusals, extra tab in page after re-creating the App (simulated restart),
  `/api/x/...` 200/400/404 and cross-drop-in 404, job-finished reaches the drop-in, enable/remove/rollback,
  403 without token.
- CLI: pack, install, list, a drop-in's own command, off, refusals.
- Browser (headless Playwright) once: drop zip → restart box visible → Restart now → page reloads with the new
  tab → its button answered → no page errors.
- Be honest in docs about what wasn't run (Windows file locking when replacing an in-use folder; macOS restart).

## Security stance (say it plainly to users)

A drop-in is ordinary Python with the user's rights; there is no sandbox. The host checks zip paths (no
absolute, `..`, drive letters), refuses symlinks, caps unpacked size (300 MB), validates manifests, requires the
session token for every action, and never downloads drop-ins. "Install only drop-ins from people you trust."

## plugkit.py (reference implementation — copy verbatim into each host app)

Licence header: replace the first two comment lines with the host project's own notice if it differs.

```python
# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""
plugkit — drop-in plug-ins for a Python app, on top of pluggy.

The same file serves every app that wants drop-ins: nothing in it knows about STL2STEP. The app
says what its hooks are (a module of pluggy hookspecs) and where drop-ins live; plugkit finds
them, checks them, loads them, and lets the app call their hooks without one broken plug-in
taking the app down.

    host = Host("stl2step", api=1, folder="dropins", specs=hookspecs)
    host.load()
    tracer = host.first("stl2step_image_tracer")       # first answer, or None
    for plugin_id, tabs in host.call("stl2step_tabs"): ...

A drop-in is a folder (or a zip of one) holding `plugin.json` and the module it names:

    dropins/imagetosketch/
        plugin.json          {"id": "imagetosketch", "version": "0.9.0-wip", "entry": "plugin.py",
                              "hosts": {"stl2step": [1, 1]}, ...}
        plugin.py            @hookimpl functions (pluggy.HookimplMarker("stl2step"))
        imagetosketch/       anything else it brings: its folder is put on the import path

What plugkit does
  * finds every folder in the drop-in folder with a plugin.json, reads and checks the manifest;
  * skips (and says why): switched off, made for another app, made for another version of this
    app's hooks (`hosts` gives the range of host API versions it was written for), needing a
    newer Python, needing a package that is not installed, two copies of one id (the newer wins);
  * imports each one under its own name (`dropin_<id>`), registers it with pluggy, and checks
    that every hook it implements is one the app offers (a typo is an error, not silence);
  * calls hooks one implementation at a time, so an exception in one plug-in is recorded against
    it and the others still run;
  * installs or updates from a zip: checks the zip (no paths outside the folder, no links, a size
    limit), the manifest and the version; the copy it replaces is kept under `.previous/` so it
    can be rolled back; a downgrade needs saying so;
  * remembers which plug-ins are switched off (`state.json` in the drop-in folder).

What it does not do
  * Sandboxing. A drop-in is Python code running with the app's rights: install only ones you
    trust, as you would any program.
  * Unloading at run time. Python cannot forget a module cleanly; switching off, installing or
    updating takes effect when the app next loads its plug-ins (a restart). A host that runs
    work in separate processes (STL2STEP's jobs) sees the new version in the next job anyway.
  * Fetching anything from the internet.

Licence of pluggy (MIT): see THIRD_PARTY.md.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import time
import traceback
import zipfile

MANIFEST = "plugin.json"
STATE = "state.json"
PREVIOUS = ".previous"
KEPT = ".kept"                          # in a kept copy: when it was kept (a sequence number)
KEEP_PREVIOUS = 2                       # older copies kept per plug-in, for rolling back
MAX_ZIP_BYTES = 300 * 1024 * 1024       # unpacked size limit of one drop-in zip
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,47}$")


class DropinError(ValueError):
    """Something about a drop-in, in plain words (bad zip, bad manifest, wrong version ...)."""


# ------------------------------------------------------------------ versions --
def parse_version(text):
    """'0.9.0-wip' -> ((0, 9, 0), 0); '1.2' -> ((1, 2, 0), 1). A pre-release ('-wip', '-beta',
    '-rc1') sorts before the release of the same number."""
    m = re.match(r"^\s*v?(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:[-+.]?(.*))?$", str(text or ""))
    if not m:
        raise DropinError("'%s' is not a version number" % text)
    nums = tuple(int(x or 0) for x in m.groups()[:3])
    return nums, (0 if (m.group(4) or "").strip() else 1)


def newer(a, b):
    """True if version a is newer than version b."""
    return parse_version(a) > parse_version(b)


# ----------------------------------------------------------------- manifests --
REQUIRED = ("id", "name", "version", "entry", "hosts")


def read_manifest(folder):
    """The checked manifest of a drop-in folder (a dict), or DropinError."""
    path = os.path.join(folder, MANIFEST)
    try:
        with open(path, encoding="utf-8") as f:
            m = json.load(f)
    except FileNotFoundError:
        raise DropinError("no %s in %s" % (MANIFEST, os.path.basename(folder)))
    except (ValueError, OSError) as e:
        raise DropinError("%s cannot be read: %s" % (MANIFEST, e))
    if not isinstance(m, dict):
        raise DropinError("%s must hold an object" % MANIFEST)
    missing = [k for k in REQUIRED if not m.get(k)]
    if missing:
        raise DropinError("%s is missing: %s" % (MANIFEST, ", ".join(missing)))
    if not ID_RE.match(str(m["id"])):
        raise DropinError("id '%s': use 2-48 characters, a-z 0-9 - and _, starting with a letter or digit" % m["id"])
    parse_version(m["version"])
    entry = str(m["entry"])
    if os.path.isabs(entry) or ".." in entry.replace("\\", "/").split("/") or not entry.endswith(".py"):
        raise DropinError("entry must be a .py file inside the drop-in folder")
    if not os.path.isfile(os.path.join(folder, entry)):
        raise DropinError("entry %s is not in the drop-in folder" % entry)
    hosts = m["hosts"]
    if not isinstance(hosts, dict) or not all(isinstance(v, list) and len(v) == 2 and all(isinstance(x, int) for x in v)
                                              for v in hosts.values()):
        raise DropinError('hosts must look like {"app-name": [lowest API version, highest API version]}')
    return m


def _python_ok(spec):
    if not spec:
        return True
    m = re.match(r"^\s*>=\s*(\d+)\.(\d+)\s*$", str(spec))
    if not m:
        return True                                   # only ">=X.Y" is understood; anything else is not checked
    return sys.version_info[:2] >= (int(m.group(1)), int(m.group(2)))


def _have(module_name):
    try:
        return importlib.util.find_spec(module_name) is not None
    except (ImportError, ValueError):
        return False


# ----------------------------------------------------------------------- host --
class Dropin:
    """One drop-in as found: where it is, what its manifest says, and what became of it."""

    def __init__(self, folder, manifest=None, status="found", message=""):
        self.folder = folder
        self.manifest = manifest or {}
        self.status = status            # loaded | off | incompatible | error | shadowed | found
        self.message = message
        self.module = None
        self.hooks = []
        self.errors = []                # errors while its hooks were being called (last few)

    @property
    def id(self):
        return self.manifest.get("id") or os.path.basename(self.folder)

    @property
    def version(self):
        return self.manifest.get("version", "?")

    def as_dict(self, host=None):
        m = self.manifest
        return {"id": self.id, "name": m.get("name", self.id), "version": self.version,
                "description": m.get("description", ""), "author": m.get("author", ""),
                "licence": m.get("licence", m.get("license", "")), "homepage": m.get("homepage", ""),
                "status": self.status, "message": self.message, "hooks": list(self.hooks),
                "errors": list(self.errors[-5:]), "folder": self.folder,
                "previous": host.previous_versions(self.id) if host else []}


class Host:
    """The plug-in side of one app.

    name     the app's name as plug-ins know it (the key in their manifest's "hosts"); also the
             pluggy project name, so plug-ins mark their hooks with pluggy.HookimplMarker(name)
    api      the version of this app's hooks. Raise it when a hook changes in a way old
             plug-ins would not understand; add hooks without raising it.
    folder   where drop-ins live (made if missing)
    specs    the module (or class) holding the app's pluggy hookspecs
    """

    def __init__(self, name, api, folder, specs):
        self.name, self.api, self.folder, self.specs = name, int(api), os.path.abspath(folder), specs
        self.plugins = []
        self.pm = None
        self.problem = ""               # why there are no plug-ins at all (pluggy missing ...)
        self.loaded_at = None

    # ---------------------------------------------------------------- state --
    def _state(self):
        try:
            with open(os.path.join(self.folder, STATE), encoding="utf-8") as f:
                s = json.load(f)
            return s if isinstance(s, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_state(self, s):
        os.makedirs(self.folder, exist_ok=True)
        tmp = os.path.join(self.folder, STATE + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(s, f, indent=1, sort_keys=True)
        os.replace(tmp, os.path.join(self.folder, STATE))

    def is_enabled(self, plugin_id):
        return plugin_id not in self._state().get("off", [])

    def set_enabled(self, plugin_id, on):
        s = self._state()
        off = set(s.get("off", []))
        (off.discard if on else off.add)(plugin_id)
        s["off"] = sorted(off)
        self._save_state(s)

    # ------------------------------------------------------------ discovery --
    def discover(self):
        """Every drop-in folder with its manifest checked, but nothing imported."""
        found = []
        if not os.path.isdir(self.folder):
            return found
        for name in sorted(os.listdir(self.folder)):
            path = os.path.join(self.folder, name)
            if name.startswith((".", "__")) or not os.path.isdir(path):
                continue
            try:
                found.append(Dropin(path, read_manifest(path)))
            except DropinError as e:
                msg = str(e)
                inner = [n for n in os.listdir(path) if os.path.isfile(os.path.join(path, n, MANIFEST))]
                if not os.path.isfile(os.path.join(path, MANIFEST)) and len(inner) == 1:
                    msg = "its %s is one folder down: move the folder %s up into %s" % (
                        MANIFEST, inner[0], os.path.basename(self.folder))
                found.append(Dropin(path, {"id": name}, "error", msg))
        # two folders with one id: the newer version is used
        by_id = {}
        for d in found:
            if d.status == "error":
                continue
            best = by_id.get(d.id)
            if best is None or newer(d.version, best.version):
                if best is not None:
                    best.status, best.message = "shadowed", "a newer copy (%s) is used" % d.version
                by_id[d.id] = d
            else:
                d.status, d.message = "shadowed", "a newer copy (%s) is used" % best.version
        return found

    def _check(self, d):
        """Why a drop-in cannot be used here, or ''."""
        m = d.manifest
        rng = m["hosts"].get(self.name)
        if rng is None:
            return "incompatible", "made for %s, not %s" % (", ".join(sorted(m["hosts"])) or "another app", self.name)
        lo, hi = rng
        if not lo <= self.api <= hi:
            return "incompatible", ("made for %s hook version %s%s; this %s offers version %d" % (
                self.name, lo, "" if lo == hi else " to %s" % hi, self.name, self.api))
        if not _python_ok(m.get("python")):
            return "incompatible", "needs Python %s" % m.get("python")
        missing = [r for r in m.get("requires", []) if not _have(str(r).split("[")[0].replace("-", "_"))]
        if missing:
            return "incompatible", "needs the package%s %s, which %s not installed" % (
                "" if len(missing) == 1 else "s", ", ".join(missing), "is" if len(missing) == 1 else "are")
        if not self.is_enabled(d.id):
            return "off", "switched off"
        return "", ""

    # --------------------------------------------------------------- loading --
    def load(self):
        """Find, check, import and register every usable drop-in. Safe to call once per process;
        returns the list of Dropin records (also in self.plugins)."""
        self.plugins = self.discover()
        self.loaded_at = time.time()
        try:
            import pluggy
        except ImportError:
            self.problem = ("the 'pluggy' package is missing, so no plug-ins can be loaded. Start the app "
                            "with its start file once more (online) to add it.")
            for d in self.plugins:
                if d.status == "found":
                    d.status, d.message = "error", "plug-ins cannot load: pluggy is missing"
            return self.plugins
        self.pm = pluggy.PluginManager(self.name)
        self.pm.add_hookspecs(self.specs)
        for d in self.plugins:
            if d.status != "found":
                continue
            status, why = self._check(d)
            if status:
                d.status, d.message = status, why
                continue
            try:
                d.module = self._import(d)
                self.pm.register(d.module, name=d.id)
                try:
                    self.pm.check_pending()
                except Exception:
                    self.pm.unregister(name=d.id)
                    raise
                d.hooks = sorted(h.name for h in self.pm.get_hookcallers(d.module) or [])
                d.status, d.message = "loaded", ""
            except (Exception, SystemExit) as e:      # one broken drop-in never stops the others
                d.status = "error"
                d.message = "%s: %s" % (e.__class__.__name__, str(e).strip().splitlines()[-1] if str(e).strip() else "")
                d.errors.append(traceback.format_exc(limit=3))
        return self.plugins

    def _import(self, d):
        """Import the drop-in's entry module under its own name, with its folder on the path."""
        if d.folder not in sys.path:
            sys.path.insert(0, d.folder)              # its own packages win over anything older
        name = "dropin_" + d.id.replace("-", "_")
        spec = importlib.util.spec_from_file_location(name, os.path.join(d.folder, d.manifest["entry"]))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        try:
            spec.loader.exec_module(mod)
        except BaseException:
            sys.modules.pop(name, None)
            raise
        return mod

    def get(self, plugin_id):
        return next((d for d in self.plugins if d.id == plugin_id and d.status not in ("shadowed",)), None)

    # ---------------------------------------------------------------- calling --
    def _impls(self, hook_name):
        if self.pm is None:
            return []
        caller = getattr(self.pm.hook, hook_name, None)
        if caller is None:
            raise AttributeError("%s has no hook called %s" % (self.name, hook_name))
        return list(reversed(caller.get_hookimpls()))   # pluggy's own calling order

    def call(self, hook_name, only=None, **kwargs):
        """Call a hook on every loaded drop-in (or only on `only`, a plug-in id), one at a time.
        Returns [(plugin id, result)] for the answers that are not None. An exception in one
        drop-in is recorded against it (status stays "loaded", message says what happened) and
        the rest are still called."""
        out = []
        for impl in self._impls(hook_name):
            if only is not None and impl.plugin_name != only:
                continue
            try:
                res = impl.function(**{a: kwargs.get(a) for a in impl.argnames})
            except (Exception, SystemExit) as e:
                d = self.get(impl.plugin_name)
                if d is not None:
                    d.errors.append("%s: %s" % (hook_name, traceback.format_exc(limit=4)))
                    d.message = "last error in %s: %s: %s" % (hook_name, e.__class__.__name__, e)
                continue
            if res is not None:
                out.append((impl.plugin_name, res))
        return out

    def first(self, hook_name, only=None, **kwargs):
        """The first answer that is not None, or None."""
        for impl in self._impls(hook_name):
            if only is not None and impl.plugin_name != only:
                continue
            got = self.call(hook_name, only=impl.plugin_name, **kwargs)
            if got:
                return got[0][1]
        return None

    def report(self):
        """Everything the app's page or command line needs to show the plug-ins."""
        return {"host": self.name, "api": self.api, "folder": self.folder, "problem": self.problem,
                "plugins": [d.as_dict(self) for d in sorted(self.plugins, key=lambda d: d.id)]}

    # ------------------------------------------------- installing and updating --
    def previous_versions(self, plugin_id):
        base = os.path.join(self.folder, PREVIOUS, plugin_id)
        if not os.path.isdir(base):
            return []
        vers = [v for v in os.listdir(base) if os.path.isfile(os.path.join(base, v, MANIFEST))]
        return sorted(vers, key=parse_version, reverse=True)

    def install_zip(self, zip_path, allow_downgrade=False):
        """Install or update a drop-in from a zip (its plugin.json at the top, or in one folder at
        the top). Returns {"id", "version", "replaced": old version or None, "status": what the
        check says about it here}. Takes effect at the next load()."""
        os.makedirs(self.folder, exist_ok=True)
        tmp = tempfile.mkdtemp(prefix=".incoming-", dir=self.folder)
        try:
            self._unpack(zip_path, tmp)
            root = tmp
            if not os.path.isfile(os.path.join(root, MANIFEST)):
                inner = [n for n in os.listdir(tmp) if os.path.isdir(os.path.join(tmp, n)) and not n.startswith("__")]
                if len(inner) == 1 and os.path.isfile(os.path.join(tmp, inner[0], MANIFEST)):
                    root = os.path.join(tmp, inner[0])
                else:
                    raise DropinError("the zip holds no %s at its top (or in one folder at its top)" % MANIFEST)
            m = read_manifest(root)
            probe = Dropin(root, m)
            status, why = self._check(probe)
            if status == "incompatible":
                raise DropinError("%s %s cannot be used here: %s" % (m.get("name", m["id"]), m["version"], why))
            dest = os.path.join(self.folder, m["id"])
            replaced = None
            if os.path.isdir(dest):
                try:
                    old = read_manifest(dest)["version"]
                except DropinError:
                    old = "0.0.0"
                if not newer(m["version"], old) and not allow_downgrade:
                    raise DropinError("%s %s is already installed%s; to put %s in its place, ask for a downgrade" % (
                        m.get("name", m["id"]), old, "" if old == m["version"] else " (newer)", m["version"]))
                self._keep_previous(m["id"], dest, old)
                replaced = old
            shutil.move(root, dest)
            return {"id": m["id"], "version": m["version"], "replaced": replaced, "name": m.get("name", m["id"]),
                    "status": status or "ready"}
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def _unpack(self, zip_path, into):
        try:
            z = zipfile.ZipFile(zip_path)
        except (zipfile.BadZipFile, OSError) as e:
            raise DropinError("not a zip file (%s)" % e)
        with z:
            total = 0
            for info in z.infolist():
                name = info.filename.replace("\\", "/")
                if name.startswith("/") or re.match(r"^[A-Za-z]:", name) or ".." in name.split("/"):
                    raise DropinError("the zip has a path outside its own folder: %s" % info.filename)
                if stat.S_ISLNK(info.external_attr >> 16):
                    raise DropinError("the zip holds a link (%s); drop-ins may not" % info.filename)
                total += info.file_size
                if total > MAX_ZIP_BYTES:
                    raise DropinError("the zip unpacks to more than %d MB" % (MAX_ZIP_BYTES // 2 ** 20))
            z.extractall(into)

    def _keep_previous(self, plugin_id, dest, old_version):
        base = os.path.join(self.folder, PREVIOUS, plugin_id)
        os.makedirs(base, exist_ok=True)
        target = os.path.join(base, old_version)
        if os.path.exists(target):
            shutil.rmtree(target)
        shutil.move(dest, target)
        seq = max([self._kept_seq(plugin_id, v) for v in self.previous_versions(plugin_id)] + [0]) + 1
        with open(os.path.join(target, KEPT), "w") as f:   # order of keeping: rollback undoes the latest change
            f.write(str(seq))
        for v in self._kept_by_age(plugin_id)[KEEP_PREVIOUS:]:
            shutil.rmtree(os.path.join(base, v), ignore_errors=True)

    def _kept_by_age(self, plugin_id):
        """Kept copies, the most recently kept first."""
        return sorted(self.previous_versions(plugin_id), key=lambda v: self._kept_seq(plugin_id, v), reverse=True)

    def _kept_seq(self, plugin_id, version):
        try:
            with open(os.path.join(self.folder, PREVIOUS, plugin_id, version, KEPT)) as f:
                return int(f.read().strip() or 0)
        except (OSError, ValueError):
            return 0

    def rollback(self, plugin_id, version=None):
        """Put back a kept copy: by default the one the latest update or removal replaced (so it
        undoes that), or `version`. The current one is kept in its place. Takes effect at the
        next load()."""
        prev = self._kept_by_age(plugin_id)
        if not prev:
            raise DropinError("no older copy of %s is kept" % plugin_id)
        version = version or prev[0]
        if version not in prev:
            raise DropinError("no copy of %s %s is kept" % (plugin_id, version))
        src = os.path.join(self.folder, PREVIOUS, plugin_id, version)
        dest = os.path.join(self.folder, plugin_id)
        tmp = None
        if os.path.isdir(dest):
            tmp = dest + ".rollback"
            shutil.move(dest, tmp)
        shutil.move(src, dest)
        try:
            os.remove(os.path.join(dest, KEPT))
        except OSError:
            pass
        if tmp:
            try:
                cur = read_manifest(tmp)["version"]
            except DropinError:
                cur = "unknown"
            self._keep_previous(plugin_id, tmp, cur)
        return {"id": plugin_id, "version": version}

    def remove(self, plugin_id):
        """Take a drop-in out (it is kept under .previous, so it can be put back with rollback)."""
        dest = os.path.join(self.folder, plugin_id)
        if not os.path.isdir(dest):
            raise DropinError("%s is not installed" % plugin_id)
        try:
            ver = read_manifest(dest)["version"]
        except DropinError:
            ver = "unknown"
        self._keep_previous(plugin_id, dest, ver)
        return {"id": plugin_id, "version": ver}


def pack(folder, zip_path):
    """Make a drop-in zip from a drop-in folder (the folder itself at the zip's top). Checks the
    manifest first; leaves out __pycache__ and editor clutter."""
    m = read_manifest(folder)
    top = m["id"]
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(folder):
            dirs[:] = sorted(d for d in dirs if d not in ("__pycache__", ".git", ".pytest_cache") and not d.startswith("."))
            for f in sorted(files):
                if f.endswith((".pyc", ".pyo")) or f in (".DS_Store", "Thumbs.db", KEPT):
                    continue
                full = os.path.join(root, f)
                z.write(full, os.path.join(top, os.path.relpath(full, folder)).replace(os.sep, "/"))
    return {"id": m["id"], "version": m["version"], "path": zip_path}
```
