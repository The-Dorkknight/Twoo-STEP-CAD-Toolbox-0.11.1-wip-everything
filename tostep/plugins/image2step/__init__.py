# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""image2step — picture -> outline -> STEP. image2step.py is the plugin's own glue; imagetosketch/ is the
tracing library (shared in spirit with the standalone ImageToSketch FreeCAD macro; see docs/ARCHITECTURE.md)."""
from __future__ import annotations


def run(job):
    from . import image2step
    return image2step.convert(job.src, job.out, job.params, log=job.log, progress=job.progress)
