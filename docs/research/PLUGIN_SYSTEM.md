# Drop-in plug-ins: why pluggy, how it is put together, what was tested

**WORK IN PROGRESS: MANY FEATURES MAY BE BROKEN** (STL2STEP 0.6.0-wip, 8 October 2026)

> **RESUMED (EVERYTHING to solid 0.9.0-wip, 14 October 2026)** with the hand-off kit made in the drop-in chat;
> see *Resumed in 0.9.0* at the end. The two 0.6 failures are designed out: the built-in ImageToSketch stays and a
> drop-in tracer can only stand in for it, and drop-ins live in `app/dropins/`. The three dependency to-dos below
> are still open.
>
> **PAUSED (STL2STEP 0.7.0-wip, 8 October 2026).** The drop-in system was taken out of the app again: the Image →
> STEP tracer is built into `app/imagetosketch` as before. What happened: the app folder alone was copied into an
> existing install, so the `dropins/` folder beside it was missing and the Image tab had no tracer. These notes and
> the code are kept for when it comes back: the loader (`plugkit.py`) and the host pattern are written out in full in
> [docs/skills/dropin-plugins/SKILL.md](../skills/dropin-plugins/SKILL.md). Paths below (`app/plugkit.py`,
> `app/hooks.py`, `dropins/`, `tools/dropin_template`, `tests/test_dropins.py`) are as they were in 0.6.1-wip.
>
> **To do before it comes back** (asked for on 8 October 2026):
> - each plug-in asks the main app to download its dependencies (no plug-in installs anything itself);
> - the app and all plug-ins share one common dependency folder;
> - when a plug-in is deleted, the app asks whether it may delete that plug-in's dependencies, and offers only those
>   that nothing else uses;
> - plug-ins must live inside the folder people actually copy (e.g. under `app/`), so copying `app` alone
>   cannot leave them behind.

*Working notes from building the drop-in plug-ins of 0.6.0-wip. Like the other notes of section 12–13 of the
[research README](README.md), these record what was decided on this project and why, not a literature survey. What
the libraries below do is described as of mid-2026 from their own documentation; check their pages before relying on
a detail.*

## The ask

> Can we develop / adopt an existing plugin system so we can independently update plugins and just drop them into
> hooks on main apps, then make the plugin system a skill.

Read as four requirements:

1. **Independently updated.** A new ImageToSketch must reach STL2STEP users without a new STL2STEP, and the other
   way round.
2. **Drop in.** No pip, no build step, no editing of the app: a folder or a zip, put in one place.
3. **Hooks on main apps.** The app says where add-ons may plug in; add-ons do not reach into the app's insides.
   "Apps" is plural: the same mechanism should serve STL2STEP and later other apps (ImageToSketch itself,
   SCAD2STEP).
4. **Reusable as a skill.** The pattern written down so that it can be applied to the next app the same way.

And the project's standing rules: low-end computers, never crash, no GPL/AGPL code absorbed, honest about what
was not tested, everything packed in one zip that works in GitHub Desktop.

## Options considered

| Option | What it is | Fit |
|---|---|---|
| **Python entry points** (`importlib.metadata.entry_points`) | Plug-ins are installed packages that declare a named group in their metadata | Standard, but a plug-in has to be *pip-installed* into the app's Python. STL2STEP's runtime is private and rebuilt from `requirements.txt` by the start file, so a user-installed package would vanish on the next rebuild. Fails "drop in". |
| **stevedore** (OpenStack, Apache-2.0) | Managers (driver, extension, hook, named) on top of entry points | Same installation model as entry points, plus a dependency chain the app does not need. |
| **Yapsy** (BSD) | Scans folders for `.yapsy-plugin` info files, loads classes derived from a base class | Closest to "drop in", but plug-ins subclass app classes (tight coupling), there is no notion of hook versions, and the project has seen little activity in recent years. |
| **pluginbase** (BSD) | Loads modules from folders into isolated namespaces | Solves import isolation only; hooks, calling, versions and updating would all be ours. Also little recent activity. |
| **Hand-rolled** (a dict of callbacks) | — | Easy to start, but call-order, signature checking, "did you misspell a hook" and first-result semantics all have to be invented and tested. |
| **pluggy** (MIT, pytest-dev) | Hook *specifications* declared by the host, *implementations* marked by plug-ins, a `PluginManager` that validates and calls them | The hook model asked for. Used by pytest, tox, Datasette, conda and others, so it is well exercised. Pure Python, no dependencies, small. Does **not** discover, version, install or update anything: that is left to the host. |

