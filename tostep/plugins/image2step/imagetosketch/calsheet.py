# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
# Copied from the ImageToSketch project by tools/sync_imagetosketch.py - edit it there.
"""Calibration sheet: a frame of numbered ArUco markers round an A4 / Letter page, and a
50 mm stand with one marker on its flat top.

The part lies in the clear middle of the sheet; the stand stands anywhere else on it.

  * The frame: 40 (A4) or 38 (Letter) markers, each 16 mm, every one numbered, so whichever of
    them are in view (a shadow or the part may hide some) say exactly where they sit on the
    sheet. That is 4 measured corners per marker - over a hundred points spread round the
    page instead of the token sheet's eight - for the sheet's position and tilt; the spread
    of those points also shows a slight barrel bend of the lens (and takes it out) and paper
    that does not lie flat (and warns).
  * The paper between and inside the markers is sampled for brightness: how the light falls
    across the sheet, and where a shadow lies on the frame.
  * The stand: its marker is 50 mm (as measured) above the paper. Seen from the camera, a face
    lifted towards it looks larger by distance / (distance - height): comparing the stand's
    marker with the frame therefore MEASURES the camera distance - no lens data, no tape
    measure. Then the part's height is the only number that matters. The label is printed on
    the same paper as the frame (page 2 of the PDF), so a printer that stretches the page
    stretches both and the comparison is unaffected.
  * Page 2 also has two check lines (150 mm across, 200 mm along): measured once with a ruler,
    they put right a printer that stretches the page differently in the two directions.

Coordinates: sheet millimetres from the paper's top left corner, X to the right, Y down. As in
scaletoken, H maps sheet (X, Y, 1) to image pixels; a plane h mm above the paper is Z = -h.
"""
import math
import struct

import cv2
import numpy as np

from . import scaletoken as T

PAGES = {"A4": (210.0, 297.0), "Letter": (215.9, 279.4)}
MARKER_MM = 16.0            # frame markers
EDGE_MM = 10.0              # paper edge to the frame's outer edge
BAND_MM = 6.0               # white band between the frame and the clear middle (light samples, caption)
PITCH_MM = 22.0             # about this far apart (centre to centre)
FIRST_ID = {"A4": 10, "Letter": 50}
STAND_ID = 99
LABEL_MARKER_MM = 28.0      # the stand's marker (5 mm of white round it: older OpenCV drops a marker
                            # whose corners come closer than that to another dark square's)
LABEL_MM = 38.0             # the cut-out label it sits on (the stand's top is 40 mm square)
STAND_MM = 50.0             # height of the stand's top above the paper (as printed, before the label)
CHECK_ACROSS_MM = 150.0
CHECK_ALONG_MM = 200.0
STAND_FOOT_R = 30.0         # the stand's feet: this far from its centre


def have_aruco():
    return hasattr(cv2, "aruco") and hasattr(cv2.aruco, "getPredefinedDictionary")


def _dict():
    return cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_100)


def marker_bits(marker_id):
    """6 x 6 array, 1 = black cell (with the black border)."""
    ar = cv2.aruco
    img = ar.generateImageMarker(_dict(), marker_id, 6) if hasattr(ar, "generateImageMarker") \
        else ar.drawMarker(_dict(), marker_id, 6)
    return (img < 128).astype(np.uint8)


# ----------------------------------------------------------------- layout --
def frame(paper="A4"):
    """[(marker id, x, y)]: top left corner (mm) of each frame marker, clockwise from the top
    left corner of the sheet."""
    pw, ph = PAGES[paper]
    m, e = MARKER_MM, EDGE_MM
    nx = int(round((pw - 2 * e - m) / PITCH_MM)) + 1
    ny = int(round((ph - 2 * e - m) / PITCH_MM)) + 1
    xs = [e + i * (pw - 2 * e - m) / (nx - 1) for i in range(nx)]
    ys = [e + j * (ph - 2 * e - m) / (ny - 1) for j in range(ny)]
    pos = [(x, ys[0]) for x in xs] + [(xs[-1], y) for y in ys[1:-1]] + \
          [(x, ys[-1]) for x in xs[::-1]] + [(xs[0], y) for y in ys[-2:0:-1]]
    return [(FIRST_ID[paper] + i, x, y) for i, (x, y) in enumerate(pos)]


def clear_area(paper="A4"):
    """(x0, y0, x1, y1) mm: the middle of the sheet, inside the frame and its white band."""
    pw, ph = PAGES[paper]
    a = EDGE_MM + MARKER_MM + BAND_MM
    return (a, a, pw - a, ph - a)


def corners_mm(x, y, side):
    """A marker's four corners in the order ArUco reports them: top left, top right, bottom
    right, bottom left (as printed)."""
    return np.array([[x, y], [x + side, y], [x + side, y + side], [x, y + side]], float)


def paper_of(marker_id):
    for p, first in FIRST_ID.items():
        if first <= marker_id < first + len(frame(p)):
            return p
    return None


