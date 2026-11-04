# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""The Image → STEP tab's routes: the trace preview, the 'what scale source does this picture carry?'
guess, and the printable sheets (calibration sheet, stand, token, marker sheet)."""
from __future__ import annotations

import os

from . import image2step as im


def _image_file(app, ref):
    f = app.files.get(ref)
    if not f:
        raise ValueError("Upload the picture first")
    if os.path.splitext(f["name"])[1].lower() not in im.IMAGE_EXT:
        raise ValueError("That is not a picture (PNG, JPG, BMP, TIFF, WebP)")
    return f["path"]


def _empty_sheet(app, p):
    """The empty-sheet photo arrives as an uploaded file's id: hand the tracer its path."""
    p = dict(p or {})
    if p.get("empty_sheet"):
        p["empty_sheet"] = _image_file(app, p["empty_sheet"])
    return p


def prepare_job(app, ctx, params):
    params = _empty_sheet(app, params)
    im.options(params)                         # bad numbers are reported now, not in the worker
    return dict(params, extras_dir=app.out, extras_stem=ctx.stem)       # where an SVG / DXF copy goes


def register(router):
    from tostep.frontends.web.server import Response

    def preview(app, req):
        src = _image_file(app, req.body.get("file", ""))
        p = _empty_sheet(app, req.body.get("params", {}))
        with app.run_lock:
            png, info = im.preview(src, p)
        return {"png": png, "info": info}

    def guess(app, req):
        return im.guess(_image_file(app, req.body.get("file", "")))

    def paper_of(req):
        return req.arg("paper", "A4")

    def sheets(kind):
        def handler(app, req):
            if kind == "calsheet":
                paper = paper_of(req)
                data, ctype = im.calsheet_pdf(paper), "application/pdf"
                fn = "calibration_sheet_%s.pdf" % ("".join(c for c in paper if c.isalnum()) or "A4")
            elif kind == "stand":
                data, ctype, fn = im.stand_stl(), "model/stl", "calibration_stand_50mm.stl"
            elif kind == "tokensheet":
                side = float(req.arg("side", "40"))
                paper = paper_of(req)
                data, ctype = im.token_pdf(paper, side), "application/pdf"
                fn = "scale_token_sheet_%s.pdf" % ("".join(c for c in paper if c.isalnum()) or "A4")
            elif kind == "token":
                side = float(req.arg("side", "40"))
                thick = float(req.arg("thick", "1.2"))
                data, ctype = im.token_stl(side, max(thick, 0.2)), "model/stl"
                fn = "scale_token_%gmm.stl" % side
            else:
                paper = paper_of(req)
                data, ctype = im.sheet_pdf(paper), "application/pdf"
                fn = "marker_sheet_%s.pdf" % paper
            return Response(data, ctype, fn)
        return handler

    router.post("/api/image/preview", preview)
    router.post("/api/image/guess", guess)
    for kind in ("token", "tokensheet", "sheet", "calsheet", "stand"):
        router.get("/api/image/" + kind, sheets(kind))
