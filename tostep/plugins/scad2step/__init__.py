# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""scad2step — OpenSCAD model -> solid STEP. The engine is scad2step_core.py; bridge.py holds what the
web page needs around it (picking a file, finding OpenSCAD, Customizer parameters, the live link)."""
from __future__ import annotations

from tostep.core.job import UserError


def run(job):
    from . import bridge
    from . import scad2step_core as scad
    try:
        return scad.convert(job.src, job.out, bridge.scad_params(job.params, job.scale),
                            log=job.log, progress=job.progress)
    except scad.ScadError as e:
        raise UserError(str(e))
