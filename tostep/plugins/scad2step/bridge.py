# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""
scad_bridge — the server side of the "SCAD → STEP" tab.

The conversion itself is scad2step_core.py (the SCAD2STEP engine: OpenSCAD evaluates the model
to a CSG tree, which is rebuilt as real B-rep geometry). This file only holds what the web
interface needs around it: picking a file from disk (so `include`/`use` libraries beside it are
found), finding OpenSCAD, the model's Customizer parameters, and the live link.

scad2step_core imports the CAD library, so it is imported only when something here needs it.
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time

SCAD_EXT = {".scad", ".csg"}


def core():
    from tostep.plugins.scad2step import scad2step_core
    return scad2step_core


# ------------------------------------------------------------------ files
def native_open_dialog(start=""):
    """Ask the OS for a file path with its own file dialog. None if cancelled or unavailable."""
    start = start or os.path.expanduser("~")
    try:
        if sys.platform == "darwin":
            scr = ('POSIX path of (choose file with prompt "Choose an OpenSCAD model" '
                   'of type {"scad", "csg", "public.item"})')
            r = subprocess.run(["osascript", "-e", scr], capture_output=True, text=True, timeout=600)
            return r.stdout.strip() or None
        if sys.platform.startswith("win"):
            ps = ("Add-Type -AssemblyName System.Windows.Forms;"
                  "$d=New-Object System.Windows.Forms.OpenFileDialog;"
                  "$d.Filter='OpenSCAD (*.scad;*.csg)|*.scad;*.csg|All files (*.*)|*.*';"
                  "$d.Title='Choose an OpenSCAD model';"
                  "$f=New-Object System.Windows.Forms.Form;$f.TopMost=$true;"
                  "if($d.ShowDialog($f) -eq 'OK'){Write-Output $d.FileName}")
            r = subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", ps], capture_output=True,
                               text=True, timeout=600, creationflags=0x08000000)
            return r.stdout.strip() or None
        if shutil.which("zenity"):
            r = subprocess.run(["zenity", "--file-selection", "--title=Choose an OpenSCAD model",
                                "--file-filter=OpenSCAD | *.scad *.csg", "--file-filter=All files | *",
                                f"--filename={start.rstrip('/')}/"], capture_output=True, text=True, timeout=600)
            return r.stdout.strip() or None
        if shutil.which("kdialog"):
            r = subprocess.run(["kdialog", "--getopenfilename", start, "*.scad *.csg|OpenSCAD models"],
                               capture_output=True, text=True, timeout=600)
            return r.stdout.strip() or None
    except Exception:
        return None
    return None


def add_local(app, path):
    """Register a .scad/.csg on this computer (not copied, so its libraries are found)."""
    path = os.path.abspath(os.path.expanduser(path.strip().strip('"')))
    if not os.path.isfile(path):
        raise ValueError("File not found: " + path)
    if os.path.splitext(path)[1].lower() not in SCAD_EXT:
        raise ValueError("Choose a .scad (or .csg) file")
    fid = secrets.token_hex(6)
    app.files[fid] = {"path": path, "name": os.path.basename(path), "local": True}
    c = core()
    rec = [path] + [p for p in c.load_settings().get("recent", []) if p != path]
    c.save_settings(recent=rec[:8])
    return fid


def add_upload(app, name, reader, size):
    """A dropped-in file: saved into its own work folder, so it has no libraries and no live link."""
    fid = secrets.token_hex(6)
    d = os.path.join(app.work, fid)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, name)
    left = size
    with open(path, "wb") as f:
        while left > 0:
            chunk = reader(min(1 << 20, left))
            if not chunk:
                break
            f.write(chunk)
            left -= len(chunk)
    app.files[fid] = {"path": path, "name": name, "local": False}
    return fid


def file_info(app, fid):
    f = app.files.get(fid)
    if not f or os.path.splitext(f["name"])[1].lower() not in SCAD_EXT:
        raise KeyError("Choose a file first")
    is_scad = f["path"].lower().endswith(".scad")
    return {"id": fid, "name": f["name"], "path": f["path"] if f.get("local") else "",
            "local": bool(f.get("local")), "params": core().scan_parameters(f["path"]) if is_scad else []}


def defines_from(values, params):
    """OpenSCAD -D values for the Customizer fields the user changed (unchanged ones are left out)."""
    out = {}
    known = {q["name"]: q for q in params}
    for k, v in (values or {}).items():
        q = known.get(k)
        if q is None:
            continue
        v = str(v).strip()
        if q["kind"] == "string":
            v = json.dumps(v)
        elif q["kind"] == "bool":
            v = "true" if v in ("true", "True", "1", "on") else "false"
        elif q["kind"] == "number":
            try:
                float(v)
            except ValueError:
                continue
        if v != q["value"]:
            out[k] = v
    return out