def light_points(paper="A4"):
    """Where the paper's own white is sampled (mm): along the white band inside the frame (the
    top side holds the caption, so its outer half is skipped) and in the gaps between markers."""
    pw, ph = PAGES[paper]
    a, b = EDGE_MM + MARKER_MM, EDGE_MM + MARKER_MM + BAND_MM
    mid = (a + b) / 2.0
    pts = []
    for x in np.arange(b, pw - b + 0.1, 8.0):
        pts += [(x, b - 1.2), (x, ph - mid)]
    for y in np.arange(b, ph - b + 0.1, 8.0):
        pts += [(mid, y), (pw - mid, y)]
    fr = frame(paper)
    for (_, x0, y0), (_, x1, y1) in zip(fr, fr[1:] + fr[:1]):
        if abs(x1 - x0) > 1 and abs(y1 - y0) < 1:              # neighbours side by side
            pts.append(((x0 + x1) / 2.0 + MARKER_MM / 2.0, y0 + MARKER_MM / 2.0))
        elif abs(y1 - y0) > 1 and abs(x1 - x0) < 1:
            pts.append((x0 + MARKER_MM / 2.0, (y0 + y1) / 2.0 + MARKER_MM / 2.0))
    return np.array(pts, float)


def _caption(paper):
    return ("ImageToSketch calibration sheet %s - print at 100 %% (actual size). Part in the clear middle; "
            "the 50 mm stand anywhere else inside the frame, a few cm from the part." % paper)


# -------------------------------------------------------------------- PDF --
def _pdf(pages, size_mm):
    """A minimal vector PDF: pages is a list of content-stream strings (PDF points)."""
    k = 72.0 / 25.4
    pw, ph = size_mm
    n = len(pages)
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            ("<< /Type /Pages /Kids [%s] /Count %d >>" % (" ".join("%d 0 R" % (4 + 2 * i) for i in range(n)), n)).encode(),
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    for i, ops in enumerate(pages):
        stream = ops.encode("latin-1")
        objs.append(("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %.3f %.3f] /Contents %d 0 R "
                     "/Resources << /Font << /F1 3 0 R >> >> >>" % (pw * k, ph * k, 5 + 2 * i)).encode())
        objs.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
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
    return bytes(out)


class _Page:
    def __init__(self, ph):
        self.k, self.ph, self.ops = 72.0 / 25.4, ph, []

    def rect(self, x, y, w, h, grey=0.0):                      # mm, y from the top
        self.ops.append("%.3f g %.3f %.3f %.3f %.3f re f" % (grey, x * self.k, (self.ph - y - h) * self.k,
                                                             w * self.k, h * self.k))

    def frame_rect(self, x, y, w, h, width=0.2, grey=0.55):
        self.ops.append("%.3f G %.3f w %.3f %.3f %.3f %.3f re S" % (grey, width * self.k, x * self.k,
                                                                   (self.ph - y - h) * self.k, w * self.k, h * self.k))

    def marker(self, mid, x, y, side):
        bits, c = marker_bits(mid), side / 6.0
        for r in range(6):                                     # runs of black cells, one rectangle each
            col = 0
            while col < 6:
                if bits[r, col]:
                    end = col
                    while end + 1 < 6 and bits[r, end + 1]:
                        end += 1
                    self.rect(x + col * c, y + r * c, (end - col + 1) * c, c + 0.01)
                    col = end + 1
                else:
                    col += 1

    def text(self, x, y, size, s, grey=0.0):
        s = s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        self.ops.append("%.3f g BT /F1 %.2f Tf %.2f %.2f Td (%s) Tj ET" % (grey, size, x * self.k, (self.ph - y) * self.k, s))


