# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
# Copied from the ImageToSketch project by tools/sync_imagetosketch.py - edit it there.
"""Printed scale token: a black square you print once - on paper (make_pdf: the sheet is then
also the white background the part lies on) or in plastic (make_stl) - and lay beside the part.

The token is a square of known side with three kinds of corner - two sharp, one clipped
(chamfered), one rounded - so it cannot be mistaken for some other dark square in the photo.
Finding it gives:

  * scale: millimetres per pixel in the plane of the token's top face;
  * tilt: the square seen as a general quadrilateral gives the homography of that plane, so a
    photo taken a little off vertical is straightened;
  * with the lens's focal length (from the photo's EXIF, or from a camera distance you type):
    where the camera was. That is what allows the two corrections a flat marker cannot give:

      - the token's own thickness. On the sides facing the camera you see its wall, so the dark
        shape is a little bigger than the top face;
      - the part's height. A face nearer the camera than the token looks bigger by
        height / camera distance. Say how much higher the traced face is than the token's top
        and the picture is straightened in THAT plane instead.

Frames used below. "Plane" coordinates are millimetres in the token's top face, X to the right
and Y down as the camera sees them, so Z points away from the camera and a height h above the
token is Z = -h. H maps plane (X, Y, 1) to image pixels (x, y, 1): H = K [r1 r2 t].
"""
import math
import struct

import cv2
import numpy as np

SIDE_MM = 40.0              # default side of the square
THICK_MM = 1.2              # thickness of the 3D-printed token (6 layers at 0.2 mm)
PHONE_35MM = 25.0           # a phone's main camera, 35 mm-equivalent focal length (most are 23 - 27 mm)
PAGES = {"A4": (210.0, 297.0), "Letter": (215.9, 279.4)}       # mm
PAGE_MARGIN_MM = 20.0       # paper edge to the token on the printed page
# the few lines of small print under the token on the printed page, as a box in units of the
# token's side (x0, y0, x1, y1; the token is the unit square, y down the page). It keeps a fifth
# of a side clear of the token, so that a soft or distant photo does not run the two together.
CAPTION = (-0.03, 1.17, 1.03, 1.56)
CAPTION_TOP = 1.225         # first baseline of the small print, in sides below the token's top edge
CHAMFER = 0.2               # clipped corner: each leg, as a fraction of the side
ROUND = 0.2                 # rounded corner: radius, as a fraction of the side
# how far the outline stays away from each corner of the bounding square, as a fraction of the
# side, going round: sharp, clipped, rounded, sharp
SIGNATURE = (0.0, CHAMFER / math.sqrt(2.0), ROUND * (math.sqrt(2.0) - 1.0), 0.0)


# ------------------------------------------------------------------ shape --
def outline(side=SIDE_MM, n_arc=24):
    """The token's outline as a closed polygon (no repeated point), in mm, starting at the
    sharp corner (0, 0) and going round by (side, 0)."""
    c, r, s = CHAMFER * side, ROUND * side, float(side)
    pts = [(0.0, 0.0), (s - c, 0.0), (s, c), (s, s - r)]
    for i in range(1, n_arc + 1):
        a = (math.pi / 2.0) * i / n_arc
        pts.append((s - r + r * math.cos(a), s - r + r * math.sin(a)))
    pts.append((0.0, s))
    return np.array(pts, float)


def make_stl(path, side=SIDE_MM, thickness=THICK_MM):
    """Write the token as a binary STL (millimetres). Returns the path."""
    if not (side > 0 and thickness > 0):
        raise ValueError("Token side and thickness must be positive.")
    P = outline(side, 48)
    n = len(P)
    tris = []
    for i in range(1, n - 1):                                  # convex: fans for the two caps
        tris.append(((P[0][0], P[0][1], thickness), (P[i][0], P[i][1], thickness), (P[i + 1][0], P[i + 1][1], thickness)))
        tris.append(((P[0][0], P[0][1], 0.0), (P[i + 1][0], P[i + 1][1], 0.0), (P[i][0], P[i][1], 0.0)))
    for i in range(n):                                         # walls
        a, b = P[i], P[(i + 1) % n]
        tris.append(((a[0], a[1], 0.0), (b[0], b[1], 0.0), (b[0], b[1], thickness)))
        tris.append(((a[0], a[1], 0.0), (b[0], b[1], thickness), (a[0], a[1], thickness)))
    head = ("ImageToSketch scale token %.2f x %.2f mm, %.2f thick" % (side, side, thickness)).encode("ascii")
    out = bytearray(head.ljust(80, b" ")[:80]) + struct.pack("<I", len(tris))
    for t in tris:
        v = np.array(t, float)
        nrm = np.cross(v[1] - v[0], v[2] - v[0])
        ln = float(np.linalg.norm(nrm)) or 1.0
        out += struct.pack("<12fH", *(nrm / ln), *v[0], *v[1], *v[2], 0)
    with open(path, "wb") as f:
        f.write(bytes(out))
    return path


def _caption_lines(side):
    return ["ImageToSketch scale token, %g mm." % side,
            "Print at 100 %, then measure a black",
            "square and type what you measure.",
            "Keep both squares in view, and the part",
            "off the squares and this text."]


def page_tokens(paper="A4", side=SIDE_MM):
    """Top left corner (mm from the top left of the paper) of the two tokens on the printed
    sheet: one near the top left corner, one near the bottom right, the same way up."""
    pw, ph = PAGES[paper]
    return [(PAGE_MARGIN_MM, PAGE_MARGIN_MM), (pw - PAGE_MARGIN_MM - side, ph - PAGE_MARGIN_MM - side)]


