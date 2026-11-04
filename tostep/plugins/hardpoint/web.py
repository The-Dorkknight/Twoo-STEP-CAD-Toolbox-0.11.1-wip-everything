# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""The Hardpoints tab's one route: what an uploaded STL contains."""
from __future__ import annotations


def register(router):
    def inspect(app, req):
        f = app.files.get(req.body.get("file", ""))
        if not f:
            raise ValueError("Upload the file first")
        from tostep.plugins.mesh2step import hardpoints
        return hardpoints.inspect(f["path"])
    router.post("/api/hp/inspect", inspect)
