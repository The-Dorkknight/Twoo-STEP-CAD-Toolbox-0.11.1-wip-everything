# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
# Copied from the ImageToSketch project by tools/sync_imagetosketch.py - edit it there.
"""Two photos: the calibration sheet empty, then with the part on it.

Ported from Pasta Inspector (the ESP32 print watcher): it never judges one picture alone, only
what changed against a picture taken before, and it tells a shadow (darker, but the same
surface) from new material. Here that gives:

  * what changed - the part (and its shadow); the printer's banding on the paper, dust, the
    texture round the sheet stay the same and drop out;
  * which of the change is only shadow - darker by a smooth factor, the paper's own texture
    still there - and which is the part.

`clean()` then hands the tracer a picture in which everything that is not the part is plain
paper: the part's pixels as photographed (so its edges are found and placed exactly as before),
the rest from the empty photo, smoothed. The tracer itself is unchanged.

Both photos must be in the same frame first: calsheet.locate warps the empty one with the
sheet's own homography, which is exact for the flat paper (where the shadow and the banding
are); only the part breaks it, and the part is what differs anyway.

Settings were chosen on synthetic scenes (seeds 1 - 20 of the hand-over's test) and scored on
others (101 - 140) and on simulated photos of the sheet (tests/test_pairdiff.py). Not tried on
real photos.
"""
import cv2
import numpy as np


def _wiggle_sigmas(d, sigma):
    """How much more d goes up and down along the 10 lines (5 rows, 5 columns) of the 5 x 5
    round each pixel than noise of `sigma` would make it, per line, in sigmas (Pasta
    Inspector's roughness_at, in units of the local noise). About 0 for a shadow; above 0 where
    a texture shows through."""
    d = d.astype(np.float32)
    H, W = d.shape
    sh = np.abs(np.diff(d, axis=1))
    sv = np.abs(np.diff(d, axis=0))
    steps_h = np.zeros_like(d)
    steps_v = np.zeros_like(d)
    for o in (-2, -1, 0, 1):
        steps_h[:, 2:W - 2] += sh[:, 2 + o:W - 2 + o]
        steps_v[2:H - 2, :] += sv[2 + o:H - 2 + o, :]
    end_h = np.zeros_like(d)
    end_v = np.zeros_like(d)
    end_h[:, 2:W - 2] = np.abs(d[:, 4:] - d[:, :-4])
    end_v[2:H - 2, :] = np.abs(d[4:, :] - d[:-4, :])
    wig = cv2.boxFilter(steps_h - end_h, -1, (1, 5), normalize=False, borderType=cv2.BORDER_REPLICATE) + \
        cv2.boxFilter(steps_v - end_v, -1, (5, 1), normalize=False, borderType=cv2.BORDER_REPLICATE)
    return wig / (10 * np.maximum(sigma, 1e-6)) - 3.4