def make_pdf(path, paper="A4", side=SIDE_MM):
    """Write the token as a one-page vector PDF to print on plain paper: the black token near the
    top left corner with a few lines of small print under it, the same token again near the
    bottom right corner, the rest of the sheet left white for the part. One token is enough for
    scale; with both in view, tilt is measured across the whole sheet. Returns the path."""
    if paper not in PAGES:
        raise ValueError("Paper must be one of: %s" % ", ".join(PAGES))
    pw, ph = PAGES[paper]
    m = PAGE_MARGIN_MM
    if not (10.0 <= side <= (min(pw, ph) - 2 * m) / 2.6):
        raise ValueError("A token of %g mm does not fit twice on %s paper: 10 to %d mm does."
                         % (side, paper, int((min(pw, ph) - 2 * m) / 2.6)))
    k = 72.0 / 25.4                                            # mm -> pt

    c, r, s = CHAMFER * side, ROUND * side, float(side)
    q = 0.5522847498 * r                                       # quarter circle as one Bezier curve
    ops = ["0 0 0 rg"]
    for ox, oy in page_tokens(paper, side):
        def pt(x, y):                                          # mm from the token's corner -> PDF points
            return "%.3f %.3f" % ((ox + x) * k, (ph - oy - y) * k)
        ops += [pt(0, 0) + " m", pt(s - c, 0) + " l", pt(s, c) + " l", pt(s, s - r) + " l",
                "%s %s %s c" % (pt(s, s - r + q), pt(s - r + q, s), pt(s - r, s)),
                pt(0, s) + " l", "h f"]

    def pt(x, y):
        return "%.3f %.3f" % ((m + x) * k, (ph - m - y) * k)

    size = 5.6 * side / SIDE_MM                                # pt; the caption scales with the token
    lead = 0.068 * side
    for i, line in enumerate(_caption_lines(side)):
        line = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        ops.append("BT /F1 %.2f Tf %s Td (%s) Tj ET" % (size, pt(0, CAPTION_TOP * side + i * lead), line))
    stream = "\n".join(ops).encode("latin-1")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        ("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %.3f %.3f] /Contents 5 0 R "
         "/Resources << /Font << /F1 4 0 R >> >> >>" % (pw * k, ph * k)).encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offs = []
    for i, o in enumerate(objs, start=1):
        offs.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n" % (len(objs) + 1) + b"0000000000 65535 f \n"
    for o in offs:
        out += b"%010d 00000 n \n" % o
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    with open(path, "wb") as f:
        f.write(bytes(out))
    return path


def render_page(paper="A4", mm_per_px=0.2, side=SIDE_MM):
    """The printed token page as a grey picture (for tests and documentation; the small print is
    drawn with OpenCV's own lettering, so it only looks roughly like the PDF)."""
    pw, ph = PAGES[paper]
    W, H = int(round(pw / mm_per_px)), int(round(ph / mm_per_px))
    ss = 4
    f = ss / mm_per_px
    big = np.full((H * ss, W * ss), 255, np.uint8)
    lead = 0.068 * side
    for i, line in enumerate(_caption_lines(side)):
        org = (int(PAGE_MARGIN_MM * f), int((PAGE_MARGIN_MM + CAPTION_TOP * side + i * lead) * f))
        cv2.putText(big, line, org, cv2.FONT_HERSHEY_SIMPLEX, 0.022 * side / SIDE_MM * f, 0,
                    max(1, int(round(0.2 * f))), cv2.LINE_AA)
    page = cv2.resize(big, (W, H), interpolation=cv2.INTER_AREA).astype(np.float32)
    # the token itself exactly: a convex shape, so its edge distance is the largest of the
    # distances to its sides, and a pixel's coverage follows from that
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    for corner in page_tokens(paper, side):
        P = (outline(side, 96) + corner) / mm_per_px - 0.5
        d = np.full((H, W), -1e9, np.float32)
        for a, b in zip(P, np.roll(P, -1, axis=0)):
            e = b - a
            n = np.array([e[1], -e[0]]) / np.hypot(*e)         # outward for this winding (y down)
            d = np.maximum(d, (xx - a[0]) * n[0] + (yy - a[1]) * n[1])
        page *= 1.0 - np.clip(0.5 - d, 0.0, 1.0)
    return np.clip(page, 0, 255).astype(np.uint8)


# -------------------------------------------------------------- matching --
def _line_fit(P):
    """Total-least-squares line through points: (a, b, c) with a*x + b*y + c = 0, a^2 + b^2 = 1."""
    c = P.mean(0)
    _, _, vt = np.linalg.svd(P - c, full_matrices=False)
    a, b = -vt[0, 1], vt[0, 0]
    return np.array([a, b, -(a * c[0] + b * c[1])])


def _cross2(a, b):
    return float(a[0] * b[1] - a[1] * b[0])


def _meet(l1, l2):
    p = np.cross(l1, l2)
    if abs(p[2]) < 1e-12:
        return None
    return p[:2] / p[2]


def _apply(H, P):
    P = np.asarray(P, float).reshape(-1, 2)
    q = np.c_[P, np.ones(len(P))] @ np.asarray(H, float).T
    return q[:, :2] / q[:, 2:3]


def _orient(order, P):
    """Points given in the token's own unit square (as `outline` draws it) -> the unit square of
    the corners found in the picture, for one of the 8 ways the token can lie."""
    P = np.asarray(P, float).reshape(-1, 2)
    rot, mirror = order
    if mirror:
        P = np.c_[P[:, 1], P[:, 0]]                            # swap axes = mirror about the diagonal
    c, s = [(1, 0), (0, 1), (-1, 0), (0, -1)][rot]
    return (P - 0.5) @ np.array([[c, s], [-s, c]]) + 0.5


def _ideal(order, n=160):
    """The token outline in unit-square coordinates for one of the 8 ways it can lie:
    order = (rotation 0..3, mirrored)."""
    Q = _orient(order, outline(1.0, 24))
    d = np.r_[0, np.cumsum(np.hypot(*np.diff(np.vstack([Q, Q[:1]]), axis=0).T))]
    t = np.linspace(0, d[-1], n, endpoint=False)
    Qc = np.vstack([Q, Q[:1]])
    return np.c_[np.interp(t, d, Qc[:, 0]), np.interp(t, d, Qc[:, 1])]


