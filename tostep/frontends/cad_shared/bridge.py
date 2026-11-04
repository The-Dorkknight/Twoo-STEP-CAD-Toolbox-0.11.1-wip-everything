# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""
tostep_bridge — lets CAD plug-ins (FreeCAD, Fusion) use an installed Twoo STEP CAD Toolbox.

ONE copy of this file lives in the repo (tostep/frontends/cad_shared/bridge.py). The installer adds it
to each CAD plug-in as tostep_bridge.py, so the FreeCAD and Fusion plug-ins can never drift apart.
The contract with the toolbox is the command line: `run_tostep.py <command> ... --machine` prints
'@@PROGRESS n', '@@DONE path' and '@@ERROR message' lines. Nothing else of the toolbox is imported.

The heavy converter is NOT inside the plugin: it lives in the user's portable Twoo STEP CAD Toolbox
folder (its own Python + libraries). The plugin only exports the mesh, runs the converter
there, streams its progress, and imports the resulting STEP. Fix the converter once and
every front end gets the fix.

Pure standard library, so it runs inside FreeCAD's and Fusion's own Python.
"""
from __future__ import annotations

import glob
import os
import platform
import struct
import subprocess
import sys
import threading

HOME = os.path.expanduser("~")
NAMES = ("Twoo-STEP-CAD-Toolbox", "twoo-step-cad-toolbox", "Twoo STEP CAD Toolbox")
CANDIDATES = [os.path.join(base, n)
              for n in NAMES
              for base in (HOME, os.path.join(HOME, "Downloads"), os.path.join(HOME, "Desktop"),
                           os.path.join(HOME, "Documents"), os.path.join(HOME, "Applications"),
                           os.path.join(HOME, "Documents", "GitHub"), os.path.join(HOME, "GitHub"))]


def is_toolbox_folder(path):
    return bool(path) and os.path.isfile(os.path.join(path, "run_tostep.py")) \
        and os.path.isdir(os.path.join(path, "tostep"))


def _installed_location():
    """Written by Twoo STEP CAD Toolbox's 'Install plugin' button: where the toolbox folder lives."""
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "tostep_location.txt"),
                  encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return None


def find_folder(saved=None):
    """The saved folder if valid, else the one the installer recorded, else the usual places."""
    if is_toolbox_folder(saved):
        return saved
    loc = _installed_location()
    if is_toolbox_folder(loc):
        return loc
    for c in CANDIDATES:
        if is_toolbox_folder(c):
            return c
    return None


def _triple():
    m = platform.machine().lower()
    arch = "aarch64" if m in ("arm64", "aarch64") else "x86_64"
    if sys.platform == "darwin":
        return f"{arch}-apple-darwin"
    if sys.platform.startswith("win"):
        return "x86_64-pc-windows-msvc"        # the CAD library only exists for x64 Windows
    return f"{arch}-unknown-linux-gnu"


def find_python(folder):
    """The private Python the Twoo STEP CAD Toolbox launcher installed (None if not set up yet)."""
    rt = os.path.join(folder, "runtime", _triple())
    if sys.platform.startswith("win"):
        pats = [os.path.join(rt, "python", "cpython-3.12.*", "python.exe")]
    else:
        pats = [os.path.join(rt, "python", "cpython-3.12.*", "bin", "python3.12")]
    for pat in pats:
        for p in sorted(glob.glob(pat)):
            if os.path.isfile(p):
                return p
    return None


def status(folder):
    """(ready, message). Not ready = the user must open Twoo STEP CAD Toolbox once (first-run setup
    or a library update) — that needs a visible window for downloads, so plugins don't
    do it themselves."""
    if not is_toolbox_folder(folder):
        return False, ("Twoo STEP CAD Toolbox folder not found. Download Twoo STEP CAD Toolbox, unzip it, and choose "
                       "its folder in the plugin settings.")
    py = find_python(folder)
    launcher = {"darwin": "Twoo STEP CAD Toolbox (Mac).command", "win32": "Twoo STEP CAD Toolbox (Windows).bat"}.get(
        sys.platform, "twoo-step-cad-toolbox-linux.sh")
    if not py:
        return False, (f"Twoo STEP CAD Toolbox isn't set up on this computer yet. Double-click "
                       f"'{launcher}' in {folder} once — the first start downloads what it "
                       f"needs — then try again.")
    req = os.path.join(folder, "requirements.txt")
    stamp = os.path.join(folder, "runtime", _triple(), "req.txt")
    try:
        if open(req, "rb").read() != open(stamp, "rb").read():
            return False, (f"Twoo STEP CAD Toolbox was updated and needs to install new libraries. "
                           f"Double-click '{launcher}' once, then try again.")
    except OSError:
        return False, f"Twoo STEP CAD Toolbox setup looks incomplete. Double-click '{launcher}' once, then try again."
    return True, "ready"


def _env(folder):
    """Same isolation the launcher uses: nothing from the host program's Python leaks in."""
    env = {k: v for k, v in os.environ.items()
           if not (k.startswith("PYTHON") or k in ("VIRTUAL_ENV", "CONDA_PREFIX", "CONDA_DEFAULT_ENV",
                                                   "PIP_REQUIRE_VIRTUALENV", "QT_PLUGIN_PATH",
                                                   "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"))}
    tmp = os.path.join(folder, "runtime", "tmp")
    os.makedirs(tmp, exist_ok=True)
    env.update(PYTHONNOUSERSITE="1", PYTHONUNBUFFERED="1", TOSTEP_NOPAUSE="1",
               TMPDIR=tmp, TMP=tmp, TEMP=tmp, MPLCONFIGDIR=os.path.join(tmp, "matplotlib"),
               TOSTEP_WORK=os.path.join(folder, "runtime", "work"),
               TOSTEP_OUTPUT=os.path.join(folder, "Output"))
    return env


