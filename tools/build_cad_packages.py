#!/usr/bin/env python3
# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""
build_cad_packages — make ready-to-copy folders of the FreeCAD workbench and the Fusion add-in.

    python tools/build_cad_packages.py [OUT_DIR]        (default: dist/cad)

The two CAD plug-ins share one bridge file and one hardpoint-STL reader that live in the toolbox
(tostep/frontends/cad_shared/bridge.py and tostep/core/hstl.py). The one-click installer adds them when it copies
a plug-in into FreeCAD or Fusion. This tool does the same into a folder, for people who copy plug-ins by hand:

    OUT_DIR/FreeCAD/TOSTEP     copy into FreeCAD's Mod folder
    OUT_DIR/Fusion/TOSTEP      add in Fusion: Utilities → Add-Ins → Add-Ins tab → + → this folder

These copies do not know where the toolbox folder is (the installer writes that down); they look in the usual
places and ask once if they cannot find it.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tostep.frontends import install  # noqa: E402


def main():
    out = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.join(ROOT, "dist", "cad")
    for kind in ("FreeCAD", "Fusion"):
        src = install._plugin_src(kind)
        if not src:
            sys.exit(f"{kind} plug-in folder not found under tostep/frontends")
        dest = os.path.join(out, kind, install.MOD_NAME)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        install.build_cad_package(src, dest, with_location=False)
        print("wrote", dest)


if __name__ == "__main__":
    main()