_CORNERS = np.array([[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]])


def _signature(order):
    """Expected keep-away distance at the unit square's corners (0,0), (1,0), (1,1), (0,1)."""
    I = _ideal(order, 400)
    return [float(np.hypot(*(I - c).T).min()) for c in _CORNERS]


_ORDERS = [(r, m) for m in (False, True) for r in range(4)]
_SIGS = None


def match(pts, min_side_px=24.0):
    """Is this closed outline (N x 2, pixels) the token? Returns None, or a dict with
    quad (4 x 2 px, the corners of the full square, in screen-clockwise order), lines (the four
    fitted side lines), side_px, rms_px (straightness of the sides), shape_err (worst deviation
    from the ideal token, as a fraction of the side) and order."""
    global _SIGS
    if _SIGS is None:
        _SIGS = [_signature(o) for o in _ORDERS]
    P = np.asarray(pts, float)
    if len(P) < 40:
        return None
    (cx, cy), (w, h), _ = cv2.minAreaRect(P.astype(np.float32))
    if min(w, h) < min_side_px or min(w, h) / max(w, h) < 0.6:
        return None
    hull_area = cv2.contourArea(cv2.convexHull(P.astype(np.float32)))
    if cv2.contourArea(P.astype(np.float32)) < 0.9 * hull_area:
        return None                                            # the token is convex
    quad = cv2.boxPoints(((cx, cy), (w, h), _)).astype(float)
    if _cross2(quad[1] - quad[0], quad[2] - quad[1]) < 0:     # make it positive in y-down pixels
        quad = quad[::-1].copy()
    lines = None
    for _it in range(3):
        lines, rms, npts = [], [], []
        for i in range(4):
            a, b = quad[i], quad[(i + 1) % 4]
            L = float(np.hypot(*(b - a)))
            u = (b - a) / L
            nrm = np.array([-u[1], u[0]])
            along = (P - a) @ u / L
            dist = np.abs((P - a) @ nrm)
            pick = (along > 0.29) & (along < 0.71) & (dist < max(2.5, 0.06 * L))
            if pick.sum() < 6:
                return None
            ln = _line_fit(P[pick])
            r = np.abs(P[pick] @ ln[:2] + ln[2])
            keep = r <= max(3.0 * np.median(r), 0.3)           # drop a speck of dirt on the edge
            if keep.sum() >= 6 and not keep.all():
                ln = _line_fit(P[pick][keep])
                r = np.abs(P[pick][keep] @ ln[:2] + ln[2])
            lines.append(ln)
            rms.append(float(np.sqrt((r ** 2).mean())))
            npts.append(int(pick.sum()))
        q = [_meet(lines[i - 1], lines[i]) for i in range(4)]
        if any(v is None for v in q):
            return None
        quad = np.array(q)
    side = float(np.mean([np.hypot(*(quad[(i + 1) % 4] - quad[i])) for i in range(4)]))
    G = cv2.getPerspectiveTransform(quad.astype(np.float32), _CORNERS.astype(np.float32)).astype(float)
    U = _apply(G, P)
    d = [float(np.hypot(*(U - c).T).min()) for c in _CORNERS]
    best = None
    for o, sig in zip(_ORDERS, _SIGS):
        e = max(abs(a - b) for a, b in zip(d, sig))
        if best is None or e < best[0]:
            best = (e, o)
    if best[0] > 0.028:
        return None
    I = _ideal(best[1], 240)
    dev = np.sqrt(((U[:, None, :] - I[None, :, :]) ** 2).sum(2).min(1))
    if float(dev.max()) > 0.035 or float(dev.mean()) > 0.012:
        return None
    return dict(quad=quad, lines=lines, side_px=side, rms_px=float(np.mean(rms)), corner_err=best[0],
                shape_err=float(dev.max()), order=best[1], outline=P)


