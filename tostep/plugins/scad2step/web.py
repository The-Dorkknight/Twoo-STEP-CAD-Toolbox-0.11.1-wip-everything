# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""The SCAD → STEP tab's routes: choosing a file from disk (so libraries beside it are found), finding
OpenSCAD, the model's Customizer parameters and the live link."""
from __future__ import annotations

import os

from . import bridge


def prepare_job(app, ctx, params):
    """A model chosen from disk saves its STEP beside it (like the live link), unless asked otherwise."""
    if ctx.local and params.get("where", "beside") == "beside":
        ctx.dest_dir = os.path.dirname(ctx.src)
        ctx.overwrite = True
    return bridge.job_params(app, ctx.file_ref, params, params.get("values"))


def open_path(app, path):
    """`tostep ui --open model.scad` lands on this tab with the model chosen."""
    app.preselect = bridge.add_local(app, path)


def shutdown(app):
    bridge.stop_watch(app)


def register(router):
    def status(app, req):
        pre, app.preselect = getattr(app, "preselect", None), None
        try:
            pre = bridge.file_info(app, pre) if pre else None
        except KeyError:
            pre = None
        return {"openscad": bridge.openscad_status(app), "recent": bridge.recent(app), "preselect": pre}

    def watch(app, req):
        return bridge.watch_state(app, int(req.arg("since", "0")))

    def upload(app, req):
        name = _safe(req.arg("name", "file"))
        if os.path.splitext(name)[1].lower() not in bridge.SCAD_EXT:
            req.read()
            return {"error": "Drop a .scad or .csg file"}, 400
        fid = bridge.add_upload(app, name, req.read, req.length)
        return bridge.file_info(app, fid)

    def action(app, req):
        what, body = req.groups[0], req.body
        if what == "browse":
            p = bridge.native_open_dialog(body.get("start", ""))
            if not p:
                return {"cancelled": True}
            return bridge.file_info(app, bridge.add_local(app, p))
        if what == "open-path":
            return bridge.file_info(app, bridge.add_local(app, body.get("path", "")))
        if what == "rescan":
            return bridge.file_info(app, body.get("file", ""))
        if what == "settings":
            return {"openscad": bridge.set_openscad(app, body.get("openscad"))}
        if what == "find-openscad":
            return {"openscad": bridge.openscad_status(app, refresh=True)}
        if what == "watch/start":
            out = bridge.start_watch(app, body.get("file", ""), body.get("opts", {}), body.get("values", {}), app.out)
            return {"ok": True, "out": out}
        if what == "watch/stop":
            bridge.stop_watch(app)
            return {"ok": True}
        if what == "open-in-openscad":
            f = app.files.get(body.get("file", ""))
            bridge.open_in_openscad(f and f["path"])
            return {"ok": True}
        return {"error": "not found"}, 404

    router.get("/api/scad/status", status)
    router.get("/api/scad/watch", watch)
    router.post("/api/scad/upload", upload)
    router.post("re:/api/scad/(.+)", action)


def _safe(name):
    from tostep.frontends.web.server import safe_name
    return safe_name(name)
