# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""
The command line of the Twoo STEP CAD Toolbox.

  tostep                              opens the app in your web browser
  tostep part.stl                     converts to part.step (same as: tostep convert part.stl)
  tostep convert part.stl --preset quick
  tostep wash old.step                jetwash: clean junk out of a STEP file
  tostep scad model.scad -D width=40  OpenSCAD model -> solid STEP (add --watch for a live link)
  tostep image photo.jpg --part-above-token 5 --thickness 5
  tostep hardpoints part.stl          what a hardpoint STL contains
  tostep diag S2S1-...                read a diagnostic code back (or a screenshot / photo showing its QR)
  tostep plugins                      what is installed, and anything broken
  tostep install [freecad] [fusion] [menu] [files]

Which commands exist comes from the plugins (their manifests). This file knows no plugin by name
except that a bare file name means "convert it", which is how the tool has always worked.

WORK IN PROGRESS: MANY FEATURES MAY BE BROKEN
"""
from __future__ import annotations

import argparse
import os
import sys

MIN_PY, MAX_PY = (3, 11), (3, 14)
BARE_FILE_COMMAND = "convert"


def _early_profile(argv):
    """--profile potato|moderate|extreme|auto must act before NumPy/SciPy load (thread counts)."""
    for i, a in enumerate(argv):
        val = a.split("=", 1)[1] if a.startswith("--profile=") else (argv[i + 1] if a == "--profile" and i + 1 < len(argv) else None)
        if val:
            try:
                from tostep.core import perf
                st = perf.settings(val, gpu_wanted="--no-gpu" not in argv)
                os.environ.update(perf.worker_env(st, {}))
                perf.apply_nice()
                print(f"Performance profile: {st['name']} ({st['threads']} thread(s)"
                      + (", GPU on" if st["gpu"] else "") + ")", file=sys.stderr)
            except Exception as e:  # noqa
                print(f"(profile ignored: {e})", file=sys.stderr)
            return


def _version_text():
    from tostep import version
    return (f"{version.NAME} {version.__version__}\nWORK IN PROGRESS: MANY FEATURES MAY BE BROKEN\n"
            "Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)\n"
            "Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.")


def _overview(reg):
    lines = [_version_text(), "", "Commands:", "  (none)      open the app in your web browser"]
    for p in reg.usable():
        if p.m.cli_command:
            hooks = p.module(p.m.cli_hooks) if p.m.cli_hooks else None
            lines.append("  %-11s %s" % (p.m.cli_command, getattr(hooks, "CLI_HELP", p.m.summary)))
    lines += ["  gpu         list the graphics cards, install support for one, or test it (gpu --help)",
              "  diag        read a diagnostic code (or a screenshot of its QR) back",
              "  plugins     what is installed, and anything broken",
              "  install     put the FreeCAD / Fusion plug-ins, menu entry or right-click entries in place",
              "", "A file name on its own means: convert it (tostep part.stl).",
              "Each command has its own --help, e.g.  tostep convert --help"]
    return "\n".join(lines)


def run_plugin_command(plugin, argv):
    m = plugin.m
    hooks = plugin.module(m.cli_hooks) if m.cli_hooks else None
    if hooks is not None and hasattr(hooks, "main"):
        return hooks.main(argv)
    from tostep.core import cliutil, params as _params
    from tostep.core.job import Job, scale_for
    ap = argparse.ArgumentParser(prog=f"tostep {m.cli_command}",
                                 description=getattr(hooks, "CLI_HELP", m.summary))
    ap.add_argument("input")
    ap.add_argument("output", nargs="?")
    if hooks is not None and hasattr(hooks, "add_arguments"):
        hooks.add_arguments(ap)
    ap.add_argument("--pause", action="store_true", help="wait for Enter before closing (for file-manager use)")
    ap.add_argument("--machine", action="store_true", help=argparse.SUPPRESS)
    if hooks is not None and hasattr(hooks, "defaults_for"):
        d = hooks.defaults_for(argv)
        if d:
            ap.set_defaults(**d)                      # explicit options still win
    a = ap.parse_args(argv)
    if a.pause:
        import atexit
        atexit.register(cliutil.pause)
    out = a.output or m.output.format(stem=os.path.splitext(a.input)[0])
    raw = hooks.params_from_args(a, out) if hooks is not None and hasattr(hooks, "params_from_args") else {}
    p = dict(raw) if m.raw_params else _params.coerce(m, raw)
    lines = []

    def log(s):
        lines.append(str(s))
        print(s, flush=True)

    def go():
        job = Job(plugin=m.id, kind=m.kind, src=a.input, out=out, params=p, scale=scale_for(p.get("units", "mm")),
                  log=log, progress=cliutil.machine_progress(a.machine) or (lambda f: None))
        rep = plugin.run_function()(job)
        if hooks is not None and hasattr(hooks, "check"):
            hooks.check(rep)
        print(f"\nSaved {out}", flush=True)
        if hooks is not None and hasattr(hooks, "after_run"):
            hooks.after_run(a, job, rep, lines, a.machine)
        return out
    return cliutil.run_machine_safe(a.machine, go)


def run_diag_cli(argv):
    """Read a diagnostic code back: the code itself, or a screenshot / photo showing its QR."""
    ap = argparse.ArgumentParser(prog="tostep diag",
                                 description="Read back the diagnostic code of a job (the QR in the app's preview "
                                             "window): which settings were used and what came out.")
    ap.add_argument("what", help="the code (S2S1-...), or a picture showing the QR (screenshot, phone photo)")
    a = ap.parse_args(argv)
    from tostep.core import diagcode
    codes = []
    if os.path.isfile(a.what):
        try:
            codes = diagcode.read_image(a.what)
        except ValueError as e:
            print("ERROR:", e)
            return 2
        if not codes:
            print("No diagnostic QR could be read in that picture. A sharper or larger screenshot "
                  "helps; or type the code shown under the QR.")
            return 2
    else:
        codes = [a.what]
    rc = 0
    for code in codes:
        try:
            lines = diagcode.text(code)
        except ValueError as e:
            print("ERROR:", e)
            rc = 2
            continue
        print(code if len(codes) == 1 and not os.path.isfile(a.what) else "Code read: " + code)
        for line in lines:
            print("  " + line)
        if any(line.startswith("CHECK FAILED") for line in lines):
            rc = 1
    return rc


def run_gpu_cli(argv):
    ap = argparse.ArgumentParser(prog="tostep gpu", description=
        "Graphics cards: which the computer has, whether the optional GPU pack can use them, and a real test. "
        "The card speeds up one step only (the v2 fitter's shape scoring, used by the Fine preset and Second pass).")
    ap.add_argument("--install", action="store_true", help="install the right PyTorch build for the card (large download)")
    ap.add_argument("--test", action="store_true", help="run a real test on the card and time it against the processor")
    ap.add_argument("--card", default=None, help="install for the card with exactly this name (default: the first usable one)")
    a = ap.parse_args(argv)
    from tostep.core import perf
    import subprocess
    cards = perf.gpu_cards(refresh=True)
    if not cards:
        print("The operating system did not list any graphics card (Linux needs the lspci tool; Windows needs PowerShell).")
    for c in cards:
        state = {"ready": "READY", "install": "needs the GPU pack", "fix": "pack installed but does not work for it",
                 "no": "not usable"}[c["state"]]
        print(f"- {c['name']}  [{state}]")
        print(f"    {c.get('why', '')}")
        if c.get("detail"):
            print(f"    last test: {c['detail']}")
    if a.install:
        steps, note, card = perf.gpu_pack_steps(a.card)
        if not steps:
            print(note)
            return 1
        print(f"\nInstalling for {card['name']} ({note})")
        for cmd in steps:
            print("$ " + " ".join(cmd))
            if subprocess.call(cmd, env=dict(os.environ, PYTHONNOUSERSITE="1")) != 0:
                print("Failed; nothing else was changed.")
                return 1
        print(perf.gpu_pack_check())
        return 0
    if a.test:
        r = perf.gpu_test()
        print("\n" + perf.gpu_test_text(r))
        return 0 if r.get("ok") else 1
    if not any(c["state"] == "ready" for c in cards) and any(c.get("plan") for c in cards):
        print("\nTo set up: tostep gpu --install    To check afterwards: tostep gpu --test")
    return 0


def run_plugins_cli(argv):
    from tostep.core import registry
    reg = registry.discover(fresh=True)
    print("Plugins:")
    for p in sorted(reg.plugins.values(), key=lambda p: p.id):
        m = p.m
        flags = [m.status] + (["built in"] if p.builtin else ["from TOSTEP_PLUGIN_PATH"])
        print(f"  {m.id:<10} {m.version}  {m.name}  [{', '.join(flags)}]")
        print(f"             {m.summary}")
        if p.problem:
            print(f"             CANNOT BE USED: {p.problem}")
    for name, msg in reg.errors:
        print(f"  (skipped {name}: {msg})")
    return 1 if reg.errors or any(p.problem for p in reg.plugins.values()) else 0


def run_ui(argv):
    ap = argparse.ArgumentParser(prog="tostep ui", description="Open the app in your web browser.")
    ap.add_argument("--port", type=int, default=0, help="port to use (default: any free one)")
    ap.add_argument("--no-browser", action="store_true", help="don't open the browser automatically")
    ap.add_argument("--open", default="", metavar="FILE", help="open this .scad/.csg on the SCAD → STEP tab")
    a = ap.parse_args(argv)
    from tostep.frontends.web import server
    server.serve(port=a.port, open_browser=not a.no_browser, open_path=a.open)
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    _early_profile(argv)
    if not (MIN_PY <= sys.version_info[:2] <= MAX_PY):
        sys.exit(f"This app needs Python {MIN_PY[0]}.{MIN_PY[1]}–{MAX_PY[0]}.{MAX_PY[1]} "
                 f"(the CAD library has no builds for {sys.version.split()[0]}).\n"
                 "Use the start script for your OS — it picks a suitable Python for you.")
    if argv and argv[0] in ("--version", "-V"):
        print(_version_text())
        return 0
    from tostep.core import registry
    reg = registry.discover()
    if not argv or argv[0] == "ui":
        return run_ui(argv[1:])
    if argv[0] in ("-h", "--help", "help"):
        print(_overview(reg))
        return 0
    cmd = argv[0]
    if cmd == "diag":
        return run_diag_cli(argv[1:])
    if cmd == "plugins":
        return run_plugins_cli(argv[1:])
    if cmd == "gpu":
        return run_gpu_cli(argv[1:])
    if cmd == "install":
        from tostep.frontends import install
        return install.run_cli(argv[1:])
    plugin = reg.for_command(cmd)
    if plugin is not None:
        return run_plugin_command(plugin, argv[1:])
    if os.path.exists(cmd) or cmd.startswith("-") or "." in cmd or os.sep in cmd:
        plugin = reg.for_command(BARE_FILE_COMMAND)
        if plugin is None:
            sys.exit("The converter plugin is not available (try: tostep plugins).")
        return run_plugin_command(plugin, argv)
    sys.exit(f"Unknown command '{cmd}'. Try: tostep --help")


if __name__ == "__main__":
    sys.exit(main())