def pair_diff(backdrop, photo, k_noise=4.0, rel=0.06, min_abs=5.0, shadow_floor=0.3, shadow_rough=0.3,
              smooth_px=9, min_area=40, ignore=None):
    """backdrop, photo: grey, same size, same frame. ignore: pixels judged like the others but left
    out of the counts and of the skip rule below (where the stand stands: seen from a slightly
    different place its top moves, which is neither part nor shadow).

    Returns (part, shadow, info): uint8 masks (255 = yes) and the numbers used.
      1. Brightness: the photo is scaled by the median ratio photo / backdrop (phones change
         exposure between shots; most of the picture is unchanged paper).
      2. Changed: |difference| over the largest of k_noise x the noise of a difference, rel x
         the brightness and min_abs grey levels, or the 3 x 3 mean over that / 2.2 (Pasta
         Inspector's slow watch).
      3. Shadow: darkened only, keeps at least shadow_floor of the brightness, and smooth in the
         ratio photo / backdrop (a shadow multiplies the paper, so the paper's texture cancels
         there; a part shows it). If that would leave under a quarter of the change as part
         (a part as smooth as a shadow), the shadow test is switched off and the note says so.
      4. Gone: brighter than before and back to the paper's own brightness - something that was
         there in the empty photo and is not now (the stand moved, a hand) - is never the part.
    """
    b = backdrop.astype(np.float32)
    p = photo.astype(np.float32)
    count = np.ones(b.shape, bool) if ignore is None else ~ignore.astype(bool)
    ok = count & (b > 8) & (b < 247) & (p < 247)
    gain = float(np.median(p[ok] / b[ok])) if ok.sum() > 100 else 1.0
    p = p / gain
    d = p - b
    sigma_d = 1.4826 * float(np.median(np.abs(d[ok] - np.median(d[ok])))) if ok.sum() > 100 else 3.0
    sigma_d = max(sigma_d, 0.5)
    thr = np.maximum(np.maximum(k_noise * sigma_d, rel * b), min_abs)
    dm = cv2.blur(d, (3, 3))
    changed = (np.abs(d) > thr) | (np.abs(dm) > thr / 2.2)
    ratio = p / np.maximum(b, 8.0)
    s1 = sigma_d / np.sqrt(2.0)
    lr = np.log(np.clip(ratio, 1e-3, None))
    sig = s1 * np.sqrt(1.0 / np.maximum(b, 8.0) ** 2 + 1.0 / np.maximum(p, 8.0) ** 2)
    rough = cv2.blur(_wiggle_sigmas(lr, sig), (smooth_px, smooth_px))
    shadow = changed & (d < 0) & (ratio >= shadow_floor) & (rough <= shadow_rough)
    paper = cv2.GaussianBlur(b, (0, 0), 6.0)
    gone = changed & (d > 0) & (p >= 0.92 * paper)
    note = "shadow test used"
    new = changed & ~gone & count
    if new.sum() and (new & ~shadow).sum() < 0.25 * new.sum():
        shadow[:] = False
        note = ("shadow test skipped: it would have left under a quarter of the change as part (a part as "
                "smooth and as grey as its shadow?); any shadow stays with the part")
    part = changed & ~shadow & ~gone
    part = cv2.morphologyEx(part.astype(np.uint8) * 255, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    part = cv2.morphologyEx(part, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(part, 8)
    keep = np.zeros(n, bool)
    keep[1:] = stats[1:, cv2.CC_STAT_AREA] >= min_area
    part = np.where(keep[lab], 255, 0).astype(np.uint8)
    return part, shadow.astype(np.uint8) * 255, {"gain": gain, "noise": sigma_d, "note": note,
                                                  "changed_px": int((changed & count).sum()),
                                                  "shadow_px": int((shadow & count).sum()),
                                                  "gone_px": int((gone & count).sum())}


def clean(photo, backdrop, margin_px=6, ignore=None):
    """The photo with everything that is not the part made plain paper. Round the part (a margin
    of `margin_px`, so its edges keep their real profile) the photo is kept, with its shadow
    divided out: the shadow's darkening factor, measured inside the shadow, is spread a few
    pixels and the photo divided by it, so the part's edge against its shadow becomes its edge
    against paper, where it was. (Replacing the shadow outright shifted that edge: the pixels
    on it are part and shadow mixed, and smooth, so the shadow test claims them too; on
    simulated photos the outline came out 1 - 2 px small all round.) Everything further away
    comes from the empty photo, smoothed (banding, dust and texture gone), at the photo's
    brightness. Returns (picture, part mask, shadow mask, info)."""
    grey = lambda a: a if a.ndim == 2 else cv2.cvtColor(a[:, :, :3], cv2.COLOR_BGR2GRAY)  # noqa: E731
    pg, bg = grey(photo).astype(np.float32), grey(backdrop).astype(np.float32)
    part, shadow, info = pair_diff(bg, pg, ignore=ignore)
    # the shadow proper: not the rim round the part (its edge pixels are part and paper mixed,
    # smooth, and the test claims them too), and not thin slivers
    rim = cv2.dilate(part, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))) > 0
    core = cv2.erode(np.where(rim, 0, shadow).astype(np.uint8), np.ones((3, 3), np.uint8), iterations=2)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(core, 8)
    big = np.zeros(n, bool)
    big[1:] = stats[1:, cv2.CC_STAT_AREA] >= 40
    core = big[lab]
    factor = np.ones(pg.shape, np.float32)
    if core.any():
        k = (pg / info["gain"]) / np.maximum(bg, 8.0)
        w = cv2.blur(core.astype(np.float32), (15, 15))
        kk = cv2.blur(np.where(core, k, 0).astype(np.float32), (15, 15)) / np.maximum(w, 1e-6)
        zone = cv2.dilate(core.astype(np.uint8), np.ones((9, 9), np.uint8)) > 0
        factor = np.where(zone & (w > 0.02), np.clip(kk, 0.2, 1.0), 1.0).astype(np.float32)
    near = cv2.dilate(part, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * margin_px + 1, 2 * margin_px + 1))) > 0
    bd = backdrop.astype(np.float32) * info["gain"]
    plain = cv2.GaussianBlur(bd, (0, 0), 6.0)
    ph = photo.astype(np.float32)
    if photo.ndim == 3:
        factor, near = factor[:, :, None], near[:, :, None]
    out = np.where(near, ph / factor, plain)
    info["part_px"] = int(((part > 0) & (True if ignore is None else ~ignore.astype(bool))).sum())
    return np.clip(out, 0, 255).astype(photo.dtype), part, shadow, info