def _edge_lines(gray, quad, order, iters=2):
    """Measure the four sides again straight from the picture: many short intensity profiles
    across each side, each giving the half-way point between dark and light to a few hundredths
    of a pixel, then a line through them. Uses as much of each side as is straight (right up to
    a sharp corner, well short of the clipped and rounded ones). Returns (lines, quad, rms_px)."""
    g = gray.astype(np.float32)
    sig = _signature(order)
    quad = np.asarray(quad, float).copy()
    lines, rms = [None] * 4, [0.0] * 4
    for _it in range(iters):
        for i in range(4):
            a, b = quad[i], quad[(i + 1) % 4]
            L = float(np.hypot(*(b - a)))
            u = (b - a) / L
            nrm = np.array([-u[1], u[0]])
            lo = 0.27 if sig[i] > 0.02 else max(0.05, 6.0 / L)
            hi = 1.0 - (0.27 if sig[(i + 1) % 4] > 0.02 else max(0.05, 6.0 / L))
            n = int(np.clip((hi - lo) * L / 1.5, 12, 400))
            ts = np.linspace(lo, hi, n)
            R = float(np.clip(0.035 * L, 4.0, 9.0))
            off = np.arange(-R, R + 1e-6, 0.25, dtype=np.float32)
            base = a[None, :] + ts[:, None] * (b - a)[None, :]
            X = (base[:, 0:1] + nrm[0] * off[None, :]).astype(np.float32)
            Y = (base[:, 1:2] + nrm[1] * off[None, :]).astype(np.float32)
            prof = cv2.remap(g, np.ascontiguousarray(X), np.ascontiguousarray(Y), cv2.INTER_LINEAR,
                             borderMode=cv2.BORDER_REPLICATE)
            k = max(2, len(off) // 5)
            p0, p1 = prof[:, :k].mean(1), prof[:, -k:].mean(1)
            mid = (p0 + p1) / 2.0
            good = np.abs(p1 - p0) > 0.5 * float(np.median(np.abs(p1 - p0)))
            sgn = np.sign(p1 - p0)
            d = (prof - mid[:, None]) * sgn[:, None]           # rises through zero at the edge
            idx = np.argmax(d > 0, axis=1)
            good &= (idx > 0) & (d[:, 0] < 0)
            j = np.clip(idx, 1, len(off) - 1)
            r = np.arange(len(ts))
            d0, d1 = d[r, j - 1], d[r, j]
            pos = off[j - 1] + 0.25 * (-d0) / np.maximum(d1 - d0, 1e-6)
            pts = base + nrm[None, :] * pos[:, None]
            if good.sum() < 8:
                return None
            Q = pts[good]
            ln = _line_fit(Q)
            res = np.abs(Q @ ln[:2] + ln[2])
            keep = res <= max(2.5 * 1.4826 * float(np.median(res)), 0.08)
            if keep.sum() >= 8:
                ln = _line_fit(Q[keep])
                res = np.abs(Q[keep] @ ln[:2] + ln[2])
            lines[i], rms[i] = ln, float(np.sqrt((res ** 2).mean()))
        q = [_meet(lines[i - 1], lines[i]) for i in range(4)]
        if any(v is None for v in q):
            return None
        quad = np.array(q)
    return lines, quad, float(np.mean(rms))


_NOT_FOUND = ("No scale token found. It must be the black square with one clipped and one "
              "rounded corner, lying flat on a light surface, fully in view, not touching "
              "the part and not in glare.")


def find_token(img, search_side=1600):
    """Find the token in a picture (uint8, grey or BGR). Returns the match dict of `match`, in the
    picture's own pixel coordinates. Raises ValueError with a plain message if there is none."""
    return find_tokens(img, search_side, limit=1)[0]


def find_tokens(img, search_side=1600, limit=2):
    """Every token in the picture (the printed sheet carries two), best-shaped first, each
    measured at full resolution. Raises ValueError with a plain message if there is none."""
    from .detect import detect
    H, W = img.shape[:2]
    f = min(1.0, search_side / float(max(H, W)))
    small = cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_AREA) if f < 1 else img
    det = detect(small, polarity="dark", keep_work=False, line_art="off", thin_features=False)
    got = []
    for L in det.loops:
        if L.hole or L.conf < 0.45:
            continue
        m = match(L.pts)
        if m is None:
            continue
        c = m["quad"].mean(0)
        same = [i for i, o in enumerate(got) if np.hypot(*(o["quad"].mean(0) - c)) < 0.5 * m["side_px"]]
        if not same:
            got.append(m)
        elif m["shape_err"] < got[same[0]]["shape_err"]:      # the same token at another threshold level
            got[same[0]] = m
    if not got:
        raise ValueError(_NOT_FOUND)
    got.sort(key=lambda m: m["shape_err"])
    gray = img if img.ndim == 2 else cv2.cvtColor(img[:, :, :3], cv2.COLOR_BGR2GRAY)
    return [_refine(img, gray, m, f) for m in got[:limit]]


def _refine(img, gray, best, f):
    """One token found on the reduced picture, measured again on the full one."""
    from .detect import detect
    H, W = img.shape[:2]
    # measure again at full resolution, on a tight crop
    q = (best["quad"] + 0.5) / f - 0.5
    pad = 0.22 * best["side_px"] / f
    x0, y0 = np.floor(q.min(0) - pad).astype(int)
    x1, y1 = np.ceil(q.max(0) + pad).astype(int)
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(W, x1), min(H, y1)
    crop = img[y0:y1, x0:x1]
    g = min(1.0, 1300.0 / float(max(crop.shape[:2])))
    if g < 1:
        crop = cv2.resize(crop, None, fx=g, fy=g, interpolation=cv2.INTER_AREA)
    fine = None
    if min(crop.shape[:2]) >= 32 and (g * 1.0 / f > 1.15 or f < 1):
        det2 = detect(crop, polarity="dark", keep_work=False, line_art="off", thin_features=False)
        for L in sorted(det2.loops, key=lambda l: -l.area):
            if L.hole:
                continue
            m = match(L.pts)
            if m is not None:
                fine = m
                break
    if fine is not None:
        for k in ("quad", "outline"):
            fine[k] = (fine[k] + 0.5) / g - 0.5 + np.array([x0, y0], float)
        fine["side_px"] /= g
        fine["rms_px"] /= g
        fine["lines"] = None
        best = fine
    else:
        best["quad"] = q
        best["outline"] = (best["outline"] + 0.5) / f - 0.5
        best["side_px"] /= f
        best["rms_px"] /= f
        best["lines"] = None
    # the four sides once more, measured directly on the picture at full resolution
    fit = _edge_lines(gray, best["quad"], best["order"])
    if fit is not None and np.abs(fit[1] - best["quad"]).max() < 0.03 * best["side_px"] + 2.0:
        best["lines"], best["quad"], best["rms_px"] = fit
        best["side_px"] = float(np.mean([np.hypot(*(fit[1][(i + 1) % 4] - fit[1][i])) for i in range(4)]))
    else:
        quad, P, lines = best["quad"], best["outline"], []
        for i in range(4):
            a, b = quad[i], quad[(i + 1) % 4]
            L = float(np.hypot(*(b - a)))
            u = (b - a) / L
            nrm = np.array([-u[1], u[0]])
            along = (P - a) @ u / L
            pick = (along > 0.29) & (along < 0.71) & (np.abs((P - a) @ nrm) < max(2.5, 0.03 * L))
            lines.append(_line_fit(P[pick]) if pick.sum() >= 6 else np.cross(np.r_[a, 1.0], np.r_[b, 1.0]))
        best["lines"] = lines
    return best


