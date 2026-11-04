# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
# Copied from the ImageToSketch project by tools/sync_imagetosketch.py - edit it there.
"""Reading picture files robustly.

Things that go wrong with a plain cv2.imread():
  * phone JPEGs are stored sideways with an EXIF "orientation" note - viewers rotate them, a raw
    read does not, so the trace would come out rotated relative to what the user sees;
  * paths with accents or other non-ASCII characters fail on Windows;
  * 16-bit scans, transparent PNGs and 1-bit images need normalising;
  * flatbed scans carry their resolution (DPI) in the file, which is an exact scale for free.
"""
import os
import struct

import cv2
import numpy as np

_ORIENT = 0x0112


def _read_bytes(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError as e:
        raise ValueError("Could not open %s (%s)" % (path, e.strerror or e))


def _exif_ifd0(app1):
    """Yield (tag, type, count, value_bytes, endian, tiff) for IFD0 of an Exif APP1 payload."""
    if not app1.startswith(b"Exif\x00\x00"):
        return
    t = app1[6:]
    if len(t) < 8 or t[:2] not in (b"II", b"MM"):
        return
    e = "<" if t[:2] == b"II" else ">"
    off = struct.unpack(e + "I", t[4:8])[0]
    if off + 2 > len(t):
        return
    n = struct.unpack(e + "H", t[off:off + 2])[0]
    for i in range(n):
        p = off + 2 + 12 * i
        if p + 12 > len(t):
            return
        tag, typ, cnt = struct.unpack(e + "HHI", t[p:p + 8])
        yield tag, typ, cnt, t[p + 8:p + 12], e, t


def _jpeg_segments(b):
    i = 2
    while i + 4 <= len(b) and b[i] == 0xFF:
        marker = b[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        ln = struct.unpack(">H", b[i + 2:i + 4])[0]
        yield marker, b[i + 4:i + 2 + ln]
        if marker == 0xDA:                                     # start of scan: no more headers
            return
        i += 2 + ln


def _rational(e, t, raw):
    off = struct.unpack(e + "I", raw)[0]
    if off + 8 > len(t):
        return None
    num, den = struct.unpack(e + "II", t[off:off + 8])
    return num / den if den else None


def dpi_from_bytes(b):
    """Resolution stored in a PNG, JPEG, BMP or TIFF, in dots per inch (None if absent or
    meaningless). Only square pixels are accepted."""
    try:
        if b[:8] == b"\x89PNG\r\n\x1a\n":
            i = 8
            while i + 12 <= len(b):
                ln, typ = struct.unpack(">I4s", b[i:i + 8])
                if typ == b"pHYs" and ln == 9:
                    x, y, unit = struct.unpack(">IIB", b[i + 8:i + 17])
                    if unit == 1 and x > 0 and abs(x - y) <= 0.01 * x:
                        return x * 0.0254
                    return None
                if typ == b"IDAT":
                    return None
                i += 12 + ln
        elif b[:2] == b"\xff\xd8":
            jfif = exif = None
            for marker, seg in _jpeg_segments(b):
                if marker == 0xE0 and seg[:5] == b"JFIF\x00" and len(seg) >= 12:
                    unit, x, y = struct.unpack(">BHH", seg[7:12])
                    if unit in (1, 2) and x > 1 and abs(x - y) <= 0.01 * x:
                        jfif = x * (1.0 if unit == 1 else 2.54)
                elif marker == 0xE1:
                    xr = unit = None
                    for tag, typ, cnt, raw, e, t in _exif_ifd0(seg):
                        if tag == 0x011A and typ == 5:
                            xr = _rational(e, t, raw)
                        elif tag == 0x0128 and typ == 3:
                            unit = struct.unpack(e + "H", raw[:2])[0]
                    if xr and xr > 1:
                        exif = xr * (2.54 if unit == 3 else 1.0)
            # cameras write a meaningless 72 dpi; a scanner writes its real resolution
            for v in (jfif, exif):
                if v and abs(v - 72) > 0.5 and abs(v - 96) > 0.5:
                    return v
            return None
        elif b[:2] == b"BM" and len(b) >= 46:
            x, y = struct.unpack("<ii", b[38:46])
            if x > 0 and abs(x - y) <= 0.01 * x:
                return x * 0.0254
        elif b[:4] in (b"II*\x00", b"MM\x00*"):
            e = "<" if b[:2] == b"II" else ">"
            off = struct.unpack(e + "I", b[4:8])[0]
            n = struct.unpack(e + "H", b[off:off + 2])[0]
            xr = None
            unit = 2
            for i in range(n):
                p = off + 2 + 12 * i
                tag, typ, cnt = struct.unpack(e + "HHI", b[p:p + 8])
                if tag == 0x011A and typ == 5:
                    xr = _rational(e, b, b[p + 8:p + 12])
                elif tag == 0x0128 and typ == 3:
                    unit = struct.unpack(e + "H", b[p + 8:p + 10])[0]
            if xr and xr > 1 and unit in (2, 3):
                return xr * (2.54 if unit == 3 else 1.0)
    except (struct.error, IndexError, ValueError):
        return None
    return None


def read_dpi(path):
    return dpi_from_bytes(_read_bytes(path))


def _ifd(t, e, off):
    """Entries of one TIFF directory: {tag: (type, count, 4 raw value bytes)}."""
    out = {}
    if off + 2 > len(t):
        return out
    n = struct.unpack(e + "H", t[off:off + 2])[0]
    for i in range(n):
        p = off + 2 + 12 * i
        if p + 12 > len(t):
            break
        tag, typ, cnt = struct.unpack(e + "HHI", t[p:p + 8])
        out[tag] = (typ, cnt, t[p + 8:p + 12])
    return out


def _num(e, t, entry):
    typ, cnt, raw = entry
    if typ == 3:
        return float(struct.unpack(e + "H", raw[:2])[0])
    if typ == 4:
        return float(struct.unpack(e + "I", raw)[0])
    if typ == 5:
        return _rational(e, t, raw)
    return None


def lens_from_bytes(b):
    """What a JPEG's EXIF says about the lens: dict with any of
    focal_35mm (the "35 mm equivalent" focal length), focal_mm, zoom (digital zoom ratio),
    width, height (the picture size the camera recorded). Empty dict if there is none."""
    out = {}
    try:
        if b[:2] != b"\xff\xd8":
            return out
        for marker, seg in _jpeg_segments(b):
            if marker != 0xE1 or not seg.startswith(b"Exif\x00\x00"):
                continue
            t = seg[6:]
            if len(t) < 8 or t[:2] not in (b"II", b"MM"):
                continue
            e = "<" if t[:2] == b"II" else ">"
            ifd0 = _ifd(t, e, struct.unpack(e + "I", t[4:8])[0])
            if 0x8769 not in ifd0:
                continue
            sub = _ifd(t, e, struct.unpack(e + "I", ifd0[0x8769][2])[0])
            for tag, key in ((0xA405, "focal_35mm"), (0x920A, "focal_mm"), (0xA404, "zoom"),
                             (0xA002, "width"), (0xA003, "height")):
                if tag in sub:
                    v = _num(e, t, sub[tag])
                    if v and v > 0:
                        out[key] = v
            break
    except (struct.error, IndexError, ValueError):
        return {}
    return out


def read_lens(image):
    """lens_from_bytes for a file path; {} for an array or an unreadable file."""
    if isinstance(image, np.ndarray):
        return {}
    try:
        return lens_from_bytes(_read_bytes(os.fspath(image)))
    except ValueError:
        return {}


def write_jpeg_with_lens(path, img, focal_35mm, quality=92):
    """Write a JPEG carrying a 35 mm-equivalent focal length in EXIF (used by the tests and the
    simulated examples; real cameras write this themselves)."""
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        return False
    b = buf.tobytes()
    h, w = img.shape[:2]
    # TIFF header, IFD0 with one entry (pointer to the Exif directory), then that directory
    ifd0 = struct.pack("<H", 1) + struct.pack("<HHII", 0x8769, 4, 1, 26) + struct.pack("<I", 0)
    sub = struct.pack("<H", 3) + struct.pack("<HHIHH", 0xA405, 3, 1, int(round(focal_35mm)), 0) \
        + struct.pack("<HHII", 0xA002, 4, 1, w) + struct.pack("<HHII", 0xA003, 4, 1, h) + struct.pack("<I", 0)
    tiff = b"II*\x00" + struct.pack("<I", 8) + ifd0 + sub
    app1 = b"Exif\x00\x00" + tiff
    seg = b"\xff\xe1" + struct.pack(">H", len(app1) + 2) + app1
    with open(path, "wb") as f:
        f.write(b[:2] + seg + b[2:])
    return True


def load_image(image):
    """Return (uint8 array: grey, BGR or BGRA; dpi or None). Accepts a path or an array."""
    if isinstance(image, np.ndarray):
        img, dpi = image, None
    else:
        path = os.fspath(image)
        data = _read_bytes(path)
        if not data:
            raise ValueError("%s is empty." % path)
        dpi = dpi_from_bytes(data)
        buf = np.frombuffer(data, np.uint8)
        # IMREAD_UNCHANGED keeps alpha and bit depth but ignores EXIF rotation; for JPEG (never
        # has alpha) use the flags that honour it.
        flags = (cv2.IMREAD_ANYCOLOR | cv2.IMREAD_ANYDEPTH) if data[:2] == b"\xff\xd8" else cv2.IMREAD_UNCHANGED
        try:
            img = cv2.imdecode(buf, flags)
        except cv2.error:
            img = None
        if img is None:
            raise ValueError("Could not read %s as a picture (supported: PNG, JPEG, BMP, TIFF, WebP)." % path)
    if img.ndim == 3 and img.shape[2] == 1:
        img = img[:, :, 0]
    if img.ndim not in (2, 3) or (img.ndim == 3 and img.shape[2] not in (3, 4)):
        raise ValueError("Unsupported picture layout %s." % (img.shape,))
    if img.dtype != np.uint8:
        if img.dtype == bool:
            img = img.astype(np.uint8) * 255
        else:                                                  # 16-bit / float: stretch what is used
            f = img.astype(np.float32)
            lo, hi = float(f.min()), float(f.max())
            img = np.zeros(img.shape, np.uint8) if hi <= lo else ((f - lo) * (255.0 / (hi - lo))).astype(np.uint8)
    if min(img.shape[:2]) < 16:
        raise ValueError("The picture is only %d x %d pixels; too small to trace." % (img.shape[1], img.shape[0]))
    return img, dpi


def write_png(path, img, mm_per_px=None):
    """Write a PNG, recording the physical pixel size so other programs open it at true scale."""
    ok, buf = cv2.imencode(".png", img)
    if not ok:
        return False
    b = buf.tobytes()
    if mm_per_px and mm_per_px > 0:
        import zlib
        ppm = int(round(1000.0 / mm_per_px))
        chunk = b"pHYs" + struct.pack(">IIB", ppm, ppm, 1)
        b = b[:33] + struct.pack(">I", 9) + chunk + struct.pack(">I", zlib.crc32(chunk) & 0xFFFFFFFF) + b[33:]
    try:
        with open(path, "wb") as f:
            f.write(b)
        return True
    except OSError:
        return False
