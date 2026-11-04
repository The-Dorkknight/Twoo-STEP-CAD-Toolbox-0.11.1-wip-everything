# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""jetwash — the plugin's front door. The cleaning itself is jetwash.py beside this file.

Known coupling (docs/ARCHITECTURE.md): the cleaner uses the converter's OpenCascade helpers
(tostep.plugins.mesh2step.classic) and, for a mesh given instead of a STEP, the converter's settings.
That is why this plugin requires mesh2step."""
from __future__ import annotations

from tostep.core import params as _params
from tostep.core import registry as _registry


def manifest():
    return _registry.discover().get("jetwash").m


def run(job):
    from tostep.plugins import mesh2step
    from . import jetwash
    p = _params.coerce(manifest(), job.params)
    mesh_p = mesh2step.coerce(job.params)          # an STL input is converted first, with the converter's settings
    prm = mesh2step.build_params(mesh_p, job.scale)
    return jetwash.jetwash(job.src, job.out, float(p["threshold"] or 0.05), int(p["coarseness"] or 2),
                           log=job.log, progress=job.progress, stl_params=prm)