# ---------------------------------------------------------------- camera --
def intrinsics(shape, focal_35mm=None, focal_px=None):
    """Camera matrix for a picture of this shape. focal_35mm is the "35 mm equivalent" focal
    length phones and cameras write into EXIF; it is defined on the frame diagonal (43.27 mm)."""
    h, w = shape[:2]
    if focal_px is None:
        if not focal_35mm or focal_35mm <= 0:
            return None
        focal_px = focal_35mm / 43.2666 * math.hypot(w, h)
    return np.array([[focal_px, 0.0, (w - 1) / 2.0], [0.0, focal_px, (h - 1) / 2.0], [0.0, 0.0, 1.0]])


def pose(H, K):
    """Rotation and translation of the plane relative to the camera from H = K [r1 r2 t]."""
    B = np.linalg.inv(K) @ H
    lam = 2.0 / (np.linalg.norm(B[:, 0]) + np.linalg.norm(B[:, 1]))
    if B[2, 2] * lam < 0:
        lam = -lam
    r1, r2, t = lam * B[:, 0], lam * B[:, 1], lam * B[:, 2]
    U, _, Vt = np.linalg.svd(np.c_[r1, r2, np.cross(r1, r2)])
    R = U @ Vt
    if np.linalg.det(R) < 0:
        R = U @ np.diag([1.0, 1.0, -1.0]) @ Vt
    return R, t


def plane_h(K, R, t, height=0.0):
    """Homography (plane mm -> image px) of the plane `height` mm ABOVE the reference plane
    (towards the camera)."""
    return K @ np.c_[R[:, 0], R[:, 1], t - height * R[:, 2]]


def _square(side):
    return np.array([[0.0, 0.0], [side, 0.0], [side, side], [0.0, side]])


def solve(found, side_mm=SIDE_MM, K=None, thickness=0.0):
    """Homography of the token's top face (plane mm -> image px), and the camera pose if the
    camera matrix K is known. With K and a thickness, the side walls the camera can see are
    allowed for: an edge on a side facing the camera is the token's BOTTOM edge, not its top."""
    sq = _square(side_mm)
    H = cv2.getPerspectiveTransform(sq.astype(np.float32), found["quad"].astype(np.float32)).astype(float)
    if K is None:
        return H, None
    R, t = pose(H, K)
    near = [False] * 4
    if thickness > 0 and found.get("lines"):
        centre = sq.mean(0)
        for _ in range(4):
            cam = -R.T @ t                                     # camera position in plane coordinates
            Hb = plane_h(K, R, t, -thickness)                  # the token's footprint
            Ht = plane_h(K, R, t, 0.0)
            pl = []
            for i, ln in enumerate(found["lines"]):
                mid = (sq[i] + sq[(i + 1) % 4]) / 2.0
                near[i] = bool(np.dot(cam[:2] - mid, mid - centre) > 0)
                pl.append((Hb if near[i] else Ht).T @ ln)
            c = [_meet(pl[i - 1], pl[i]) for i in range(4)]
            if any(v is None for v in c):
                break
            x = _apply(Ht, np.array(c))
            H = cv2.getPerspectiveTransform(sq.astype(np.float32), x.astype(np.float32)).astype(float)
            R, t = pose(H, K)
    return H, _camera(R, t, K, near)


def _camera(R, t, K, near=()):
    cam = -R.T @ t
    tilt = math.degrees(math.acos(max(-1.0, min(1.0, float(R[2, 2])))))
    return dict(R=R, t=t, K=K, camera=cam, height=float(-cam[2]), tilt_deg=tilt, near=list(near))


def page_layouts(side_mm):
    """Where the second token of a printed sheet lies relative to the first (mm, along the first
    token's own axes), for each paper size, given the side as MEASURED on the print. A print
    that came out a little small or large scales the whole sheet, so the offset scales with the
    side; the nominal side is taken to be the nearest 5 mm."""
    out = []
    for nominal in sorted({round(side_mm / 5.0) * 5.0, SIDE_MM}):
        if nominal <= 0 or abs(side_mm / nominal - 1.0) > 0.08:
            continue
        k = side_mm / nominal
        for paper, (pw, ph) in PAGES.items():
            d = (pw - 2 * PAGE_MARGIN_MM - nominal, ph - 2 * PAGE_MARGIN_MM - nominal)
            if min(d) > 1.5 * nominal:
                out.append((paper, k * d[0], k * d[1]))
    return out


def _own_corners(found):
    """The picture positions of the token's corners in the token's OWN order: the sharp corner
    the outline starts at, then round by the clipped one, the rounded one, the other sharp one."""
    u = _orient(found["order"], _CORNERS)
    idx = [int(np.argmin(np.hypot(*(_CORNERS - p).T))) for p in u]
    return found["quad"][idx]


def sheet_pair(tokens, side_mm):
    """Are these two tokens the pair printed on one sheet (make_pdf)? Returns None, or
    (first, second, paper, H) with H the homography sheet mm -> picture px, the origin at the
    first token's sharp corner; fitted through all eight corners, so tilt is measured across
    the width of the sheet instead of across one token."""
    if len(tokens) < 2:
        return None
    for a, b in ((tokens[0], tokens[1]), (tokens[1], tokens[0])):
        # (the "order" of a match counts from wherever its corner list happens to start, so two
        # tokens lying the same way can have different ones: compare them corner by corner below)
        if a["order"][1] or b["order"][1] or not 0.7 < a["side_px"] / b["side_px"] < 1.43:
            continue
        sq = _square(side_mm)
        qa, qb = _own_corners(a), _own_corners(b)
        Ha = cv2.getPerspectiveTransform(sq.astype(np.float32), qa.astype(np.float32)).astype(float)
        at = _apply(np.linalg.inv(Ha), qb).mean(0) - side_mm / 2.0      # where b's corner is, by a's reckoning
        for paper, dx, dy in page_layouts(side_mm):
            if np.hypot(at[0] - dx, at[1] - dy) > 0.06 * np.hypot(dx, dy):
                continue
            src = np.vstack([sq, sq + [dx, dy]]).astype(np.float64)
            H, _ = cv2.findHomography(src, np.vstack([qa, qb]).astype(np.float64), 0)
            if H is None:
                continue
            if np.abs(_apply(H, src) - np.vstack([qa, qb])).max() > 0.02 * a["side_px"] + 1.0:
                continue                                       # not flat, or not this sheet
            return a, b, paper, H / H[2, 2]
    return None