**Chosen: pluggy for the hooks, plus a small loader of our own (`app/plugkit.py`) for the drop-in part.** The
loader is about 500 lines with comments and does only what pluggy leaves out: find folders, read and check a
manifest, decide whether a plug-in may load here, import it safely, install/update/roll back from a zip, remember
on/off. Nothing in it knows about STL2STEP, so the next app copies it unchanged.

Licence: pluggy is MIT, compatible with distributing alongside PolyForm-licensed code; it is installed by the start
file from `app/requirements.txt` like the other libraries, and since 0.6.1-wip an unchanged copy (pluggy 1.6.0 with
its MIT licence file) also ships in `app/_vendor`, used only when the installed one is missing (see *After the first
report* below). It is listed in
`THIRD_PARTY.md`. No GPL/AGPL code was read or absorbed.

## Design

### The two files a host app owns

- `plugkit.py` — generic, identical in every app.
- `hooks.py` — the app's own: its name (`"stl2step"`), the hook version `API`, the drop-in folder, and a class of
  pluggy hook specifications whose docstrings are the contract.

### A drop-in

```
dropins/<id>/
    plugin.json     id, name, version, entry, hosts, [python, requires, description, author, licence, homepage]
    plugin.py       functions marked with pluggy.HookimplMarker("<host name>")
    ...             anything else; the folder is put first on the import path
```

`hosts` maps a host name to the range of that host's hook versions the drop-in was written for:
`{"stl2step": [1, 1]}`. One drop-in can name several hosts (the ImageToSketch drop-in could serve both STL2STEP and
a future SCAD2STEP), and the host picks its own entry. This is the same idea as the `engines` field of a VS Code
extension, reduced to whole numbers because a hook version is a promise, not a release number.

### Rules for hook versions

- Adding a hook does **not** raise `API`: old drop-ins simply do not implement it.
- Adding an optional argument a drop-in may ignore does not raise it either (pluggy only passes the arguments an
  implementation names).
- Changing what a hook is given, or what it must return, **raises** `API`. Drop-ins whose range does not include
  the new number are set aside with a message ("made for stl2step hook version 1; this stl2step offers version 2")
  instead of failing in the middle of a job.

### Loading

For each folder (names starting with `.` or `__` skipped): read the manifest, or record an error (including the
common mistake of a zip unpacked one folder too deep, which gets its own message). Two folders with one id: the
newer version loads, the other is *shadowed*. Then, in order, refuse if: wrong host, hook version out of range,
Python too old, a module in `requires` not importable, switched off in `state.json`. Import the entry module as
`dropin_<id>` with the folder at the front of `sys.path`, register it with pluggy under its id, and call
`check_pending()`: an implementation of a hook the host does not offer is an error, so a misspelt hook is reported
rather than silently never called. Every failure is caught per drop-in; one bad drop-in cannot stop the app or the
others.

### Calling

