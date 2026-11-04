# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
# Copied from the ImageToSketch project by tools/sync_imagetosketch.py - edit it there.
"""Printable marker sheet: four ArUco markers at known positions on an A4 / Letter page.

Photograph the part lying on the sheet. Finding the markers gives a homography from the photo to
millimetres on the paper, which fixes scale AND perspective skew in one go. The sheet is written
as a PDF (vector, true physical size) with no extra dependencies.

Limit worth knowing: the correction is exact for things *in the paper plane*. The top face of a
thick part is nearer the camera and reads slightly large (about height / camera distance).
"""
import cv2
import numpy as np

MARKER_MM = 20.0
CHECK_MM = 100.0            # length of the printed check line
SHEETS = {
    # page size (mm), marker ids clockwise from top-left
    "A4": dict(page=(210.0, 297.0), ids=(0, 1, 2, 3)),
    "Letter": dict(page=(215.9, 279.4), ids=(4, 5, 6, 7)),
}
EDGE_MM = 20.0              # marker centre to paper edge


def have_aruco():
    return hasattr(cv2, "aruco")


def _dict():
    return cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)


def marker_bits(marker_id):
    """6x6 array, 1 = black cell (includes the black border ring)."""
    ar = cv2.aruco
    if hasattr(ar, "generateImageMarker"):
        img = ar.generateImageMarker(_dict(), marker_id, 6)
    else:                                                      # OpenCV < 4.7
        img = ar.drawMarker(_dict(), marker_id, 6)
    return (img < 128).astype(np.uint8)


def marker_centres(sheet):
    w, h = SHEETS[sheet]["page"]
    e = EDGE_MM
    return [(e, e), (w - e, e), (w - e, h - e), (e, h - e)]


def footer_box(sheet):
    """(x0, y0, x1, y1) mm of the strip holding the check line and text."""
    w, h = SHEETS[sheet]["page"]
    return (EDGE_MM + 18, h - 31, w - EDGE_MM - 18, h - 7)


# ------------------------------------------------------------ write a PDF --
def make_sheet(path, sheet="A4"):
    """Write the printable marker sheet as a one-page vector PDF."""
    if not have_aruco():
        raise RuntimeError("This OpenCV build has no ArUco module (needs OpenCV 4.7+ or contrib).")
    pw, ph = SHEETS[sheet]["page"]
    k = 72.0 / 25.4                                            # mm -> pt
    ops = ["0 0 0 rg"]

    def rect(x, y, w, h):                                      # mm, y measured from the top
        ops.append("%.3f %.3f %.3f %.3f re f" % (x * k, (ph - y - h) * k, w * k, h * k))

    cell = MARKER_MM / 6.0
    for mid, (cx, cy) in zip(SHEETS[sheet]["ids"], marker_centres(sheet)):
        bits = marker_bits(mid)
        for r in range(6):
            for c in range(6):
                if bits[r, c]:
                    rect(cx - MARKER_MM / 2 + c * cell, cy - MARKER_MM / 2 + r * cell, cell + 0.02, cell + 0.02)
    x0, y0, x1, y1 = footer_box(sheet)
    lx = (pw - CHECK_MM) / 2
    ly = y0 + 6
    rect(lx, ly, CHECK_MM, 0.4)
    rect(lx, ly - 2, 0.4, 4.4)
    rect(lx + CHECK_MM - 0.4, ly - 2, 0.4, 4.4)

    def text(x, y, size, s):
        s = s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        ops.append("BT /F1 %d Tf %.2f %.2f Td (%s) Tj ET" % (size, x * k, (ph - y) * k, s))

    text(lx, ly + 6, 8, "This line must measure %g mm. If it does not, type its real length into ImageToSketch." % CHECK_MM)
    text(lx, ly + 10, 8, "Print at 100% / actual size. Lay the part flat in the middle, keep all four markers visible,")
    text(lx, ly + 14, 8, "photograph from straight above. ImageToSketch marker sheet (%s)." % sheet)
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
        f.write(out)
    return path


def render_sheet(sheet="A4", mm_per_px=0.2):
    """Raster of the sheet (for tests and previews): uint8 grey, white paper."""
    pw, ph = SHEETS[sheet]["page"]
    W, H = int(round(pw / mm_per_px)), int(round(ph / mm_per_px))
    img = np.full((H, W), 255, np.uint8)
    cell = MARKER_MM / 6.0
    for mid, (cx, cy) in zip(SHEETS[sheet]["ids"], marker_centres(sheet)):
        bits = marker_bits(mid)
        for r in range(6):
            for c in range(6):
                if bits[r, c]:
                    xa = (cx - MARKER_MM / 2 + c * cell) / mm_per_px
                    ya = (cy - MARKER_MM / 2 + r * cell) / mm_per_px
                    img[int(round(ya)):int(round(ya + cell / mm_per_px)),
                        int(round(xa)):int(round(xa + cell / mm_per_px))] = 0
    return img