def rectify(img, Hobj, max_side=2400, crop=None):
    """Straight-on view of the plane whose homography (plane mm -> image px) is Hobj.

    The token may lie at any angle, so the plane's axes are first turned to keep the picture the
    way up it was taken. Returns (image, mm_per_px, M) where M maps output pixels to input pixels.
    crop = (x0, y0, x1, y1) in INPUT pixels limits the output to that part of the picture."""
    h, w = img.shape[:2]
    Hinv = np.linalg.inv(Hobj)
    c0 = _apply(Hinv, [[(w - 1) / 2.0, (h - 1) / 2.0]])[0]
    c1 = _apply(Hinv, [[(w - 1) / 2.0 + 1.0, (h - 1) / 2.0]])[0]
    phi = math.atan2(c1[1] - c0[1], c1[0] - c0[0])
    cs, sn = math.cos(phi), math.sin(phi)
    Rot = np.array([[cs, -sn, 0.0], [sn, cs, 0.0], [0.0, 0.0, 1.0]])     # turned frame -> plane frame
    Hrot = Hobj @ Rot
    Hri = np.linalg.inv(Hrot)
    x0, y0, x1, y1 = crop if crop is not None else (0, 0, w, h)
    frame = np.array([[x0, y0], [x1 - 1, y0], [x1 - 1, y1 - 1], [x0, y1 - 1]], float)
    q = np.c_[frame, np.ones(4)] @ Hri.T
    if (q[:, 2] * np.sign(q[0, 2]) <= 1e-9).any():
        raise ValueError("The photo is taken at too shallow an angle to straighten. Shoot from above.")
    pf = q[:, :2] / q[:, 2:3]
    lo, hi = pf.min(0), pf.max(0)
    s = float(np.hypot(*(c1 - c0)))                            # mm per input pixel at the centre
    s = max(s, float((hi - lo).max()) / float(max_side))
    Wo, Ho = int(math.ceil((hi[0] - lo[0]) / s)) + 1, int(math.ceil((hi[1] - lo[1]) / s)) + 1
    A = np.array([[s, 0.0, lo[0]], [0.0, s, lo[1]], [0.0, 0.0, 1.0]])    # output px -> turned frame
    M = Hrot @ A
    shrink = s / float(np.hypot(*(c1 - c0)))
    src = cv2.GaussianBlur(img, (0, 0), 0.45 * shrink) if shrink > 1.5 else img
    out = cv2.warpPerspective(src, M, (Wo, Ho), flags=cv2.INTER_CUBIC | cv2.WARP_INVERSE_MAP,
                              borderMode=cv2.BORDER_REPLICATE)
    return out, s, M, float(np.hypot(*(c1 - c0)))