def make_pdf(path, paper="A4"):
    """The calibration sheet as a two-page vector PDF. Page 1: the marker frame round a clear
    middle. Page 2: four labels for the stand's top, the two check lines, and how to use them."""
    if not have_aruco():
        raise RuntimeError("This OpenCV build has no ArUco module (OpenCV 4.7 or later has it).")
    if paper not in PAGES:
        raise ValueError("Paper must be one of: %s" % ", ".join(PAGES))
    pw, ph = PAGES[paper]
    p1 = _Page(ph)
    for mid, x, y in frame(paper):
        p1.marker(mid, x, y, MARKER_MM)
    a = EDGE_MM + MARKER_MM
    p1.text(a + 1.0, a + BAND_MM - 3.3, 5.0, _caption(paper), grey=0.25)

    p2 = _Page(ph)
    p2.text(15, 18, 12, "ImageToSketch calibration sheet %s - page 2: stand labels and check lines" % paper)
    # check lines, with end ticks
    x0, y0 = 15.0, 32.0
    p2.rect(x0, y0, CHECK_ACROSS_MM, 0.35)
    for x in (x0, x0 + CHECK_ACROSS_MM - 0.35):
        p2.rect(x, y0 - 3, 0.35, 6.35)
    p2.text(x0, y0 + 7, 8, "Across: measure this line (end to end, outer edges of the ticks). Printed right it is "
                           "%.0f mm." % CHECK_ACROSS_MM)
    lx, ly = pw - 25.0, 45.0
    p2.rect(lx, ly, 0.35, CHECK_ALONG_MM)
    for y in (ly, ly + CHECK_ALONG_MM - 0.35):
        p2.rect(lx - 3, y, 6.35, 0.35)
    p2.text(15, y0 + 11, 8, "Along: measure the long upright line on the right. Printed right it is %.0f mm." % CHECK_ALONG_MM)
    p2.text(15, y0 + 15, 8, "Type both into ImageToSketch once (it remembers them); 150 and 200 if they are right.")
    # four stand labels
    gap = 8.0
    for i in range(4):
        cx = 15.0 + (i % 2) * (LABEL_MM + gap)
        cy = 62.0 + (i // 2) * (LABEL_MM + gap)
        p2.frame_rect(cx, cy, LABEL_MM, LABEL_MM)
        off = (LABEL_MM - LABEL_MARKER_MM) / 2.0
        p2.marker(STAND_ID, cx + off, cy + off, LABEL_MARKER_MM)
    t = 62.0 + 2 * (LABEL_MM + gap) + 6
    for i, line in enumerate([
            "The stand: print stand_50mm.stl (top face down on the bed, no supports). Cut out one label",
            "along its grey line and glue it flat on the stand's top, any way round.",
            "Measure the stand's height to the top of the label with calipers (about 50.1 mm) and type it in.",
            "",
            "Taking the photo: page 1 flat on a table, the part in the clear middle, the stand anywhere else",
            "inside the frame, a few cm from the part and out of its shadow. Keep as much of the frame in view",
            "as you can (markers hidden by a shadow or the part are simply left out). For a tall part, shoot",
            "from further away with the zoom, part in the middle of the picture."]):
        p2.text(15, t + i * 5.2, 8.5, line)
    with open(path, "wb") as f:
        f.write(_pdf(["\n".join(p1.ops), "\n".join(p2.ops)], (pw, ph)))
    return path


def render_page(paper="A4", mm_per_px=0.2, page=1):
    """Page 1 (or 2) as a grey picture, for tests and the documentation. The markers are drawn
    exactly (cells are axis-aligned squares, rendered with area coverage); the caption is
    left out."""
    pw, ph = PAGES[paper]
    W, H = int(round(pw / mm_per_px)), int(round(ph / mm_per_px))
    img = np.full((H, W), 255.0, np.float32)

    def square(x, y, w, h):                                    # exact area coverage
        xa, xb, ya, yb = x / mm_per_px, (x + w) / mm_per_px, y / mm_per_px, (y + h) / mm_per_px
        cx = np.clip(np.minimum(np.arange(W) + 1, xb) - np.maximum(np.arange(W), xa), 0, 1)
        cy = np.clip(np.minimum(np.arange(H) + 1, yb) - np.maximum(np.arange(H), ya), 0, 1)
        i0, i1 = max(0, int(ya) - 1), min(H, int(yb) + 2)
        j0, j1 = max(0, int(xa) - 1), min(W, int(xb) + 2)
        img[i0:i1, j0:j1] *= 1.0 - np.outer(cy[i0:i1], cx[j0:j1])

    def marker(mid, x, y, side):
        bits, c = marker_bits(mid), side / 6.0
        for r in range(6):
            for col in range(6):
                if bits[r, col]:
                    square(x + col * c, y + r * c, c, c)

    if page == 1:
        for mid, x, y in frame(paper):
            marker(mid, x, y, MARKER_MM)
    else:
        for i in range(4):
            cx = 15.0 + (i % 2) * (LABEL_MM + 8.0) + (LABEL_MM - LABEL_MARKER_MM) / 2.0
            cy = 62.0 + (i // 2) * (LABEL_MM + 8.0) + (LABEL_MM - LABEL_MARKER_MM) / 2.0
            marker(STAND_ID, cx, cy, LABEL_MARKER_MM)
    return np.clip(img, 0, 255).astype(np.uint8)


# ------------------------------------------------------------------ stand --
def _box(x0, y0, z0, x1, y1, z1):
    v = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0), (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    return _hexa(v)


def _hexa(v):
    """Triangles of a hexahedron given by 8 corners (bottom 0-3, top 4-7, both counter-clockwise
    seen from above)."""
    f = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7)]
    for i in range(4):
        j = (i + 1) % 4
        f += [(i, j, j + 4), (i, j + 4, i + 4)]
    return [(v[a], v[b], v[c]) for a, b, c in f]


def _cylinder(cx, cy, r, z0, z1, n=32):
    t = []
    for i in range(n):
        a0, a1 = 2 * math.pi * i / n, 2 * math.pi * (i + 1) / n
        p0 = (cx + r * math.cos(a0), cy + r * math.sin(a0))
        p1 = (cx + r * math.cos(a1), cy + r * math.sin(a1))
        t += [((cx, cy, z1), (p0[0], p0[1], z1), (p1[0], p1[1], z1)),
              ((cx, cy, z0), (p1[0], p1[1], z0), (p0[0], p0[1], z0)),
              ((p0[0], p0[1], z0), (p1[0], p1[1], z0), (p1[0], p1[1], z1)),
              ((p0[0], p0[1], z0), (p1[0], p1[1], z1), (p0[0], p0[1], z1))]
    return t


