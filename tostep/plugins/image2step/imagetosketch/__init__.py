# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
# Copied from the ImageToSketch project by tools/sync_imagetosketch.py - edit it there.
"""ImageToSketch - trace a photo, scan or drawing into clean, extrudable CAD outlines.

Core library (no FreeCAD needed):  from imagetosketch import trace, Options
FreeCAD front end:                 imagetosketch.fc_gui.show()
"""

__version__ = "0.10.1"
__build_date__ = "2026-10-20 12:00"
__release__ = __version__ + "-wip"         # what people are shown: still work in progress
__title__ = "ImageToSketch v%s (Built %s)" % (__release__, __build_date__)


def __getattr__(name):                     # lazy: importing the package must not require OpenCV
    if name in ("trace", "Options", "Result"):
        from . import pipeline
        return getattr(pipeline, name)
    raise AttributeError(name)
