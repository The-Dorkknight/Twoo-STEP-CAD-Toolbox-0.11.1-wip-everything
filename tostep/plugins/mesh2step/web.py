# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""mesh2step's part of the web interface: the outline preview, the quality-preset table, and the
hardpoint-STL side file."""
from __future__ import annotations

import json
import os

from tostep.core.job import scale_for


def page_vars():
    from . import presets
    return {"__PRESETS__": json.dumps(presets.for_page())}


def prepare_job(app, ctx, params):
    """Convert: 'make_labelled' asks for the mesh back as a hardpoint STL carrying the faces found."""
    if params.get("make_labelled"):
        params = dict(params, save_labelled=os.path.join(app.work, ctx.jid + "_hardpoints.stl"))
    return params


def register(router):
    def preview(app, req):
        from . import coerce, profile2d, profile_params
        b = req.body
        f = app.files.get(b.get("file", ""))
        if not f:
            raise ValueError("Upload the file first")
        p = coerce(b.get("params", {}))
        with app.run_lock:
            svg, info = profile2d.preview_svg(f["path"], profile_params(p, scale_for(p["units"])))
        return {"svg": svg, "info": info}
    router.post("/api/preview", preview)