def stand_triangles(height=STAND_MM, top=40.0, plate=3.0, leg=6.0, foot_r=STAND_FOOT_R, foot=4.5):
    """The stand, as it prints: upside down, its top face on the bed (z = 0) so that face comes
    out flat. A 40 mm square plate, three legs splayed out to feet 30 mm from the middle,
    each foot a small disc. Separate closed bodies that overlap (slicers join them)."""
    tris = _box(-top / 2, -top / 2, 0.0, top / 2, top / 2, plate)
    for i in range(3):
        a = math.radians(90 + 120 * i)
        c, s = math.cos(a), math.sin(a)
        top_r, h = 9.0, height - 1.5                           # leg: from under the plate to the foot

        def at(r, d, z):                                       # r along the leg's direction, d across it
            return (r * c - d * s, r * s + d * c, z)
        w = leg / 2
        v = [at(top_r - w, -w, plate - 0.5), at(top_r + w, -w, plate - 0.5), at(top_r + w, w, plate - 0.5),
             at(top_r - w, w, plate - 0.5),
             at(foot_r - w, -w, h + 0.3), at(foot_r + w, -w, h + 0.3), at(foot_r + w, w, h + 0.3),
             at(foot_r - w, w, h + 0.3)]
        tris += _hexa(v)
        tris += _cylinder(foot_r * c, foot_r * s, foot, h, height)
    return tris


def make_stand_stl(path, height=STAND_MM):
    """Write the stand as a binary STL (millimetres, printed top face down). Returns the path."""
    if not 20.0 <= height <= 150.0:
        raise ValueError("The stand's height must be 20 to 150 mm.")
    tris = stand_triangles(height)
    head = ("ImageToSketch calibration stand, %.1f mm, print this way up" % height).encode("ascii")
    out = bytearray(head.ljust(80, b" ")[:80]) + struct.pack("<I", len(tris))
    for t in tris:
        v = np.array(t, float)
        nrm = np.cross(v[1] - v[0], v[2] - v[0])
        ln = float(np.linalg.norm(nrm)) or 1.0
        out += struct.pack("<12fH", *(nrm / ln), *v[0], *v[1], *v[2], 0)
    with open(path, "wb") as f:
        f.write(bytes(out))
    return path


# ----------------------------------------------------------------- detect --
def detect_all(img):
    """Every marker of the dictionary in the picture, repeats included: [(id, 4 x 2 corners px)]
    as the detector gives them (not refined)."""
    if not have_aruco():
        raise ValueError("This OpenCV has no ArUco module (OpenCV 4.7 or later has it; older ones only with "
                         "their contrib part); use the scale token instead.")
    gray = img if img.ndim == 2 else cv2.cvtColor(img[:, :, :3], cv2.COLOR_BGR2GRAY)
    ar = cv2.aruco
    prm = ar.DetectorParameters() if hasattr(ar, "ArucoDetector") else ar.DetectorParameters_create()
    prm.cornerRefinementMethod = ar.CORNER_REFINE_SUBPIX
    prm.cornerRefinementWinSize = 4
    prm.cornerRefinementMaxIterations = 60
    prm.cornerRefinementMinAccuracy = 0.005
    prm.adaptiveThreshWinSizeMax = 63                          # big markers in a sharp photo too
    prm.adaptiveThreshWinSizeStep = 10
    if hasattr(ar, "ArucoDetector"):
        corners, ids, _ = ar.ArucoDetector(_dict(), prm).detectMarkers(gray)
    else:                                                      # OpenCV before 4.7 (with its contrib part)
        corners, ids, _ = ar.detectMarkers(gray, _dict(), parameters=prm)
    if ids is None:
        return []
    return [(int(i), c.reshape(4, 2).astype(float)) for c, i in zip(corners, ids.ravel().tolist())]


def detect(img):
    """All markers of the sheet and the stand in the picture: {id: 4 x 2 corners (px)}, each
    refined to a small fraction of a pixel. A number seen twice is left out (not ours)."""
    gray = img if img.ndim == 2 else cv2.cvtColor(img[:, :, :3], cv2.COLOR_BGR2GRAY)
    found = {}
    for i, c in detect_all(gray):
        found[i] = None if i in found else c
    return {i: _refine(gray, c) for i, c in found.items() if c is not None}


def _refine(gray, quad):
    """Corners to a small fraction of a pixel: each side of the marker's black border is located
    along its length (the brightness step across it, fitted with a straight line) and the
    corners are where the four lines meet. Falls back to the detector's corners."""
    side = float(np.mean([np.hypot(*(quad[(i + 1) % 4] - quad[i])) for i in range(4)]))
    if side < 24:
        return quad
    cell = side / 6.0
    reach = max(2.0, 0.35 * cell)
    lines = []
    g = gray.astype(np.float32)
    h, w = g.shape
    centre = quad.mean(0)
    for i in range(4):
        a, b = quad[i], quad[(i + 1) % 4]
        d = (b - a) / np.hypot(*(b - a))
        n = np.array([d[1], -d[0]])
        if np.dot(n, (a + b) / 2 - centre) < 0:
            n = -n                                             # pointing out of the marker
        pts = []
        for t in np.linspace(0.15, 0.85, 24):
            p = a + t * (b - a)
            s = np.arange(-reach, reach + 0.01, 0.25)
            q = p[None, :] + s[:, None] * n[None, :]
            if (q < 1).any() or q[:, 0].max() > w - 2 or q[:, 1].max() > h - 2:
                continue
            v = cv2.remap(g, q[:, 0:1].astype(np.float32), q[:, 1:2].astype(np.float32), cv2.INTER_LINEAR).ravel()
            lo_, hi_ = float(np.median(v[s < -0.5 * reach])), float(np.median(v[s > 0.5 * reach]))
            if hi_ - lo_ < 20:
                continue
            half = 0.5 * (lo_ + hi_)                           # where the step crosses half way
            up = np.nonzero((v[:-1] < half) & (v[1:] >= half))[0]
            if len(up) != 1:
                continue
            k = int(up[0])
            off = (half - v[k]) / (v[k + 1] - v[k])
            pts.append(q[k] + off * 0.25 * n)
        if len(pts) < 8:
            return quad
        P = np.array(pts)
        lines.append(T._line_fit(P))
    out = []
    for i in range(4):
        c = T._meet(lines[i - 1], lines[i])
        if c is None:
            return quad
        out.append(c)
    out = np.array(out, float)
    if np.abs(out - quad).max() > 0.25 * cell:
        return quad
    return out