def job_params(app, fid, opts, values):
    """The worker's parameter dictionary for one SCAD → STEP job."""
    f = app.files[fid]
    is_scad = f["path"].lower().endswith(".scad")
    params = core().scan_parameters(f["path"]) if is_scad else []
    return {"units": opts.get("units", "mm"), "faceted_fn": int(float(opts.get("faceted_fn") or 8)),
            "verify": bool(opts.get("verify", True)), "smooth_outlines": bool(opts.get("smooth_outlines", True)),
            "defines": defines_from(values, params), "openscad": core().load_settings().get("openscad", "")}


def scad_params(p, scale):
    """core.ScadParams from the worker's dictionary."""
    return core().ScadParams(defines=dict(p.get("defines") or {}), scale=scale,
                             faceted_fn=int(p.get("faceted_fn") or 8), verify=bool(p.get("verify", True)),
                             smooth_outlines=bool(p.get("smooth_outlines", True)),
                             openscad=p.get("openscad") or "")


# ------------------------------------------------------------------ OpenSCAD
def openscad_status(app, refresh=False):
    if getattr(app, "oscad_info", None) is None or refresh:
        c = core()
        cmd = c.find_openscad()
        info = {"found": bool(cmd), "cmd": " ".join(cmd) if cmd else "", "version": ""}
        if cmd:
            try:
                r = subprocess.run(cmd + ["--version"], capture_output=True, text=True, timeout=60,
                                   **c._nowindow())
                info["version"] = (r.stdout + r.stderr).strip().splitlines()[-1]
            except Exception:
                pass
        app.oscad_info = info
    return app.oscad_info


def set_openscad(app, path):
    path = (path or "").strip().strip('"')
    c = core()
    if path and not c.find_openscad(path):
        raise ValueError("OpenSCAD didn't start from that location")
    c.save_settings(openscad=path)
    return openscad_status(app, refresh=True)


def recent(app):
    try:
        return [p for p in core().load_settings().get("recent", []) if os.path.exists(p)]
    except Exception:
        return []


def open_in_openscad(path):
    c = core()
    cmd = c.find_openscad()
    if cmd and path and os.path.exists(path):
        subprocess.Popen(cmd + [path], **c._nowindow())


# ------------------------------------------------------------------ live link
def start_watch(app, fid, opts, values, out_dir):
    """Re-export next to the .scad (or into Output) every time it, or a library it uses, is saved."""
    stop_watch(app)
    f = app.files.get(fid)
    if not f or not f.get("local"):
        raise ValueError("The live link needs a file chosen from this computer (not dropped in)")
    c = core()
    jp = job_params(app, fid, opts, values)
    prm = scad_params(jp, app_units(opts))
    prm.verify = False                       # keep each update quick
    stem = os.path.splitext(f["name"])[0]
    out = (os.path.join(os.path.dirname(f["path"]), stem + ".step") if opts.get("where", "beside") == "beside"
           else os.path.join(out_dir, stem + ".step"))
    w = {"file": f["path"], "out": out, "stop": threading.Event(), "log": [], "runs": 0,
         "ok": None, "when": "", "summary": ""}

    def log(s):
        w["log"].extend(str(s).splitlines() or [""])
        del w["log"][:-400]

    def done(rep, err):
        w["runs"] += 1
        w["when"] = time.strftime("%H:%M:%S")
        w["ok"] = rep is not None and rep.is_solid
        w["summary"] = rep.summary() if rep else f"ERROR: {err}"

    def loop():
        c.watch(f["path"], out, prm, log=log, stop=w["stop"].is_set, on_done=done, lock=app.run_lock)
    w["thread"] = threading.Thread(target=loop, daemon=True)
    app.watcher = w
    w["thread"].start()
    return out


def stop_watch(app):
    w = getattr(app, "watcher", None)
    if w:
        w["stop"].set()
        app.watcher = None


def watch_state(app, since):
    w = getattr(app, "watcher", None)
    if not w:
        return {"on": False}
    return {"on": True, "file": w["file"], "out": w["out"], "runs": w["runs"], "ok": w["ok"],
            "when": w["when"], "summary": w["summary"],
            "log": w["log"][since:] if since <= len(w["log"]) else w["log"], "next": len(w["log"])}


def app_units(opts):
    return {"mm": 1.0, "inches": 25.4, "in": 25.4, "cm": 10.0, "m": 1000.0}.get(opts.get("units", "mm"), 1.0)
