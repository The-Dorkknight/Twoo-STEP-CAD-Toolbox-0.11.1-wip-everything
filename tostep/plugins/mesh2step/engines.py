# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""
engines — pick which surface converter runs.

  classic : the proven converter (default)
  staged  : EXPERIMENTAL — classic plus a roughing stage that claims lathe shapes (surfaces of
            revolution, with true cylinders, cones and spheres) and extruded freeform outlines as
            exact faces before any patch fitting, sturdier face outlines, and repair after the
            STEP file is read back (see rough.py)
  v2      : EXPERIMENTAL second-generation fitter (mesh2step.v2)
  best    : EXPERIMENTAL second pass — run classic, then v2, keep whichever result is better
            (valid closed solid first, then no worse deviation, then fewer faces)
"""
from __future__ import annotations

import os
import shutil

ENGINES = ("classic", "staged", "ladder", "v2", "best", "quad", "auto")


def _params(mod, kw):
    fields = mod.Params.__dataclass_fields__
    return mod.Params(**{k: v for k, v in kw.items() if k in fields})


def _v2_child(src, alt, kw, prog_val, q):
    """Runs in a separate process (parallel Second pass)."""
    try:
        from tostep.plugins.mesh2step import v2 as v2
        rep = v2.convert(src, alt, _params(v2, kw), log=lambda s: None,
                         progress=lambda f: setattr(prog_val, "value", float(f)))
        q.put(("ok", rep))
    except Exception as e:  # noqa
        q.put(("err", str(e) or e.__class__.__name__))


def _run_v2_parallel(src, alt, kw, rep_classic_runner, prog):
    """Run classic (in this process) and v2 (in a child) at the same time."""
    import multiprocessing as mp
    ctx = mp.get_context("spawn")
    pv = ctx.Value("d", 0.0)
    q = ctx.Queue()
    child = ctx.Process(target=_v2_child, args=(src, alt, kw, pv, q), daemon=True)
    child.start()
    state = {"c": 0.0}

    def pc(f):
        state["c"] = f
        prog(0.5 * (f + pv.value))
    try:
        rep1 = rep_classic_runner(pc)
        while child.is_alive() and q.empty():
            prog(0.5 * (1.0 + pv.value))
            child.join(0.5)
        kind, val = q.get(timeout=5) if not q.empty() or child.is_alive() else ("err", "v2 process ended without a result")
    finally:
        if child.is_alive():
            child.kill()
    return rep1, kind, val


def convert(src, dst, kw, engine="classic", log=None, progress=None):
    """kw: Params fields (mode, tol, scale, ...). Returns the Report of the kept result."""
    log = log or (lambda s: None)
    from tostep.plugins.mesh2step import classic as classic
    # the performance profile's time ceiling and plate-fill allowance, unless set explicitly.
    # Started without a profile (plain command line, the CAD plug-ins, the benchmark): use
    # what "auto" would pick for this computer, so every way in has the same ceiling.
    if "TOSTEP_BUDGET" not in os.environ:
        try:
            from tostep.core import perf
            budget, fills = perf.auto_limits()
            os.environ["TOSTEP_BUDGET"] = str(budget)
            os.environ.setdefault("TOSTEP_MAX_FILLS", str(fills))
        except Exception:  # noqa
            pass
    try:
        if not kw.get("budget_s") and os.environ.get("TOSTEP_BUDGET"):
            kw = dict(kw, budget_s=float(os.environ["TOSTEP_BUDGET"]))
        if not kw.get("max_fills") and os.environ.get("TOSTEP_MAX_FILLS"):
            kw = dict(kw, max_fills=int(os.environ["TOSTEP_MAX_FILLS"]))
    except ValueError:
        pass
    if engine == "auto":
        # the Standard preset since 0.10.2: the part's shape picks the engine. Smooth organic
        # parts go to Quad patches (within the tolerance, no facets); everything else, and any
        # organic part Quad patches can't do well, to the Ladder engine (routed: exact shapes
        # first, dead-end searches skipped, organic leftovers guesstimated)
        from tostep.plugins.mesh2step import quadfit
        if quadfit.remesher_name():
            try:
                rq = quadfit.convert(src, dst, _params(classic, kw), log=log, progress=progress)
                if rq.ok and rq.is_solid and rq.is_valid and rq.max_dev <= 2.0 * rq.tol:
                    return rq
                log(f"  Quad patches ended {rq.max_dev:.3f} mm from the mesh (tolerance {rq.tol:.3g}) — "
                    "converting with the Ladder engine instead")
            except quadfit.QuadError as e:
                log(f"  Not for Quad patches: {e} — converting with the Ladder engine")
            except Exception as e:  # noqa  (never lose the job over the first try)
                log(f"  Quad patches stopped ({e.__class__.__name__}) — converting with the Ladder engine")
            log("")
        return convert(src, dst, kw, "ladder", log=log, progress=progress)
    if engine == "quad":
        from tostep.plugins.mesh2step import quadfit
        log("Using the EXPERIMENTAL quad-patch engine (a quad layout of the mesh, one Coons patch per quad)")
        try:
            return quadfit.convert(src, dst, _params(classic, kw), log=log, progress=progress)
        except quadfit.QuadError as e:
            log(f"  Quad patches: {e} — converting with Classic instead")
            log("")
        return classic.convert(src, dst, _params(classic, kw), log=log, progress=progress)
    if engine == "v2":
        from tostep.plugins.mesh2step import v2 as v2
        log("Using the EXPERIMENTAL v2 surface fitter")
        return v2.convert(src, dst, _params(v2, kw), log=log, progress=progress)
    if engine == "staged":
        log("Using the EXPERIMENTAL staged converter (roughing first: lathe shapes and extruded outlines as exact faces)")
        return classic.convert(src, dst, _params(classic, {**kw, "rough": True}), log=log, progress=progress)
    if engine == "ladder":
        log("Using the EXPERIMENTAL Ladder engine (easy things first: the mesh is looked at, a route is chosen, "
            "the hard leftover is capped or guesstimated)")
        prog = progress or (lambda f: None)
        rep1 = classic.convert(src, dst, _params(classic, {**kw, "ladder": True}), log=log, progress=prog)
        d1 = rep1.max_dev if rep1.max_dev == rep1.max_dev else float("inf")
        tol = float(getattr(rep1, "tol", 0.0) or 0.0)
        route = getattr(rep1, "route", "")
        roughed = (route == "prismatic" and kw.get("ladder_rough", True)) or route == "lathe"
        if roughed and tol and (d1 > 2.0 * tol or not (rep1.ok and rep1.is_solid and rep1.is_valid)):
            # roughing missed on this part (0.10.0: one coarse test plate of seven extruded
            # parts; a plate fill bridging a groove of the knob): the plain fitter is run as
            # well and the better of the two is kept
            log("")
            log(f"  roughing left the result {d1:.3f} mm from the mesh (tolerance {tol:.3g}): trying the plain fitter too…")
            alt = dst + ".plain.step"
            plain = ({**kw, "ladder": True, "ladder_rough": False, "route": "prismatic"} if route == "prismatic"
                     else {**kw, "ladder": False, "rough": False})
            try:
                rep2 = classic.convert(src, alt, _params(classic, {**plain, "save_labelled": ""}),
                                       log=lambda s: None, progress=lambda f: None)
            except Exception as e:  # noqa
                log(f"  the plain fitter failed ({e}) — keeping the roughed result")
                return rep1
            d2 = rep2.max_dev if rep2.max_dev == rep2.max_dev else float("inf")
            good1 = bool(rep1.ok and rep1.is_solid and rep1.is_valid)
            good2 = bool(rep2.ok and rep2.is_solid and rep2.is_valid)
            log(f"  roughed: {rep1.n_faces} faces, max deviation {d1:.4f} mm, solid {rep1.is_solid}, valid {rep1.is_valid}")
            log(f"  plain:   {rep2.n_faces} faces, max deviation {d2:.4f} mm, solid {rep2.is_solid}, valid {rep2.is_valid}")
            if (good2 and not good1) or (good2 == good1 and d2 < d1):
                shutil.move(alt, dst)
                log("  → kept the plain fitter's result")
                return rep2
            try:
                os.remove(alt)
            except OSError:
                pass
            log("  → kept the roughed result")
        return rep1
    if engine != "best":
        return classic.convert(src, dst, _params(classic, kw), log=log, progress=progress)

    prog = progress or (lambda f: None)
    alt = dst + ".v2.step"
    kw_classic = kw
    kw = dict(kw, save_labelled="")     # only the first pass writes the hardpoint STL
    if os.environ.get("TOSTEP_PARALLEL_ENGINES") == "1":
        log("Experimental second pass: running the first pass and v2 side by side (uses two cores)…")
        if os.environ.get("TOSTEP_BEST_FIRST", "auto") == "classic":
            first_run = lambda pc: classic.convert(src, dst, _params(classic, kw_classic), log=log, progress=pc)  # noqa: E731
        else:
            first_run = lambda pc: convert(src, dst, kw_classic, "auto", log=log, progress=pc)  # noqa: E731
        rep1, kind, val = _run_v2_parallel(src, alt, kw, first_run, prog)
        if kind != "ok":
            log(f"  v2 failed ({val}) — keeping the first result")
            return rep1
        rep2 = val
    else:
        # (0.10.2: the first pass is the Standard preset's engine, which on every test part was as
        # good as Classic or better; TOSTEP_BEST_FIRST=classic keeps the old first pass)
        first = os.environ.get("TOSTEP_BEST_FIRST", "auto")
        if first == "classic":
            rep1 = classic.convert(src, dst, _params(classic, kw_classic), log=log, progress=lambda f: prog(0.5 * f))
        else:
            rep1 = convert(src, dst, kw_classic, "auto", log=log, progress=lambda f: prog(0.5 * f))
        log("")
        log("Experimental second pass: trying the v2 fitter as well…")
        from tostep.plugins.mesh2step import v2 as v2
        try:
            rep2 = v2.convert(src, alt, _params(v2, kw), log=lambda s: None, progress=lambda f: prog(0.5 + 0.5 * f))
        except Exception as e:  # noqa
            log(f"  v2 failed ({e}) — keeping the first result")
            return rep1

    def good(r):
        return bool(r.ok and r.is_solid and r.is_valid)
    d1 = rep1.max_dev if rep1.max_dev == rep1.max_dev else float("inf")
    d2 = rep2.max_dev if rep2.max_dev == rep2.max_dev else float("inf")
    log(f"  first:   {rep1.n_faces} faces, max deviation {d1:.4f} mm, solid {rep1.is_solid}, valid {rep1.is_valid}")
    log(f"  v2:      {rep2.n_faces} faces, max deviation {d2:.4f} mm, solid {rep2.is_solid}, valid {rep2.is_valid}")
    tol1 = float(getattr(rep1, "tol", 0.0) or 0.0)
    use_v2 = good(rep2) and (not good(rep1) or (d2 <= 1.1 * d1 and rep2.n_faces < rep1.n_faces)
                             # (0.10.2) the first pass missed its tolerance and v2 is clearly closer
                             # (the blob plates' cusps: 0.191 against 0.108 mm)
                             or (tol1 and d1 > 1.05 * tol1 and d2 < 0.8 * d1))
    if use_v2:
        shutil.move(alt, dst)
        log("  → kept the v2 result")
        return rep2
    try:
        os.remove(alt)
    except OSError:
        pass
    log("  → kept the first pass's result")
    return rep1
