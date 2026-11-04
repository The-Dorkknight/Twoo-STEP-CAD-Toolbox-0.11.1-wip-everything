# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""Tests for the Image → STEP tab: picture -> traced outline -> STEP (flat or with a thickness).

    python -m unittest discover -s tests -v        (needs the app's requirements)

The pictures are drawn by the test itself (a plate with a round hole, and the same plate beside
the scale token, or lying on the printed token sheet, seen from straight above). They are not
photographs.
"""
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from tostep.plugins.image2step import image2step  # noqa: E402
from tostep.plugins.mesh2step import classic as core  # noqa: E402

MM_PER_PX = 0.2
PLATE = (90.0, 60.0, 6.0, 8.0)                                 # width, height, corner radius, hole radius (mm)
PLATE_AREA = 90 * 60 - (4 - math.pi) * 6 * 6 - math.pi * 8 * 8


def _canvas(w_mm=260.0, h_mm=190.0, k=4):
    return np.full((int(h_mm / MM_PER_PX) * k, int(w_mm / MM_PER_PX) * k), 238, np.uint8), k / MM_PER_PX


def _plate(big, s, cx=150.0, cy=95.0):
    w, h, r, hole = PLATE
    for x0, y0, x1, y1 in ((cx - w / 2 + r, cy - h / 2, cx + w / 2 - r, cy + h / 2), (cx - w / 2, cy - h / 2 + r, cx + w / 2, cy + h / 2 - r)):
        cv2.rectangle(big, (round(x0 * s), round(y0 * s)), (round(x1 * s), round(y1 * s)), 50, -1)
    for sx in (-1, 1):
        for sy in (-1, 1):
            cv2.circle(big, (round((cx + sx * (w / 2 - r)) * s), round((cy + sy * (h / 2 - r)) * s)), round(r * s), 50, -1)
    cv2.circle(big, (round(cx * s), round(cy * s)), round(hole * s), 238, -1)


def drawing(path):
    """The plate alone, 0.2 mm per pixel."""
    big, s = _canvas()
    _plate(big, s)
    cv2.imwrite(path, cv2.resize(big, None, fx=0.25, fy=0.25, interpolation=cv2.INTER_AREA))
    return path


def token_photo(path, turn=25.0):
    """The plate with the scale token beside it, seen from straight above (no perspective)."""
    from tostep.plugins.image2step.imagetosketch import scaletoken
    big, s = _canvas()
    _plate(big, s)
    a = math.radians(turn)
    R = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])
    tok = (scaletoken.outline(40.0) - 20.0) @ R.T + [45.0, 55.0]
    cv2.fillPoly(big, [np.round(tok * s).astype(np.int32)], 22)
    cv2.imwrite(path, cv2.resize(big, None, fx=0.25, fy=0.25, interpolation=cv2.INTER_AREA))
    return path


def sheet_photo(path):
    """The plate lying on the printed token sheet (A4), seen from straight above."""
    from tostep.plugins.image2step.imagetosketch import scaletoken
    page = scaletoken.render_page("A4", MM_PER_PX)
    big, s = _canvas(210.0, 297.0)
    big[:] = 255
    _plate(big, s, 105.0, 150.0)
    plate = cv2.resize(big, (page.shape[1], page.shape[0]), interpolation=cv2.INTER_AREA)
    cv2.imwrite(path, np.minimum(page, plate))
    return path


def calsheet_photo(path):
    """The plate lying in the middle of the calibration sheet's page 1 (A4, ImageToSketch 0.10),
    seen from straight above; no stand."""
    from tostep.plugins.image2step.imagetosketch import calsheet
    page = calsheet.render_page("A4", MM_PER_PX)
    big, s = _canvas(210.0, 297.0)
    big[:] = 255
    _plate(big, s, 105.0, 150.0)
    plate = cv2.resize(big, (page.shape[1], page.shape[0]), interpolation=cv2.INTER_AREA)
    cv2.imwrite(path, np.minimum(page, plate))
    return path


def shadow_photos(d):
    """The plate on the calibration sheet with a hard shadow beside it, and the same sheet without
    the part (ImageToSketch 0.10.1's empty-sheet photo); faint printer banding on both."""
    from tostep.plugins.image2step.imagetosketch import calsheet
    page = calsheet.render_page("A4", MM_PER_PX)

    def photo(with_part):
        big, s = _canvas(210.0, 297.0)
        big[:] = 255
        for y in range(0, big.shape[0], 40):
            big[y:y + 6, :] = 225
        if with_part:
            w, h = PLATE[:2]
            cv2.rectangle(big, (round((105 - w / 2 + 6) * s), round((150 - h / 2 + 5) * s)),
                          (round((105 + w / 2 + 6) * s), round((150 + h / 2 + 5) * s)), 150, -1)
            _plate(big, s, 105.0, 150.0)
        pl = cv2.resize(big, (page.shape[1], page.shape[0]), interpolation=cv2.INTER_AREA)
        return np.minimum(page, pl)
    a, b = os.path.join(d, "part.png"), os.path.join(d, "empty.png")
    cv2.imwrite(a, photo(True))
    cv2.imwrite(b, photo(False))
    return a, b


def read(step):
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.TopAbs import TopAbs_FACE, TopAbs_SOLID
    sh = core.read_step(step)
    v, a = GProp_GProps(), GProp_GProps()
    BRepGProp.VolumeProperties_s(sh, v)
    BRepGProp.SurfaceProperties_s(sh, a, 1e-6)
    return dict(valid=BRepCheck_Analyzer(sh).IsValid(), solids=sum(1 for _ in core._explore(sh, TopAbs_SOLID)),
                faces=sum(1 for _ in core._explore(sh, TopAbs_FACE)), volume=v.Mass(), area=a.Mass())


def cli(*args, **kw):
    return subprocess.run([sys.executable, os.path.join(ROOT, "run_tostep.py"), *args], capture_output=True,
                          text=True, timeout=600, **kw)


class Image(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.addCleanup(self.d.cleanup)
        self.png = drawing(os.path.join(self.d.name, "plate.png"))
        self.out = os.path.join(self.d.name, "plate.step")

    def test_flat_outline_needs_no_extrusion(self):
        """Thickness 0: the outline goes into the STEP as a face (with its hole), not as a solid."""
        rep = image2step.convert(self.png, self.out, {"scale": "mm_per_px", "mm_per_px": MM_PER_PX})
        self.assertEqual((rep.outlines, rep.holes, rep.step["kind"], rep.step["pieces"]), (2, 1, "face", 1))
        back = read(self.out)
        self.assertTrue(back["valid"])
        self.assertEqual((back["solids"], back["faces"]), (0, 1))
        self.assertAlmostEqual(back["area"] / PLATE_AREA, 1.0, delta=0.004)
        self.assertIn("Flat shape, no thickness", rep.summary())

    def test_thickness_gives_a_closed_solid(self):
        rep = image2step.convert(self.png, self.out, {"scale": "mm_per_px", "mm_per_px": MM_PER_PX, "thickness": 6})
        self.assertTrue(rep.is_solid)
        back = read(self.out)
        self.assertTrue(back["valid"])
        self.assertEqual(back["solids"], 1)
        self.assertAlmostEqual(back["volume"] / (PLATE_AREA * 6.0), 1.0, delta=0.004)
        self.assertIn("Closed solid: yes", rep.summary())
        self.assertIn("1 solid, 6.00 mm thick", rep.summary())

    def test_curves_only(self):
        rep = image2step.convert(self.png, self.out, {"scale": "mm_per_px", "mm_per_px": MM_PER_PX, "curves_only": True})
        self.assertEqual(rep.step["kind"], "wire")
        self.assertEqual(read(self.out)["faces"], 0)

    def test_token_sets_the_scale(self):
        photo = token_photo(os.path.join(self.d.name, "photo.png"))
        self.assertEqual(image2step.guess(photo).get("scale"), "token")
        self.assertEqual(image2step.guess(self.png), {})
        rep = image2step.convert(photo, self.out, {"scale": "token", "token_side": 40, "token_thickness": 0, "thickness": 5})
        self.assertAlmostEqual(rep.mm_per_px / MM_PER_PX, 1.0, delta=0.004)
        self.assertEqual(rep.outlines, 2)                      # the plate and its hole; the token is not traced
        self.assertAlmostEqual(read(self.out)["volume"] / (PLATE_AREA * 5.0), 1.0, delta=0.01)
        with self.assertRaises(ValueError) as e:               # a drawing without a token
            image2step.convert(self.png, self.out, {"scale": "token"})
        self.assertIn("No scale token found", str(e.exception))

    def test_calibration_sheet_sets_the_scale(self):
        photo = calsheet_photo(os.path.join(self.d.name, "cal.png"))
        g = image2step.guess(photo)
        self.assertEqual(g.get("scale"), "calsheet")
        self.assertIn("40 frame markers", g["note"])
        rep = image2step.convert(photo, self.out, {"scale": "calsheet", "thickness": 5})
        self.assertAlmostEqual(rep.mm_per_px / MM_PER_PX, 1.0, delta=0.004)
        self.assertEqual(rep.outlines, 2)                      # the plate and its hole; no marker is traced
        self.assertAlmostEqual(read(self.out)["volume"] / (PLATE_AREA * 5.0), 1.0, delta=0.01)

    def test_empty_sheet_photo_takes_the_shadow_out(self):
        part, empty = shadow_photos(self.d.name)
        one = image2step.convert(part, self.out, {"scale": "calsheet", "thickness": 5})
        two = image2step.convert(part, self.out, {"scale": "calsheet", "thickness": 5, "empty_sheet": empty})
        self.assertEqual((one.outlines, two.outlines), (2, 2))
        self.assertLess(two.borderline, one.borderline)        # the shadow's outline is no longer borderline
        self.assertAlmostEqual(read(self.out)["volume"] / (PLATE_AREA * 5.0), 1.0, delta=0.01)
        with self.assertRaises(ValueError):
            image2step.convert(part, self.out, {"scale": "calsheet", "empty_sheet": empty + ".gone"})

    def test_token_sheet_printed_on_paper(self):
        """The default kind of token: the sheet with a token in two corners and small print."""
        photo = sheet_photo(os.path.join(self.d.name, "sheet.png"))
        self.assertEqual(image2step.guess(photo).get("scale"), "token")
        lines = []
        rep = image2step.convert(photo, self.out, {"scale": "token", "token_kind": "paper", "thickness": 4},
                                 log=lines.append)
        self.assertAlmostEqual(rep.mm_per_px / MM_PER_PX, 1.0, delta=0.002)
        self.assertEqual((rep.outlines, rep.borderline), (2, 0))      # neither token nor small print traced
        self.assertAlmostEqual(read(self.out)["volume"] / (PLATE_AREA * 4.0), 1.0, delta=0.006)
        self.assertIn("Both tokens of the printed A4 sheet found", "\n".join(lines))
        self.assertEqual(image2step.options({"scale": "token"}).token_thickness_mm, 0.0)
        self.assertEqual(image2step.options({"scale": "token", "token_kind": "plastic"}).token_thickness_mm, 1.2)
        self.assertEqual(image2step.options({"scale": "token", "token_kind": "paper", "token_thickness": 3}).token_thickness_mm, 0.0)
        with self.assertRaises(ValueError):
            image2step.options({"scale": "token", "token_kind": "wood"})

    def test_preview_is_a_picture_with_a_summary(self):
        png, info = image2step.preview(self.png, {"scale": "mm_per_px", "mm_per_px": MM_PER_PX})
        self.assertTrue(png.startswith("data:image/png;base64,") and len(png) > 5000)
        self.assertIn("2 outlines (1 hole)", info)

    def test_bad_input_is_explained(self):
        with self.assertRaises(ValueError):
            image2step.options({"scale": "mm_per_px", "mm_per_px": "abc"})
        with self.assertRaises(ValueError):
            image2step.options({"scale": "nonsense"})
        blank = os.path.join(self.d.name, "blank.png")
        cv2.imwrite(blank, np.full((300, 400), 230, np.uint8))
        with self.assertRaises(ValueError) as e:
            image2step.convert(blank, self.out, {"scale": "mm_per_px", "mm_per_px": 0.2})
        self.assertIn("Nothing in the picture", str(e.exception))

    def test_printables(self):
        stl = image2step.token_stl(40, 1.2)
        self.assertEqual(len(stl), 84 + 50 * int.from_bytes(stl[80:84], "little"))
        self.assertIn(b"scale token", stl[:80])
        self.assertTrue(image2step.sheet_pdf("A4").startswith(b"%PDF"))
        with self.assertRaises(ValueError):
            image2step.sheet_pdf("A0")
        pdf = image2step.token_pdf("Letter", 40)
        self.assertTrue(pdf.startswith(b"%PDF") and b"scale token, 40 mm" in pdf)
        with self.assertRaises(ValueError):
            image2step.token_pdf("A4", 500)

    def test_command_line(self):
        r = cli("image", self.png, self.out, "--mm-per-px", str(MM_PER_PX), "--thickness", "3")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Closed solid: yes", r.stdout)
        self.assertAlmostEqual(read(self.out)["volume"] / (PLATE_AREA * 3.0), 1.0, delta=0.004)
        r = cli("image", self.png, "--mm-per-px", str(MM_PER_PX), "--machine")          # default name, flat
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("@@DONE " + os.path.splitext(self.png)[0] + ".step", r.stdout)
        r = cli("image", self.png, self.out)                                           # token mode, no token
        self.assertEqual(r.returncode, 1)
        self.assertIn("No scale token found", r.stdout)
        tok = os.path.join(self.d.name, "t.stl")
        self.assertEqual(cli("image", "--make-token", tok, "--token-side", "30").returncode, 0)
        self.assertGreater(os.path.getsize(tok), 1000)
        pdf = os.path.join(self.d.name, "t.pdf")
        self.assertEqual(cli("image", "--make-token-sheet", pdf, "--paper", "Letter").returncode, 0)
        self.assertEqual(open(pdf, "rb").read(5), b"%PDF-")
        self.assertEqual(cli("image", "--make-token-sheet", pdf, "--token-side", "300").returncode, 2)


def icon(path, n=256):
    """A drawn icon: a rounded square that fills the picture edge to edge (transparent corners)
    with a round hole. ImageToSketch 0.5 left the square out as "cut off by the edge" and
    wrote only the hole, as a disc."""
    img = np.zeros((n, n, 4), np.uint8)
    mask = np.zeros((n, n), np.uint8)
    r = 48
    cv2.rectangle(mask, (r, 0), (n - 1 - r, n - 1), 255, -1)
    cv2.rectangle(mask, (0, r), (n - 1, n - 1 - r), 255, -1)
    for cx, cy in ((r, r), (n - 1 - r, r), (r, n - 1 - r), (n - 1 - r, n - 1 - r)):
        cv2.circle(mask, (cx, cy), r, 255, -1)
    img[..., :3] = (40, 90, 200)
    img[..., 3] = mask
    cv2.circle(img, (n // 2, n // 2), 50, (255, 255, 255, 255), -1)
    cv2.imwrite(path, img)
    return path


class Tracer07(unittest.TestCase):
    """What came with ImageToSketch 0.7: the shape that fills the picture, borderline shapes,
    SVG / DXF copies, and the library's own front door."""
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.addCleanup(self.d.cleanup)
        self.src = icon(os.path.join(self.d.name, "icon.png"))
        self.out = os.path.join(self.d.name, "icon.step")

    def size(self):
        from OCP.Bnd import Bnd_Box
        from OCP.BRepBndLib import BRepBndLib
        bb = Bnd_Box()
        BRepBndLib.Add_s(core.read_step(self.out), bb)
        a, b = bb.CornerMin(), bb.CornerMax()
        return b.X() - a.X(), b.Y() - a.Y(), b.Z() - a.Z()

    def test_tracer_is_0_7_and_reached_through_its_front_door(self):
        from tostep.plugins.image2step import imagetosketch
        from tostep.plugins.image2step.imagetosketch import service
        self.assertGreaterEqual(tuple(int(x) for x in imagetosketch.__version__.split(".")[:2]), (0, 7))
        self.assertTrue(callable(service.export) and callable(service.options))
        self.assertRegex(image2step.tracer_version(), r"^\d+\.\d+\.\d+")

    def test_shape_that_fills_the_picture_is_traced(self):
        rep = image2step.convert(self.src, self.out, dict(scale="image_width", width_mm=50, thickness=2))
        self.assertEqual((rep.outlines, rep.holes), (2, 1))
        self.assertTrue(rep.is_solid)
        w, h, t = self.size()
        self.assertAlmostEqual(w, 50.0, delta=0.6)
        self.assertAlmostEqual(h, 50.0, delta=0.6)
        self.assertAlmostEqual(t, 2.0, delta=1e-6)

    def test_picture_edge_cut_leaves_it_out(self):
        rep = image2step.convert(self.src, self.out, dict(scale="image_width", width_mm=50, thickness=2,
                                                          picture_edge="cut"))
        self.assertEqual(rep.outlines, 1)
        self.assertLess(self.size()[0], 25.0)

    def test_unknown_choices_fall_back_and_bad_numbers_are_reported(self):
        o = image2step.options(dict(scale="image_width", width_mm=50, picture_edge="sideways", tone="", line_art=None))
        self.assertEqual((o.picture_edge, o.polarity, o.line_art), ("auto", "auto", "auto"))
        with self.assertRaises(ValueError):
            image2step.options(dict(scale="image_width", width_mm="wide"))

    def test_svg_and_dxf_copies(self):
        extras = os.path.join(self.d.name, "Output")
        os.makedirs(extras)
        log = []
        rep = image2step.convert(self.src, self.out, dict(scale="image_width", width_mm=50, also_svg=True,
                                                          also_dxf="true", borderline=True,
                                                          extras_dir=extras, extras_stem="icon"), log=log.append)
        self.assertEqual(sorted(os.path.basename(e) for e in rep.extras), ["icon.dxf", "icon.svg"])
        self.assertIn("<svg", open(os.path.join(extras, "icon.svg"), encoding="utf-8").read(400))
        self.assertIn("SECTION", open(os.path.join(extras, "icon.dxf"), encoding="utf-8").read(200))
        self.assertIn("Also saved: icon.svg", rep.summary())
        self.assertFalse(rep.borderline_used and not rep.borderline)

    def test_command_line_options(self):
        r = cli("image", self.src, self.out, "--width-mm", "50", "--thickness", "2", "--picture-edge", "edge",
                "--also-svg", "--include-borderline")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(os.path.exists(os.path.splitext(self.out)[0] + ".svg"))
        self.assertIn("Outlines traced: 2", r.stdout)


def colour_icon(path, n=384, k=4):
    """A drawn icon with several colours, like the one this was reported with: a grey rounded
    square to the picture's edge (transparent corners), a yellow ring on its floor, a cube in
    three shades of blue standing on the ring's far corner. Drawn k times its size and reduced."""
    N = n * k
    img = np.zeros((N, N, 4), np.uint8)

    def P(pts):
        return (np.array(pts, float) * N).round().astype(np.int32)

    mask = np.zeros((N, N), np.uint8)
    r = int(0.19 * N)
    cv2.rectangle(mask, (r, 0), (N - 1 - r, N - 1), 255, -1)
    cv2.rectangle(mask, (0, r), (N - 1, N - 1 - r), 255, -1)
    for cx, cy in ((r, r), (N - 1 - r, r), (r, N - 1 - r), (N - 1 - r, N - 1 - r)):
        cv2.circle(mask, (cx, cy), r, 255, -1)
    img[mask > 0] = (72, 66, 62, 255)
    ring = np.zeros((N, N), np.uint8)
    cv2.fillPoly(ring, [P([(0.50, 0.44), (0.90, 0.63), (0.50, 0.82), (0.10, 0.63)])], 255)
    cv2.fillPoly(ring, [P([(0.50, 0.475), (0.83, 0.63), (0.50, 0.785), (0.17, 0.63)])], 0)
    img[ring > 0] = (78, 196, 242, 255)
    for poly, col in (([(0.50, 0.25), (0.685, 0.335), (0.50, 0.42), (0.315, 0.335)], (238, 214, 120)),
                      ([(0.315, 0.335), (0.50, 0.42), (0.50, 0.70), (0.315, 0.615)], (216, 176, 62)),
                      ([(0.50, 0.42), (0.685, 0.335), (0.685, 0.615), (0.50, 0.70)], (190, 146, 44))):
        cv2.fillPoly(img, [P(poly)], col + (255,))
    cv2.imwrite(path, cv2.resize(img, (n, n), interpolation=cv2.INTER_AREA))
    return path


class Transitions(unittest.TestCase):
    """EXPERIMENTAL "every colour change" tracing (ImageToSketch 0.8): each colour area of a
    picture gets its own outline and becomes its own face or solid. Off unless asked for."""
    AREA = (1.0 - (4 - math.pi) * 0.19 ** 2) * 50.0 ** 2      # the rounded square, 50 mm wide

    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.addCleanup(self.d.cleanup)
        self.src = colour_icon(os.path.join(self.d.name, "icon.png"))
        self.out = os.path.join(self.d.name, "icon.step")

    def test_weld_option_reaches_the_tracer(self):
        from tostep.plugins.image2step import image2step
        self.assertTrue(image2step.options({"find": "transitions"}).weld_edges)
        self.assertFalse(image2step.options({"find": "transitions", "weld_edges": False}).weld_edges)
        self.assertFalse(image2step.options({"find": "transitions", "weld_edges": "off"}).weld_edges)

    def test_the_default_is_unchanged_and_says_what_it_leaves_out(self):
        log = []
        rep = image2step.convert(self.src, self.out, dict(scale="image_width", width_mm=50, thickness=2), log=log.append)
        self.assertEqual(rep.areas, 0)
        self.assertLess(rep.outlines, 7)                       # the cube's faces are not parted
        self.assertIn("Every colour change", "\n".join(log))  # ... and the log says how to get them
        self.assertNotIn("colour areas", rep.summary())

    def test_every_colour_area_becomes_its_own_solid(self):
        log = []
        rep = image2step.convert(self.src, self.out, dict(scale="image_width", width_mm=50, thickness=2,
                                                          find="transitions"), log=log.append)
        self.assertEqual((rep.outlines, rep.holes, rep.areas), (7, 1, 6), "\n".join(log))
        self.assertEqual(rep.step["pieces"], 6)                # square, floor, ring, three faces
        self.assertTrue(rep.is_solid)                          # six closed solids, read back
        self.assertAlmostEqual(rep.step["volume_mm3"], self.AREA * 2.0, delta=0.01 * self.AREA * 2.0)
        self.assertIn("6 colour areas", rep.summary())
        self.assertIn("Transition mode (experimental)", "\n".join(log))

    def test_flat_faces_and_the_drawings_beside_them(self):
        rep = image2step.convert(self.src, self.out, dict(scale="image_width", width_mm=50, find="transitions",
                                                          also_svg=True, also_dxf=True, shared_edges="both"))
        self.assertEqual((rep.step["kind"], rep.step["pieces"]), ("face", 6))
        self.assertAlmostEqual(rep.step["area_mm2"], self.AREA, delta=0.01 * self.AREA)
        svg = open(os.path.splitext(self.out)[0] + ".svg", encoding="utf-8").read()
        self.assertEqual(svg.count("<path"), 7)
        self.assertEqual(svg.count(' Z"'), 7)                  # "both": every outline closed
        image2step.convert(self.src, self.out, dict(scale="image_width", width_mm=50, find="transitions",
                                                    also_svg=True))
        once = open(os.path.splitext(self.out)[0] + ".svg", encoding="utf-8").read()
        self.assertLess(once.count(' Z"'), once.count("<path"))   # "once": shared edges are not repeated

    def test_choices_and_fallbacks(self):
        o = image2step.options(dict(scale="image_width", width_mm=50))
        self.assertEqual((o.find, o.colour_step, o.shared_edges), ("tone", 0.0, "once"))
        o = image2step.options(dict(scale="image_width", width_mm=50, find="transitions", colour_step="10",
                                    shared_edges="both"))
        self.assertEqual((o.find, o.colour_step, o.shared_edges), ("transitions", 10.0, "both"))
        o = image2step.options(dict(scale="image_width", width_mm=50, find="everything", colour_step="",
                                    shared_edges=None))
        self.assertEqual((o.find, o.colour_step, o.shared_edges), ("tone", 0.0, "once"))
        with self.assertRaises(ValueError):
            image2step.options(dict(scale="image_width", width_mm=50, colour_step="lots"))

    def test_preview_and_command_line(self):
        url, text = image2step.preview(self.src, dict(scale="image_width", width_mm=50, find="transitions"))
        self.assertTrue(url.startswith("data:image/png;base64,"))
        self.assertIn("7 outlines of 6 colour areas", text)
        r = cli("image", self.src, self.out, "--width-mm", "50", "--thickness", "2", "--find", "transitions",
                "--colour-step", "6")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("6 colour areas", r.stdout)
        self.assertIn("6 solids", r.stdout)


class Server(unittest.TestCase):
    """The Image → STEP endpoints, with a real server and a real worker process."""

    @classmethod
    def setUpClass(cls):
        from tostep.frontends.web import server as webui
        cls.tmp = tempfile.mkdtemp()
        cls.app = webui.App(os.path.join(cls.tmp, "work"), os.path.join(cls.tmp, "out"))
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), webui.make_handler(cls.app, webui.build_page(cls.app.reg)))
        cls.httpd.daemon_threads = True
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        cls.png = drawing(os.path.join(cls.tmp, "plate.png"))

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def call(self, path, body=None, raw=None, binary=False):
        req = urllib.request.Request(self.url + path, data=raw if raw is not None else (None if body is None else json.dumps(body).encode()),
                                     headers={"X-Token": self.app.token, "Content-Type": "application/json"},
                                     method="GET" if body is None and raw is None else "POST")
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                data = r.read()
                return r.status, (data if binary else json.loads(data or b"{}"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def wait(self, jid):
        for _ in range(900):
            code, j = self.call("/api/job/" + jid)
            if j["state"] in ("done", "error", "cancelled"):
                return j
            time.sleep(0.2)
        self.fail("job did not finish")

    def upload(self, name="plate.png"):
        with open(self.png, "rb") as f:
            code, up = self.call("/api/upload?name=" + urllib.parse.quote(name), raw=f.read())
        self.assertEqual(code, 200)
        return up["id"]

    def test_page_has_the_tab(self):
        html = urllib.request.urlopen(self.url + "/").read().decode()
        for t in ("p-img", "Image → STEP", "im-thick", "imRun()", 'value="calsheet"', "im-standh", "imEmptyPick()"):
            self.assertIn(t, html)

    def test_preview_then_convert_to_a_solid(self):
        fid = self.upload()
        params = {"scale": "mm_per_px", "mm_per_px": MM_PER_PX, "thickness": 4}
        code, g = self.call("/api/image/guess", {"file": fid})
        self.assertEqual((code, g), (200, {}))
        code, pv = self.call("/api/image/preview", {"file": fid, "params": params})
        self.assertEqual(code, 200, pv)
        self.assertTrue(pv["png"].startswith("data:image/png;base64,"))
        self.assertIn("2 outlines (1 hole)", pv["info"])
        code, r = self.call("/api/run", {"kind": "image", "file": fid, "params": params})
        self.assertEqual(code, 200, r)
        j = self.wait(r["job"])
        self.assertEqual(j["state"], "done", j.get("error"))
        self.assertEqual((j["kind"], j["filename"]), ("image", "plate.step"))
        self.assertIn("Closed solid: yes", j["summary"])
        self.assertTrue(j["saved_to"].startswith(os.path.join(self.tmp, "out")))
        self.assertAlmostEqual(read(j["saved_to"])["volume"] / (PLATE_AREA * 4.0), 1.0, delta=0.004)
        code, data = self.call(f"/api/download/{r['job']}", binary=True)
        self.assertTrue(code == 200 and data.startswith(b"ISO-10303-21"))
        code, p3 = self.call(f"/api/preview3d/{r['job']}?which=result")
        self.assertEqual(code, 200)
        self.assertGreater(p3["tris"], 10)
        code, e = self.call(f"/api/preview3d/{r['job']}?which=mesh")
        self.assertEqual(code, 400)
        code, last = self.call("/api/last")                    # a solid from a picture can go to Jetwash too
        self.assertEqual(last.get("job"), r["job"])

    def test_flat_job_and_its_preview(self):
        fid = self.upload("flat plate.png")
        code, r = self.call("/api/run", {"kind": "image", "file": fid, "params": {"scale": "mm_per_px", "mm_per_px": MM_PER_PX}})
        j = self.wait(r["job"])
        self.assertEqual(j["state"], "done", j.get("error"))
        self.assertIn("Flat shape, no thickness", j["summary"])
        code, p3 = self.call(f"/api/preview3d/{r['job']}?which=result")
        self.assertEqual(code, 200, p3)

    def test_errors_come_back_as_messages(self):
        fid = self.upload()
        code, r = self.call("/api/run", {"kind": "image", "file": fid, "params": {"scale": "token"}})
        j = self.wait(r["job"])
        self.assertEqual(j["state"], "error")
        self.assertIn("No scale token found", j["error"])
        code, e = self.call("/api/run", {"kind": "image", "file": fid, "params": {"scale": "mm_per_px", "mm_per_px": "x"}})
        self.assertEqual(code, 400)
        code, up = self.call("/api/upload?name=part.stl", raw=b"solid x")
        code, e = self.call("/api/run", {"kind": "image", "file": up["id"], "params": {}})
        self.assertEqual(code, 400)
        code, e = self.call("/api/image/preview", {"file": up["id"], "params": {}})
        self.assertEqual(code, 400)
        code, e = self.call("/api/image/preview", {"file": "nope", "params": {}})
        self.assertEqual(code, 400)

    def test_printable_downloads(self):
        code, stl = self.call(f"/api/image/token?t={self.app.token}&side=30&thick=1", binary=True)
        self.assertTrue(code == 200 and len(stl) == 84 + 50 * int.from_bytes(stl[80:84], "little"))
        code, pdf = self.call(f"/api/image/sheet?t={self.app.token}&paper=Letter", binary=True)
        self.assertTrue(code == 200 and pdf.startswith(b"%PDF"))
        code, e = self.call("/api/image/sheet?paper=A0")
        self.assertEqual(code, 400)
        code, pdf = self.call(f"/api/image/tokensheet?t={self.app.token}&paper=A4&side=40", binary=True)
        self.assertTrue(code == 200 and pdf.startswith(b"%PDF") and b"scale token" in pdf)
        code, e = self.call("/api/image/tokensheet?paper=A4&side=900")
        self.assertEqual(code, 400)
        code, pdf = self.call(f"/api/image/calsheet?t={self.app.token}&paper=Letter", binary=True)
        self.assertTrue(code == 200 and pdf.startswith(b"%PDF"))
        code, stl = self.call(f"/api/image/stand?t={self.app.token}", binary=True)
        self.assertTrue(code == 200 and len(stl) == 84 + 50 * int.from_bytes(stl[80:84], "little"))
        req = urllib.request.Request(self.url + "/api/image/token")             # no token: refused
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req)
        self.assertEqual(cm.exception.code, 403)


if __name__ == "__main__":
    unittest.main()