def locate(img, side_mm=SIDE_MM, thickness=0.0, above=0.0, focal_35mm=0.0, camera_distance=0.0,
           max_side=2400):
    """Everything the token gives, in one call.

    thickness        of the token: 0 for one printed on paper, THICK_MM for the 3D-printed one
    above            how much higher (mm) the face being traced is than the token's top face
    focal_35mm       the lens's 35 mm-equivalent focal length (from EXIF), or 0
    camera_distance  camera to the surface the token lies on (mm), typed by the user, or 0
    Returns a dict: image (straightened in the traced plane), mm_per_px, exclude (pixel
    rectangle hiding the token), M (output px -> input px), info (what was found, for messages).
    """
    if not (side_mm > 0 and thickness >= 0):
        raise ValueError("Token side must be positive and its thickness zero or more.")
    on_paper = thickness <= 0.05
    tokens = find_tokens(img, limit=2 if on_paper else 1)
    found, pair = tokens[0], None
    if on_paper:
        pair = sheet_pair(tokens, side_mm)
        if pair is not None:
            found = pair[0]
        elif found["order"][1] and len(tokens) > 1 and not tokens[1]["order"][1]:
            found = tokens[1]

    def est(K):
        """(homography of the token's top face, camera) for a camera matrix (or None)."""
        if pair is None:
            return solve(found, side_mm, K, thickness if K is not None else 0.0)
        if K is None:
            return pair[3], None
        R, t = pose(pair[3], K)
        return plane_h(K, R, t, 0.0), _camera(R, t, K)

    # Where the camera is. Best: the lens data (EXIF focal length) - the photo then measures the
    # distance itself, and a typed distance is only compared with it. Else the distance typed by
    # the user. Else, when a height has to be allowed for, a phone's main camera (no zoom) is
    # assumed and the result says so.
    def height_with(K):
        _, c = est(K)
        return c["height"] if c is not None else 0.0

    typed = float(camera_distance) if camera_distance and camera_distance > 0 else 0.0
    if typed and typed <= thickness + above + 5:
        raise ValueError("The camera distance must be the distance from the camera to the surface "
                         "the token lies on, in mm, and more than the height of the part.")
    phone = height_with(intrinsics(img.shape, focal_35mm=PHONE_35MM))     # what a main camera would mean
    K, how = None, None
    if focal_35mm and focal_35mm > 0:
        K, how = intrinsics(img.shape, focal_35mm=focal_35mm), "lens"
    elif typed:
        # the focal length that puts the camera at the typed distance: with it the token's
        # perspective places the camera sideways (above which point of the sheet it was)
        fpx = (typed - thickness) * found["side_px"] / side_mm
        for _ in range(4):
            fpx *= (typed - thickness) / max(height_with(intrinsics(img.shape, focal_px=fpx)), 1.0)
        K, how = intrinsics(img.shape, focal_px=fpx), "typed"
    elif above:
        K, how = intrinsics(img.shape, focal_35mm=PHONE_35MM), "assumed"
    Htop, cam = est(K)
    # How far the point under the camera moves if the distance is out by the usual margin for
    # where it came from: the side-wall correction leans on that point, so pipeline skips it
    # when this is too much.
    nadir_spread = 0.0
    if cam is not None:
        u = {"lens": 0.02, "typed": 0.10, "assumed": 0.10}[how]
        for m in (1 - u, 1 + u):
            _, c2 = est(intrinsics(img.shape, focal_px=K[0, 0] * m))
            if c2 is not None:
                nadir_spread = max(nadir_spread, float(np.hypot(*(c2["camera"][:2] - cam["camera"][:2]))))
        cam["spread_u"] = u
    dist = None                                                # camera above the token's top face
    if cam is not None:
        dist = typed - thickness if how == "typed" else cam["height"]
    if dist is not None and dist - above < 20:
        raise ValueError("The height of the part (%.0f mm) is too close to the camera distance (%.0f mm): "
                         "check both." % (above + thickness, dist + thickness))
    if how == "lens" and above:
        # The focal length is known: the plane of the traced face follows from the camera's pose
        # (which, being a rigid motion, also evens out the noise in the token's corners).
        Hobj = plane_h(K, cam["R"], cam["t"], above)
    elif cam is not None and above:
        # Distance typed or assumed, so the focal length is only as good as that guess. A pose
        # built on it would bend the plane (v0.9: a distance 10 % out distorted the outline by
        # tens of percent on a tall part). Instead: the traced face is a copy of the sheet's
        # plane lifted towards the camera. Seen through the camera's centre, lifting it is a
        # scaling about the point under the camera (the nadir) by dist / (dist - above) - so the
        # measured homography is kept exactly and only that scaling is added. Sizes then depend
        # on the ratio height / distance only; the nadir only moves the outline as a whole.
        if pair is not None:
            Htop = pair[3]
        k = dist / (dist - above)
        nx, ny = float(cam["camera"][0]), float(cam["camera"][1])
        Hobj = Htop @ np.array([[k, 0.0, (1 - k) * nx], [0.0, k, (1 - k) * ny], [0.0, 0.0, 1.0]])
    else:
        Hobj = Htop
    out, s, M, s_native = rectify(img, Hobj, max_side=max_side, crop=paper_box(img, found))
    Mi = np.linalg.inv(M)
    tq = _apply(Mi, found["outline"])
    pad = 3.0 / s
    excl = (int(math.floor(tq[:, 0].min() - pad)), int(math.floor(tq[:, 1].min() - pad)),
            int(math.ceil(tq[:, 0].max() + pad)) + 1, int(math.ceil(tq[:, 1].max() + pad)) + 1)
    q = found["quad"]
    exclude = [excl]
    if pair is not None:
        t2 = _apply(Mi, pair[1]["outline"])
        exclude.append((int(math.floor(t2[:, 0].min() - pad)), int(math.floor(t2[:, 1].min() - pad)),
                        int(math.ceil(t2[:, 0].max() + pad)) + 1, int(math.ceil(t2[:, 1].max() + pad)) + 1))
    if on_paper and not found["order"][1]:
        # a token printed on paper has a few lines of small print under it (make_pdf): hide them
        x0, y0, x1, y1 = CAPTION
        G = cv2.getPerspectiveTransform(_CORNERS.astype(np.float32), q.astype(np.float32)).astype(float)
        cq = _apply(Mi, _apply(G, _orient(found["order"], [[x0, y0], [x1, y0], [x1, y1], [x0, y1]])))
        cap = (int(math.floor(cq[:, 0].min())), int(math.floor(cq[:, 1].min())),
               int(math.ceil(cq[:, 0].max())) + 1, int(math.ceil(cq[:, 1].max())) + 1)
        # ... and token and print together, which a soft photo can run into one blob
        exclude += [cap, (min(excl[0], cap[0]), min(excl[1], cap[1]), max(excl[2], cap[2]), max(excl[3], cap[3]))]
    sides = [float(np.hypot(*(q[(i + 1) % 4] - q[i]))) for i in range(4)]
    info = dict(side_px=found["side_px"], straightness_px=found["rms_px"], lens=how,
                skew=float(max(sides) / min(sides) - 1.0), mm_per_source_px=s_native,
                thickness_corrected=bool(cam is not None and thickness > 0), above=float(above),
                quad=q.tolist(), on_paper=bool(on_paper), thickness=float(thickness),
                tokens=2 if pair is not None else 1, sheet=pair[2] if pair is not None else None)
    info.update(phone_distance_mm=float(phone + thickness) if phone else None, typed_distance_mm=typed or None)
    if cam is not None:
        nadir = _apply(Mi, _apply(Hobj, [cam["camera"][:2]]))[0]       # under the camera, output px
        info.update(tilt_deg=cam["tilt_deg"], camera_height_mm=float(dist + thickness),
                    nadir_px=(float(nadir[0]), float(nadir[1])),
                    height_factor=float((dist - above) / dist),
                    # how much sizes change for each 1 % the camera distance is out
                    distance_sensitivity=float(abs(above) / max(dist - above, 1e-9)),
                    nadir_spread_mm=nadir_spread, distance_margin=cam["spread_u"])
    return dict(image=out, mm_per_px=s, exclude=exclude, M=M, info=info)


