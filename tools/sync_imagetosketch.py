#!/usr/bin/env python3
# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""
sync_imagetosketch — refresh tostep/plugins/image2step/imagetosketch from a checkout of the ImageToSketch project.

    python tools/sync_imagetosketch.py /path/to/imagetosketch-repo

The Image → STEP tab uses the tracing library of ImageToSketch (same author). Only the parts
that need no FreeCAD are copied: reading pictures, finding outlines, fitting lines / arcs /
splines, the marker sheet, the printed scale token, the STEP writer, the welding of shared
edges between colour areas, and "service" - the front door the ImageToSketch standalone app and
this toolbox's Image → STEP plugin both go through. Each
copied file carries the notice lines plus one saying where it came from; nothing else is changed.
"""
import os
import re
import shutil
import sys

# Everything except the FreeCAD front end (fc_*), the add-on's OpenCV installer and the command
# line: so a module ImageToSketch adds later (weld.py in 0.9) comes along without editing this.
SKIP = ("deps.py", "cli.py", "__main__.py")


def wanted(src):
    return sorted(n for n in os.listdir(src)
                  if n.endswith(".py") and not n.startswith("fc_") and n not in SKIP)
NOTICE = ("# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)\n"
          "# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.\n"
          "# Copied from the ImageToSketch project by tools/sync_imagetosketch.py - edit it there.\n")


def main(argv):
    if len(argv) != 1 or not os.path.isdir(os.path.join(argv[0], "imagetosketch")):
        print(__doc__)
        return 2
    src = os.path.join(argv[0], "imagetosketch")
    dst = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tostep", "plugins", "image2step", "imagetosketch")
    shutil.rmtree(dst, ignore_errors=True)
    os.makedirs(dst)
    keep = wanted(src)
    for name in keep:
        with open(os.path.join(src, name), encoding="utf-8") as f:
            lines = f.read().splitlines(keepends=True)
        while lines and (lines[0].startswith("# Required Notice:") or lines[0].startswith("# Licensed under the PolyForm")):
            lines.pop(0)                                       # the source's own notice: the same two lines
        text = "".join(lines)
        with open(os.path.join(dst, name), "w", encoding="utf-8", newline="\n") as f:
            f.write(NOTICE + text)
    ver = re.search(r'__version__ = "([^"]+)"', open(os.path.join(src, "__init__.py"), encoding="utf-8").read())
    print(f"Copied {len(keep)} files of ImageToSketch {ver.group(1) if ver else '?'} to {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
