# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
# Copied from the ImageToSketch project by tools/sync_imagetosketch.py - edit it there.
"""Top level: image in, scaled CAD primitives out.

    from tostep.plugins.image2step.imagetosketch import trace, Options
    res = trace("photo.jpg", Options(scale_mode="markers"))
    res.shapes[0].prims      # primitives in mm, y up, ready for a sketch
"""
import math
import time
from dataclasses import dataclass, field

import cv2
import numpy as np

from . import pattern
from .detect import detect, detect_transitions, palette, composite, MERGE
from .imageio import load_image, read_lens
from .fit import fit_loop, fit_quick, prim_counts
from .geom import sample_prims, poly_area


@dataclass
class Options:
    scale_mode: str = "mm_per_px"   # "mm_per_px" | "image_width" | "markers" | "token" | "calsheet" | "square" | "dpi"
    scale_value: float = 1.0        # mm per px | image width in mm | (unused) | square side in mm
    check_line_mm: float = pattern.CHECK_MM   # measured length of the sheet's check line
    polarity: str = "auto"          # "auto" | "dark" | "light"   (tone of the part)
    channel: str = "auto"           # "auto" | "gray" | "red" | "green" | "blue" | "saturation"
    prefer: str = "material"        # competing edges: "material" (ignore hard shadows) | "outline"
    accept: float = 0.75            # confidence to become real geometry
    maybe: float = 0.45             # confidence to be kept as borderline (construction)
    tol_px: float = 0.0             # fit tolerance in px; 0 = automatic (0.5, more if edges are soft)
    min_feature_mm: float = 0.0     # 0 = automatic; smaller loops are only ever "borderline"
    detect_circles: bool = True
    bridge_bumps: bool = True       # let a line/arc run straight through dirt smaller than min feature
    centre: bool = True             # put the middle of the traced shapes on the sketch origin
    max_side: int = 2400            # larger images are reduced first (speed, memory)
    max_borderline: int = 80        # cap on borderline shapes passed on (largest first)
    line_art: str = "auto"          # "auto" | "on" | "off": trace drawn strokes along their centre
    thin_features: bool = True      # look for thin walls / slots / gaps the clean-up would erase
    enlarge: float = 0.0            # small pictures are enlarged smoothly first: 0 = automatic, 1 = off, else factor
    picture_edge: str = "auto"      # a shape running to the picture's edge: "edge" = the picture's edge is
                                    # its edge (icons, cropped graphics) | "cut" = it is cut off, leave it
                                    # out (photos) | "auto" = "edge" for drawn graphics, "cut" for photos
    # --- EXPERIMENTAL: what counts as an edge
    find: str = "tone"              # "tone" = the part against its background (one outline per part) |
                                    # "transitions" = every change of colour: each flat colour area gets
                                    # its own outline (icons, logos, painted or multi-colour parts)
    colour_step: float = 0.0        # transitions: colours closer than this (CIE Lab units; about 2.3 is
                                    # just noticeable) are one colour. 0 = automatic (6, more if noisy)
    shared_edges: str = "once"      # transitions: an edge between two neighbouring areas belongs to both
                                    # outlines. "once" = draw it once in the sketch / SVG / DXF |
                                    # "both" = every outline complete and closed, shared edges doubled
    weld_edges: bool = True         # transitions: neighbouring areas use one and the same curve for the
                                    # edge they share (weld.py), so their pieces fit exactly
    # --- scale_mode "token": the printed black square with one clipped and one rounded corner
    token_side_mm: float = 40.0     # measured side of the token
    token_thickness_mm: float = 0.0    # 0 = printed on paper; 1.2 for the 3D-printed token
    part_above_token_mm: float = 0.0   # how much higher the traced face is than the token's top face
                                       # (paper token: simply the part's height)
    camera_distance_mm: float = 0.0    # camera to the table; used when the photo has no lens data (lens data
                                       # first). Neither: a phone's main camera without zoom is assumed
    focal_35mm: float = 0.0            # 35 mm-equivalent focal length; 0 = read it from the file
    # --- scale_mode "calsheet": the calibration sheet (marker frame) and its 50 mm stand; also
    # uses part_above_token_mm (the part's height above the paper), camera_distance_mm,
    # focal_35mm and straight_walls
    stand_height_mm: float = 50.0      # stand's top (with its label) above the paper, as measured
    sheet_across_mm: float = 150.0     # what the page-2 check lines measure (printer stretch)
    sheet_along_mm: float = 200.0
    empty_sheet: object = None         # a photo of the same sheet WITHOUT the part (file or array),
                                       # optional: what did not change, and the part's shadow, are
                                       # made plain paper before tracing (pairdiff.py)
    straight_walls: bool = True        # the part has vertical sides: where the camera sees a wall, move
                                       # the outline from the bottom edge it shows to the top edge


@dataclass
class Shape:
    prims: list                     # mm, y up
    hole: bool
    conf: float
    status: str                     # "accept" | "maybe"
    note: str
    area_mm2: float
    outline: np.ndarray = field(default=None, repr=False)      # refined px outline
    prims_px: list = field(default=None, repr=False)
    group: int = -1                 # transitions: which colour area this outline bounds (-1: tone mode)
    colour: tuple = None            # ... and that colour (B, G, R)
    draw: list = None               # transitions: per primitive, False = this piece is an edge shared
    #                                 with a neighbouring area that already draws it. None = draw all.
    #                                 (STEP export always uses the whole closed outline.)

    def drawn(self, px=False):
        """The primitives to draw, as runs that join end to end: [(prims, closed)]. Normally one
        closed run - the whole outline. Where pieces are left to a neighbour, the open runs
        between them. px=True gives them in pixels of the working picture instead of mm."""
        prims = self.prims_px if px else self.prims
        if not prims:
            return []
        if self.draw is None or all(self.draw) or len(self.draw) != len(prims):
            return [(list(prims), True)]
        n = len(prims)
        if not any(self.draw):
            return []
        start = next(i for i in range(n) if self.draw[i] and not self.draw[i - 1])
        runs, cur = [], []
        for k in range(n):
            i = (start + k) % n
            if self.draw[i]:
                cur.append(prims[i])
            elif cur:
                runs.append((cur, False))
                cur = []
        if cur:
            runs.append((cur, False))
        return runs