pluggy's own `hook.name(...)` stops at the first exception. The host instead walks `get_hookimpls()` itself
(pluggy's call order: last registered first) and calls each implementation in its own `try`, recording errors
against that drop-in (shown on the Plugins tab, last five kept). `first()` gives the first non-`None` answer for
`firstresult` hooks. Requests from a drop-in's own tab (`/api/x/<id>/<path>`) are routed to that drop-in's
implementation only, so one drop-in cannot answer another's requests.

### Installing, updating, rolling back

A zip is unpacked into `dropins/.incoming-*` after checks: no absolute paths, no `..`, no drive letters, no
symbolic links, at most 300 MB unpacked. The manifest is at the top or in one folder at the top. It must be usable
here (an incompatible drop-in is refused at install time, not discovered later). It must be newer than the
installed copy unless a downgrade is asked for (pre-releases sort before their release: `0.9.0-wip` < `0.9.0`).
The replaced copy moves to `.previous/<id>/<version>/` with a sequence number; two are kept. *Roll back* undoes
the latest change (the most recently kept copy, not the highest version), and also brings back a removed drop-in.

### Why a restart, and how it is made painless

Python cannot unload a module cleanly (other modules, threads and objects keep references), so changes take effect
at the next load. Two things soften that: conversions run in a fresh worker process per job, which loads the
drop-ins afresh, so a new tracer is used by the very next conversion; and the page's *Restart now* (Linux, macOS)
waits until no job is running, then the server re-executes itself on the same port with the same session key
(passed through the environment, removed from it on start), and the page reloads itself. Windows has no `exec`
that keeps the console, so there the user closes and restarts the app; the page says so.

### Security

A drop-in is code with the user's rights; there is no sandbox (a real one would need a separate process with
restricted rights per drop-in, which is far beyond this app and would break the tracer's in-process speed). What
the app does: path checks on zips, manifest validation, the session token for every install/switch/restart, no
network fetching. `SECURITY.md` says plainly to install only trusted drop-ins.

## The first drop-in: ImageToSketch

The tracing library used to be copied into `app/imagetosketch`. It now lives in `dropins/imagetosketch/` with a
four-line `plugin.py` that answers `stl2step_image_tracer()` with an object carrying `service` (the library's front
door), `version` and `describe_step`. `app/image2step.py` asks the hook for it, so STL2STEP no longer imports
ImageToSketch by name anywhere. `tools/sync_imagetosketch.py CHECKOUT --zip out.zip` refreshes the folder from an
ImageToSketch checkout and packs it; the ImageToSketch project can ship that zip with each release, and users drop it
on the Plugins tab.

This build carries ImageToSketch **0.9.0**, whose new `weld.py` makes neighbouring colour areas share one curve per
edge they share (*Neighbours meet exactly*, on by default; `--no-weld`).

## Tests (tests/test_dropins.py, 16 tests)

- Loading: a working drop-in; one that raises in a hook, one with a syntax error, one with a misspelt hook, one that
  fails on import — the good one still answers and each bad one carries its message; drop-ins for a later hook
  version, for another app, needing a missing module, needing a future Python, switched off; bad manifests
  (unreadable JSON, bad id, entry outside the folder, zip unpacked one level too deep); two copies, newer wins;
  version ordering.
- Installing: install, update, refusal of same/older, downgrade on request, rollback undoing the latest change,
  rollback to a named version, number of kept copies, remove and bring back; manifest at the zip's top; refusal of
  zip-slip, absolute paths, symbolic links, no manifest, incompatible, not a zip — and that nothing was written;
  pack leaves out `__pycache__`.
- The shipped drop-ins: ImageToSketch loads and answers; with it switched off the Image tab's tracer call fails with
  the plain message; the template loads with its four hooks.
- Server: the Plugins API lists drop-ins; installing the template by upload; the same zip again and a non-zip
  refused; after a (simulated) restart the template's tab is in the page, its `/api/x/hello-dropin/...` requests are
  answered (400 and 404 cases too, and another drop-in's id gives 404); the job-finished hook is told; switch off,
  rollback with nothing kept refused, remove, roll back; every plug-in endpoint refuses requests without the
  session key.
- Command line: `dropins pack`, `install`, `list`, the template's own `hello` command, `off`, refusal of a same-version
  install, rollback of an unknown id.

## After the first report (0.6.1-wip)

The first real run: STL → STEP converted perfectly, but the Image → STEP tab said *"needs a picture tracer plug-in
(the ImageToSketch drop-in), and none is loaded"*. Reproduced here exactly by running 0.6.0-wip on a Python without
pluggy: STL → STEP needs no pluggy, so it worked; the loader could load no drop-in, and the tab's message did not
say why. (How pluggy came to be missing on that computer is not known; the start file installs it whenever
`app/requirements.txt` changes.) Fixed twice over:

- An unchanged copy of pluggy 1.6.0 (MIT, 88 kB, its licence file beside it) ships in `app/_vendor`. `hooks.py` adds
  it to the **end** of the import path only when `import pluggy` fails, so an installed pluggy always wins, and
  drop-ins that `import pluggy` get the same copy. Test: `WithoutInstalledPluggy` hides the installed pluggy and
  checks the tracer loads from the copy (and fails without it).
- The Image tab's message now ends with the actual reason, read from the loader: pluggy missing, no
  `imagetosketch` folder, switched off, not usable here (and why), broken (and the error), or loaded but failing
  (and the last error).
- The ImageToSketch drop-in makes sure `import imagetosketch` finds its own copy, even if an old
  `app/imagetosketch` from 0.5.x is still there after unzipping a new version over an old folder.

## Not tested

- *Restart now* end to end in a real browser session was checked on Linux only; macOS untested; Windows has no
  restart button by design.
- Drop-ins on Windows at all (paths, file locking while replacing a folder that is in use are the likely trouble
  spots: Windows may refuse to move a folder whose files are open).
- Two drop-ins that bring different versions of the same Python package: whichever folder is first on the import
  path wins for both. Documented limit; the fix (vendoring under a private name) is the drop-in author's.
- Malicious drop-ins: nothing stops one, by design (see Security).


## Resumed in 0.9.0 (14 October 2026)

The system came back as a hand-off kit (`stl2step-dropins-kit`, made for 0.7.2-wip in the drop-in chat) and was
applied by hand to 0.8.0-wip (the app had been renamed and its page changed, so the kit's patch did not apply as a
file; the same edits were made at the same places). What it changed, and why:

* **The built-in tracer stays the default.** A loaded drop-in that answers `stl2step_image_tracer` is used instead;
  switched off, removed or broken, the built-in ImageToSketch is used again. The Image tab's log names the tracer:
  *Tracing the picture with ImageToSketch 0.9.1 (built in)…*. The 0.6 failure (an app-only copy lost the tab) cannot
  happen.
* **`app/dropins/`, not a `dropins/` folder beside `app/`**, because people copy `app`.
* **pluggy bundled** in `app/_vendor/pluggy` (1.6.0, byte-identical to the released wheel, MIT licence file beside
  it), used only when `import pluggy` fails. Nothing to install, nothing downloaded.
* **The loader is dropin-kit's `plugkit.py`, unchanged** (semantic-version ordering of pre-releases, text-only
  manifest fields, drive-letter entries refused). Fixes go to dropin-kit first, then the file is copied again.
* **All drop-in knowledge in `app/dropin_glue.py`.** `webui.py` gained about 20 lines (an import, the page markers,
  the two request hooks, the job-finished call); taking the system out again means removing those and the new files.
* **No restart button.** Changes wait for the next start; a yellow *Waiting for a restart* box lists them.
* **Hook names and the host name stay `stl2step`** although the app is now called EVERYTHING to solid, so a drop-in
  written for 0.6 still loads (API 1).

Checked here, on Linux: the kit's 14 tests (one adjusted so a hand-set `PYTHONPATH` cannot hide the bundled-pluggy
case), the full suite, and the Plugins card in headless Chromium (see the CHANGELOG). Still open: the three
dependency to-dos at the top of this note.