def _undistort_pts(P, centre, R0, k1):
    d = P - centre
    r2 = (d ** 2).sum(1) / R0 ** 2
    return centre + d * (1.0 + k1 * r2)[:, None]


def _fit(src_mm, dst_px):
    """Least-squares homography sheet mm -> px, and the residual (mm) of every point."""
    H, _ = cv2.findHomography(src_mm, dst_px, 0)
    if H is None:
        return None, None
    back = T._apply(np.linalg.inv(H), dst_px)
    return H / H[2, 2], np.hypot(*(back - src_mm).T)


def find(img, stretch=(1.0, 1.0)):
    """Locate the sheet. stretch = (across, along): measured / nominal length of the page-2
    check lines. Returns dict(paper, H (sheet mm -> undistorted px), k1, n (frame markers),
    resid_mm (per corner), seen, stand (4 x 2 px corners or None), centre, R0, pts...) or raises
    ValueError."""
    found = detect(img)
    papers = {}
    for i in found:
        p = paper_of(i)
        if p:
            papers[p] = papers.get(p, 0) + 1
    if not papers:
        if found and all(i < 8 for i in found):
            raise ValueError("This is the older four-marker sheet: choose 'printed marker sheet' as the scale.")
        raise ValueError("No calibration sheet found: none of its frame markers is in the picture (or in focus).")
    paper = max(papers, key=papers.get)
    n = papers[paper]
    if n < 4:
        raise ValueError("Only %d markers of the calibration sheet's frame were found; at least 4 are needed. "
                         "Keep more of the frame in view, out of shadow and in focus." % n)
    ax, ay = stretch
    src, dst, owner = [], [], []
    for mid, x, y in frame(paper):
        if mid in found:
            c = corners_mm(x, y, MARKER_MM)
            src.append(c * [ax, ay])
            dst.append(found[mid])
            owner += [mid] * 4
    src, dst = np.vstack(src), np.vstack(dst)
    # drop markers that do not fit (a misread, or one on a curled corner): RANSAC, then by marker
    Hr, mask = cv2.findHomography(src, dst, cv2.RANSAC, 3.0)
    if Hr is None:
        raise ValueError("The frame markers found do not fit the sheet's layout.")
    good = mask.ravel().astype(bool)
    keep = np.array([good[np.array(owner) == m].all() for m in owner])
    src, dst, owner = src[keep], dst[keep], [o for o, k in zip(owner, keep) if k]
    h, w = img.shape[:2]
    centre = np.array([(w - 1) / 2.0, (h - 1) / 2.0])
    R0 = math.hypot(w, h) / 2.0
    H, res = _fit(src, dst)
    if H is None:
        raise ValueError("Could not work out the sheet's position from its markers.")
    k1 = 0.0
    rms0 = float(np.sqrt(np.mean(res ** 2)))
    # the lens's barrel / pincushion bend: one coefficient, kept only if it explains the spread
    if len(set(owner)) >= 12:
        def cost(k):
            Hk, rk = _fit(src, _undistort_pts(dst, centre, R0, k))
            return float(np.sqrt(np.mean(rk ** 2))) if Hk is not None else 1e9
        lo, hi = -0.25, 0.25
        g = (math.sqrt(5) - 1) / 2
        a, b = hi - g * (hi - lo), lo + g * (hi - lo)
        fa, fb = cost(a), cost(b)
        for _ in range(40):
            if fa < fb:
                hi, b, fb = b, a, fa
                a = hi - g * (hi - lo)
                fa = cost(a)
            else:
                lo, a, fa = a, b, fb
                b = lo + g * (hi - lo)
                fb = cost(b)
        kb = (lo + hi) / 2
        if cost(kb) < 0.75 * rms0 and abs(kb) > 1e-4:
            k1 = kb
            H, res = _fit(src, _undistort_pts(dst, centre, R0, k1))
    stand = found.get(STAND_ID)
    if stand is not None:
        stand = _undistort_pts(stand, centre, R0, k1)
    side = np.mean([np.hypot(*(found[m][(i + 1) % 4] - found[m][i])) for m in set(owner) for i in range(4)])
    return dict(paper=paper, H=H, k1=k1, n=len(set(owner)), n_found=n, resid_mm=res, rms0_mm=rms0,
                seen=sorted(set(owner)), stand=stand, centre=centre, R0=R0, side_px=float(side),
                src=src, owner=owner)