# ----------------------------------------------------------------- detect --
def find_sheet(img):
    """Locate the marker sheet. Returns dict(sheet, H (image px -> page mm), n_markers,
    mm_per_px_native) or raises ValueError with a plain-language reason."""
    if not have_aruco():
        raise ValueError("This OpenCV build has no ArUco module; use another scale mode.")
    gray = img if img.ndim == 2 else cv2.cvtColor(img[:, :, :3], cv2.COLOR_BGR2GRAY)
    ar = cv2.aruco
    if hasattr(ar, "ArucoDetector"):
        prm = ar.DetectorParameters()
        prm.cornerRefinementMethod = ar.CORNER_REFINE_SUBPIX
        corners, ids, _ = ar.ArucoDetector(_dict(), prm).detectMarkers(gray)
    else:
        prm = ar.DetectorParameters_create()
        prm.cornerRefinementMethod = ar.CORNER_REFINE_SUBPIX
        corners, ids, _ = ar.detectMarkers(gray, _dict(), parameters=prm)
    if ids is None or len(ids) == 0:
        raise ValueError("No markers found. Is the marker sheet fully in the picture and in focus?")
    ids = ids.ravel().tolist()
    best = None
    for name, sh in SHEETS.items():
        n = sum(1 for i in sh["ids"] if i in ids)
        if best is None or n > best[1]:
            best = (name, n)
    sheet, n = best
    if n < 3:
        raise ValueError("Only %d of 4 markers found; at least 3 are needed. Keep them uncovered." % n)
    src, dst = [], []
    h = MARKER_MM / 2
    for mid, (cx, cy) in zip(SHEETS[sheet]["ids"], marker_centres(sheet)):
        if mid not in ids:
            continue
        c = corners[ids.index(mid)].reshape(4, 2)
        src.extend(c.tolist())
        dst.extend([(cx - h, cy - h), (cx + h, cy - h), (cx + h, cy + h), (cx - h, cy + h)])
    src, dst = np.float64(src), np.float64(dst)
    Hm, _ = cv2.findHomography(src, dst, 0)
    if Hm is None:
        raise ValueError("Could not work out the page position from the markers.")
    side = np.mean([np.hypot(*(src[i] - src[i + 1])) for i in range(0, len(src), 4)])
    proj = cv2.perspectiveTransform(src.reshape(-1, 1, 2), Hm).reshape(-1, 2)
    rms = float(np.sqrt(np.mean(np.sum((proj - dst) ** 2, axis=1))))
    return dict(sheet=sheet, H=Hm, n_markers=n, mm_per_px_native=MARKER_MM / side, fit_rms_mm=rms,
                seen=[i for i in SHEETS[sheet]["ids"] if i in ids])


def rectify(img, found, mm_per_px=None, max_side=2400, inset_mm=3.0, print_scale=1.0):
    """Warp the photo to a straight-on view of the page.

    Returns (image, mm_per_px, exclude) where exclude is a list of pixel rectangles
    (x0, y0, x1, y1) covering the markers that were seen and the sheet's text strip. The tracer
    ignores anything lying wholly inside one of them - but a part that merely overlaps the text
    strip, or covers a marker, is left alone.
    print_scale = measured check-line length / nominal (corrects a printer that scaled the page)."""
    pw, ph = SHEETS[found["sheet"]]["page"]
    s = mm_per_px or found["mm_per_px_native"]
    s = max(s, max(pw, ph) / float(max_side))
    Wo = int(round((pw - 2 * inset_mm) / s))
    Ho = int(round((ph - 2 * inset_mm) / s))
    T = np.array([[1 / s, 0, -inset_mm / s], [0, 1 / s, -inset_mm / s], [0, 0, 1]])
    # shrinking by a large factor needs area averaging, which warpPerspective cannot do: blur first
    shrink = s / found["mm_per_px_native"]
    src = cv2.GaussianBlur(img, (0, 0), 0.45 * shrink) if shrink > 1.5 else img
    out = cv2.warpPerspective(src, T @ found["H"], (Wo, Ho), flags=cv2.INTER_CUBIC,
                              borderMode=cv2.BORDER_REPLICATE)

    def box(x0, y0, x1, y1):
        return tuple(int(round((v - inset_mm) / s)) for v in (x0, y0, x1, y1))

    q = MARKER_MM / 2 + 3.0
    exclude = [box(cx - q, cy - q, cx + q, cy + q)
               for mid, (cx, cy) in zip(SHEETS[found["sheet"]]["ids"], marker_centres(found["sheet"]))
               if mid in found.get("seen", SHEETS[found["sheet"]]["ids"])]
    exclude.append(box(*footer_box(found["sheet"])))
    return out, s * print_scale, exclude
