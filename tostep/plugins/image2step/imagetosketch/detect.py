# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
# Copied from the ImageToSketch project by tools/sync_imagetosketch.py - edit it there.
"""Stage 1-4: normalise -> propose outlines -> verify each against the real edge -> discard noise.

Design (chosen by benchmark, see docs/BENCHMARK.md):
  * Regions *propose* closed outlines (thresholds at several levels between the part tone and
    the background tone). Regions always give closed loops with a known inside/outside.
  * The image gradient *verifies* them: every outline point is probed across its normal for a
    sharp step. Smooth ramps (shadow penumbra, lighting falloff) are subtracted out, so they
    neither count as support nor pull the position.
  * Confidence = fraction of the outline that sits on a sharp, significant step.
  * Position = centroid of the slope bump -> sub-pixel, unbiased for any symmetric blur.

All heavy lifting is OpenCV (C++); this file is glue.
"""
import math
from dataclasses import dataclass, field

import cv2
import numpy as np


# --------------------------------------------------------------------- data --
@dataclass
class Loop:
    pts: np.ndarray                 # Nx2 float, refined, image px (x right, y down)
    hole: bool                      # True = hole in material, False = outer outline
    conf: float                     # 0..1 final confidence
    support: float                  # fraction of outline on a sharp step
    area: float                     # px^2
    level: float                    # which threshold level proposed it
    note: str = ""                  # why it is not plain geometry: small, debris, alt, tone,
    #                                 nested, centre (centre-line of a drawn stroke), inner, ...
    raw: np.ndarray = field(default=None, repr=False)
    group: int = -1                 # transition mode: which colour area it bounds (-1 = tone mode)
    colour: tuple = None            # ... and that colour (B, G, R)


@dataclass
class Detection:
    loops: list                     # accepted + borderline, best first
    rejected: list                  # low-confidence loops (for the debug overlay)
    edge_sigma: float               # estimated blur of real edges, px
    contrast: float                 # part-vs-background tone difference (grey levels)
    noise: float                    # pixel noise sigma
    dark_on_light: bool
    channel: str
    reach: float
    min_feature_px: float = 14.0
    work: np.ndarray = field(default=None, repr=False)   # normalised single-channel image
    flags: dict = field(default_factory=dict)            # counts that become user messages