@dataclass
class Result:
    shapes: list
    rejected: list                  # px outlines that failed the confidence test
    mm_per_px: float
    size_px: tuple                  # (W, H) of the working image
    origin_px: tuple                # px position that maps to sketch (0, 0)
    image: np.ndarray = field(default=None, repr=False)        # working image (rectified if markers)
    info: dict = field(default_factory=dict)

    def to_mm(self, p):
        s = self.mm_per_px
        return ((p[0] - self.origin_px[0]) * s, (self.origin_px[1] - p[1]) * s)

    @property
    def accepted(self):
        return [s for s in self.shapes if s.status == "accept"]

    @property
    def borderline(self):
        return [s for s in self.shapes if s.status == "maybe"]

    def islands(self):
        """How many accepted outlines are solid shapes lying inside a hole of another (a letter
        inside a cut-out). They are separate pieces of material: STEP export and Part > Extrude
        keep them, Part Design's Pad - one solid per body - leaves them out."""
        import cv2
        holes = [np.asarray(s.outline, np.float32).reshape(-1, 1, 2) for s in self.accepted
                 if s.hole and s.outline is not None and len(s.outline) >= 3]
        n = 0
        for s in self.accepted:
            if s.hole or s.outline is None or not len(s.outline):
                continue
            x, y = (float(v) for v in np.asarray(s.outline, np.float32).reshape(-1, 2)[0])
            n += any(cv2.pointPolygonTest(h, (x, y), False) > 0 for h in holes)
        return n

    def image_extent_mm(self):
        """(xmin, ymin, xmax, ymax) of the working image in sketch coordinates."""
        W, H = self.size_px
        x0, y1 = self.to_mm((-0.5, -0.5))
        x1, y0 = self.to_mm((W - 0.5, H - 0.5))
        return x0, y0, x1, y1

    def summary(self):
        cnt = {}
        for s in self.accepted:
            for prims, _ in s.drawn():
                for k, v in prim_counts(prims).items():
                    cnt[k] = cnt.get(k, 0) + v
        parts = ", ".join("%d %s%s" % (v, k, "" if v == 1 else "s") for k, v in sorted(cnt.items()))
        n_col = len({s.group for s in self.accepted if s.group >= 0})
        return ("%d outline%s%s (%d hole%s): %s. %d borderline, %d rejected. Scale %.5f mm/px." % (
            len(self.accepted), "" if len(self.accepted) == 1 else "s",
            (" of %d colour area%s" % (n_col, "" if n_col == 1 else "s")) if n_col else "",
            sum(s.hole for s in self.accepted), "" if sum(s.hole for s in self.accepted) == 1 else "s",
            parts or "nothing", len(self.borderline), len(self.rejected),
            self.info.get("mm_per_source_px") or self.mm_per_px))      # per pixel of the picture given

    def overlay(self, max_side=1400):
        """BGR picture of the verdicts: green = accepted, amber = borderline, red = rejected."""
        img = self.image
        if img.ndim == 2:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        img = img[:, :, :3].copy()
        f = min(1.0, max_side / float(max(img.shape[:2])))
        if f < 1:
            img = cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
        img = (img * 0.55 + 255 * 0.45).astype(np.uint8)       # wash out so lines read clearly
        th = max(1, int(round(max(img.shape[:2]) / 600.0)))

        def draw(P, col, t, closed=True):
            q = (((P + 0.5) * f - 0.5) * 16).astype(np.int32).reshape(-1, 1, 2)
            cv2.polylines(img, [q], closed, col, t, cv2.LINE_AA, 4)

        for L in self.rejected:
            draw(L, (40, 40, 220), th)
        for status, col in (("maybe", (0, 150, 255)), ("accept", (0, 150, 0))):   # accepted on top
            for s in self.shapes:
                if s.status != status:
                    continue
                for prims, closed in s.drawn(px=True):
                    P = sample_prims(prims, 2.0)
                    if not closed:                             # an open run: up to its last end point
                        e = prims[-1]
                        P = np.vstack([P, [e[2] if e[0] == "line" else e[3] if e[0] == "arc" else e[1][-1]]])
                    if len(P) >= 2:
                        draw(P, col, th + 1, closed)
        return img


def _to_mm_prims(prims, tf, s):
    out = []
    for p in prims:
        if p[0] == "circle":
            x, y = tf((p[1], p[2]))
            out.append(("circle", x, y, p[3] * s))
        elif p[0] == "line":
            out.append(("line", tf(p[1]), tf(p[2])))
        elif p[0] == "arc":
            out.append(("arc", tf(p[1]), tf(p[2]), tf(p[3])))
        elif p[0] == "ellipse":                                # angle flips with the y axis
            x, y = tf((p[1], p[2]))
            out.append(("ellipse", x, y, p[3] * s, p[4] * s, -p[5]))
        else:
            out.append(("spline", [tf(q) for q in p[1]], p[2]))
    return out


def _square_side(prims):
    """Side length if the primitives form a square (4 lines, equal, right-angled), else None."""
    if len(prims) != 4 or any(p[0] != "line" for p in prims):
        return None
    v = [np.subtract(p[2], p[1]) for p in prims]
    L = [float(np.hypot(*d)) for d in v]
    if min(L) < 8 or max(L) / min(L) > 1.06:
        return None
    for i in range(4):
        c = abs(float(np.dot(v[i], v[(i + 1) % 4]))) / (L[i] * L[(i + 1) % 4])
        if c > math.sin(math.radians(4)):
            return None
    return float(np.mean(L))


ENLARGE_BELOW = 1000        # automatic enlargement: only pictures smaller than this (long side, px)
ENLARGE_TO = 1400           # ... are brought up to about this size
ENLARGE_MAX = 6.0           # ... but never by more than this factor
BLUR_TARGET = 2.6           # edge sigma (px) a blurred picture is brought back to
WALL_DOUBT_MM = 1.0         # side walls are corrected only if a camera distance out by its usual margin
                            # would move the corrected outline by no more than this
TRANSITION_MAX_SIDE = 1600  # transition mode: larger pictures are reduced to this first (time, memory)


def hard_pixels(img):
    """True if the picture has stair-stepped edges (no anti-aliasing): pixel art, 1-bit scans,
    screenshots saved at a hard threshold. Such edges are only known to half a pixel."""
    g = img if img.ndim == 2 else cv2.cvtColor(img[:, :, :3], cv2.COLOR_BGR2GRAY)
    if img.ndim == 3 and img.shape[2] == 4 and int(img[:, :, 3].min()) < 250:
        a = img[:, :, 3].astype(np.float32) / 255.0             # judge what is seen: picture over white
        g = (g.astype(np.float32) * a + 255.0 * (1 - a)).astype(np.uint8)
    k = np.ones((3, 3), np.uint8)
    lo, hi = cv2.erode(g, k), cv2.dilate(g, k)
    rng = hi.astype(np.int16) - lo
    span = float(np.percentile(g, 99)) - float(np.percentile(g, 1))
    E = rng > max(24.0, 0.5 * span)
    n = int(E.sum())
    if n < 40:
        return False
    t = (g.astype(np.float32)[E] - lo[E]) / rng[E]
    mid = float(((t > 0.2) & (t < 0.8)).mean())
    return mid < 0.04


def flat_noise(img):
    """Noise (grey levels, sigma) of the flat areas of a picture. Unlike a plain average it is
    not fooled by edges and fine detail: a clean drawing or icon gives 0."""
    g = img if img.ndim == 2 else cv2.cvtColor(img[:, :, :3], cv2.COLOR_BGR2GRAY)
    k = np.float32([[1, -2, 1], [-2, 4, -2], [1, -2, 1]])
    r = np.abs(cv2.filter2D(g.astype(np.float32), -1, k))[1:-1, 1:-1]
    return float(np.median(r)) / 6.0 * 1.4826


