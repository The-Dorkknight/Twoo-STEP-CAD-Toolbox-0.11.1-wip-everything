# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""
mesh2step — the mesh -> solid STEP converter, as a plugin.

The engines (classic, v2, ladder, quad, auto, ...) are the modules beside this file. This file is the
plugin's front door: it turns a job's typed parameters into the engines' own settings and runs them.
Other plugins (jetwash, hardpoint) come in through params_for() and run(), never through the engines'
private helpers, except where a plugin's own notes say otherwise.
"""
from __future__ import annotations

import os

from tostep.core import params as _params
from tostep.core import registry as _registry

ENGINES = ("classic", "staged", "ladder", "v2", "best", "quad", "auto")


def manifest():
    return _registry.discover().get("mesh2step").m


def coerce(raw):
    """A front end's dictionary -> typed values, defaults filled in (see tostep.core.params)."""
    return _params.coerce(manifest(), raw)


def build_params(p, scale):
    """Typed job parameters -> classic.Params (the engines' settings). `p` must be coerced."""
    from . import classic as core
    return core.Params(
        mode=p["mode"] if p["mode"] in ("surface", "faceted") else "surface",
        tol=float(p["tol"] or 0), scale=scale,
        plane_angle=float(p["plane_angle"] or 3), crease_angle=float(p["crease_angle"] or 30),
        patch_size=float(p["patch_size"] or 30) / 100.0, max_bend=float(p["max_bend"] or 60),
        decimate=int(p["decimate"] or 0), smooth=int(p["smooth"] or 0),
        fit_primitives=bool(p["primitives"]), memory_guard=bool(p["memory_guard"]),
        organic_tol=(max(0.0, float(p["organic_tol"] if p["organic_tol"] is not None else 0.3))
                     if p["guess"] else 0.0),
        use_labels=bool(p["use_labels"]), route=str(p["route"] or "auto"),
        ladder_guess=bool(p["ladder_guess"]), ladder_rough=bool(p["ladder_rough"]),
        budget_s=max(0.0, float(p["budget_s"] or 0)), max_fills=max(0, int(p["max_fills"] or 0)),
        plane_rule=str(p["plane_rule"] or "classic"), save_labelled=str(p["save_labelled"] or ""),
        gap_fill=str(p.get("gap_fill") or "plate"))


def heights(text):
    """'2, 10.5, 30' -> (2.0, 10.5, 30.0); blank -> None."""
    parts = [t for t in str(text or "").replace(";", ",").replace(" ", ",").split(",") if t]
    try:
        vals = tuple(float(t) for t in parts)
    except ValueError:
        raise ValueError("Slice heights must be numbers separated by commas, e.g. 2, 10, 30")
    return vals or None


def profile_params(p, scale):
    """Typed job parameters -> profile2d.ProfileParams (the extrude / loft modes)."""
    from . import profile2d
    return profile2d.ProfileParams(
        mode=p["mode"], source=p["source"], axis=p["axis"], slice_at=p["slice_at"],
        simplify=float(p["simplify"] or 0), height=float(p["height"] or 0),
        max_slices=int(p["max_slices"] or 15), sensitivity=float(p["sensitivity"] or 0.02), scale=scale,
        smooth=bool(p["smooth_sketch"]), symmetric=bool(p["symmetric"]), heights=heights(p["heights"]))


def run(job):
    """The plugin contract's entry point. Returns the engine's Report."""
    p = job.params
    if p["mode"] in ("extrude", "loft"):
        from . import profile2d
        return profile2d.convert(job.src, job.out, profile_params(p, job.scale), log=job.log,
                                 progress=job.progress)
    from . import engines
    prm = build_params(p, job.scale)
    kw = {k: getattr(prm, k) for k in prm.__dataclass_fields__}
    eng = p["engine"]
    if p.get("no_ceiling"):                    # the command line's --no-ceiling
        os.environ["TOSTEP_BUDGET"] = "0"
        os.environ["TOSTEP_MAX_FILLS"] = "0"
    return engines.convert(job.src, job.out, kw, eng if eng in engines.ENGINES else "classic",
                           log=job.log, progress=job.progress)