# ------------------------------------------------------------------ helpers --
def smooth_closed(pts, sigma):
    """Gaussian-smooth a closed polyline (circular)."""
    n = len(pts)
    if sigma <= 0 or n < 8:
        return pts.copy()
    rad = int(min(max(2, round(sigma * 3)), n // 2 - 1))
    if rad < 1:
        return pts.copy()
    xs = np.arange(-rad, rad + 1)
    ker = np.exp(-xs ** 2 / (2.0 * sigma ** 2))
    ker /= ker.sum()
    ext = np.vstack([pts[-rad:], pts, pts[:rad]])
    return np.c_[np.convolve(ext[:, 0], ker, "valid"), np.convolve(ext[:, 1], ker, "valid")]


def _normals(pts, span=3):
    t = np.roll(pts, -span, 0) - np.roll(pts, span, 0)
    n = np.c_[t[:, 1], -t[:, 0]]
    return n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-9)


def noise_sigma(gray):
    """Immerkaer's fast noise estimate (one 3x3 filter)."""
    k = np.float32([[1, -2, 1], [-2, 4, -2], [1, -2, 1]])
    r = cv2.filter2D(gray.astype(np.float32), -1, k)[1:-1, 1:-1]
    return float(np.sqrt(np.pi / 2) / 6 * np.mean(np.abs(r)))


def _tone_gap(ch):
    """Grey-level distance between the two tones of a channel (class means at the Otsu split)."""
    t, _ = cv2.threshold(ch, 0, 255, cv2.THRESH_OTSU)
    lo, hi = ch[ch <= t], ch[ch > t]
    if len(lo) == 0 or len(hi) == 0:
        return 0.0
    return float(hi.mean() - lo.mean())


def composite(img):
    """Flatten a picture with transparency onto a plain backdrop: white if what is drawn is
    mostly dark, black if it is mostly light, so the drawn shape always stands out.
    Returns (BGR image, "white" | "black" | None)."""
    if img.ndim != 3 or img.shape[2] != 4:
        return img, None
    alpha = img[:, :, 3]
    if int(alpha.min()) >= 250:                               # fully opaque: alpha says nothing
        return np.ascontiguousarray(img[:, :, :3]), None
    solid = alpha > 128
    lum = float(cv2.cvtColor(img[:, :, :3], cv2.COLOR_BGR2GRAY)[solid].mean()) if solid.any() else 0.0
    back = 0.0 if lum > 140 else 255.0
    a = alpha[:, :, None].astype(np.float32) / 255.0
    out = np.clip(img[:, :, :3].astype(np.float32) * a + back * (1 - a) + 0.5, 0, 255).astype(np.uint8)
    return out, ("black" if back == 0 else "white")


def pick_channel(img, channel="auto"):
    """Reduce a colour image to the single channel in which part and background separate best.
    (A yellow part on white paper is nearly invisible in grey but obvious in blue.) A picture
    with a transparent background is first laid on a contrasting backdrop."""
    if img.ndim == 2:
        return img, "gray"
    alpha = img[:, :, 3] if img.shape[2] == 4 else None
    img, _ = composite(img)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    cands = {"gray": gray, "blue": img[:, :, 0], "green": img[:, :, 1], "red": img[:, :, 2]}
    if channel != "auto":
        if channel == "saturation":
            return cv2.cvtColor(img, cv2.COLOR_BGR2HSV)[:, :, 1], "saturation"
        if channel == "alpha" and alpha is not None:
            return alpha, "alpha"
        return cands.get(channel, gray), channel if channel in cands else "gray"
    f = 256.0 / max(img.shape[:2])
    small = {k: (cv2.resize(v, None, fx=f, fy=f, interpolation=cv2.INTER_AREA) if f < 1 else v)
             for k, v in cands.items()}
    scores = {k: _tone_gap(v) for k, v in small.items()}
    best = max(scores, key=scores.get)
    if scores[best] < 1.5 * scores["gray"]:                   # only leave grey for a clear win
        best = "gray"
    return cands[best], best


def dark_on_light(gray):
    """True when the background (what touches the image border) is the lighter tone."""
    b = np.concatenate([gray[0], gray[-1], gray[:, 0], gray[:, -1]])
    t, _ = cv2.threshold(gray, 0, 255, cv2.THRESH_OTSU)
    return bool(np.median(b) > t)


def flatten(gray, dol, iters=2):
    """Divide out smooth lighting falloff: fit a quadratic surface to the background pixels of a
    1/8-scale copy. A few milliseconds; makes one global tone scale valid across the image."""
    small = cv2.resize(gray, None, fx=0.125, fy=0.125, interpolation=cv2.INTER_AREA).astype(np.float32)
    h, w = small.shape
    if h < 8 or w < 8:
        return gray
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    xn, yn = xs / w - 0.5, ys / h - 0.5
    A = np.stack([np.ones_like(xn), xn, yn, xn * xn, xn * yn, yn * yn], -1).reshape(-1, 6)
    v = small.reshape(-1)
    sel = np.ones(len(v), bool)
    coef = None
    for _ in range(iters + 1):
        coef, *_ = np.linalg.lstsq(A[sel], v[sel], rcond=None)
        ratio = np.clip(v / np.maximum(A @ coef, 1) * 128, 0, 255).astype(np.uint8)
        t, _ = cv2.threshold(ratio, 0, 255, cv2.THRESH_OTSU)
        new = (ratio > t) if dol else (ratio < t)
        if new.sum() < 50:
            break
        sel = new
    H, W = gray.shape
    bg = cv2.resize((A @ coef).reshape(h, w), (W, H), interpolation=cv2.INTER_LINEAR)
    bg = np.maximum(bg, 8.0)
    target = float(np.median(bg))
    return np.clip(gray.astype(np.float32) / bg * target, 0, 255).astype(np.uint8)


def probe(gsm, pts, reach, hw, step=0.5):
    """Find the sharp step nearest each outline point.

    Samples the intensity across the outline, takes the signed slope, removes the ramp baseline
    (median slope) and integrates the remaining bump around its peak.
    Returns base points, unit normals, offset along normal, step height, step width
    (width = height / peak slope = 2.5 x blur sigma for a real edge)."""
    base = smooth_closed(pts, 1.0)
    n = _normals(base)
    ts = np.arange(-reach, reach + step / 2, step, dtype=np.float32)
    X = (base[:, 0:1] + n[:, 0:1] * ts[None, :]).astype(np.float32)
    Y = (base[:, 1:2] + n[:, 1:2] * ts[None, :]).astype(np.float32)
    prof = cv2.remap(gsm, X, Y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    g = np.diff(prof, axis=1) / step
    tm = (ts[:-1] + ts[1:]) / 2
    k = max(2, len(ts) // 6)
    s = float(np.sign(np.median(prof[:, -k:].mean(1) - prof[:, :k].mean(1)))) or 1.0
    g *= s
    gp = np.maximum(g - np.median(g, axis=1, keepdims=True), 0)
    prox = np.exp(-tm ** 2 / (2 * (0.6 * reach) ** 2))
    pk = (gp * prox[None, :]).argmax(1)
    j = np.arange(len(tm))[None, :]
    gw = gp * (np.abs(j - pk[:, None]) <= hw)
    area = gw.sum(1)
    height = area * step
    off = (gw * tm[None, :]).sum(1) / np.maximum(area, 1e-6)
    peak = gp[np.arange(len(pts)), pk]
    width = height / np.maximum(peak, 1e-6)
    return base, n, off, height, width


def _poly_area(p):
    x, y = p[:, 0], p[:, 1]
    return 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))


# ------------------------------------------------------------------- detect --
MAX_LOOPS = 1500            # per threshold level; a picture with more than this is texture, not parts


def _fill(shape, contour, kids, origin):
    """uint8 mask of the region a contour bounds, minus the regions of its direct children."""
    m = np.zeros(shape, np.uint8)
    cv2.drawContours(m, [(contour - origin).astype(np.int32).reshape(-1, 1, 2)], -1, 1, -1)
    for k in kids:
        cv2.drawContours(m, [(k - origin).astype(np.int32).reshape(-1, 1, 2)], -1, 0, -1)
        cv2.drawContours(m, [(k - origin).astype(np.int32).reshape(-1, 1, 2)], -1, 1, 1)
    return m


def _nearest(A, B, chunk=256):
    """For each point of A the nearest point of B (brute force, chunked)."""
    B32 = B.astype(np.float32)
    out = np.empty(len(A), np.int64)
    for i in range(0, len(A), chunk):
        a = A[i:i + chunk].astype(np.float32)
        d = (a[:, None, 0] - B32[None, :, 0]) ** 2 + (a[:, None, 1] - B32[None, :, 1]) ** 2
        out[i:i + chunk] = d.argmin(1)
    return B[out]


def _perimeter(P):
    return float(np.hypot(*np.diff(np.vstack([P, P[:1]]), axis=0).T).sum())


def detect(img, levels=(0.5, 0.3, 0.15, 0.7), accept=0.75, maybe=0.45, polarity="auto",
           channel="auto", prefer="material", min_feature_px=None, keep_work=True,
           line_art="auto", exclude=(), thin_features=True, even_light=True):
    """Find closed outlines in an image.

    img       uint8 grey or BGR/BGRA
    levels    threshold positions between part tone (0) and background tone (1)
    accept    confidence needed to count as real geometry
    maybe     confidence needed to be kept as "borderline" (construction geometry)
    polarity  "auto", "dark" (dark part on light background) or "light"
    prefer    when two different sharp outlines compete for the same feature (hard shadow, or a
              bevel): "material" keeps the one hugging the part's own tone, "outline" the outer.
    min_feature_px  loops smaller than this across are capped to borderline (default: auto)
    line_art  "auto" | "on" | "off": turn thin drawn strokes into their centre-lines
    exclude   rectangles (x0, y0, x1, y1) px; anything lying wholly inside one is ignored
    thin_features  also look for thin walls, thin slots and narrow gaps the clean-up would erase
    even_light  divide out smooth lighting falloff first (off for maps that have none)
    """
    work, ch_name = pick_channel(img, channel)
    H, W = work.shape
    diag = float(np.hypot(W, H))
    ref = diag / 2000.0                                       # size relative to the benchmark image
    dol = dark_on_light(work) if polarity == "auto" else (polarity == "dark")
    g = flatten(work, dol) if even_light else work
    gb = cv2.GaussianBlur(g, (0, 0), 1.5)
    flag = cv2.THRESH_BINARY_INV if dol else cv2.THRESH_BINARY
    _, m0 = cv2.threshold(gb, 0, 255, flag | cv2.THRESH_OTSU)
    if m0.min() == m0.max():
        return Detection([], [], 1.0, 0.0, 0.0, dol, ch_name, 6.0, 14.0, g if keep_work else None)
    fgv = float(np.median(gb[m0 > 0]))
    bgv = float(np.median(gb[m0 == 0]))
    C = abs(bgv - fgv)
    if C < 4:
        return Detection([], [], 1.0, C, 0.0, dol, ch_name, 6.0, 14.0, g if keep_work else None)
    noise = noise_sigma(g)
    sn = noise * 0.3 + 0.5                                    # noise left after sigma-1 smoothing
    gsm = cv2.GaussianBlur(g, (0, 0), 1.0).astype(np.float32)
    gl = cv2.GaussianBlur(g, (0, 0), 0.7)                     # light blur: keeps thin features

    ksz = int(np.clip(round(7 * ref), 5, 11)) | 1             # despur: removes pen lines / hairs
    kern = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (ksz, ksz))
    if min_feature_px is None:
        min_feature_px = 14.0 * max(ref, 0.5)                 # below this a hole looks like dust
    small_area = np.pi * (min_feature_px / 2.0) ** 2          # under this: borderline at best
    min_area = 0.4 * small_area                               # under this: ignored outright
    thin = 2.0 * max(ref, 0.75)
    flags = dict(border_cut=0, thin=0, strokes=0)

    def tone(v):
        return (v - fgv) / (bgv - fgv)

    # ---------------------------------------------------------- proposals --
    def proposals(level, despur=True):
        """Closed loops of the thresholded image, with their place in the nesting tree."""
        t = fgv + level * (bgv - fgv)
        if despur:
            _, m = cv2.threshold(gb, t, 255, flag)
            m = cv2.morphologyEx(cv2.morphologyEx(m, cv2.MORPH_OPEN, kern), cv2.MORPH_CLOSE, kern)
        else:
            _, m = cv2.threshold(gl, t, 255, flag)
        cnts, hier = cv2.findContours(m, cv2.RETR_TREE, cv2.CHAIN_APPROX_NONE)
        if hier is None:
            return []
        hier = hier[0]
        n = len(cnts)
        depth = np.zeros(n, np.int32)
        for i in range(n):                                    # parents always precede children? not
            d, p = 0, hier[i][3]                              # guaranteed, so walk up each time
            while p != -1:
                d += 1
                p = hier[p][3]
            depth[i] = d
        boxes = [cv2.boundingRect(c) for c in cnts]
        on_border = [x <= 0 or y <= 0 or x + w >= W or y + h >= H for x, y, w, h in boxes]
        kids = [[] for _ in range(n)]
        for i in range(n):
            if hier[i][3] != -1:
                kids[hier[i][3]].append(i)
        out = []
        order = range(n) if n <= MAX_LOOPS else np.argsort([-len(c) for c in cnts])   # biggest first
        for i in order:
            c = cnts[i]
            area = cv2.contourArea(c)
            if area < min_area:
                continue
            per = cv2.arcLength(c, True)
            if 2 * area / per < thin:                         # sliver: scratch or pen stroke
                continue
            x, y, w, h = boxes[i]
            if any(x >= e[0] and y >= e[1] and x + w <= e[2] and y + h <= e[3] for e in exclude):
                continue
            hole = bool(depth[i] % 2 == 1)
            parent = hier[i][3]
            # an island cut by the picture edge is not a closed part; nor are the holes in it
            border = on_border[i] if not hole else (parent != -1 and on_border[parent])
            kid_area = sum(cv2.contourArea(cnts[k]) for k in kids[i])
            kid_per = sum(cv2.arcLength(cnts[k], True) for k in kids[i])
            if len(out) >= MAX_LOOPS:
                flags["too_many"] = True
                break
            out.append(dict(raw=c[:, 0, :].astype(np.float64), hole=hole, area=area, per=per,
                            box=(x, y, w, h), level=level, border=bool(border), despur=despur,
                            kids=[cnts[k][:, 0, :].astype(np.float64) for k in kids[i]
                                  if cv2.contourArea(cnts[k]) >= 4],
                            thick=2.0 * (area - kid_area) / (per + kid_per), hollow=kid_area / max(area, 1.0)))
        return out

    # how sharp are real edges in this image?
    first = proposals(levels[0])
    raw_first = proposals(0.7, despur=False) if thin_features else []
    ws = [np.zeros(0)]
    for c in (first or raw_first):
        _, _, _, hgt, wid = probe(gsm, c["raw"][::4], 8.0, 8)
        ws.append(wid[hgt > 0.5 * C])
    ws = np.concatenate(ws)
    if len(ws) >= 8:
        w_ref = float(np.clip(np.percentile(ws, 30), 1.5, 14.0))
    else:
        # No step fits in the probe window: either there is nothing, or everything is badly
        # blurred. Read the edge width straight off the slope at the mid-tone contour instead.
        w_ref = 3.0
        pts = [c["raw"][::4] for c in first if not c["border"]]
        if pts:
            pts = np.vstack(pts).astype(np.float32)
            gx = cv2.Sobel(gsm, cv2.CV_32F, 1, 0, ksize=3) / 8.0
            gy = cv2.Sobel(gsm, cv2.CV_32F, 0, 1, ksize=3) / 8.0
            mag = cv2.remap(cv2.magnitude(gx, gy), np.ascontiguousarray(pts[:, 0:1]),
                            np.ascontiguousarray(pts[:, 1:2]), cv2.INTER_LINEAR).ravel()
            if len(mag) >= 8 and float(np.percentile(mag, 70)) > 1e-3:
                w_ref = float(np.clip(C / np.percentile(mag, 70), 1.5, 60.0))
    reach = float(np.clip(2.5 * w_ref, 6.0, 14.0))
    hw = int(np.ceil(1.3 * w_ref / 0.5))
    hmin = max(6 * sn, 0.12 * C)

    def score(c):
        base, n, off, hgt, wid = probe(gsm, c["raw"], reach, hw)
        valid = (hgt > hmin) & (wid < 2.0 * w_ref) & (np.abs(off) < reach - 1)
        c["support"] = float(valid.mean())
        c["height"] = float(np.median(hgt))
        if valid.any() and not valid.all():                   # bridge unsupported stretches
            idx = np.arange(len(off))
            off = np.interp(idx, idx[valid], off[valid], period=len(off))
        o = smooth_closed(np.c_[off, off], 2.0)[:, 0]
        c["P"] = base + n * o[:, None]
        c["shift"] = float(np.median(np.abs(off)))
        c["rarea"] = _poly_area(c["P"])
        c["rper"] = _perimeter(c["P"])

    buckets = {}                                              # (hole, x // 8, y // 8) -> loops

    def twin(c):
        """Has this same loop (same box, same length) been seen already at another level?"""
        bx, by = c["box"][0] // 8, c["box"][1] // 8
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for q in buckets.get((c["hole"], bx + dx, by + dy), ()):
                    if abs(q["per"] - c["per"]) < 0.03 * q["per"] + 4 and \
                            all(abs(a - b) <= 4 for a, b in zip(q["box"], c["box"])):
                        return True
        return False

    def remember(c):
        buckets.setdefault((c["hole"], c["box"][0] // 8, c["box"][1] // 8), []).append(c)

    cands = []
    for li, lv in enumerate(levels):
        for c in (first if li == 0 else proposals(lv)):
            if twin(c):
                continue                                      # identical loop at another level
            if not c["hole"] and c["thick"] < ksz and c["hollow"] < 0.05:
                continue                                      # scrap of a thin stroke that survived
            score(c)
            cands.append(c)
            remember(c)

    # A loop from another level that swallows two or more shapes which the mid level shows as
    # separate is blur filling the gap between close neighbours (letters, holes side by side),
    # not a shape of its own.
    mid = [c for c in cands if c["level"] == levels[0] and c["support"] >= accept and not c["border"]]
    others = [c for c in cands if c["level"] != levels[0]]
    if len(mid) >= 2 and len(mid) * len(others) <= 250000:
        def merger(m):
            x, y, w, h = m["box"]
            poly = None
            n, a = 0, 0.0
            for c in mid:
                cx, cy, cw, ch = c["box"]
                if c["hole"] != m["hole"] or cx < x - 2 or cy < y - 2 or cx + cw > x + w + 2 \
                        or cy + ch > y + h + 2 or c["area"] > 0.9 * m["area"]:
                    continue
                if poly is None:
                    poly = m["raw"].astype(np.float32).reshape(-1, 1, 2)
                q = c["raw"][0]
                if cv2.pointPolygonTest(poly, (float(q[0]), float(q[1])), True) >= -3:
                    n += 1
                    a += c["area"]
            return n >= 2 and a >= 0.5 * m["area"]
        gone = set(id(c) for c in others if merger(c))
        if gone:
            cands = [c for c in cands if id(c) not in gone]

    # ------------------------------------------------------------ select --
    # one winner per feature; a second, genuinely different sharp outline becomes an "alt"
    cands.sort(key=lambda c: (-round(c["support"] * 20) / 20.0, c["shift"]))
    label = [np.zeros((H, W), np.int32), np.zeros((H, W), np.int32)]
    keep, alts, rejected = {}, [], []
    next_id = [0]

    def region(c):
        x, y, w, h = c["box"]
        return _fill((h, w), c["raw"], c["kids"], np.array([x, y]))

    def view(c):
        x, y, w, h = c["box"]
        return label[int(c["hole"])][y:y + h, x:x + w]

    def add(c, roi):
        next_id[0] += 1
        c["id"] = next_id[0]
        view(c)[roi > 0] = c["id"]
        keep[c["id"]] = c

    def drop(q):
        v = view(q)
        v[v == q["id"]] = 0
        del keep[q["id"]]

    def band_tone(c, q):
        """Median tone (0 = part, 1 = background) of the strip between two competing outlines."""
        x0 = min(c["box"][0], q["box"][0])
        y0 = min(c["box"][1], q["box"][1])
        x1 = max(c["box"][0] + c["box"][2], q["box"][0] + q["box"][2])
        y1 = max(c["box"][1] + c["box"][3], q["box"][1] + q["box"][3])
        o = np.array([x0, y0])
        band = (_fill((y1 - y0, x1 - x0), c["raw"], [], o) ^ _fill((y1 - y0, x1 - x0), q["raw"], [], o)) > 0
        if not band.any():
            return None
        return tone(float(np.median(gb[y0:y1, x0:x1][band])))

    def rival(c, roi):
        """The kept loop of the same kind that c overlaps, if any."""
        hit = view(c)[roi > 0]
        hit = hit[hit > 0]
        if len(hit) > 0.3 * roi.sum():
            return keep.get(int(np.bincount(hit).argmax()))
        return None

    for c in cands:
        if c["support"] < maybe or c["border"]:
            rejected.append(c)
            continue
        roi = region(c)
        q = rival(c, roi)
        if q is None:
            add(c, roi)
            continue
        differs = abs(c["rarea"] - q["rarea"]) / max(min(c["per"], q["per"]), 1) > 1.5
        if not differs or c["support"] < accept:
            continue                                          # same boundary, or a worse one
        better = (c["level"] < q["level"]) if prefer == "material" else (c["level"] > q["level"])
        if better and prefer == "material":
            bt = band_tone(c, q)
            if bt is None or not (0.2 < bt < 0.85):
                better = False                                # e.g. a pen line cutting the part in two
        kc = c["rper"] ** 2 / max(c["rarea"], 1.0)            # raggedness (12.6 = perfect circle)
        kq = q["rper"] ** 2 / max(q["rarea"], 1.0)
        if kc > 1.25 * kq:
            better = False                                    # c is the ragged one (relief, glare)
        elif kq > 1.25 * kc:
            better = True                                     # q is the ragged one (spur, clutter)
        if better and c["support"] >= q["support"] - 0.1:
            drop(q)                                           # demote the earlier winner
            q["note"] = "alt"
            alts.append(q)
            add(c, roi)
        else:
            c["note"] = "alt"
            alts.append(c)

    # ------------------------------------------------------ thin features --
    # The clean-up above (open + close) deliberately erases anything narrower than a pen line,
    # which also erases thin walls and thin slots. Look again without it and add back only what
    # is clearly real: closed, sharp on both sides, at full contrast. (A narrow gap between two
    # parts is NOT reopened here: it looks exactly like a pen line or a hair across one part.
    # The separate outlines are still offered as borderline alternatives by the level search.)
    if thin_features:
        for c in raw_first:
            if c["border"] or twin(c):
                continue
            if not c["hole"] and c["thick"] < ksz and c["hollow"] < 0.05:
                continue                                      # an open thin stroke: pen mark, hair
            score(c)
            if c["support"] < accept or c["height"] < 0.6 * C:
                continue
            roi = region(c)
            if rival(c, roi) is None:                         # only ever adds; never overrides
                # A thin closed wall or drawn stroke is taken at face value. A thin slot is not:
                # a pen line, hair or scratch on the part looks exactly the same.
                c["note"] = "thin" if c["hole"] else ""
                add(c, roi)
                flags["thin"] += 1

    kept = list(keep.values())
    # an alt that has since been matched by a kept loop of near-identical shape is redundant
    alts = [a for a in alts if not any(k["hole"] == a["hole"] and
                                       abs(k["rarea"] - a["rarea"]) / max(k["per"], 1) < 1.5 and
                                       np.hypot(*(k["P"].mean(0) - a["P"].mean(0))) < 3 for k in kept)]

    # --------------------------------------------------------- relations --
    def parents(loops):
        """Containment tree: for each loop the index of the smallest loop that contains it."""
        order = sorted(range(len(loops)), key=lambda i: -loops[i]["rarea"])
        par = [None] * len(loops)
        cnt = [loops[i]["P"].astype(np.float32).reshape(-1, 1, 2) for i in range(len(loops))]
        for a, i in enumerate(order):
            px, py = float(loops[i]["P"][0, 0]), float(loops[i]["P"][0, 1])
            x, y, w, h = loops[i]["box"]
            for j in reversed(order[:a]):                     # smallest larger loop first
                bx, by, bw, bh = loops[j]["box"]
                if x < bx - 2 or y < by - 2 or x + w > bx + bw + 2 or y + h > by + bh + 2:
                    continue
                if cv2.pointPolygonTest(cnt[j], (px, py), False) >= 0:
                    par[i] = j
                    break
        return par

    # --------------------------------------------------------- line art --
    # A drawn outline is a thin dark stroke: an island that is mostly hole. What is wanted is the
    # line down the middle of the stroke, not its two edges.
    th_max = {"auto": 0.0062 * diag, "on": 0.03 * diag, "off": -1.0}[line_art]
    centre_lines = []
    if th_max > 0 and kept:
        par = parents(kept)
        holes_of = {}
        for i, p in enumerate(par):
            if kept[i]["hole"] and p is not None and not kept[p]["hole"]:
                holes_of.setdefault(p, []).append(i)
        gone = set()
        for i, hs in holes_of.items():
            isl = kept[i]
            a_h = sum(kept[h]["rarea"] for h in hs)
            thick = 2.0 * (isl["rarea"] - a_h) / (isl["rper"] + sum(kept[h]["rper"] for h in hs))
            if thick > th_max or a_h < 0.5 * isl["rarea"]:
                continue
            flags["strokes"] += 1
            gone.add(i)
            gone.update(hs)
            others = [kept[h]["P"] for h in hs]
            if len(hs) == 1:
                h = kept[hs[0]]
                P = (h["P"] + _nearest(h["P"], isl["P"])) / 2.0
                centre_lines.append(dict(isl, P=P, raw=P, hole=False, note="centre", kids=[],
                                         support=min(isl["support"], h["support"]),
                                         rarea=_poly_area(P), rper=_perimeter(P), area=_poly_area(P)))
            else:                                             # strokes that touch: several cells
                P = (isl["P"] + _nearest(isl["P"], np.vstack(others))) / 2.0
                centre_lines.append(dict(isl, P=P, raw=P, hole=False, note="centre", kids=[],
                                         support=min([isl["support"]] + [kept[h]["support"] for h in hs]),
                                         rarea=_poly_area(P), rper=_perimeter(P), area=_poly_area(P)))
                for k, hi in enumerate(hs):
                    h = kept[hi]
                    rest = np.vstack([isl["P"]] + [o for m, o in enumerate(others) if m != k])
                    P = (h["P"] + _nearest(h["P"], rest)) / 2.0
                    centre_lines.append(dict(h, P=P, raw=P, hole=True, note="inner", kids=[],
                                             rarea=_poly_area(P), rper=_perimeter(P), area=_poly_area(P)))
        if gone:
            kept = [c for i, c in enumerate(kept) if i not in gone] + centre_lines
            for c in centre_lines:
                c["box"] = cv2.boundingRect(c["P"].astype(np.float32).reshape(-1, 1, 2))

    # ----------------------------------------------------------- classify --
    # A real through-hole shows the background and/or has a clean shape; a ragged patch of
    # glare, print or relief inside the part has neither. And a part sitting inside another
    # part's hole is rare. Both are kept only as borderline.
    par = parents(kept) if kept else []
    depth = [0] * len(kept)
    for i in sorted(range(len(kept)), key=lambda i: -kept[i]["rarea"]):
        depth[i] = 0 if par[i] is None else depth[par[i]] + 1
    nested = set()
    for i in sorted(range(len(kept)), key=lambda i: -kept[i]["rarea"]):
        c, p = kept[i], par[i]
        if c.get("note") == "centre":                         # drawn outlines: holes by nesting
            c["hole"] = bool(depth[i] % 2 == 1)
            continue
        if c.get("note") == "inner":
            continue
        if c["hole"]:
            if p is not None and p in nested:
                c["tone_ok"] = False
                nested.add(i)
                continue
            x, y, w, h = c["box"]
            roi = _fill((h, w), c["raw"], c["kids"], np.array([x, y]))
            inside = roi > 0
            if not inside.any():
                c["tone_ok"] = True
            elif c.get("thick", 99.0) < 1.5 * ksz:
                # a narrow slot: judge by the most background-like fifth of a lightly blurred
                # copy (a 3 px slot still reads as background there), and by whether it wanders
                t = tone(float(np.percentile(gl[y:y + h, x:x + w][inside], 80 if dol else 20)))
                hull = cv2.arcLength(cv2.convexHull(c["P"].astype(np.float32).reshape(-1, 1, 2)), True)
                c["tone_ok"] = t > 0.75 or c["rper"] <= 1.5 * hull
            else:
                # tone in the middle of the shape, away from its own blurred rim
                dist = cv2.distanceTransform(roi, cv2.DIST_L2, 3)
                core = dist >= 0.6 * float(dist.max())
                t = tone(float(np.median(gl[y:y + h, x:x + w][core])))
                ragged = c["rper"] ** 2 / max(c["rarea"], 1.0) > 30.0   # circle 12.6, square 16, slot ~22
                c["tone_ok"] = t > 0.7 or not ragged          # a clean shape in shadow is still a hole
        elif p is not None and kept[p]["hole"] and kept[p].get("note") != "centre" \
                and par[p] is not None and not kept[par[p]]["hole"]:
            # An island inside a hole of an island (the star inside a ring, a washer lying in a
            # bore). Real if it is as solid as the part itself in the middle; a reflection or a
            # glimpse of the table through the hole is not.
            x, y, w, h = c["box"]
            roi = _fill((h, w), c["raw"], c["kids"], np.array([x, y]))
            dist = cv2.distanceTransform(roi, cv2.DIST_L2, 3)
            core = dist >= 0.6 * float(dist.max())
            if not core.any() or tone(float(np.median(gl[y:y + h, x:x + w][core]))) > 0.3:
                nested.add(i)
    big = max([c["area"] for i, c in enumerate(kept) if not c["hole"] and i not in nested] or [1.0])
    loops = []
    for i, c in list(enumerate(kept)) + [(None, a) for a in alts]:
        conf, note = c["support"], c.get("note", "")
        if c["hole"] and note == "" and not c["kids"] and 2.0 * c["rarea"] / max(c["rper"], 1.0) < ksz:
            note = "thin"                                     # slot or pen line? cannot tell
        if note in ("alt", "inner", "thin"):
            conf = min(conf, 0.6)
        elif c["hole"] and not c.get("tone_ok", True):
            conf, note = min(conf, 0.6), "tone"               # not background-coloured inside
        elif not c["hole"] and i in nested:
            conf, note = min(conf, 0.6), "nested"
        if c["area"] < small_area:
            conf, note = min(conf, 0.6), (note if note in ("alt", "tone", "nested", "thin") else "small")
        elif not c["hole"] and note != "centre" and c["area"] < 0.005 * big:
            conf, note = min(conf, 0.6), note or "debris"     # tiny island beside real parts
        loops.append(Loop(c["P"], c["hole"], conf, c["support"], c["area"], c["level"], note, c["raw"]))
    loops.sort(key=lambda l: (-(l.conf >= accept), -l.area))
    rej = []
    seen = {}                                                 # the same reject found at several levels
    for c in rejected:                                        # is one reject, not four
        x, y, w, h = c["box"]
        cell = (c["hole"], (x + w // 2) // 32, (y + h // 2) // 32)
        dup = False
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for (qx, qy, qw, qh) in seen.get((cell[0], cell[1] + dx, cell[2] + dy), ()):
                    iw = min(x + w, qx + qw) - max(x, qx)
                    ih = min(y + h, qy + qh) - max(y, qy)
                    if iw > 0 and ih > 0 and iw * ih > 0.5 * max(w * h, qw * qh):
                        dup = True
        if dup:
            continue
        seen.setdefault(cell, []).append((x, y, w, h))
        if c["border"] and not c["hole"] and c["support"] >= maybe and c["area"] >= 4 * small_area \
                and c["area"] < 0.5 * W * H:
            flags["border_cut"] += 1
        rej.append(Loop(c["P"], c["hole"], c["support"], c["support"], c["area"], c["level"],
                        "border" if c["border"] else "weak", c["raw"]))
    flags["partly_sharp"] = sum(1 for c in rejected if not c["border"] and c["area"] >= 4 * small_area) + \
        sum(1 for l in loops if l.note == "" and l.conf < accept and l.area >= 4 * small_area)
    return Detection(loops, rej, w_ref / 2.507, C, noise, dol, ch_name, reach, float(min_feature_px),
                     g if keep_work else None, flags)


# ------------------------------------------------------ colour transitions --
# EXPERIMENTAL. The detector above answers one question: where does the part end and the
# background begin? A picture with several colours (an icon, a logo, a painted part) has more
# edges than that: every place where one colour changes sharply into another.
#
# Transition mode therefore starts from the edges, not from the tones:
#   1. Mark every pixel that sits on a sharp colour step. A smooth blend (a gradient, a soft
#      shadow, lighting falloff) is not a step and is not marked, however far the colour
#      travels in the end.
#   2. What is left falls apart into areas, each closed off from the next by marked pixels.
#   3. For each area, build a single-channel map "how far is this pixel from the area's own
#      colour just here, compared with the neighbour's colour just here". That is a two-tone
#      picture again, so the detector above traces it: same proposals, same sharp-step test,
#      same sub-pixel placement. Colours are compared locally, so an area may shade from one
#      end to the other, and two neighbours may be closer in colour than that shading.
# Areas that do not touch are traced together in one pass, so a logo with forty letters costs
# a handful of passes, not forty.
STEP_AUTO = 5.0             # a colour step smaller than this (CIE Lab units) is not an edge, clean picture
STEP_MAX = 20.0             # ... and the automatic setting never asks for more than this
MAX_CENTRES = 24            # palette(): flat colours looked for (shades of a gradient included)
MAX_AREAS = 12              # palette(): colours reported (largest first)
RAMP = 0.35                 # palette(): two colours whose border is this soft are one colour
BLOCK = 16                  # px: the scale at which a ramp is told from a step
RAMP_CAP = 4.0              # Lab units per 5 px: the steepest blend still treated as a ramp
MERGE = 0.7                 # areas differing by less than this much of the step where they meet are one
MAX_REGIONS = 600           # areas traced (largest first); more than this is a texture
MAX_PASSES = 10             # detector passes: areas that do not touch share a pass


def lab(img):
    """float32 CIE Lab (L 0..100) of a grey / BGR / BGRA picture."""
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    return cv2.cvtColor(np.ascontiguousarray(img[:, :, :3]).astype(np.float32) / 255.0, cv2.COLOR_BGR2Lab)


def _d2(L, c):
    """Squared Lab distance of every pixel to one colour."""
    d = L[:, :, 0] - float(c[0])
    out = d * d
    for k in (1, 2):
        d = L[:, :, k] - float(c[k])
        out += d * d
    return out


def palette(img, step=0.0, min_share=0.003, max_side=600):
    """The flat colours of a picture.

    step   two colours closer than this (Lab units, about 2.3 = just noticeable) count as one;
           0 = automatic: 6 on a clean picture, more on a noisy one.
    Returns a dict: centres (K x 3 Lab), group (K: which colour area each centre belongs to -
    shades joined by a smooth ramp share one), share (per area, of the picture's flat pixels),
    bgr (per area, its main shade), step, noise (typical local colour range, Lab units)."""
    f = min(1.0, max_side / float(max(img.shape[:2])))
    small = cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_AREA) if f < 1 else img
    L = lab(small)
    k3 = np.ones((3, 3), np.uint8)
    rng = np.sqrt(sum((cv2.dilate(L[:, :, k], k3) - cv2.erode(L[:, :, k], k3)) ** 2 for k in range(3)))
    noise = float(np.median(rng))
    if not step or step <= 0:
        step = float(np.clip(3.0 * noise, STEP_AUTO, STEP_MAX))
    flat = rng <= max(1.5, 1.5 * noise)
    if flat.mean() < 0.2:
        flat = np.ones(flat.shape, bool)
    px = L[flat]
    empty = dict(centres=np.zeros((0, 3), np.float32), group=np.zeros(0, int), share=[], bgr=[],
                 step=step, noise=noise)
    if len(px) < 16:
        return empty
    # coarse bins, biggest first, each joining the first centre within reach or founding one
    q = np.floor((px + np.float32([0.0, 128.0, 128.0])) / (step / 2.0)).astype(np.int64)
    keys, inv, cnt = np.unique((q[:, 0] << 40) | (q[:, 1] << 20) | q[:, 2], return_inverse=True,
                               return_counts=True)
    inv = inv.ravel()
    means = np.stack([np.bincount(inv, px[:, k], len(keys)) / cnt for k in range(3)], 1)
    cen, wsum, csum = [], [], []
    for i in np.argsort(-cnt):
        m = means[i]
        if cen:
            d = np.linalg.norm(np.array(cen) - m, axis=1)
            j = int(d.argmin())
            if d[j] < step or len(cen) >= 4 * MAX_CENTRES:
                wsum[j] += cnt[i]
                csum[j] += m * cnt[i]
                continue
        cen.append(m.copy())
        wsum.append(float(cnt[i]))
        csum.append(m * cnt[i])
    C = np.array([c / w for c, w in zip(csum, wsum)], np.float32)
    for _ in range(2):                                         # settle: nearest centre, then its mean
        a = np.stack([((px - c) ** 2).sum(1) for c in C], 0).argmin(0)
        n = np.bincount(a, minlength=len(C)).astype(np.float64)
        keep = n >= max(8.0, 0.0008 * len(px))
        C = np.stack([np.bincount(a, px[:, k], len(C)) / np.maximum(n, 1) for k in range(3)], 1)[keep]
        C = C.astype(np.float32)
        n = n[keep]
        if len(C) == 0:
            return empty
    order = np.argsort(-n)[:MAX_CENTRES]
    C, n = C[order], n[order]
    K = len(C)

    # shades joined by a smooth ramp are one area: look at every border between two centres in
    # the small picture and ask how sharp it is, relative to how different the two colours are
    lbl = np.stack([_d2(L, c) for c in C], 0).argmin(0).astype(np.int32)
    parent = list(range(K))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    pairs = {}
    for a_, b_, ra, rb in ((lbl[:, :-1], lbl[:, 1:], rng[:, :-1], rng[:, 1:]),
                           (lbl[:-1, :], lbl[1:, :], rng[:-1, :], rng[1:, :])):
        m = a_ != b_
        if not m.any():
            continue
        lo, hi = np.minimum(a_[m], b_[m]), np.maximum(a_[m], b_[m])
        r = np.maximum(ra[m], rb[m])
        key = lo * K + hi
        for kk in np.unique(key):
            pairs.setdefault(int(kk), []).append(r[key == kk])
    for kk, rs in pairs.items():
        i, j = divmod(kk, K)
        rs = np.concatenate(rs)
        dist = float(np.linalg.norm(C[i] - C[j]))
        if len(rs) >= 8 and dist > 1e-6 and float(np.median(rs)) / dist < RAMP:
            parent[root(i)] = root(j)
    roots = [root(i) for i in range(K)]
    share = {}
    for i, r in enumerate(roots):
        share[r] = share.get(r, 0.0) + n[i] / float(len(px))
    areas = [r for r in sorted(share, key=lambda r: -share[r]) if share[r] >= min_share][:MAX_AREAS]
    if not areas:
        return empty
    index = {r: g for g, r in enumerate(areas)}
    sel = [i for i in range(K) if roots[i] in index]
    group = np.array([index[roots[i]] for i in sel], int)
    centres = C[sel]
    bgr = []
    for g in range(len(areas)):
        main = centres[group == g][0]                          # centres are in order of size
        one = cv2.cvtColor(np.float32([[main]]), cv2.COLOR_Lab2BGR)[0, 0]
        bgr.append(tuple(int(v) for v in np.clip(one * 255.0 + 0.5, 0, 255)))
    return dict(centres=centres, group=group, share=[share[r] for r in areas], bgr=bgr, step=step, noise=noise)


def colour_areas(L, step=0.0, min_area=60.0):
    """Split a picture into the areas that sharp colour steps close off from one another.

    L      float32 Lab picture (lightly smoothed)
    step   a colour step smaller than this is not an edge; 0 = automatic
    Returns (labels, n, step, noise, boxes, blur): labels int32, 0 = on an edge (or too small to
    be an area), 1..n = the areas, largest first; boxes[i] = (x0, y0, x1, y1) of area i; blur =
    how soft the picture's edges are (sigma, px; 0 = could not tell)."""
    k5 = np.ones((5, 5), np.uint8)
    r5 = np.zeros(L.shape[:2], np.float32)                     # how far the colour moves within 5 px
    for c in range(3):
        ch = np.ascontiguousarray(L[:, :, c])
        d = cv2.dilate(ch, k5) - cv2.erode(ch, k5)
        r5 += d * d
    np.sqrt(r5, out=r5)
    noise = float(np.median(r5))                               # what flat (or evenly shading) areas show
    if not step or step <= 0:
        # Lossy compression leaves ripples beside every strong edge (JPEG "mosquito noise")
        # while the flat areas stay perfectly clean; so also look at what the ground a little
        # way off the strong edges shows. A clean drawing shows nothing there.
        strong = (r5 > 2.5 + 2.5 * noise).astype(np.uint8)
        beside = (cv2.dilate(strong, np.ones((25, 25), np.uint8)) > 0) & \
                 (cv2.dilate(strong, np.ones((11, 11), np.uint8)) == 0)
        ripple = float(np.percentile(r5[beside], 90)) if int(beside.sum()) > 500 else noise
        step = float(np.clip(STEP_AUTO + 3.0 * max(0.0, ripple - noise) + 2.0 * noise, STEP_AUTO, STEP_MAX))
    thr = max(0.4 * step, 2.5 * noise)
    # A step stands out from its surroundings; a ramp is its surroundings. The surroundings'
    # level is the low end of what each 16 px block shows, evened over the blocks around it.
    # (Capped: where everything nearby is edges - small print, fine detail - a strong step
    # must still count.)
    H, W = r5.shape
    hb, wb = H // BLOCK, W // BLOCK
    if hb >= 3 and wb >= 3:
        blocks = r5[:hb * BLOCK, :wb * BLOCK].reshape(hb, BLOCK, wb, BLOCK).transpose(0, 2, 1, 3)
        low = np.percentile(blocks.reshape(hb, wb, -1), 20, axis=2).astype(np.float32)
        low = cv2.medianBlur(low, 3)
        around = cv2.resize(low, (W, H), interpolation=cv2.INTER_LINEAR)
        thr_map = np.maximum(thr, 2.2 * np.minimum(around, RAMP_CAP))
    else:
        thr_map = np.float32(thr)
    edge = r5 > thr_map
    # How soft are the edges? At the middle of an edge, compare how much of the change falls
    # within 5 px with how much within 13: nearly all of it if the edge is sharp.
    blur = 0.0
    mid = edge & (r5 >= cv2.dilate(r5, np.ones((9, 9), np.uint8)) - 1e-6)
    if int(mid.sum()) >= 40:
        k13 = np.ones((13, 13), np.uint8)
        r13 = np.zeros(L.shape[:2], np.float32)
        for c in range(3):
            ch = np.ascontiguousarray(L[:, :, c])
            d = cv2.dilate(ch, k13) - cv2.erode(ch, k13)
            r13 += d * d
        ratio = float(np.median(r5[mid] / np.maximum(np.sqrt(r13[mid]), 1e-6)))
        sig = np.linspace(0.8, 12.0, 113)
        tab = np.array([math.erf(2.0 / (v * 1.41421)) / math.erf(6.0 / (v * 1.41421)) for v in sig])
        blur = float(np.interp(ratio, tab[::-1], sig[::-1]))   # the table falls as sigma grows
    # areas join only through a shared side, never across a corner, so a marked line one pixel
    # wide already parts them
    n, lbl, stats, _ = cv2.connectedComponentsWithStats((~edge).astype(np.uint8), connectivity=4)
    area = stats[:, cv2.CC_STAT_AREA].astype(np.float64)
    area[0] = 0                                                # label 0 is the edge zone itself
    keep = [i for i in np.argsort(-area) if area[i] >= min_area][:MAX_REGIONS]
    lut = np.zeros(n, np.int32)
    lut[keep] = np.arange(1, len(keep) + 1)
    boxes = np.zeros((len(keep) + 1, 4), np.int64)
    for new, old in enumerate(keep, start=1):
        x, y, w_, h_ = (int(v) for v in stats[old, :4])
        boxes[new] = (x, y, x + w_, y + h_)
    return lut[lbl], len(keep), step, noise, boxes, blur


def detect_transitions(img, step=0.0, accept=0.75, maybe=0.45, min_feature_px=None, exclude=(),
                       thin_features=True, levels=(0.5, 0.3, 0.15, 0.7)):
    """Find the closed outline of every colour area: an outline wherever the colour changes sharply.

    The area that fills the picture's border (the paper, the canvas, the backdrop behind a
    transparent picture) is the background and gets no outline of its own. Returns a Detection
    like detect(); each Loop carries the area it bounds in .group and that area's colour in
    .colour, and flags["areas"] lists the areas found."""
    img, _ = composite(img)
    H, W = img.shape[:2]
    ref = float(np.hypot(W, H)) / 2000.0
    if min_feature_px is None:
        min_feature_px = 14.0 * max(ref, 0.5)
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    L = lab(img)
    k3 = np.ones((3, 3), np.uint8)
    raw = float(np.median(np.sqrt(sum((cv2.dilate(L[:, :, c], k3) - cv2.erode(L[:, :, c], k3)) ** 2
                                      for c in range(3)))))
    # Where the edges are is judged in Lab (a step there is a step to the eye), on a calmed copy.
    L = cv2.GaussianBlur(L, (0, 0), 1.0 if raw < 2.0 else 1.6 if raw < 6.0 else 2.2)
    lbl, n, step, noise, boxes, soft = colour_areas(L, step, 0.8 * np.pi * (min_feature_px / 2.0) ** 2)
    soft = float(np.sqrt(max(soft * soft - 1.0, 0.0)))        # less the smoothing just applied
    # How far a pixel is between two colours is measured in the picture's own values: an edge
    # pixel is a plain mix of the colours either side, so its place between them is exact there.
    L = np.ascontiguousarray(img[:, :, :3]).astype(np.float32)
    if raw >= 2.0:
        L = cv2.GaussianBlur(L, (0, 0), 0.8 if raw < 6.0 else 1.4)

    def to_lab(v):
        v = np.asarray(v, np.float32).reshape(-1, 1, 3) / 255.0
        return cv2.cvtColor(v, cv2.COLOR_BGR2Lab).reshape(-1, 3)
    flags = dict(border_cut=0, thin=0, strokes=0, partly_sharp=0, areas=[], step=step, canvas=None,
                 n_areas=n, left_out=0, soft=soft)
    # A badly blurred picture hides its areas from the edge test. Report the blur even when
    # nothing was found, so the caller can try again on a smaller copy where edges are sharp.
    blurred = soft if soft > 3.2 else 0.0
    if n < 2:
        return Detection([], [], max(1.0, blurred), 0.0, noise, True, "colours", 6.0, float(min_feature_px),
                         None, flags)

    w = int(np.clip(round(12 * ref), 9, 24))                   # how far "just here" reaches, px
    sep = np.ones((4 * w + 3, 4 * w + 3), np.uint8)
    k_er = np.ones((3, 3), np.uint8)

    def nearest(lbl):
        """Every pixel's nearest area (pixels on an edge belong to nobody yet)."""
        _, idx = cv2.distanceTransformWithLabels((lbl == 0).astype(np.uint8), cv2.DIST_L2, 3,
                                                 labelType=cv2.DIST_LABEL_PIXEL)
        owner = np.zeros(int(idx.max()) + 1, np.int32)
        owner[idx[lbl > 0]] = lbl[lbl > 0]
        return owner[idx]

    def schedule(lbl, n, boxes):
        """Areas that lie well apart share a pass: within one pass no pixel is near two of them.
        Returns the passes (each a picture of the area numbers it holds) and area -> pass."""
        passes, where = [], {}
        for r in range(1, n + 1):                              # largest first: numbers run by size
            x0, y0, x1, y1 = (int(v) for v in boxes[r])
            X0, Y0 = max(0, x0 - 2 * w - 2), max(0, y0 - 2 * w - 2)
            X1, Y1 = min(W, x1 + 2 * w + 2), min(H, y1 + 2 * w + 2)
            here = lbl[Y0:Y1, X0:X1] == r
            grown = cv2.dilate(here.astype(np.uint8), sep) > 0
            for k, occ in enumerate(passes):
                if not (occ[Y0:Y1, X0:X1][grown] > 0).any():
                    break
            else:
                if len(passes) >= MAX_PASSES:
                    flags["left_out"] += 1
                    continue
                passes.append(np.zeros((H, W), np.uint16))
                k = len(passes) - 1
            passes[k][Y0:Y1, X0:X1][here] = r
            where[r] = k
        return passes, where

    def local_colour(occ):
        """For one pass: each pixel's distance from the colour its area has just here (inf where
        none of the pass's areas is near), that colour itself, and which area it belongs to.
        The colour of an area changes slowly, so it is worked out at half size."""
        core = (occ > 0).astype(np.uint8)
        inner = cv2.erode(core, k_er)                          # keep clear of the edge's own blend
        lost = np.setdiff1d(np.unique(occ[core > 0]), np.unique(occ[inner > 0]))
        if len(lost):                                          # too thin to survive that: all of it
            inner[np.isin(occ, lost)] = 1
        m = inner.astype(np.float32)
        hs, ws = (H + 1) // 2, (W + 1) // 2
        wh = (w + 1) // 2
        kh = (2 * wh + 1, 2 * wh + 1)
        den = cv2.blur(cv2.resize(m, (ws, hs), interpolation=cv2.INTER_AREA), kh)
        F = cv2.blur(cv2.resize(L * m[:, :, None], (ws, hs), interpolation=cv2.INTER_AREA), kh)
        F /= np.maximum(den, 1e-6)[:, :, None]
        ids = cv2.resize(cv2.dilate(occ.astype(np.float32), np.ones((2, 2), np.uint8)), (ws, hs),
                         interpolation=cv2.INTER_NEAREST)
        own = cv2.dilate(ids, np.ones(kh, np.uint8)).astype(np.int32)
        # carry that colour outwards from where it is known (a pointed tip of an area has no
        # ground of its own nearby): each pixel takes it from the nearest pixel that has it
        known = den > 1e-4
        away, idx = cv2.distanceTransformWithLabels((~known).astype(np.uint8), cv2.DIST_L2, 3,
                                                    labelType=cv2.DIST_LABEL_PIXEL)
        need = ~known & (away <= 2 * wh)
        src = np.zeros(int(idx.max()) + 1, np.int64)
        src[idx[known]] = np.flatnonzero(known)
        src = src[idx[need]]                                   # flat index of the nearest known pixel
        F[need] = F.reshape(-1, 3)[src]
        own[need] = own.ravel()[src]
        own[~(known | need)] = 0
        F = cv2.resize(F, (W, H), interpolation=cv2.INTER_LINEAR)
        own = cv2.resize(own.astype(np.float32), (W, H), interpolation=cv2.INTER_NEAREST).astype(np.uint16)
        d = np.sqrt(_d2x(L, F))
        d[own == 0] = np.inf
        # each area's own colour (in Lab), from its inner ground
        sel = inner > 0
        ids = occ[sel]
        cnt = np.bincount(ids, minlength=n + 1).astype(np.float64)
        sums = np.stack([np.bincount(ids, L[:, :, c][sel], n + 1) for c in range(3)], 1)
        means = {int(r): to_lab(sums[r] / cnt[r])[0] for r in np.nonzero(cnt)[0]}
        return d, F, own, means

    # Two areas whose colours hardly differ where they meet are one area: what parts them is a
    # faint line or a speckle of noise (JPEG blocks), not a change of colour. A small area has
    # to stand out more than a large one to be kept. Joining areas changes what "the colour
    # just here" is for their neighbours, so look again until nothing more joins.
    small = 4.0 * np.pi * (min_feature_px / 2.0) ** 2
    short = 8.0 * min_feature_px                               # border points come two to the pixel
    flags["merged"] = 0
    for _ in range(4):
        near = nearest(lbl)
        passes, where = schedule(lbl, n, boxes)
        ya, xa = np.nonzero(near[:, :-1] != near[:, 1:])
        yb, xb = np.nonzero(near[:-1, :] != near[1:, :])
        py = np.r_[ya, yb]
        px = np.r_[xa, xb]
        pi = near[py, px]
        pj = np.r_[near[ya, xa + 1], near[yb + 1, xb]]
        fields = []
        Fi = np.full((len(py), 3), np.nan, np.float32)
        Fj = np.full((len(py), 3), np.nan, np.float32)
        in_pass = np.full(n + 1, -1, int)
        for r, k in where.items():
            in_pass[r] = k
        for k, occ in enumerate(passes):
            d, F, own, means = local_colour(occ)
            fields.append((d, own, means))
            at = F[py, px]
            ok = np.isfinite(d[py, px])
            si = ok & (in_pass[pi] == k) & (own[py, px] == pi)
            sj = ok & (in_pass[pj] == k) & (own[py, px] == pj)
            Fi[si] = at[si]
            Fj[sj] = at[sj]
            del F
        gap = np.full(len(py), np.nan, np.float32)
        both = np.isfinite(Fi[:, 0]) & np.isfinite(Fj[:, 0])
        if both.any():
            gap[both] = np.linalg.norm(to_lab(Fi[both]) - to_lab(Fj[both]), axis=1)
        okp = np.isfinite(gap) & (pi > 0) & (pj > 0)
        if not okp.any():
            break
        sizes = np.bincount(lbl.ravel(), minlength=n + 1)
        parent = list(range(n + 1))

        def root(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        lo, hi = np.minimum(pi[okp], pj[okp]).astype(np.int64), np.maximum(pi[okp], pj[okp]).astype(np.int64)
        key = lo * (n + 1) + hi
        order = np.argsort(key, kind="stable")
        key, g = key[order], gap[okp][order]
        cut = np.r_[0, np.nonzero(np.diff(key))[0] + 1, len(key)]
        joined = 0
        closest = {}                                           # small area -> (gap, its most alike neighbour)

        def join(i, j):
            ri, rj = root(i), root(j)
            if ri != rj:
                parent[max(ri, rj)] = min(ri, rj)
                return 1
            return 0

        for a, b in zip(cut[:-1], cut[1:]):
            if b - a < 6:
                continue
            i, j = divmod(int(key[a]), n + 1)
            gm = float(np.median(g[a:b]))
            if gm < MERGE * step:
                joined += join(i, j)
            elif b - a < short and gm < 2.0 * step:
                joined += join(i, j)                           # a short border has to stand out more
            for u, v in ((i, j), (j, i)):
                if sizes[u] < small and gm < closest.get(u, (2.0 * step, 0))[0]:
                    closest[u] = (gm, v)
        for u, (gm, v) in closest.items():                     # a small area joins one neighbour only,
            joined += join(u, v)                               # or it would bridge two that differ
        if not joined:
            break
        flags["merged"] += joined
        roots = np.array([root(i) for i in range(n + 1)])
        sizes = np.bincount(roots[lbl].ravel(), minlength=n + 1).astype(np.float64)
        sizes[0] = 0
        keep = [i for i in np.argsort(-sizes) if sizes[i] > 0]
        lut = np.zeros(n + 1, np.int32)
        lut[keep] = np.arange(1, len(keep) + 1)
        lbl = lut[roots[lbl]]
        joined_boxes = np.zeros((len(keep) + 1, 4), np.int64)
        joined_boxes[:, :2] = max(W, H)
        for old in range(1, n + 1):
            new = lut[roots[old]]
            joined_boxes[new, :2] = np.minimum(joined_boxes[new, :2], boxes[old, :2])
            joined_boxes[new, 2:] = np.maximum(joined_boxes[new, 2:], boxes[old, 2:])
        boxes = joined_boxes
        n = len(keep)
    flags["n_areas"] = n
    if n < 2:
        return Detection([], [], max(1.0, blurred), 0.0, noise, True, "colours", 6.0, float(min_feature_px),
                         None, flags)

    ring = np.r_[near[0], near[-1], near[1:-1, 0], near[1:-1, -1]]
    on_ring = np.bincount(ring, minlength=n + 1) / float(len(ring))
    canvas = int(on_ring.argmax()) if on_ring.max() >= 0.6 else None
    flags["canvas"] = canvas
    dist = [f[0] for f in fields]
    own = [f[1] for f in fields]
    mean = {}
    for f in fields:
        mean.update(f[2])
    # the background showing through (the table seen through a hole, the page inside a letter)
    # is not an area of its own: it is a hole in whatever surrounds it
    ground = set()
    if canvas is not None and canvas in mean:
        ground = {r for r in mean if float(np.linalg.norm(mean[r] - mean[canvas])) < max(1.5 * step, 8.0)}
    flags["ground"] = len(ground)

    def bgr_of(r):
        one = cv2.cvtColor(np.float32([[mean[r]]]), cv2.COLOR_Lab2BGR)[0, 0]
        return tuple(int(v) for v in np.clip(one * 255.0 + 0.5, 0, 255))

    colours = {r: bgr_of(r) for r in mean}
    sizes = np.bincount(lbl.ravel(), minlength=n + 1)
    for r in range(1, n + 1):
        flags["areas"].append(dict(colour=colours.get(r), share=float(sizes[r]) / float(H * W), outlines=0,
                                   canvas=(r in ground), traced=r in where))
    floor = 2.55 * max(2.0, 0.4 * step)                        # picture values (0..255), not Lab
    loops, rejected, sig, reach = [], [], [], 6.0
    is_ground = np.zeros(n + 1, bool)
    is_ground[list(ground)] = True
    for k, occ in enumerate(passes):
        if not (~is_ground[occ[occ > 0]]).any():
            continue                                           # a pass holding only background
        other = np.full((H, W), np.inf, np.float32)
        for j, d in enumerate(dist):
            if j != k:
                np.minimum(other, d, out=other)
        d = dist[k]
        with np.errstate(invalid="ignore", divide="ignore"):
            t = d / (d + other)                                # 0 = this area's colour, 1 = the neighbour's
        t[~np.isfinite(d)] = 1.0                               # nowhere near one of this pass's areas
        t[np.isfinite(d) & ~np.isfinite(other)] = 0.0          # nothing else near: inside
        mine = near == own[k]
        # the two colours are the same just here: colour cannot say whose pixel it is, so the
        # nearer area takes it
        same = np.isfinite(d) & np.isfinite(other) & (d + other < floor)
        t[same] = np.where(mine[same], 0.0, 1.0)
        t[(lbl > 0) & (lbl != own[k])] = 1.0                   # another area's own ground
        t[occ > 0] = np.minimum(t[occ > 0], 0.25)              # this area's own ground
        t[is_ground[own[k]]] = 1.0                             # the background is nobody's shape
        m = np.clip(255.0 * t + 0.5, 0, 255).astype(np.uint8)
        del t, other, same, mine
        det = detect(m, levels=levels, accept=accept, maybe=maybe, polarity="dark", channel="gray",
                     min_feature_px=min_feature_px, keep_work=False, line_art="off", exclude=exclude,
                     thin_features=thin_features, even_light=False)
        for group, found in ((loops, det.loops), (rejected, det.rejected)):
            for lp in found:
                xi = np.clip(np.round(lp.pts[:, 0]).astype(int), 0, W - 1)
                yi = np.clip(np.round(lp.pts[:, 1]).astype(int), 0, H - 1)
                ids = own[k][yi, xi]
                ids = ids[ids > 0]
                lp.group = int(np.bincount(ids).argmax()) if len(ids) else -1
                lp.colour = colours.get(lp.group)
                group.append(lp)
                if group is loops and lp.conf >= accept and 0 < lp.group <= n:
                    flags["areas"][lp.group - 1]["outlines"] += 1
        for key in ("border_cut", "thin", "strokes", "partly_sharp"):
            flags[key] += int(det.flags.get(key, 0) or 0)
        if det.flags.get("too_many"):
            flags["too_many"] = True
        if det.loops:
            sig.append(det.edge_sigma)
            reach = max(reach, det.reach)
    loops.sort(key=lambda l: (-(l.conf >= accept), -l.area))
    return Detection(loops, rejected, max(float(np.median(sig)) if sig else 1.0, blurred),
                     255.0 if loops else 0.0, noise, True, "colours", reach, float(min_feature_px), None, flags)


def _d2x(L, F):
    """Squared distance of every pixel to its own colour in F (both float32, 3 channels)."""
    diff = cv2.subtract(L, F)
    return cv2.transform(cv2.multiply(diff, diff), np.ones((1, 3), np.float32))