MODES = ["surface", "faceted", "extrude", "loft"]
MODE_NAMES = ["Smooth surfaces (planes, cylinders, NURBS)", "Exact facets",
              "EXPERIMENTAL: extrude outline (flat profile parts only)",
              "EXPERIMENTAL: loft cross-sections (tapered / stepped parts only)"]


def convert_args(src, dst, mode="surface", units="mm", tol=0.0, decimate=0, axis="z"):
    a = [src, dst, "--mode", mode, "--units", units, "--machine"]
    if mode in ("extrude", "loft"):
        return a + ["--axis", axis]
    if tol and tol > 0:
        a += ["--tol", repr(float(tol))]
    if decimate and decimate > 0:
        a += ["--decimate", str(int(decimate))]
    return a


def scad_args(src, dst, units="mm", verify=True):
    """OpenSCAD (.scad / .csg) -> solid STEP with the SCAD2STEP engine inside Twoo STEP CAD Toolbox."""
    a = ["scad", src, "-o", dst, "--units", units, "--machine"]
    return a if verify else a + ["--no-verify"]


def wash_args(src, dst, threshold=0.05, coarseness=2, units="mm"):
    return ["wash", src, dst, "--threshold", repr(float(threshold)),
            "--coarseness", str(int(coarseness)), "--units", units, "--machine"]


class Job:
    """Runs one Twoo STEP CAD Toolbox command in the background.

    on_line(text), on_progress(percent 0-100) and on_finish(ok, result_path_or_error) are
    called from a worker thread — GUI plugins must hop back to their main thread."""

    def __init__(self, folder, args, on_line=None, on_progress=None, on_finish=None):
        self.folder, self.args = folder, args
        self.on_line = on_line or (lambda s: None)
        self.on_progress = on_progress or (lambda p: None)
        self.on_finish = on_finish or (lambda ok, r: None)
        self.proc = None
        self.cancelled = False
        self.thread = None

    def command(self):
        py = find_python(self.folder)
        return [py, "-E", "-s", "-u", os.path.join(self.folder, "run_tostep.py")] + list(self.args)

    def start(self):
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        return self

    def run_blocking(self):
        self._run()
        return self

    def cancel(self):
        self.cancelled = True
        try:
            if self.proc and self.proc.poll() is None:
                self.proc.terminate()
        except Exception:
            pass

    def _run(self):
        result, err = None, None
        try:
            flags = 0x08000000 if sys.platform.startswith("win") else 0   # no console window
            self.proc = subprocess.Popen(self.command(), env=_env(self.folder), stdout=subprocess.PIPE,
                                         stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                         text=True, encoding="utf-8", errors="replace",
                                         creationflags=flags, cwd=self.folder)
            for line in self.proc.stdout:
                line = line.rstrip("\r\n")
                if line.startswith("@@PROGRESS "):
                    try:
                        self.on_progress(int(line.split()[1]))
                    except ValueError:
                        pass
                elif line.startswith("@@DONE "):
                    result = line[7:].strip()
                elif line.startswith("@@ERROR "):
                    err = line[8:].strip()
                elif line.strip():
                    self.on_line(line)
            self.proc.wait()
        except Exception as e:  # noqa
            err = f"Couldn't run Twoo STEP CAD Toolbox: {e}"
        if self.cancelled:
            self.on_finish(False, "Cancelled")
        elif result and os.path.isfile(result):
            self.on_finish(True, result)
        else:
            self.on_finish(False, err or f"Twoo STEP CAD Toolbox stopped (exit code {getattr(self.proc, 'returncode', '?')})")


def write_binary_stl(path, coords, tris, scale=1.0):
    """coords: flat [x0,y0,z0,x1,...]; tris: flat [a0,b0,c0,a1,...] vertex indices."""
    n = len(tris) // 3
    with open(path, "wb") as f:
        f.write(b"Twoo STEP CAD Toolbox plugin export".ljust(80, b" "))
        f.write(struct.pack("<I", n))
        pack = struct.Struct("<12fH").pack
        for i in range(n):
            a, b, c = tris[3 * i], tris[3 * i + 1], tris[3 * i + 2]
            p = [coords[3 * a] * scale, coords[3 * a + 1] * scale, coords[3 * a + 2] * scale,
                 coords[3 * b] * scale, coords[3 * b + 1] * scale, coords[3 * b + 2] * scale,
                 coords[3 * c] * scale, coords[3 * c + 1] * scale, coords[3 * c + 2] * scale]
            ux, uy, uz = p[3] - p[0], p[4] - p[1], p[5] - p[2]
            vx, vy, vz = p[6] - p[0], p[7] - p[1], p[8] - p[2]
            nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
            ln = (nx * nx + ny * ny + nz * nz) ** 0.5 or 1.0
            f.write(pack(nx / ln, ny / ln, nz / ln, *p, 0))
    return n