def unwall(pts, hole, nadir, beta, eps=0.04, margin=0.35):
    """Move an outline from where the camera saw it to the part's TOP edge, for a part with
    straight vertical sides.

    Seen from above and to one side, a thick part shows its wall on the side facing the camera:
    the outline there is the BOTTOM edge, which appears nearer the point under the camera (the
    nadir) by the factor beta = camera height above the top face / camera height above the
    bottom face. The top edge is recovered by scaling those points away from the nadir by
    1 / beta. The same goes for the far side of every through-hole, where you look down the
    bore at its bottom rim. Between the two kinds of stretch the outline runs along the
    edge-on view of a wall, which belongs to neither edge and is dropped.

    Which is which is read from the direction the outline faces: a solid's outline shows its
    wall where it faces the nadir; a hole's where it faces away. Exact for convex outlines
    with clean sides; a close approximation otherwise.
    pts: N x 2 closed outline in pixels (about a pixel apart), hole: True for a hole,
    nadir: (x, y) in pixels. Returns the corrected outline."""
    P = np.asarray(pts, float)
    N = np.asarray(nadir, float)
    n = len(P)
    if n < 16 or not (0 < beta < 1):
        return P
    x, y = P[:, 0], P[:, 1]
    area = 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))
    w = int(min(4, max(1, n // 16)))
    t = np.roll(P, -w, axis=0) - np.roll(P, w, axis=0)
    out = np.c_[t[:, 1], -t[:, 0]] * (1.0 if area > 0 else -1.0)      # pointing out of the polygon
    out /= np.maximum(np.hypot(out[:, 0], out[:, 1]), 1e-12)[:, None]
    r = P - N
    dist = np.hypot(r[:, 0], r[:, 1])
    c = (out * r).sum(1) / np.maximum(dist, 1e-9)              # +1: faces away from the nadir
    # averaged along the outline (13 points): a straight side that points at the nadir has c
    # near 0 all along, and single points flipping to "wall" by noise were moved along that
    # side by the full correction, making a spike several mm long (v0.10.1)
    k = min(13, max(1, (n // 8) | 1))
    c = np.convolve(np.r_[c[-(k // 2):], c, c[:k // 2]], np.ones(k) / k, mode="valid") if k > 1 else c
    move = dist * (1.0 / beta - 1.0)
    small = move < 2.0 * margin                                # nothing worth correcting here
    wall = (c > eps) if hole else (c < -eps)                   # this stretch is a bottom edge
    edge_on = (np.abs(c) <= eps) & ~small
    keep = ~edge_on
    if keep.sum() < max(8, 0.5 * n):
        return P                                               # not a shape this model fits: leave it
    Q = P.copy()
    Q[wall] = N + r[wall] / beta
    return despike(Q[keep])


def despike(Q, max_deg=25.0):
    """Take out thin spikes: where a closed outline turns back on itself (the two neighbours of a
    point lie within max_deg of the same direction from it), that point is dropped, until none
    is left. Moving a wall's points to the top edge can leave the moved stretch overshooting
    past the end of the unmoved one by a fraction of a millimetre to a few millimetres, and the
    outline then runs out along a line and back (found on simulated photos of a 140 mm tall
    part, v0.10.1); the fitted outline then had a spike several millimetres long."""
    Q = np.asarray(Q, float)
    c = math.cos(math.radians(max_deg))
    for _ in range(len(Q)):
        if len(Q) < 8:
            break
        a = np.roll(Q, 1, axis=0) - Q
        b = np.roll(Q, -1, axis=0) - Q
        la, lb = np.hypot(a[:, 0], a[:, 1]), np.hypot(b[:, 0], b[:, 1])
        cos = (a * b).sum(1) / np.maximum(la * lb, 1e-12)
        bad = (cos > c) | (la < 1e-9) | (lb < 1e-9)
        if not bad.any():
            break
        # drop every other flagged point at most, so that both sides of a spike shrink evenly
        idx = np.nonzero(bad)[0]
        drop = np.zeros(len(Q), bool)
        drop[idx[::2]] = True
        Q = Q[~drop]
    return Q


def paper_box(img, found):
    """Bounding box (x0, y0, x1, y1), in pixels, of the light sheet the token lies on, so that a
    darker table around the paper is left out of the trace. The whole picture if the light area
    reaches its edges anyway, or if nothing sensible is found."""
    h, w = img.shape[:2]
    f = min(1.0, 500.0 / float(max(h, w)))
    g = img if img.ndim == 2 else cv2.cvtColor(img[:, :, :3], cv2.COLOR_BGR2GRAY)
    small = cv2.resize(g, None, fx=f, fy=f, interpolation=cv2.INTER_AREA) if f < 1 else g
    small = cv2.GaussianBlur(small, (0, 0), 1.0)
    q = (found["quad"] + 0.5) * f - 0.5
    c = q.mean(0)
    ring = c + (q - c) * 1.5                                   # just outside the token: paper
    hh, ww = small.shape
    vals = [int(small[int(np.clip(round(p[1]), 0, hh - 1)), int(np.clip(round(p[0]), 0, ww - 1))]) for p in ring]
    paper = float(np.median(vals))
    dark = float(np.percentile(small, 2))
    if paper - dark < 40:
        return 0, 0, w, h
    thr = paper - 0.35 * (paper - dark)
    light = (small >= thr).astype(np.uint8)
    # the token and the part are dark islands inside the sheet: close them so the sheet is whole
    n, lab = cv2.connectedComponents(light, connectivity=4)
    seeds = [lab[int(np.clip(round(p[1]), 0, hh - 1)), int(np.clip(round(p[0]), 0, ww - 1))] for p in ring]
    seeds = [s for s in seeds if s > 0]
    if not seeds:
        return 0, 0, w, h
    sheet = (lab == max(set(seeds), key=seeds.count)).astype(np.uint8)
    ys, xs = np.nonzero(sheet)
    bx0, by0, bx1, by1 = xs.min(), ys.min(), xs.max() + 1, ys.max() + 1
    if (bx1 - bx0) * (by1 - by0) > 0.9 * hh * ww:
        return 0, 0, w, h
    m = 3                                                      # small margin so the paper edge is in
    return (int(max(0, (bx0 - m) / f)), int(max(0, (by0 - m) / f)),
            int(min(w, (bx1 + m) / f)), int(min(h, (by1 + m) / f)))