def undistort_image(img, centre, R0, k1):
    """Take the lens bend k1 (as fitted by find) out of the whole picture."""
    if not k1:
        return img
    h, w = img.shape[:2]
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    ux, uy = xs - centre[0], ys - centre[1]
    dx, dy = ux.copy(), uy.copy()
    for _ in range(6):                                         # distorted point whose image is (x, y)
        r2 = (dx * dx + dy * dy) / R0 ** 2
        f = 1.0 + k1 * r2
        dx, dy = ux / f, uy / f
    return cv2.remap(img, (dx + centre[0]).astype(np.float32), (dy + centre[1]).astype(np.float32),
                     cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


def _similarity(A, B):
    """Least-squares B ~ s R A + t (2 x 2 rotation R). Returns s, rms residual."""
    ma, mb = A.mean(0), B.mean(0)
    a, b = A - ma, B - mb
    sxx = (a ** 2).sum()
    c = (a[:, 0] * b[:, 0] + a[:, 1] * b[:, 1]).sum()
    d = (a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]).sum()
    s = math.hypot(c, d) / sxx
    th = math.atan2(d, c)
    R = np.array([[math.cos(th), -math.sin(th)], [math.sin(th), math.cos(th)]])
    fit = (a @ R.T) * s + mb
    return s, float(np.sqrt(np.mean(((fit - B) ** 2).sum(1)))), mb


def stand_distance(H, stand_px, stand_mm, stretch=(1.0, 1.0)):
    """The camera's distance above the paper from the stand's marker: its corners mapped into the
    sheet's plane come out larger than the marker by k = D / (D - stand height). Returns
    (D, k, relative uncertainty of D, centre of the marker in sheet mm) or None."""
    side = LABEL_MARKER_MM * (stretch[0] + stretch[1]) / 2.0
    Q = T._apply(np.linalg.inv(H), stand_px)
    k, rms, centre = _similarity(corners_mm(0, 0, side), Q)
    if not k > 1.002:
        return None
    D = stand_mm * k / (k - 1.0)
    e = max(0.0012, rms / side)                                # scale precision of the comparison
    u = math.hypot(e / (k - 1.0), 0.15 / stand_mm)             # + the stand's height, measured to 0.15 mm
    return D, k, max(u, 0.01), centre


def light(img, H, paper):
    """The paper's brightness round the frame: a smooth surface (quadratic in sheet mm) fitted
    to samples of the white, and the samples that fall well below it (a shadow on the frame).
    Returns dict(coef, falloff over the clear area, shadow points (mm), samples)."""
    gray = img if img.ndim == 2 else cv2.cvtColor(img[:, :, :3], cv2.COLOR_BGR2GRAY)
    P = light_points(paper)
    px = T._apply(H, P)
    h, w = gray.shape
    mm_px = 1.0 / max(1e-9, np.hypot(*(T._apply(H, [[0, 0], [1, 0]])[1] - T._apply(H, [[0, 0]])[0])))
    rad = max(1, int(round(1.2 / mm_px)))
    vals, keep = [], []
    for (x, y) in px:
        xi, yi = int(round(x)), int(round(y))
        if rad <= xi < w - rad and rad <= yi < h - rad:
            win = gray[yi - rad:yi + rad + 1, xi - rad:xi + rad + 1]
            vals.append(float(np.percentile(win, 80)))
            keep.append(True)
        else:
            keep.append(False)
    keep = np.array(keep)
    P = P[keep]
    v = np.log(np.maximum(np.array(vals), 1.0))
    if len(P) < 12:
        return None
    pw, ph = PAGES[paper]
    X, Y = P[:, 0] / pw - 0.5, P[:, 1] / ph - 0.5
    A = np.c_[np.ones_like(X), X, Y, X * X, X * Y, Y * Y]
    ok = np.ones(len(v), bool)
    for _ in range(4):
        coef, *_ = np.linalg.lstsq(A[ok], v[ok], rcond=None)
        r = v - A @ coef
        sd = 1.4826 * np.median(np.abs(r[ok] - np.median(r[ok]))) + 0.01
        ok = r > -3.0 * sd                                     # dark outliers: shadow, dirt
    x0, y0, x1, y1 = clear_area(paper)
    gx, gy = np.meshgrid(np.linspace(x0, x1, 9) / pw - 0.5, np.linspace(y0, y1, 9) / ph - 0.5)
    G = np.exp(np.c_[np.ones(gx.size), gx.ravel(), gy.ravel(), gx.ravel() ** 2, (gx * gy).ravel(), gy.ravel() ** 2] @ coef)
    shade = P[~ok]
    return dict(coef=coef.tolist(), falloff=float(1.0 - G.min() / G.max()), shadow_mm=shade.tolist(),
                white=float(np.exp(coef[0])), samples=int(len(P)))


