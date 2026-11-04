# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""
install — hook the portable Twoo STEP CAD Toolbox folder into the rest of the computer, on request:

  * install the FreeCAD workbench into every FreeCAD found (normal install, AppImage,
    Flatpak, Snap; versioned 1.x settings folders too)
  * install the Fusion add-in (macOS / Windows)
  * add Twoo STEP CAD Toolbox to the Linux app menu
  * open the app in a real web browser (never in some other program that has claimed web
    links, e.g. FreeCAD's built-in browser)

The installed plugins get a small file pointing back at this folder, so they find it
wherever it lives. Re-run after moving the folder or updating Twoo STEP CAD Toolbox.
"""
from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))                  # the portable folder (the repo root)
HOME = os.path.expanduser("~")
LOCATION_FILE = "tostep_location.txt"
MOD_NAME = "TOSTEP"                                            # folder name inside FreeCAD's Mod / Fusion's AddIns
LAUNCHER_LINUX = "twoo-step-cad-toolbox-linux.sh"
LAUNCHER_WIN = "Twoo STEP CAD Toolbox (Windows).bat"
# What goes into an installed CAD plug-in besides its own folder: ONE copy of each shared file lives
# in the repo and is added at install time, so the FreeCAD and Fusion plug-ins can never drift apart.
SHARED = (("cad_shared/bridge.py", "tostep_bridge.py"), ("../core/hstl.py", "hstl.py"))


def _plugin_src(kind):
    """frontends/<freecad|fusion>/TOSTEP inside the portable folder."""
    p = os.path.join(HERE, kind.lower(), MOD_NAME)
    return p if os.path.isdir(p) else None


def build_cad_package(src, dest, with_location=True):
    """Copy a CAD plug-in's folder to dest, add the shared files, and (installed copies) note where
    the portable folder is. Used by the installer and by tools/build_cad_packages.py."""
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "settings.json"))
    for rel, name in SHARED:
        shutil.copyfile(os.path.normpath(os.path.join(HERE, rel)), os.path.join(dest, name))
    if with_location:
        with open(os.path.join(dest, LOCATION_FILE), "w", encoding="utf-8") as fh:
            fh.write(ROOT + "\n")


# ---------------------------------------------------------------- FreeCAD
def freecad_user_dirs():
    """FreeCAD user-data folders that exist on this computer (the Mod folder goes inside)."""
    c = []
    if sys.platform == "darwin":
        c += [os.path.join(HOME, "Library", "Application Support", "FreeCAD")]
        c += [os.path.join(HOME, "Library", "Preferences", "FreeCAD")]
    elif sys.platform.startswith("win"):
        c += [os.path.join(os.environ.get("APPDATA", HOME), "FreeCAD")]
    else:
        c += [os.path.join(HOME, ".local", "share", "FreeCAD"), os.path.join(HOME, ".FreeCAD"),
              os.path.join(HOME, ".var", "app", "org.freecad.FreeCAD", "data", "FreeCAD"),
              os.path.join(HOME, ".var", "app", "org.freecadweb.FreeCAD", "data", "FreeCAD")]
        c += glob.glob(os.path.join(HOME, "snap", "freecad", "*", ".local", "share", "FreeCAD"))
    found = []
    for d in c:
        if not os.path.isdir(d):
            continue
        # FreeCAD 1.1+ keeps settings per version: <dir>/v1-1/Mod
        vers = sorted(glob.glob(os.path.join(d, "v[0-9]*-[0-9]*")))
        found += [d] + vers        # 1.0 uses the folder itself, 1.1+ a versioned subfolder
    return found


def _default_freecad_dir():
    if sys.platform == "darwin":
        return os.path.join(HOME, "Library", "Application Support", "FreeCAD")
    if sys.platform.startswith("win"):
        return os.path.join(os.environ.get("APPDATA", HOME), "FreeCAD")
    return os.path.join(HOME, ".local", "share", "FreeCAD")


def _copy_plugin(src, dest):
    build_cad_package(src, dest)


def install_freecad():
    """Returns a list of human-readable result lines."""
    src = _plugin_src("FreeCAD")
    if not src:
        return ["FreeCAD plugin files not found in this Twoo STEP CAD Toolbox folder (tostep/frontends/freecad)."]
    dirs = freecad_user_dirs()
    made = False
    if not dirs:
        dirs = [_default_freecad_dir()]
        made = True
    out = []
    for d in dirs:
        dest = os.path.join(d, "Mod", MOD_NAME)
        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            _copy_plugin(src, dest)
            out.append(f"FreeCAD: installed into {dest}")
        except OSError as e:
            out.append(f"FreeCAD: couldn't write {dest} ({e})")
    if made:
        out.append("(No FreeCAD settings folder found yet — start FreeCAD once, then install again "
                   "if the workbench doesn't show up.)")
    out.append("Restart FreeCAD, then pick 'Twoo STEP CAD Toolbox' in the workbench list (View → Workbench). "
               "It won't be listed in the Addon Manager — that only lists add-ons it downloaded itself.")
    return out


# ---------------------------------------------------------------- Fusion
def fusion_addins_dir():
    if sys.platform == "darwin":
        return os.path.join(HOME, "Library", "Application Support", "Autodesk", "Autodesk Fusion 360", "API", "AddIns")
    if sys.platform.startswith("win"):
        return os.path.join(os.environ.get("APPDATA", HOME), "Autodesk", "Autodesk Fusion 360", "API", "AddIns")
    return None


def install_fusion():
    d = fusion_addins_dir()
    if d is None:
        return ["Fusion runs on macOS and Windows only — nothing to install on Linux."]
    src = _plugin_src("Fusion")
    if not src:
        return ["Fusion add-in files not found in this Twoo STEP CAD Toolbox folder (tostep/frontends/fusion)."]
    if not os.path.isdir(os.path.dirname(d)):
        return [f"Fusion doesn't seem to be installed for this user ({os.path.dirname(d)} is missing). "
                "Start Fusion once, then try again."]
    dest = os.path.join(d, MOD_NAME)
    try:
        os.makedirs(d, exist_ok=True)
        _copy_plugin(src, dest)
    except OSError as e:
        return [f"Fusion: couldn't write {dest} ({e})"]
    return [f"Fusion: installed into {dest}",
            "Restart Fusion. If the buttons don't appear under Utilities → Add-Ins, open "
            "Utilities → Add-Ins (Shift+S), select Twoo STEP CAD Toolbox, tick 'Run on Startup' and click Run."]


# ---------------------------------------------------------------- Linux app menu
def install_menu_entry():
    if not sys.platform.startswith("linux"):
        return ["The app-menu shortcut is for Linux. On macOS use 'Twoo STEP CAD Toolbox (Mac).command' "
                "(drag it to the Dock); on Windows right-click the .bat → Send to → Desktop."]
    apps = os.path.join(HOME, ".local", "share", "applications")
    icons = os.path.join(HOME, ".local", "share", "icons")
    os.makedirs(apps, exist_ok=True); os.makedirs(icons, exist_ok=True)
    icon = os.path.join(icons, "tostep.svg")
    with open(icon, "w", encoding="utf-8") as fh:
        fh.write('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="14" '
                 'fill="#2f6fed"/><path d="M16 44 32 16l16 28z" fill="none" stroke="#fff" stroke-width="5" '
                 'stroke-linejoin="round"/></svg>\n')
    launcher = os.path.join(ROOT, LAUNCHER_LINUX)
    try:
        os.chmod(launcher, 0o755)
    except OSError:
        pass
    entry = os.path.join(apps, "tostep.desktop")
    with open(entry, "w", encoding="utf-8") as fh:
        fh.write("[Desktop Entry]\nType=Application\nName=Twoo STEP CAD Toolbox\n"
                 "Comment=Convert meshes (STL/OBJ/PLY/3MF) to solid STEP; clean STEP files\n"
                 f'Exec="{launcher}"\nPath={ROOT}\nIcon={icon}\nTerminal=true\n'
                 "Categories=Graphics;Engineering;3DGraphics;\n")
    os.chmod(entry, 0o755)
    if shutil.which("update-desktop-database"):
        subprocess.run(["update-desktop-database", apps], capture_output=True)
    return [f"Added Twoo STEP CAD Toolbox to the app menu ({entry}).",
            "It opens a small terminal window (the app's engine) plus your web browser."]


# ---------------------------------------------------------------- right-click entries
TAG = "added by Twoo STEP CAD Toolbox"
MESH_EXTS = "stl;obj;ply;3mf;off;"


def _write(path, text, exe=False):
    import stat
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="\n" if not sys.platform.startswith("win") else None, encoding="utf-8") as f:
        f.write(text)
    if exe:
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _rm(path, out):
    if os.path.exists(path):
        os.remove(path)
        out.append("removed " + path)


def install_filemanager(remove=False):
    """Right-click entries: Convert to STEP for meshes and OpenSCAD models, Live link for .scad.
    Linux: Nemo (Linux Mint) actions and Nautilus scripts. Windows: Send to."""
    out = []
    if sys.platform.startswith("win"):
        sendto = os.path.join(os.environ.get("APPDATA", HOME), "Microsoft", "Windows", "SendTo")
        a = os.path.join(sendto, "STEP (Twoo STEP CAD Toolbox).bat")
        b = os.path.join(sendto, "STEP live link (Twoo STEP CAD Toolbox).bat")
        if remove:
            _rm(a, out)
            _rm(b, out)
            return out or ["Nothing to remove."]
        bat = os.path.join(ROOT, LAUNCHER_WIN)
        _write(a, f'@echo off\r\nrem {TAG}\r\nif /i "%~x1"==".scad" (call "{bat}" scad %* --pause) else if /i "%~x1"==".csg" '
                  f'(call "{bat}" scad %* --pause) else (call "{bat}" %1 --pause)\r\n')
        _write(b, f'@echo off\r\nrem {TAG}\r\ncall "{bat}" scad %1 --watch\r\n')
        return [f"Added {a}", f"Added {b}",
                "Right-click a .stl or .scad file → Send to → “STEP (Twoo STEP CAD Toolbox)” (Windows 11: Show more options → Send to)."]
    if sys.platform == "darwin":
        return ["Finder integration isn't available yet. Use the app window, or the command line:",
                f'  bash "{os.path.join(ROOT, "launch", "portable.sh")}" part.stl --pause',
                f'  bash "{os.path.join(ROOT, "launch", "portable.sh")}" scad model.scad --watch']
    share = os.environ.get("XDG_DATA_HOME") or os.path.join(HOME, ".local", "share")
    wrapper = os.path.join(share, "tostep", "tostep")
    nemo = os.path.join(share, "nemo", "actions")
    naut = os.path.join(share, "nautilus", "scripts")
    files = {"wrapper": wrapper,
             "n_mesh": os.path.join(nemo, "tostep-convert-mesh.nemo_action"),
             "n_scad": os.path.join(nemo, "tostep-convert-scad.nemo_action"),
             "n_live": os.path.join(nemo, "tostep-live-scad.nemo_action"),
             "g_conv": os.path.join(naut, "Convert to STEP (Twoo STEP CAD Toolbox)"),
             "g_live": os.path.join(naut, "Live-link OpenSCAD to STEP (Twoo STEP CAD Toolbox)")}
    if remove:
        for p in files.values():
            _rm(p, out)
        return out or ["Nothing to remove."]
    launcher = os.path.join(ROOT, LAUNCHER_LINUX)
    _write(wrapper, f'#!/bin/bash\n# {TAG}: runs the portable app wherever its folder is\nexec bash "{launcher}" "$@"\n', exe=True)
    _write(files["n_mesh"], "[Nemo Action]\nName=Convert to STEP (Twoo STEP CAD Toolbox)\n"
           "Comment=Convert this mesh to a solid STEP file\n"
           f"Exec={wrapper} %F --pause\nIcon-Name=document-save-as\nSelection=s\n"
           f"Extensions={MESH_EXTS}\nTerminal=true\n")
    _write(files["n_scad"], "[Nemo Action]\nName=Convert to STEP (Twoo STEP CAD Toolbox)\n"
           "Comment=Convert this OpenSCAD model to a solid STEP file\n"
           f"Exec={wrapper} scad %F --pause\nIcon-Name=document-save-as\nSelection=notnone\n"
           "Extensions=scad;csg;\nTerminal=true\n")
    _write(files["n_live"], "[Nemo Action]\nName=Live-link to STEP (updates on save)\n"
           "Comment=Keep a STEP copy of this OpenSCAD model updated every time you save it\n"
           f"Exec={wrapper} scad %F --watch\nIcon-Name=view-refresh\nSelection=s\n"
           "Extensions=scad;\nTerminal=true\n")
    term = ('T="$(command -v gnome-terminal || command -v x-terminal-emulator || command -v konsole || command -v xterm)"\n'
            'mapfile -t F <<< "$NAUTILUS_SCRIPT_SELECTED_FILE_PATHS"\n[ -z "${F[-1]}" ] && unset "F[-1]"\n'
            'case "${F[0],,}" in *.scad|*.csg) ARGS=(scad "${F[@]}");; *) ARGS=("${F[0]}");; esac\n')
    for key, extra in (("g_conv", "--pause"), ("g_live", "--watch")):
        pre = term if key == "g_conv" else term.replace('ARGS=("${F[0]}")', 'ARGS=(scad "${F[0]}")')
        _write(files[key], "#!/bin/bash\n# " + TAG + "\n" + pre +
               f'case "$T" in\n  *gnome-terminal) exec "$T" -- "{wrapper}" "${{ARGS[@]}}" {extra};;\n'
               f'  "") exec "{wrapper}" "${{ARGS[@]}}";;\n'
               f'  *) exec "$T" -e "{wrapper}" "${{ARGS[@]}}" {extra};;\nesac\n', exe=True)
    return [f"Added right-click entries ({', '.join(os.path.basename(p) for k, p in files.items() if k != 'wrapper')}).",
            "Linux Mint (Nemo): right-click a mesh or .scad file → “Convert to STEP (Twoo STEP CAD Toolbox)”; for a .scad also "
            "“Live-link to STEP”. If Nemo was open it may need a restart: nemo -q",
            "GNOME Files: right-click → Scripts. Remove them again with: tostep install files --remove"]


# ---------------------------------------------------------------- browser
_NOT_BROWSERS = ("freecad", "code", "gedit", "xed", "kate", "libreoffice", "gimp", "inkscape")


def open_browser(url):
    """Open url in a real web browser. On Linux, the 'default' handler for web links can be
    claimed by other programs (FreeCAD's start page browser, editors); skip those."""
    if sys.platform.startswith("linux"):
        default = ""
        try:
            default = subprocess.run(["xdg-settings", "get", "default-web-browser"], capture_output=True,
                                     text=True, timeout=5).stdout.strip().lower()
        except Exception:
            pass
        if not default or any(k in default for k in _NOT_BROWSERS):
            for b in ("firefox", "google-chrome", "chromium", "chromium-browser", "brave-browser",
                      "microsoft-edge", "vivaldi", "opera", "epiphany"):
                if shutil.which(b):
                    subprocess.Popen([b, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     start_new_session=True)
                    return True
    try:
        return webbrowser.open(url)
    except Exception:
        return False


def run_cli(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="tostep install",
                                 description="Install the FreeCAD / Fusion plugins and the app-menu shortcut.")
    ap.add_argument("what", nargs="*", help="freecad, fusion, menu, files or all (default: all; files is only "
                    "added when named: it puts entries in your file manager's right-click menu)")
    ap.add_argument("--remove", action="store_true", help="with 'files': take the right-click entries out again")
    a = ap.parse_args(argv)
    w = set(a.what or ["all"])
    bad = w - {"freecad", "fusion", "menu", "files", "all"}
    if bad:
        ap.error("choose from: freecad, fusion, menu, files, all")
    lines = []
    if "files" in w:
        lines += install_filemanager(a.remove)
        w.discard("files")
        if not w:
            print("\n".join(lines))
            return 0
    if w & {"freecad", "all"}:
        lines += install_freecad()
    if w & {"fusion", "all"} and fusion_addins_dir():
        lines += install_fusion()
    if w & {"menu", "all"} and sys.platform.startswith("linux"):
        lines += install_menu_entry()
    print("\n".join(lines))
    return 0
