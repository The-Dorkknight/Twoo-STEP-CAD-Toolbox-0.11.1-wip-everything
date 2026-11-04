# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""
worker — runs ONE job in its own process so the app can cancel it (kill the process) without touching
the app itself.

    python -m tostep.core.worker job.json

job.json: {"plugin": "jetwash", "src": "...", "out": "...", "params": {...}}
Prints the "@@" protocol lines described in tostep/core/job.py.
"""
from __future__ import annotations

import json
import sys
import traceback

from tostep.core import params as _params
from tostep.core.job import Job, UserError, emit, scale_for


def main(path):
    from tostep.core import perf, registry
    perf.apply_nice()
    with open(path, encoding="utf-8") as fh:
        j = json.load(fh)
    log = lambda s: [emit(l=x) for x in (str(s).splitlines() or [""])]
    prog = lambda x: emit(p=float(x))
    try:
        reg = registry.discover()
        plugin = reg.plugins.get(j["plugin"])
        if plugin is None or plugin.problem:
            raise UserError(f"The plugin '{j['plugin']}' is not available"
                            + (f": {plugin.problem}" if plugin else "."))
        m = plugin.m
        p = dict(j["params"]) if m.raw_params else _params.coerce(m, j["params"])
        job = Job(plugin=m.id, kind=m.kind, src=j["src"], out=j["out"], params=p,
                  scale=scale_for(p.get("units", "mm")), log=log, progress=prog)
        rep = plugin.run_function()(job)
        if rep is None:
            return 1
        _finish(rep)
        return 0
    except UserError as e:
        emit(e=str(e))
        return 1
    except Exception as e:  # noqa
        emit(e=str(e) or e.__class__.__name__)
        emit(l=traceback.format_exc())
        return 1


def _finish(rep):
    try:                                   # the report as plain numbers, for the diagnostic code
        from tostep.core import diagcode
        emit(d=diagcode.facts(rep))
    except Exception:                      # never let the extra spoil a finished job
        pass
    emit(s=rep.summary())


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