def locate(img, above=0.0, focal_35mm=0.0, camera_distance=0.0, stand_mm=STAND_MM, stretch=(1.0, 1.0),
           max_side=2400, backdrop=None):
    """Everything the sheet (and the stand, if it is in view) gives, in one call: the picture
    straightened in the plane `above` mm over the paper and cut to the sheet's clear middle.
    backdrop: a second photo of the same sheet without the part (optional): it is lined up with
    the first through the sheet's markers, and everything that did not change - and the part's
    shadow - is made plain paper before tracing (pairdiff.py).
    Returns dict(image, mm_per_px, exclude, M, info) like scaletoken.locate."""
    ax, ay = stretch
    if not (0.9 < ax < 1.1 and 0.9 < ay < 1.1):
        raise ValueError("The check lines must measure within 10 % of 150 and 200 mm.")
    f = find(img, stretch)
    img = undistort_image(img, f["centre"], f["R0"], f["k1"])
    H, paper = f["H"], f["paper"]
    # the camera's distance: the stand measures it; else lens data, a typed distance, or a phone
    D_stand = stand_distance(H, f["stand"], stand_mm, stretch) if f["stand"] is not None else None
    typed = float(camera_distance) if camera_distance and camera_distance > 0 else 0.0
    side_px, side_mm = f["side_px"], MARKER_MM * (ax + ay) / 2.0

    def cam_for(K):
        R, t = T.pose(H, K)
        return T._camera(R, t, K)

    def refit(D):                                              # the focal length that puts the camera at D
        fpx = D * side_px / side_mm
        for _ in range(5):
            fpx *= D / max(cam_for(T.intrinsics(img.shape, focal_px=fpx))["height"], 1.0)
        return T.intrinsics(img.shape, focal_px=fpx)

    phone = cam_for(T.intrinsics(img.shape, focal_35mm=T.PHONE_35MM))["height"]
    lens_D = cam_for(T.intrinsics(img.shape, focal_35mm=focal_35mm))["height"] if focal_35mm and focal_35mm > 0 else None
    if D_stand is not None:
        dist, how, u = D_stand[0], "stand", D_stand[2]
        K = refit(dist)
    elif lens_D is not None:
        dist, how, u = lens_D, "lens", 0.02
        K = T.intrinsics(img.shape, focal_35mm=focal_35mm)
    elif typed:
        if typed <= above + 5:
            raise ValueError("The camera distance must be the distance from the camera to the paper, in mm, "
                             "and more than the height of the part.")
        dist, how, u = typed, "typed", 0.10
        K = refit(dist)
    elif above:
        dist, how, u = phone, "assumed", 0.10
        K = T.intrinsics(img.shape, focal_35mm=T.PHONE_35MM)
    else:
        dist, how, u, K = None, None, 0.0, None
    cam = cam_for(K) if K is not None else None
    if dist is not None and dist - above < 20:
        raise ValueError("The height of the part (%.0f mm) is too close to the camera distance (%.0f mm): "
                         "check both." % (above, dist))
    nadir_spread = 0.0
    if cam is not None:
        for m in (1 - u, 1 + u):
            c2 = cam_for(refit(dist * m) if how in ("stand", "typed") else
                         T.intrinsics(img.shape, focal_px=K[0, 0] * m))
            nadir_spread = max(nadir_spread, float(np.hypot(*(c2["camera"][:2] - cam["camera"][:2]))))
    # the plane of the traced face: the measured sheet, scaled about the point under the camera
    Hobj = H
    if cam is not None and above:
        k = dist / (dist - above)
        nx, ny = float(cam["camera"][0]), float(cam["camera"][1])
        Hobj = H @ np.array([[k, 0.0, (1 - k) * nx], [0.0, k, (1 - k) * ny], [0.0, 0.0, 1.0]])
    # straighten: output pixels along the sheet's axes, turned by quarter turns to keep the
    # photo's way up; only the clear middle (as it lies at the traced face's height)
    x0, y0, x1, y1 = clear_area(paper)
    cx0, cy0, cx1, cy1 = x0 * ax, y0 * ay, x1 * ax, y1 * ay
    if cam is not None and above:
        k = dist / (dist - above)
        nx, ny = float(cam["camera"][0]), float(cam["camera"][1])
        cx0, cx1 = nx + (cx0 - nx) / k, nx + (cx1 - nx) / k
        cy0, cy1 = ny + (cy0 - ny) / k, ny + (cy1 - ny) / k
    Hi = np.linalg.inv(Hobj)
    h, w = img.shape[:2]
    c0 = T._apply(Hi, [[(w - 1) / 2.0, (h - 1) / 2.0]])[0]
    c1 = T._apply(Hi, [[(w - 1) / 2.0 + 1.0, (h - 1) / 2.0]])[0]
    s_native = float(np.hypot(*(c1 - c0)))
    up = T._apply(Hi, [[(w - 1) / 2.0, (h - 1) / 2.0 - 50.0]])[0] - c0     # the photo's "up" on the sheet
    q = int(round(math.atan2(up[0], -up[1]) / (math.pi / 2))) % 4        # 0: sheet's top is the photo's top
    s = max(s_native, max(cx1 - cx0, cy1 - cy0) / float(max_side))
    Wp, Hp = (cx1 - cx0, cy1 - cy0) if q % 2 == 0 else (cy1 - cy0, cx1 - cx0)
    Wo, Ho = int(math.ceil(Wp / s)), int(math.ceil(Hp / s))
    # output (u, v) -> sheet (X, Y) for each quarter turn
    A = {0: [[s, 0, cx0], [0, s, cy0]],
         1: [[0, -s, cx1], [s, 0, cy0]],
         2: [[-s, 0, cx1], [0, -s, cy1]],
         3: [[0, s, cx0], [-s, 0, cy1]]}[q]
    A = np.vstack([np.array(A, float), [0, 0, 1]])
    A[0, 2] += 0.5 * (A[0, 0] + A[0, 1])                       # pixel centres
    A[1, 2] += 0.5 * (A[1, 0] + A[1, 1])
    M = Hobj @ A
    shrink = s / s_native
    src = cv2.GaussianBlur(img, (0, 0), 0.45 * shrink) if shrink > 1.5 else img
    out = cv2.warpPerspective(src, M, (Wo, Ho), flags=cv2.INTER_CUBIC | cv2.WARP_INVERSE_MAP,
                              borderMode=cv2.BORDER_REPLICATE)
    Mi = np.linalg.inv(M)
    exclude = []
    stand_info = None
    if D_stand is not None:
        # leave the stand out of the trace: its top and its legs down to the paper
        D, ks, uD, cen = D_stand
        if cam is not None:
            nxy = cam["camera"][:2]
            base = nxy + (cen - nxy) / ks                      # the stand's middle on the paper
        else:
            base = cen
        ring = [base + STAND_FOOT_R * 1.25 * np.array([math.cos(a), math.sin(a)])
                for a in np.linspace(0, 2 * math.pi, 24, endpoint=False)]
        top = f["stand"].mean(0) + 1.2 * (f["stand"] - f["stand"].mean(0))     # the 40 mm top, with room
        pts = np.vstack([T._apply(Mi, T._apply(H, ring)), T._apply(Mi, top)])
        pad = 2.0 / s
        exclude.append((int(math.floor(pts[:, 0].min() - pad)), int(math.floor(pts[:, 1].min() - pad)),
                        int(math.ceil(pts[:, 0].max() + pad)) + 1, int(math.ceil(pts[:, 1].max() + pad)) + 1))
        stand_info = dict(distance_mm=float(D), k=float(ks), margin=float(uD), at_mm=[float(base[0]), float(base[1])])
    pair = None
    if backdrop is not None:
        from . import pairdiff
        try:
            fb = find(backdrop, stretch)
        except ValueError as e:
            raise ValueError("The empty-sheet photo: %s" % e)
        if fb["paper"] != paper:
            raise ValueError("The empty-sheet photo shows a %s sheet, the photo with the part a %s one." % (fb["paper"], paper))
        bimg = undistort_image(backdrop, fb["centre"], fb["R0"], fb["k1"])
        if bimg.ndim != out.ndim:
            bimg = cv2.cvtColor(bimg, cv2.COLOR_GRAY2BGR) if out.ndim == 3 else cv2.cvtColor(bimg[:, :, :3], cv2.COLOR_BGR2GRAY)
        Mb = fb["H"] @ np.linalg.inv(H) @ M                     # output px -> the paper -> the empty photo
        bh, bw = bimg.shape[:2]
        bs = float(np.hypot(*(T._apply(np.linalg.inv(fb["H"]), [[bw / 2.0 + 1, bh / 2.0]])[0] -
                              T._apply(np.linalg.inv(fb["H"]), [[bw / 2.0, bh / 2.0]])[0])))
        bsrc = cv2.GaussianBlur(bimg, (0, 0), 0.45 * s / bs) if s / bs > 1.5 else bimg
        bout = cv2.warpPerspective(bsrc, Mb, (Wo, Ho), flags=cv2.INTER_CUBIC | cv2.WARP_INVERSE_MAP,
                                   borderMode=cv2.BORDER_REPLICATE)
        stand_area = np.zeros((Ho, Wo), bool)                # the stand is left out of the counts:
        for (x0_, y0_, x1_, y1_) in exclude:                   # seen from elsewhere, its top moves
            stand_area[max(0, y0_):max(0, y1_), max(0, x0_):max(0, x1_)] = True
        out, part_m, shadow_m, pinfo = pairdiff.clean(out, bout, ignore=stand_area)
        pair = dict(markers=fb["n"], gain=pinfo["gain"], noise=pinfo["noise"], note=pinfo["note"],
                    part_mm2=pinfo["part_px"] * s * s, shadow_mm2=pinfo["shadow_px"] * s * s,
                    changed_mm2=pinfo["changed_px"] * s * s, gone_mm2=pinfo["gone_px"] * s * s,
                    area_mm2=float(Wo * Ho * s * s))
    lt = light(img, H, paper)
    res = f["resid_mm"]
    info = dict(sheet_kind="calibration", paper=paper, markers=f["n"], markers_found=f["n_found"],
                frame_total=len(frame(paper)), lens=how, lens_bend=float(f["k1"]), fit_rms_mm=float(np.sqrt(np.mean(res ** 2))),
                fit_max_mm=float(res.max()), fit_rms_before_bend_mm=float(f["rms0_mm"]),
                stretch=[float(ax), float(ay)], mm_per_source_px=s_native, stand=stand_info,
                light=lt, typed_distance_mm=typed or None, phone_distance_mm=float(phone),
                lens_distance_mm=float(lens_D) if lens_D else None, above=float(above), on_paper=True,
                side_px=float(side_px), quarter_turns=q, pair=pair)
    if cam is not None:
        nadir = T._apply(Mi, T._apply(H, [cam["camera"][:2]]))[0]
        info.update(tilt_deg=cam["tilt_deg"], camera_height_mm=float(dist), nadir_px=(float(nadir[0]), float(nadir[1])),
                    height_factor=float((dist - above) / dist),
                    distance_sensitivity=float(abs(above) / max(dist - above, 1e-9)),
                    nadir_spread_mm=nadir_spread, distance_margin=float(u))
    return dict(image=out, mm_per_px=s, exclude=exclude, M=M, info=info)