# Fit tolerance on an enlarged picture, per unit of enlargement: how well an edge is located in
# the original's own pixels - a tenth of a pixel when clean, more with noise, never over half.
TOL_BASE, TOL_NOISE = 0.1, 0.05
NOISE_LIMIT = 12.0          # noisier than this (grey levels): enlarging only turns noise into shapes


def flat_graphic(img):
    """True for a drawn picture (icon, logo, screenshot, clean scan of a drawing): no noise in
    its flat areas and nearly all of it in a few flat tones. A photograph has neither."""
    if flat_noise(img) > 0.75:
        return False
    g = img if img.ndim == 2 else cv2.cvtColor(img[:, :, :3], cv2.COLOR_BGR2GRAY)
    h = np.bincount(g.ravel(), minlength=256).astype(np.float64)
    h = np.convolve(h, np.ones(9), "same")                     # a tone and its 4 neighbours each way
    total, covered = float(g.size), 0.0
    for _ in range(4):
        i = int(h.argmax())
        covered += h[i]
        h[max(0, i - 12):i + 13] = 0
    return covered / total > 0.9


def canvas_tone(img, backdrop, mode, polarity="auto"):
    """What lies beyond the edge of the picture, if a shape that runs to the edge is to be
    closed along it: the tone (per channel) to continue the picture with, or None to leave
    such shapes as "cut off". backdrop: what `composite` put behind a transparent picture."""
    if mode == "cut":
        return None
    g = (img if img.ndim == 2 else cv2.cvtColor(img[:, :, :3], cv2.COLOR_BGR2GRAY)).astype(np.float32)
    H, W = g.shape
    if min(H, W) < 12:
        return None
    ring = np.r_[g[0], g[-1], g[1:-1, 0], g[1:-1, -1]]
    k = max(2, min(H, W) // 40)
    cs = [(slice(0, k), slice(0, k)), (slice(0, k), slice(W - k, W)),
          (slice(H - k, H), slice(0, k)), (slice(H - k, H), slice(W - k, W))]
    cg = np.array([float(np.median(g[c])) for c in cs])
    if backdrop:
        tone = np.full(1 if img.ndim == 2 else 3, 255.0 if backdrop == "white" else 0.0)
        ref = float(tone[0])
    else:
        order = np.argsort(cg)
        if cg.max() - cg.min() > 24:                           # the corners disagree
            if mode != "edge":
                return None
            # told to: go by the tone the part is not, or by most of the corners
            if polarity == "dark":
                pick = [order[-1]]
            elif polarity == "light":
                pick = [order[0]]
            else:
                gap = int(np.argmax(np.diff(cg[order])))
                pick = list(order[:gap + 1]) if gap + 1 >= 2 else list(order[gap + 1:])
        else:
            pick = list(order)
        px = np.vstack([img[cs[i]].reshape(-1, 1 if img.ndim == 2 else img.shape[2])[:, :3] for i in pick])
        tone = np.median(px.astype(np.float32), axis=0)
        ref = float(np.median(cg[pick]))
    touching = float((np.abs(ring - ref) > 40).mean())
    if touching < 0.02:
        return None                                            # nothing runs to the edge
    if mode == "auto" and not backdrop and not flat_graphic(img):
        return None                                            # a photo: the frame cut the part off
    return tone


def _auto_zoom(long_side, opt):
    if opt.enlarge == 0:
        return min(ENLARGE_MAX, ENLARGE_TO / long_side) if long_side < ENLARGE_BELOW else 1.0
    return min(float(opt.enlarge), max(1.0, 4800.0 / long_side))


def busy(img, limit=400):
    """True if the picture is a pattern or texture (hundreds of separate little shapes): a
    checkerboard, halftone, QR code, fabric. Enlarging one only makes more of it."""
    g = img if img.ndim == 2 else cv2.cvtColor(img[:, :, :3], cv2.COLOR_BGR2GRAY)
    _, m = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    cnts, _ = cv2.findContours(m, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    return len(cnts) > limit


def _resized(img, k, hard):
    """A copy of the picture k times the size. Enlarging is smooth (cubic), so the edge of an
    anti-aliased shape keeps its sub-pixel position; stair-stepped pictures are softened first."""
    if abs(k - 1.0) < 1e-6:
        return img
    if k < 1.0:
        return cv2.resize(img, None, fx=k, fy=k, interpolation=cv2.INTER_AREA)
    if hard:
        img = cv2.GaussianBlur(img, (0, 0), hard)
    return cv2.resize(img, None, fx=k, fy=k, interpolation=cv2.INTER_CUBIC)


def weld_slivers(prims, min_len):
    """Transition mode: where three areas meet, the two outlines that part there can leave a
    line a pixel or two long between the pieces that matter. Take such a line out and let its
    neighbours meet at its middle. Only lines between lines and arcs; the loop stays closed."""
    prims = list(prims)
    i = 0
    while len(prims) >= 4 and i < len(prims):
        p = prims[i]
        if p[0] == "line" and math.hypot(p[2][0] - p[1][0], p[2][1] - p[1][1]) < min_len:
            a, b = prims[i - 1], prims[(i + 1) % len(prims)]
            if a[0] in ("line", "arc") and b[0] in ("line", "arc") and a is not b:
                mid = ((p[1][0] + p[2][0]) / 2.0, (p[1][1] + p[2][1]) / 2.0)
                prims[i - 1] = a[:-1] + (mid,)                 # its end is the last item
                prims[(i + 1) % len(prims)] = (b[0], mid) + b[2:]
                del prims[i]
                i = max(i - 1, 0)
                continue
        i += 1
    return prims


def share_edges(res, reach_px):
    """Transition mode: an edge between two neighbouring colour areas is part of both their
    outlines. Mark, in each accepted shape, the primitives a neighbour already draws, so the
    sketch / SVG / DXF carry every transition line once. Longest pieces are kept first, outer
    outlines before holes: a hole that only repeats the outlines of what lies inside it draws
    nothing. Returns how many primitives were left to a neighbour."""
    acc = [s for s in res.shapes if s.status == "accept" and s.group >= 0 and s.prims_px]
    if len({s.group for s in acc}) < 2:
        return 0
    W, H = res.size_px
    th = int(2 * max(2, round(reach_px)) + 1)
    owner = np.zeros((H, W), np.uint16)                        # which areas have drawn through each pixel
    items = []
    for s in acc:
        s.draw = [True] * len(s.prims_px)
        for i, p in enumerate(s.prims_px):
            P = sample_prims([p], 1.5)
            if p[0] == "line":
                P = np.vstack([P, [p[2]]])
            elif p[0] == "arc":
                P = np.vstack([P, [p[3]]])
            closed = p[0] in ("circle", "ellipse") or (p[0] == "spline" and bool(p[2]))
            if len(P) < 2:
                continue
            length = float(np.hypot(*np.diff(P, axis=0).T).sum())
            items.append((bool(s.hole), -length, len(items), s, i, P, closed))
    items.sort(key=lambda t: t[:3])
    left = 0
    for _, _, _, s, i, P, closed in items:
        bit = np.uint16(1 << (s.group % 16))
        xi = np.clip(np.round(P[:, 0]).astype(int), 0, W - 1)
        yi = np.clip(np.round(P[:, 1]).astype(int), 0, H - 1)
        theirs = (owner[yi, xi] & np.uint16(~int(bit) & 0xFFFF)) != 0
        if theirs.mean() >= 0.9:
            s.draw[i] = False
            left += 1
            continue
        x0, y0 = max(0, int(xi.min()) - th), max(0, int(yi.min()) - th)
        x1, y1 = min(W, int(xi.max()) + th + 1), min(H, int(yi.max()) + th + 1)
        roi = np.zeros((y1 - y0, x1 - x0), np.uint8)
        q = ((P - (x0, y0)) * 16).astype(np.int32).reshape(-1, 1, 2)
        cv2.polylines(roi, [q], closed, 1, th, cv2.LINE_8, 4)
        owner[y0:y1, x0:x1] |= roi.astype(np.uint16) * bit
    return left


def colour_count(img):
    """How many flat colours a clean, drawn picture has, not counting the one round its edge:
    0 for a photo or anything noisy. Used to suggest transition mode."""
    h, w = img.shape[:2]                                       # (a quick look: this runs on every ordinary trace)
    mid = img[max(0, h // 2 - 400):h // 2 + 400, max(0, w // 2 - 400):w // 2 + 400]
    if flat_noise(mid) > 0.75:
        return 0
    pal = palette(img, max_side=256)
    n = sum(1 for sh in pal["share"] if sh >= 0.01)
    if n:
        from .detect import lab
        ring = np.concatenate([img[0].reshape(-1, img.shape[2] if img.ndim == 3 else 1),
                               img[-1].reshape(-1, img.shape[2] if img.ndim == 3 else 1)])
        edge = lab(ring.reshape(1, -1, ring.shape[1]) if ring.shape[1] > 1 else ring.reshape(1, -1))[0]
        near = np.stack([((edge - c) ** 2).sum(1) for c in pal["centres"]], 0).argmin(0)
        if np.bincount(pal["group"][near], minlength=len(pal["share"])).max() >= 0.6 * len(near):
            n -= 1
    return n


def _focal(image, img, opt):
    """The 35 mm-equivalent focal length to use (typed, else the photo's lens data, else 0), and
    whether the picture was cropped since it was taken (its lens data then no longer fits)."""
    lens = read_lens(image)
    f35 = opt.focal_35mm if opt.focal_35mm > 0 else lens.get("focal_35mm", 0.0)
    cropped = False
    if lens.get("width") and lens.get("height") and not opt.focal_35mm > 0:
        a0 = max(lens["width"], lens["height"]) / min(lens["width"], lens["height"])
        a1 = max(img.shape[:2]) / float(min(img.shape[:2]))
        cropped = abs(a0 / a1 - 1) > 0.02
        if cropped and opt.camera_distance_mm > 0:
            f35 = 0.0                                          # the lens data no longer fits: use the distance
    return f35, cropped


def _camera_msgs(tk, opt, msgs, cropped, ref, thickness):
    """Where the camera was and what the part's height did to the scale (token or calibration
    sheet). ref: what the height is measured from, for the messages."""
    src_txt = {"typed": "from the distance you gave",
               "stand": "measured with the stand's marker",
               "lens": "measured, using the lens data in the photo",
               "assumed": "estimated: the photo has no lens data and no distance was given, so a "
                          "phone's main camera without zoom is assumed"}[tk["lens"]]
    msgs.append("Camera about %.0f mm above the table (%s), %.1f degrees off vertical; the photo "
                "was straightened." % (tk["camera_height_mm"], src_txt, tk["tilt_deg"]))
    typed = tk.get("typed_distance_mm")
    if tk["lens"] == "lens" and typed and abs(typed / tk["camera_height_mm"] - 1) > 0.15:
        msgs.append("You gave %.0f mm for the camera distance; the photo's lens data put it at %.0f mm, "
                    "and that is what was used (lens data first; the typed distance is only used "
                    "for photos without it)." % (typed, tk["camera_height_mm"]))
    if cropped and tk["lens"] == "lens":
        msgs.append("This picture has been cropped since it was taken, so the lens data no "
                    "longer fits it: type the camera distance instead for the height and "
                    "thickness corrections to be right.")
    ph = tk.get("phone_distance_mm")
    if tk["lens"] == "typed" and ph and not 0.6 < typed / ph < 1.67:
        msgs.append("Check the camera distance: you gave %.0f mm, but from the %s's size in the "
                    "picture a phone's main camera without zoom would have been about %.0f mm away. "
                    "If you did not zoom, type about %.0f." % (typed, "sheet" if ref == "the paper" else "token", ph, ph))
    if opt.part_above_token_mm:
        sens = tk["distance_sensitivity"]
        msgs.append("Traced face is %.1f mm %s than %s: sizes %s by %.2f %% to allow for it."
                    % (abs(opt.part_above_token_mm), "higher" if opt.part_above_token_mm > 0 else "lower", ref,
                       "reduced" if opt.part_above_token_mm > 0 else "increased",
                       abs(1 - tk["height_factor"]) * 100))
        per_mm = 100.0 / max(tk["camera_height_mm"] - opt.part_above_token_mm - thickness, 1.0)
        if tk["lens"] not in ("lens", "stand") and sens > 0.02:
            msgs.append("Sizes depend on the camera distance: if it is out by 10 %%, sizes are out by "
                        "about %.1f %%. Each 1 mm the part's height is out changes sizes by "
                        "%.2f %%." % (10 * sens, per_mm))
        elif tk["lens"] == "stand" and per_mm > 0.05:
            msgs.append("Each 1 mm the part's height is out changes sizes by %.2f %%: measure it." % per_mm)
        if sens > 0.3 and tk["lens"] == "stand":
            msgs.append("For a part this tall, a photo from further away with the zoom (for example 2 - 3x "
                        "from about 1 m), part in the middle of the picture, shows less of its side walls.")
        elif sens > 0.3:
            msgs.append("For a part this tall, photograph it from further away with the zoom "
                        "(for example 2 - 3x from about 1 m), with the part in the middle of the "
                        "picture: the camera distance then matters much less, and less of the side "
                        "walls shows.")


def trace(image, opt=None):
    """Run the whole pipeline. `image` is a path or a uint8 array (grey / BGR / BGRA)."""
    opt = opt or Options()
    t0 = time.perf_counter()
    if opt.scale_mode not in ("mm_per_px", "image_width", "markers", "token", "calsheet", "square", "dpi"):
        raise ValueError("Unknown scale mode %r." % (opt.scale_mode,))
    if opt.scale_mode in ("mm_per_px", "image_width", "square") and not (opt.scale_value and opt.scale_value > 0
                                                                          and math.isfinite(opt.scale_value)):
        raise ValueError("The scale value must be a positive number.")
    for name, allowed in (("polarity", ("auto", "dark", "light")), ("prefer", ("material", "outline")),
                          ("line_art", ("auto", "on", "off")), ("picture_edge", ("auto", "edge", "cut")),
                          ("find", ("tone", "transitions")), ("shared_edges", ("once", "both"))):
        if getattr(opt, name) not in allowed:
            raise ValueError("Option %s must be one of %s." % (name, ", ".join(allowed)))
    if not (0 < opt.maybe <= opt.accept <= 1):
        raise ValueError("Confidence limits must satisfy 0 < maybe <= accept <= 1.")
    if not (math.isfinite(opt.colour_step) and (opt.colour_step == 0 or 1 <= opt.colour_step <= 60)):
        raise ValueError("The colour difference must be 0 (automatic) or a number from 1 to 60.")
    if not (opt.enlarge == 0 or (math.isfinite(opt.enlarge) and 1 <= opt.enlarge <= 12)):
        raise ValueError("Enlargement must be 0 (automatic) or a factor from 1 to 12.")
    # transition mode makes one map per group of colour areas: it works on a smaller picture
    max_side = min(opt.max_side, TRANSITION_MAX_SIDE) if opt.find == "transitions" else opt.max_side
    img, dpi = load_image(image)
    img, backdrop = composite(img)                             # transparency -> contrasting backdrop
    info = {"messages": [], "backdrop": backdrop}
    msgs = info["messages"]
    s = None
    exclude = []
    zr, sheet_noise = 1.0, 0.0

    if opt.scale_mode == "markers":
        found = pattern.find_sheet(img)
        # a sheet that is small in the photo is rectified straight to a larger size (one
        # resampling), for the same reason a small picture is enlarged below
        pw_, ph_ = pattern.SHEETS[found["sheet"]]["page"]
        zr = _auto_zoom(max(pw_, ph_) / found["mm_per_px_native"], opt)
        if zr > 1.0:
            sheet_noise = flat_noise(img)
            if sheet_noise > NOISE_LIMIT:
                zr = 1.0
        img, s, exclude = pattern.rectify(img, found, mm_per_px=found["mm_per_px_native"] / zr,
                                          max_side=max_side,
                                          print_scale=opt.check_line_mm / pattern.CHECK_MM)
        if zr > 1.0:
            info["sheet_zoom"] = zr
        info["sheet"] = found["sheet"]
        info["markers"] = found["n_markers"]
        msgs.append("%s marker sheet: %d of 4 markers, fit error %.2f mm." % (
            found["sheet"], found["n_markers"], found["fit_rms_mm"]))
    elif opt.scale_mode == "token":
        from . import scaletoken
        f35, cropped = _focal(image, img, opt)
        got = scaletoken.locate(img, opt.token_side_mm, opt.token_thickness_mm, opt.part_above_token_mm,
                                f35, opt.camera_distance_mm, max_side)
        src_shape = img.shape
        img, s, exclude = got["image"], got["mm_per_px"], got["exclude"]
        tk = info["token"] = got["info"]
        token_scale = tk["mm_per_source_px"]
        msgs.append("Scale token found: %.1f mm side seen as %.0f px (%.4f mm per pixel of the photo). "
                    "Taken as %s." % (opt.token_side_mm, tk["side_px"], tk["mm_per_source_px"],
                                      "printed on paper" if tk["on_paper"]
                                      else "%.1f mm thick" % opt.token_thickness_mm))
        if tk["tokens"] == 2:
            msgs.append("Both tokens of the printed %s sheet found: tilt is measured across the sheet."
                        % tk["sheet"])
        elif tk["side_px"] < 130:
            msgs.append("The token is only %.0f px across in this photo, too few to measure the camera's "
                        "tilt closely: sizes well away from the token may be out by around half a percent. "
                        "Photograph from closer or at a higher resolution%s."
                        % (tk["side_px"], ", or keep both tokens of the printed sheet in view"
                           if tk["on_paper"] else ""))
        if tk["lens"]:
            _camera_msgs(tk, opt, msgs, cropped, "the token", opt.token_thickness_mm)
        else:
            msgs.append("No lens data in this picture and no camera distance given: %sanything higher "
                        "than the token reads large by height / camera distance."
                        % ("" if tk["on_paper"] else "the token's thickness is not allowed for (sizes may "
                                                     "read a few tenths of a percent small), and "))
            if tk["skew"] > 0.01:
                msgs.append("The photo was taken at an angle; it was straightened using the token.")
    elif opt.scale_mode == "calsheet":
        from . import calsheet
        f35, cropped = _focal(image, img, opt)
        empty = None
        if opt.empty_sheet is not None and not (isinstance(opt.empty_sheet, str) and not opt.empty_sheet):
            empty, _ = load_image(opt.empty_sheet)
            empty, _ = composite(empty)
        got = calsheet.locate(img, opt.part_above_token_mm, f35, opt.camera_distance_mm, opt.stand_height_mm,
                              (opt.sheet_across_mm / calsheet.CHECK_ACROSS_MM,
                               opt.sheet_along_mm / calsheet.CHECK_ALONG_MM), max_side, backdrop=empty)
        img, s, exclude = got["image"], got["mm_per_px"], got["exclude"]
        tk = info["token"] = got["info"]
        token_scale = tk["mm_per_source_px"]
        msgs.append("Calibration sheet (%s) found: %d of its %d frame markers used, fit error %.3f mm "
                    "(%.4f mm per pixel of the photo)." % (tk["paper"], tk["markers"], tk["frame_total"],
                                                          tk["fit_rms_mm"], tk["mm_per_source_px"]))
        if tk["markers"] < tk["frame_total"] // 2:
            msgs.append("Less than half of the frame is in view or readable: tilt and the lens are measured "
                        "less closely. Keep the whole frame in the picture.")
        if tk["lens_bend"]:
            msgs.append("The lens bends straight lines slightly (%s, %.1f %% at the picture's corners); taken "
                        "out (fit error %.3f mm before, %.3f mm after)."
                        % ("barrel" if tk["lens_bend"] < 0 else "pincushion", 100 * abs(tk["lens_bend"]),
                           tk["fit_rms_before_bend_mm"], tk["fit_rms_mm"]))
        if tk["fit_max_mm"] > 0.4:
            msgs.append("The frame's markers do not all lie where a flat sheet would put them (up to %.1f mm "
                        "out): the paper is probably curled or wavy. Flatten it (weigh the edges down) for "
                        "the best accuracy." % tk["fit_max_mm"])
        pr = tk.get("pair")
        if pr:
            msgs.append("Empty-sheet photo used (%d frame markers): %.0f mm2 changed between the two photos, of "
                        "which %.0f mm2 is taken as the part and %.0f mm2 as its shadow; the rest of the sheet "
                        "(banding, dust, shadow) was made plain paper before tracing.%s"
                        % (pr["markers"], pr["changed_mm2"], pr["part_mm2"], pr["shadow_mm2"],
                           "" if pr["note"] == "shadow test used" else " Note: " + pr["note"] + "."))
            if pr["gone_mm2"] > 50:
                msgs.append("%.0f mm2 was there in the empty photo and is not now (something moved or taken away "
                            "between the photos?): left out." % pr["gone_mm2"])
            if pr["part_mm2"] < 4:
                msgs.append("Hardly anything changed between the two photos: is the part in the second one?")
        st = tk.get("stand")
        if st:
            other = tk.get("lens_distance_mm") or tk.get("typed_distance_mm")
            msgs.append("Stand found: its marker reads %.2f %% larger than the frame's, so the camera was "
                        "%.0f mm above the paper (to within about %.1f %%)."
                        % (100 * (st["k"] - 1), st["distance_mm"], 100 * st["margin"]))
            if other and abs(other / st["distance_mm"] - 1) > 0.1:
                msgs.append("The %s said %.0f mm; the stand's measurement was used."
                            % ("photo's lens data" if tk.get("lens_distance_mm") else "distance you typed", other))
        elif opt.part_above_token_mm:
            msgs.append("No stand in the picture: the camera distance comes from %s instead."
                        % {"lens": "the photo's lens data", "typed": "the distance you typed",
                           "assumed": "a guess (a phone's main camera without zoom)"}.get(tk["lens"], "nowhere"))
        lt = tk.get("light")
        if lt:
            if lt["falloff"] > 0.15:
                msgs.append("The light falls off by %.0f %% across the sheet." % (100 * lt["falloff"]))
            if len(lt["shadow_mm"]) >= 3:
                msgs.append("A shadow lies across part of the frame (%d sample points much darker than the "
                            "paper round them)." % len(lt["shadow_mm"]))
        if tk["lens"]:
            _camera_msgs(tk, opt, msgs, cropped, "the paper", 0.0)
    elif opt.scale_mode == "dpi":
        if not dpi:
            raise ValueError("This file does not say what resolution it was scanned at. "
                             "Choose another way to set the scale.")
        info["dpi"] = dpi

    H0, W0 = img.shape[:2]
    f = min(1.0, max_side / float(max(H0, W0)))
    if f < 1.0:
        img = cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
        msgs.append("Image reduced to %d x %d for tracing." % (img.shape[1], img.shape[0]))
        if s is not None:
            s /= f
    if opt.scale_mode == "mm_per_px":
        s = opt.scale_value / f
    elif opt.scale_mode == "image_width":
        s = opt.scale_value / img.shape[1]
    elif opt.scale_mode == "dpi":
        s = 25.4 / dpi / f

    # ---- beyond the picture's edge ----------------------------------------------------------
    # An icon or logo often fills its canvas edge to edge. Continued a little way in the tone
    # of what lies outside (the transparent corners, or the corners' own tone), such a shape is
    # whole again and is traced like any other. A photo is left alone: there the frame really
    # did cut the part off.
    pad = 0
    if opt.scale_mode not in ("markers", "token", "calsheet"):
        tone = canvas_tone(img, backdrop, opt.picture_edge, opt.polarity)
        if tone is not None:
            pad = int(max(6, round(0.04 * max(img.shape[:2]))))
            val = float(tone[0]) if img.ndim == 2 else [float(v) for v in tone[:3]]
            img = cv2.copyMakeBorder(img, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=val)
    info["canvas_pad_px"] = pad

    def run(im, excl):
        min_px = opt.min_feature_mm / (s / zoom) if (opt.min_feature_mm > 0 and s) else None
        # on a token photo the light sheet is the background, whatever else is in the picture
        pol = "dark" if (opt.scale_mode in ("token", "calsheet") and opt.polarity == "auto") else opt.polarity
        if opt.find == "transitions":
            return detect_transitions(im, step=opt.colour_step, accept=opt.accept, maybe=opt.maybe,
                                      min_feature_px=min_px, exclude=excl, thin_features=opt.thin_features)
        return detect(im, accept=opt.accept, maybe=opt.maybe, polarity=pol, channel=opt.channel,
                      prefer=opt.prefer, min_feature_px=min_px, keep_work=False, line_art=opt.line_art,
                      exclude=excl, thin_features=opt.thin_features)

    # ---- working size ---------------------------------------------------------------------
    # Every test and fit works in pixels and needs features a fair number of pixels across.
    # A small picture is therefore enlarged first; a badly blurred one is reduced.
    img0, excl0 = img, exclude
    long0 = float(max(img0.shape[:2]))
    hard, noise0, soften = False, 0.0, 0.0
    zoom = 1.0 if opt.scale_mode in ("markers", "token", "calsheet") else _auto_zoom(long0, opt)
    if zoom > 1.0:
        noise0 = flat_noise(img0)
        if noise0 > NOISE_LIMIT or (opt.enlarge == 0 and busy(img0)):
            zoom = 1.0
        else:
            hard = hard_pixels(img0)
            soften = 0.8 if hard else 0.0
    info["noise_native"] = noise0

    def at(z):
        return (_resized(img0, z, soften),
                [tuple(int(round(v * z)) for v in e) for e in excl0])

    img, exclude = at(zoom)
    det = run(img, exclude)
    if det.edge_sigma > 3.2:
        # Every edge is many pixels wide (blur, or an enlargement that went further than the
        # picture's sharpness). Nothing is lost by working on a copy in which the edges are a
        # normal width again, and the tests behave as designed.
        z2 = zoom * BLUR_TARGET / det.edge_sigma
        if z2 < 1.0 and min(img0.shape[:2]) * z2 < 120:
            z2 = min(1.0, 120.0 / min(img0.shape[:2]))
        if abs(z2 - zoom) > 0.05 * zoom:
            if z2 < 1.0:
                msgs.append("The picture is blurred (edges about %.0f px wide); traced on a %d x %d copy. "
                            "A sharper picture will be more accurate."
                            % (2.5 * det.edge_sigma / zoom, round(img0.shape[1] * z2), round(img0.shape[0] * z2)))
            zoom = z2
            img, exclude = at(zoom)
            det = run(img, exclude)
    if info.get("sheet_zoom"):
        msgs.append("The marker sheet is small in the photo: straightened at %.1f times its size "
                    "(%d x %d) for tracing." % (info["sheet_zoom"], img.shape[1], img.shape[0]))
    if zoom > 1.0:
        msgs.append("Small picture: enlarged %.1f times (to %d x %d) before tracing%s."
                    % (zoom, img.shape[1], img.shape[0],
                       "; its edges are stair-stepped, so they are only known to about half a pixel"
                       if hard else ""))
    if s is not None:
        info["mm_per_source_px"] = float(s) * zr              # scale of the picture as it was given
        if opt.scale_mode in ("token", "calsheet"):
            info["mm_per_source_px"] = token_scale
        s *= img0.shape[1] / float(img.shape[1])
    info["zoom"] = zoom
    info["hard_pixels"] = hard
    H, W = img.shape[:2]
    t1 = time.perf_counter()

    tk = info.get("token")
    if tk and tk.get("nadir_px") and opt.straight_walls:
        wall = opt.part_above_token_mm + (opt.token_thickness_mm if opt.scale_mode == "token" else 0.0)
        cam_top = tk["camera_height_mm"] - wall                         # camera above the traced face
        # the correction leans on the point under the camera; if that is only roughly known
        # (distance typed or assumed), it can do more harm than good: skip it then
        wall_doubt = tk.get("nadir_spread_mm", 0.0) * wall / max(cam_top, 1e-9)
        tk["wall_doubt_mm"] = float(wall_doubt)
        if wall > 0.05 and cam_top > 20 and wall_doubt <= WALL_DOUBT_MM:
            from . import scaletoken
            nadir = ((tk["nadir_px"][0] + 0.5) * zoom - 0.5, (tk["nadir_px"][1] + 0.5) * zoom - 0.5)
            beta = cam_top / (cam_top + wall)
            for L in det.loops:
                if L.note not in ("centre", "inner"):
                    L.pts = scaletoken.unwall(L.pts, L.hole, nadir, beta)
            tk["walls_corrected_mm"] = float(wall)

    bump = det.min_feature_px if opt.bridge_bumps else 0.0
    tol = opt.tol_px * max(1.0, zoom) if opt.tol_px > 0 else \
        max(0.5 * (zoom if hard and zoom > 1 else 1.0), 0.35 * det.edge_sigma,
            (min(0.5, TOL_BASE + TOL_NOISE * max(noise0, sheet_noise)) * zoom * zr) if zoom * zr > 1 else 0.0)
    info["tol_px"] = tol
    # An enlarged picture carries a faint ripple from its original pixel grid. Lines and arcs
    # average it out; a spline would follow it, so spline runs are smoothed over about one
    # original pixel first.
    smooth = 0.9 * zoom * zr if zoom * zr > 1.2 else 0.0
    fitted = []
    n_maybe = 0
    for L in det.loops:                                        # accepted first, then by size
        strokes = L.note in ("centre", "inner")               # hand-drawn lines wobble: fit looser
        t = tol * (1.6 if strokes else 1.0)
        if L.conf >= opt.accept or opt.scale_mode == "square":
            prims = fit_loop(L.pts, t, det.edge_sigma, opt.detect_circles, 0.0 if strokes else bump, smooth)
        elif n_maybe < opt.max_borderline:
            n_maybe += 1
            prims = fit_loop(L.pts, t, det.edge_sigma, opt.detect_circles, bump, smooth) \
                if (L.note in ("alt", "inner") and n_maybe <= 12) else fit_quick(L.pts, tol, det.edge_sigma, smooth)
        else:
            info["dropped_borderline"] = info.get("dropped_borderline", 0) + 1
            continue
        if prims and opt.find == "transitions":
            prims = weld_slivers(prims, max(2.0 * tol, 0.45 * det.min_feature_px))
        if prims:
            fitted.append((L, prims))
    if opt.find == "transitions" and opt.weld_edges:
        from .weld import weld
        idx = [i for i, (L, _) in enumerate(fitted) if L.conf >= opt.accept and getattr(L, "group", -1) >= 0]
        if len({fitted[i][0].group for i in idx}) > 1:
            welded, wrep = weld([fitted[i][1] for i in idx], [fitted[i][0].group for i in idx],
                                [bool(fitted[i][0].hole) for i in idx], reach=max(2.0, 0.5 * det.reach, 1.5 * tol))
            for j, i in enumerate(idx):
                fitted[i] = (fitted[i][0], welded[j])
            info["weld"] = wrep
    if info.get("dropped_borderline"):
        msgs.append("%d more small borderline shapes were left out to keep the sketch usable."
                    % info["dropped_borderline"])

    if opt.scale_mode == "square":
        best = None
        for i, (L, prims) in enumerate(fitted):
            side = None if L.hole or L.conf < opt.accept else _square_side(prims)
            if side and (best is None or side > best[1]):
                best = (i, side)
        if best is None:
            raise ValueError("No square found to take the scale from. It must be a solid, sharp-"
                             "cornered square lying flat, separate from the part.")
        s = opt.scale_value / best[1]
        info["mm_per_source_px"] = float(s) * img.shape[1] / float(img0.shape[1])
        msgs.append("Scale square: %.2f px per side." % (best[1] * img0.shape[1] / float(img.shape[1])))
        fitted.pop(best[0])
    if not s or s <= 0:
        raise ValueError("Scale must be positive.")

    ok = [L for L, _ in fitted if L.conf >= opt.accept] or [L for L, _ in fitted]
    if opt.centre and ok:
        allp = np.vstack([L.pts for L in ok])
        origin = (float((allp[:, 0].min() + allp[:, 0].max()) / 2),
                  float((allp[:, 1].min() + allp[:, 1].max()) / 2))
    else:
        origin = ((W - 1) / 2.0, (H - 1) / 2.0)

    res = Result([], [r.pts for r in sorted(det.rejected, key=lambda r: -r.area)[:300]], float(s), (W, H),
                 origin, img, info)
    for L, prims in fitted:
        mm = _to_mm_prims(prims, res.to_mm, s)
        res.shapes.append(Shape(mm, L.hole, L.conf, "accept" if L.conf >= opt.accept else "maybe",
                                L.note, poly_area(sample_prims(mm, s)), L.pts, prims,
                                getattr(L, "group", -1), getattr(L, "colour", None)))
    fl = det.flags
    info["find"] = opt.find
    if opt.find == "transitions":
        info["areas"] = fl.get("areas", [])
        info["colour_step"] = fl.get("step", 0.0)
        info["shared_left"] = share_edges(res, max(2.0, 0.5 * det.reach, 1.5 * tol)) \
            if opt.shared_edges == "once" else 0
    info.update(edge_sigma_px=det.edge_sigma, contrast=det.contrast, noise=det.noise,
                dark_on_light=det.dark_on_light, channel=det.channel, flags=fl,
                t_detect=t1 - t0, t_total=time.perf_counter() - t0)

    # ---- plain-language notes about what happened --------------------------------------
    acc = res.accepted
    if opt.find == "transitions":
        n_all = len(info["areas"])
        n_back = sum(1 for a in info["areas"] if a["canvas"])
        msgs.append("Transition mode (experimental): %d colour area%s found%s. Colour difference "
                    "setting %.0f: neighbours count as two areas from about %.1f Lab units apart." % (
                        n_all, "" if n_all == 1 else "s",
                        (", %d of them background (the colour round the picture's edge)" % n_back)
                        if n_back else "", info["colour_step"], MERGE * info["colour_step"]))
        if fl.get("left_out"):
            msgs.append("%d small area%s not traced: the picture has more separate areas packed together "
                        "than this mode handles." % (fl["left_out"], " was" if fl["left_out"] == 1 else "s were"))
        if not res.shapes and not det.rejected:
            msgs.append("Nothing found: no area closed off by a sharp change of colour. A smooth blend "
                        "from one colour into another is not an edge.")
        if info.get("shared_left"):
            msgs.append("Edges shared by two neighbouring areas are drawn once: %d piece%s left to the "
                        "neighbour, so some outlines are open in the sketch. STEP export keeps every area "
                        "whole. For every outline complete, draw shared edges for both (--shared-edges both); "
                        "in FreeCAD, \"A sketch for each colour area\" gives every area a closed sketch of its "
                        "own." % (info["shared_left"], "" if info["shared_left"] == 1 else "s"))
    elif not res.shapes and not det.rejected:
        msgs.append("Nothing found: the picture has no clear part-against-background contrast.")
    elif det.contrast < 25:
        msgs.append("Low contrast between part and background; expect weaker results.")
    if opt.find == "tone" and opt.scale_mode not in ("markers", "token", "calsheet"):
        n_col = colour_count(img0)
        info["flat_colours"] = n_col
        if n_col >= 3:
            msgs.append("This picture has %d flat colours. Only the part against its background is traced "
                        "here, so edges between two colours inside it are not outlined. To outline every "
                        "colour area, set \"What to trace\" to \"Every colour change\" (--find transitions)."
                        % n_col)
    if fl.get("too_many"):
        msgs.append("The picture is full of small shapes (a pattern or texture?). Only the largest "
                    "were examined.")
    if fl.get("strokes"):
        msgs.append("This looks like a line drawing: %d drawn outline%s traced along the middle of the "
                    "line." % (fl["strokes"], "" if fl["strokes"] == 1 else "s"))
    if fl.get("border_cut"):
        if opt.scale_mode == "calsheet":
            msgs.append("%d shape%s the edge of the sheet's clear middle and %s not traced: keep the part "
                        "inside the frame (and the stand's legs clear of it)."
                        % (fl["border_cut"], " runs over" if fl["border_cut"] == 1 else "s run over",
                           "is" if fl["border_cut"] == 1 else "are"))
        elif opt.scale_mode == "token":
            msgs.append("%d dark area%s off the edge of the picture (the table around the sheet, if it is "
                        "in view) and %s not traced."
                        % (fl["border_cut"], " runs" if fl["border_cut"] == 1 else "s run",
                           "is" if fl["border_cut"] == 1 else "are"))
        else:
            msgs.append("%d part%s cut off by the edge of the picture and not traced.%s"
                        % (fl["border_cut"], " is" if fl["border_cut"] == 1 else "s are",
                           " If the picture's edge is the shape's real edge (an icon, a cropped drawing), "
                           "set \"Shape running off the picture\" to \"The picture's edge is its edge\" "
                           "(--picture-edge edge)."
                           if (opt.picture_edge == "auto" and not pad) else ""))
    if pad:
        # which accepted shapes owe part of their outline to the picture's edge
        lo, hi_x, hi_y, near = pad * zoom - 0.5, W - pad * zoom - 0.5, H - pad * zoom - 0.5, 2.0 * max(1.0, zoom)
        n_edge = 0
        for s_ in acc:
            if s_.hole or s_.outline is None or not len(s_.outline):
                continue
            o = s_.outline
            if (o[:, 0].min() < lo + near or o[:, 1].min() < lo + near
                    or o[:, 0].max() > hi_x - near or o[:, 1].max() > hi_y - near):
                n_edge += 1
        info["edge_closed"] = n_edge
        if n_edge:
            msgs.append("%d shape%s to the edge of the picture: the picture's edge was taken as %s edge%s. "
                        "If %s really cut off there, set \"Shape running off the picture\" to \"Leave it "
                        "out\" (--picture-edge cut)."
                        % (n_edge, " runs" if n_edge == 1 else "s run", "its" if n_edge == 1 else "their",
                           " (the picture is a drawn graphic, not a photo)" if opt.picture_edge == "auto" else "",
                           "it is" if n_edge == 1 else "they are"))
    if fl.get("partly_sharp") and not acc:
        msgs.append("Large outlines were found but only partly backed by a sharp edge (blur in one "
                    "direction, glare, or a shadow as dark as the part). They are borderline only.")
    if any(s_.note == "thin" for s_ in res.shapes):
        msgs.append("Very narrow slots were found. A pen line, hair or scratch on the part looks the "
                    "same, so they are borderline only; switch them to real geometry if they are slots.")
    if any(s_.note in ("tone", "nested") for s_ in res.shapes):
        msgs.append("Some shapes inside the part are not background-coloured (glare, print, "
                    "relief?) and were kept as borderline only.")
    if any(s_.note == "alt" for s_ in res.shapes):
        msgs.append("Two different edges competed for some outlines (hard shadow or bevel); "
                    "the other candidate is included as borderline.")
    tk = info.get("token")
    if tk and tk.get("nadir_px") and acc:
        # What a camera above a thick part really shows: under the camera the top edge all round;
        # off to one side the wall on the near side, so that side's outline is the BOTTOM edge.
        # A through-hole always shows its far (bottom) rim.
        part_h = opt.part_above_token_mm + (opt.token_thickness_mm if opt.scale_mode == "token" else 0.0)
        cam_h = tk["camera_height_mm"]
        if part_h > 0.2 and cam_h > part_h:
            if tk.get("walls_corrected_mm"):
                msgs.append("The part is taken to have straight vertical sides %.1f mm high (its top is "
                            "that far above the table): where the camera could see a wall - the side "
                            "of the outline facing the camera, the far side of each hole - the outline "
                            "was moved from the bottom edge to the top edge." % part_h)
            else:
                if opt.straight_walls and tk.get("wall_doubt_mm", 0.0) > WALL_DOUBT_MM:
                    msgs.append("The side walls were not corrected: with the camera distance %s, the point "
                                "under the camera is only known to about %.0f mm, and the correction could "
                                "move the outline by up to %.1f mm the wrong way. Leave the camera distance at "
                                "0 for a photo straight from the phone (its lens data is then used), or shoot "
                                "from further away with the part in the middle of the picture."
                                % ("typed" if tk["lens"] == "typed" else "assumed" if tk["lens"] == "assumed"
                                   else "from the lens data", tk.get("nadir_spread_mm", 0.0), tk["wall_doubt_mm"]))
                nx, ny = tk["nadir_px"]
                outer = [s_ for s_ in acc if not s_.hole]
                under = any(cv2.pointPolygonTest(s_.outline.astype(np.float32).reshape(-1, 1, 2),
                                                 (float(nx), float(ny)), False) >= 0 for s_ in outer)
                far = max(float(np.hypot(s_.outline[:, 0] - nx, s_.outline[:, 1] - ny).max()) for s_ in outer) * s
                if not under and far * part_h / cam_h > 0.1:
                    msgs.append("The camera was not straight above the part: on the side facing the camera "
                                "the outline is the part's bottom edge, up to about %.1f mm too far out."
                                % (far * part_h / cam_h))
                if any(s_.hole for s_ in acc):
                    msgs.append("Through-holes show their far rim: they read up to %.1f %% small."
                                % (100.0 * part_h / cam_h))
    if opt.find == "tone" and opt.polarity == "auto" and acc and opt.scale_mode not in ("token", "calsheet"):
        big = max((s_ for s_ in acc if not s_.hole), key=lambda s_: s_.area_mm2, default=None)
        if big is not None and big.area_mm2 > 0.3 * W * H * s * s and len(big.prims) == 4 \
                and all(p[0] == "line" for p in big.prims) and any(s_.hole for s_ in acc):
            other = "darker" if not det.dark_on_light else "lighter"
            msgs.append("The big rectangle may be a sheet of paper rather than the part. If so, set "
                        "the part tone to '%s than background' and trace again." % other)
    return res
