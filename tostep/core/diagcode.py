# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""
diagcode — one short code (and QR) that carries a job's settings and its result.

For support: the preview window shows the code as a QR beside the same facts in plain words,
so ONE screenshot says which mode, which surface fitter and which quality settings were used
and what came out. The code can be read back from the screenshot (or typed in):

    python run_tostep.py diag S2S1-H...                the code itself
    python run_tostep.py diag screenshot.png           a screenshot or photo showing the QR

What is in the code, and what is not
  In it:  app version, time (UTC, to the minute), a tag for the job, the kind of job, the
          operating system, the settings of an STL -> STEP conversion (mode, surface fitter,
          tolerances, switches, performance profile and its limits), the result (closed? valid?
          face counts, deviation, time, which limits were hit), and for a failure a class of
          error.
  Not in it: the file's name, any path, the log, the error text, anything about the computer
          beyond "Linux / Windows / macOS". Nothing is sent anywhere: it is a picture on screen.

Format ("multiplexed": several records in one string, each a row of the table below):

    S2S1-H<header>-C<convert settings>[-X<outline settings>]-R<result>[-E<error>]-K<check>

  * Only 0-9, A-Z and "-" are used: the densest kind of QR, and the characters are Crockford
    base32 (no I, L, O, U; a typed O is read as 0, I and L as 1).
  * Each record is a letter and then bits, most significant first, packed 5 to a character.
    FIELDS below is the whole dictionary: name, number of bits, and how to read them.
  * A reader skips records whose letter it does not know, so later versions can add some.
  * K is a 15-bit check over everything before it.
  * Time and job tag are written exactly as in the ErrCode project's report strings
    (EC1-<TIME:5><SESSION:3>-...): minutes since 2026-01-01 00:00 UTC, and a 15-bit tag.

The QR picture needs the small pure-Python package `segno` (in requirements.txt). Without it
the code and the plain words are still shown.
"""
from __future__ import annotations

import math
import re
import time
import zlib

ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"          # Crockford base32
PREFIX = "S2S1"
EPOCH = 1767225600                                      # 2026-01-01 00:00:00 UTC

KINDS = ["convert", "wash", "scad", "image"]
OSES = ["linux", "windows", "macos", "other"]
STATES = ["done", "error", "cancelled", "running"]
MODES = ["surface", "faceted", "extrude", "loft"]
ENGINES = ["classic", "staged", "ladder", "v2", "best", "quad", "auto"]   # (appended only: old codes still read)
UNITS = ["mm", "inches", "cm", "m"]
PROFILES = ["auto", "potato", "moderate", "extreme"]
ROUTES = ["auto", "prismatic", "lathe", "organic", "mixed"]
AXES = ["z", "y", "x"]
SOURCES = ["silhouette", "slice"]
ERRORS = ["none", "crash_or_memory", "no_triangles", "bad_file", "missing_program", "no_scale",
          "nothing_traced", "geometry_kernel", "cancelled", "other"]

MODE_NAMES = {"surface": "Smooth surfaces", "faceted": "Exact facets", "extrude": "Extrude outline (experimental)",
              "loft": "Loft cross-sections (experimental)"}
ENGINE_NAMES = {"classic": "Classic", "staged": "Staged (experimental)", "ladder": "Ladder (experimental)",
                "v2": "v2 fitter (experimental)", "best": "Second pass (experimental)",
                "quad": "Quad patches (experimental)",
                "auto": "Auto (picks by shape)"}
KIND_NAMES = {"convert": "STL -> STEP", "wash": "Jetwash", "scad": "SCAD -> STEP", "image": "Image -> STEP"}
ERROR_NAMES = {"none": "none", "crash_or_memory": "the worker stopped (crash or out of memory)",
               "no_triangles": "no triangles in the file", "bad_file": "the file could not be read",
               "missing_program": "a program it needs is missing", "no_scale": "no scale found in the picture",
               "nothing_traced": "nothing could be traced or built", "geometry_kernel": "the geometry kernel refused",
               "cancelled": "cancelled", "other": "another error"}

# flags of the result: (name, the words in the log that mean it, how it reads in the panel)
FLAGS = [
    ("checkpoint", "Checkpoint written", "checkpoint written before the slow fitting"),
    ("checkpoint_kept", "keeping the checkpoint", "FITTER STOPPED: the checkpoint file was kept"),
    ("budget", "time budget of", "time budget reached: the rest became facets"),
    ("memory_reduced", "needs more memory than this computer", "mesh reduced to fit this computer's memory"),
    ("memory_tight", "memory is tight", "memory was tight: some slow fills skipped"),
    ("guesstimated", ("Guesstimate:", "will be guesstimated"), "organic areas guesstimated"),
    ("kept_facets", "failed the geometry check", "some faces failed the check and stay as facets"),
    ("v2_failed", "keeping the first result", "second pass: v2 failed, first pass kept"),
]

# The dictionary. kinds: ("enum", list) | "flag" | "uint" | ("q", step) = a number in steps, all
# ones meaning "not measured" | ("sq", step) = the same, signed.
FIELDS = {
    "H": [("time", 25, "uint"), ("tag", 15, "uint"), ("major", 4, "uint"), ("minor", 7, "uint"), ("patch", 7, "uint"),
          ("wip", 1, "flag"), ("kind", 3, ("enum", KINDS)), ("os", 2, ("enum", OSES)), ("state", 2, ("enum", STATES))],
    "C": [("mode", 2, ("enum", MODES)), ("engine", 3, ("enum", ENGINES)), ("units", 2, ("enum", UNITS)),
          ("profile", 2, ("enum", PROFILES)), ("gpu", 1, "flag"), ("primitives", 1, "flag"),
          ("memory_guard", 1, "flag"), ("guess", 1, "flag"), ("tol", 10, ("q", 0.005)),
          ("organic_tol", 7, ("q", 0.05)), ("plane_angle", 8, ("q", 0.1)), ("crease_angle", 7, ("q", 1.0)),
          ("patch_size", 7, ("q", 1.0)), ("max_bend", 7, ("q", 1.0)), ("decimate_k", 12, ("q", 1.0)),
          ("smooth", 5, ("q", 1.0)), ("threads", 6, ("q", 1.0)), ("budget_min", 9, ("q", 1.0)),
          ("max_fills", 8, ("q", 1.0)), ("route", 3, ("enum", ROUTES)), ("ladder_guess", 1, "flag"),
          ("use_labels", 1, "flag")],
    "X": [("axis", 2, ("enum", AXES)), ("source", 1, ("enum", SOURCES)), ("smooth_sketch", 1, "flag"),
          ("symmetric", 1, "flag"), ("simplify", 10, ("q", 0.005)), ("height", 14, ("q", 0.1)),
          ("max_slices", 6, ("q", 1.0)), ("sensitivity", 10, ("q", 0.001))],
    "R": [("solid", 1, "flag"), ("valid", 1, "flag"), ("route", 3, ("enum", ROUTES))]
         + [(name, 1, "flag") for name, _, _ in FLAGS]
         + [("tris", 24, ("q", 1.0)), ("faces", 20, ("q", 1.0)), ("planes", 20, ("q", 1.0)),
            ("round", 16, ("q", 1.0)), ("nurbs", 18, ("q", 1.0)), ("faceted", 18, ("q", 1.0)),
            ("max_dev", 16, ("q", 0.001)), ("mean_dev", 16, ("q", 0.0001)), ("dvol", 16, ("sq", 0.001)),
            ("seconds", 17, ("q", 1.0))],
    "E": [("error", 5, ("enum", ERRORS)), ("hash", 15, "uint")],
}


# ------------------------------------------------------------------- bits --
def _pack(spec, values):
    """Values of one record -> base32 characters."""
    bits = []
    for name, n, kind in spec:
        v = values.get(name)
        top = (1 << n) - 1
        if kind == "flag":
            x = 1 if v else 0
        elif kind == "uint":
            x = int(v or 0) & top
        elif kind[0] == "enum":
            x = kind[1].index(v) if v in kind[1] else 0
        elif kind[0] == "q":
            x = top if v is None or (isinstance(v, float) and not math.isfinite(v)) \
                else min(top - 1, max(0, int(round(float(v) / kind[1]))))
        else:                                               # "sq": signed, the most negative code = not measured
            half = 1 << (n - 1)
            x = half if v is None or (isinstance(v, float) and not math.isfinite(v)) \
                else (min(half - 1, max(-half + 1, int(round(float(v) / kind[1])))) & top)
        bits += [(x >> (n - 1 - i)) & 1 for i in range(n)]
    bits += [0] * (-len(bits) % 5)
    return "".join(ALPHABET[int("".join(map(str, bits[i:i + 5])), 2)] for i in range(0, len(bits), 5))


def _unpack(spec, text):
    bits = []
    for ch in text:
        v = ALPHABET.index(ch)
        bits += [(v >> (4 - i)) & 1 for i in range(5)]
    need = sum(n for _, n, _ in spec)
    if len(bits) < need:
        raise ValueError("a record of the code is too short")
    out, pos = {}, 0
    for name, n, kind in spec:
        x = int("".join(map(str, bits[pos:pos + n])), 2)
        pos += n
        top = (1 << n) - 1
        if kind == "flag":
            out[name] = bool(x)
        elif kind == "uint":
            out[name] = x
        elif kind[0] == "enum":
            out[name] = kind[1][x] if x < len(kind[1]) else "?"
        elif kind[0] == "q":
            out[name] = None if x == top else x * kind[1]
        else:
            half = 1 << (n - 1)
            out[name] = None if x == half else (x - (1 << n) if x > half else x) * kind[1]
    return out


def _check(text):
    v = zlib.crc32(text.encode("ascii")) & 0x7FFF
    return ALPHABET[(v >> 10) & 31] + ALPHABET[(v >> 5) & 31] + ALPHABET[v & 31]


def _clean(code):
    """A code as typed or scanned: upper case, look-alike letters folded, anything else dropped."""
    s = str(code).strip().upper().replace("O", "0").replace("I", "1").replace("L", "1")
    i = s.find(PREFIX.replace("O", "0").replace("I", "1").replace("L", "1"))
    return re.sub(r"[^0-9A-Z-]", "", s[i:] if i >= 0 else s)


# ---------------------------------------------------------------- collect --
def facts(rep):
    """What a finished job's report says, as plain numbers (sent from the worker process)."""
    def num(name, default=None):
        v = getattr(rep, name, default)
        try:
            v = float(v)
            return v if math.isfinite(v) else None
        except (TypeError, ValueError):
            return None
    faces = num("n_faces")
    if faces is None:
        faces = num("faces_after")                          # Jetwash counts faces under another name
    mv, sv = num("mesh_volume") or num("scad_volume") or num("volume_before"), \
        num("step_volume") or num("volume_after")
    step = getattr(rep, "step", None)
    if sv is None and isinstance(step, dict):
        sv = step.get("volume_mm3")
    valid = getattr(rep, "is_valid", getattr(rep, "valid_after", None))
    solid = getattr(rep, "is_solid", None)
    if solid is None:
        solid = bool(getattr(rep, "solids_after", 0))
    return dict(solid=bool(solid), valid=bool(valid if valid is not None else solid),
                route=str(getattr(rep, "route", "") or "auto"), tris=num("n_input_tris"), faces=faces,
                planes=num("n_planes"), round=num("n_primitives"), nurbs=num("n_nurbs"),
                faceted=num("n_faceted_regions"), max_dev=num("max_dev"), mean_dev=num("mean_dev"),
                dvol=(100.0 * (sv - mv) / mv) if (mv and sv) else None, seconds=num("seconds"))


def error_class(text):
    t = (text or "").lower()
    for name, words in (("cancelled", ("cancel",)),
                        ("crash_or_memory", ("stopped unexpectedly", "out of memory", "memoryerror", "killed")),
                        ("no_triangles", ("no triangles",)),
                        ("no_scale", ("no scale token", "marker sheet", "does not say what resolution", "no square found")),
                        ("nothing_traced", ("nothing in the picture", "nothing to write", "no outline", "nothing to convert")),
                        ("missing_program", ("openscad", "not installed", "not found on this computer")),
                        ("bad_file", ("could not read", "cannot read", "not a valid", "unsupported", "no such file",
                                      "could not be read", "needs a ")),
                        ("geometry_kernel", ("kernel", "brep", "opencascade", "standard_", "could not translate"))):
        if any(w in t for w in words):
            return name
    return "other" if t else "none"


def _version(v):
    m = re.match(r"(\d+)\.(\d+)\.(\d+)", str(v))
    a, b, c = (int(x) for x in m.groups()) if m else (0, 0, 0)
    return dict(major=a, minor=b, patch=c, wip="wip" in str(v))


def _num(p, key, default=0.0):
    try:
        return float(p.get(key) if p.get(key) not in (None, "") else default)
    except (TypeError, ValueError):
        return default


def build(kind, params, state="done", result=None, log=(), error="", version="0.0.0", job_id="",
          profile=None, when=None, platform=None):
    """The code for one job.

    kind     "convert" | "wash" | "scad" | "image"
    params   the job's settings as the page sent them (strings or numbers)
    state    "done" | "error" | "cancelled" | "running"
    result   facts() of the report, or None when there is none
    log      the job's log lines (searched for the words that mean a limit was hit)
    profile  the performance settings the job ran with: {"name", "threads", "gpu", "budget_s", "max_fills"}
    """
    import sys
    p = dict(params or {})
    prof = dict(profile or {})
    plat = platform or sys.platform
    os_name = "linux" if plat.startswith("linux") else "windows" if plat.startswith("win") else \
        "macos" if plat == "darwin" else "other"
    try:
        tag = int(str(job_id)[:4], 16) & 0x7FFF
    except ValueError:
        tag = zlib.crc32(str(job_id).encode()) & 0x7FFF
    minutes = int(((time.time() if when is None else when) - EPOCH) // 60)
    head = dict(_version(version), time=max(0, minutes), tag=tag, kind=kind if kind in KINDS else "convert",
                os=os_name, state=state if state in STATES else "running")
    parts = [PREFIX, "H" + _pack(FIELDS["H"], head)]
    if kind == "convert":
        mode = p.get("mode") if p.get("mode") in MODES else "surface"
        guess = bool(p.get("guess"))
        budget = _num(p, "budget_s") or _num(prof, "budget_s")
        c = dict(mode=mode, engine=p.get("engine") if p.get("engine") in ENGINES else "classic",
                 units=p.get("units") if p.get("units") in UNITS else "mm",
                 profile=str(prof.get("name", "auto")).lower(), gpu=bool(prof.get("gpu")),
                 primitives=bool(p.get("primitives", True)), memory_guard=bool(p.get("memory_guard", True)),
                 guess=guess, tol=_num(p, "tol"), organic_tol=_num(p, "organic_tol", 0.3) if guess else 0.0,
                 plane_angle=_num(p, "plane_angle", 3), crease_angle=_num(p, "crease_angle", 30),
                 patch_size=_num(p, "patch_size", 30), max_bend=_num(p, "max_bend", 60),
                 decimate_k=_num(p, "decimate") / 1000.0, smooth=_num(p, "smooth"),
                 threads=_num(prof, "threads"), budget_min=budget / 60.0,
                 max_fills=_num(p, "max_fills") or _num(prof, "max_fills"),
                 route=p.get("route") if p.get("route") in ROUTES else "auto",
                 ladder_guess=bool(p.get("ladder_guess", True)), use_labels=bool(p.get("use_labels", True)))
        parts.append("C" + _pack(FIELDS["C"], c))
        if mode in ("extrude", "loft"):
            x = dict(axis=p.get("axis") if p.get("axis") in AXES else "z",
                     source=p.get("source") if p.get("source") in SOURCES else "silhouette",
                     smooth_sketch=bool(p.get("smooth_sketch")), symmetric=bool(p.get("symmetric")),
                     simplify=_num(p, "simplify"), height=_num(p, "height"),
                     max_slices=_num(p, "max_slices", 15), sensitivity=_num(p, "sensitivity", 0.02))
            parts.append("X" + _pack(FIELDS["X"], x))
    if result is not None:
        r = dict(result)
        text = "\n".join(str(x) for x in (log or ()))
        for name, words, _ in FLAGS:
            r[name] = any(w in text for w in ((words,) if isinstance(words, str) else words))
        if r.get("route") not in ROUTES:
            r["route"] = "auto"
        parts.append("R" + _pack(FIELDS["R"], r))
    if state == "error":
        parts.append("E" + _pack(FIELDS["E"], dict(error=error_class(error),
                                                   hash=zlib.crc32((error or "").strip().encode("utf-8")) & 0x7FFF)))
    body = "-".join(parts)
    return body + "-K" + _check(body)


# ----------------------------------------------------------------- decode --
def decode(code):
    """A code -> {"ok": the check passed, "records": {letter: values}, "unknown": [letters]}.
    Raises ValueError with a plain message if it is not one of these codes at all."""
    s = _clean(code)
    if not s.startswith(PREFIX + "-"):
        raise ValueError("This is not an Twoo STEP CAD Toolbox diagnostic code (it should start with %s-)." % PREFIX)
    parts = s.split("-")
    ok = None
    if len(parts) >= 3 and parts[-1].startswith("K"):
        ok = parts[-1][1:] == _check("-".join(parts[:-1]))
        parts = parts[:-1]
    out = {"ok": ok, "records": {}, "unknown": [], "code": s}
    for part in parts[1:]:
        if not part:
            continue
        letter, payload = part[0], part[1:]
        if letter not in FIELDS:
            out["unknown"].append(letter)
            continue
        if any(ch not in ALPHABET for ch in payload):
            raise ValueError("The code has a character that cannot be part of it.")
        out["records"][letter] = _unpack(FIELDS[letter], payload)
    if "H" not in out["records"]:
        raise ValueError("The code is cut short: its first part is missing.")
    return out


def _n(v, fmt="%d"):
    return "?" if v is None else fmt % v


def describe(d):
    """The decoded code in plain words: a list of (heading or "", text) rows."""
    R = d["records"]
    h = R["H"]
    when = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(EPOCH + 60 * h["time"]))
    rows = [("App", "Twoo STEP CAD Toolbox %d.%d.%d%s  ·  %s  ·  %s  ·  job tag %s" % (
        h["major"], h["minor"], h["patch"], "-wip" if h["wip"] else "", when,
        {"linux": "Linux", "windows": "Windows", "macos": "macOS"}.get(h["os"], "other system"),
        "".join(ALPHABET[(h["tag"] >> s) & 31] for s in (10, 5, 0)))),
        ("Job", "%s  ·  %s" % (KIND_NAMES.get(h["kind"], h["kind"]), h["state"].upper()))]
    c = R.get("C")
    if c:
        rows.append(("Mode", MODE_NAMES.get(c["mode"], c["mode"])))
        if c["mode"] in ("surface", "faceted"):
            eng = ENGINE_NAMES.get(c["engine"], c["engine"])
            if c["engine"] == "ladder" and c["route"] != "auto":
                eng += ", route forced: " + c["route"]
            rows.append(("Surface fitter", eng if c["mode"] == "surface" else eng + " (not used: exact facets)"))
        q = ["tolerance " + ("auto" if not c["tol"] else "%.3f mm" % c["tol"]),
             ("guesstimate ON, organic areas within %.2f mm" % c["organic_tol"]) if c["guess"] else "guesstimate off"]
        rows.append(("Quality", "  ·  ".join(q)))
        rows.append(("", "flat within %.1f°  ·  sharp edge from %.0f°  ·  patch up to %.0f %%  ·  bend up to %.0f°" % (
            c["plane_angle"], c["crease_angle"], c["patch_size"], c["max_bend"])))
        rows.append(("", "cylinders/spheres %s  ·  memory guard %s  ·  reduce %s  ·  denoise %s  ·  units %s%s" % (
            "on" if c["primitives"] else "OFF", "on" if c["memory_guard"] else "OFF",
            ("to %.0f k triangles" % c["decimate_k"]) if c["decimate_k"] else "off",
            _n(c["smooth"], "%.0f"), c["units"], "" if c["use_labels"] else "  ·  hardpoint labels ignored")))
        rows.append(("Performance", "%s profile  ·  %s  ·  graphics card %s  ·  time budget %s  ·  slow fills %s" % (
            c["profile"].capitalize(), ("%.0f thread(s)" % c["threads"]) if c["threads"] else "threads not set",
            "on" if c["gpu"] else "off",
            ("%.0f min" % c["budget_min"]) if c["budget_min"] else "none",
            ("at most %.0f" % c["max_fills"]) if c["max_fills"] else "no limit")))
    x = R.get("X")
    if x:
        rows.append(("Outline", "along %s  ·  from the %s  ·  %s  ·  tolerance %s  ·  height %s%s" % (
            x["axis"].upper(), "whole part" if x["source"] == "silhouette" else "cross-section",
            "smooth sketch" if x["smooth_sketch"] else "straight segments",
            "auto" if not x["simplify"] else "%.3f mm" % x["simplify"],
            "the part's own" if not x["height"] else "%.1f mm" % x["height"],
            "  ·  symmetric" if x["symmetric"] else "")))
        rows.append(("", "loft: up to %s slices, sensitivity %s" % (_n(x["max_slices"], "%.0f"),
                                                                  _n(x["sensitivity"], "%.3f"))))
    r = R.get("R")
    if r:
        rows.append(("Result", "closed solid: %s  ·  geometry check: %s%s" % (
            "yes" if r["solid"] else "NO", "valid" if r["valid"] else "HAS ISSUES",
            ("  ·  route taken: " + r["route"]) if r["route"] != "auto" else "")))
        if r["faces"] is not None:
            detail = ""
            if r["planes"] is not None:
                detail = " (flat %s, round %s, NURBS %s, faceted regions %s)" % tuple(
                    _n(r[k], "%.0f") for k in ("planes", "round", "nurbs", "faceted"))
            rows.append(("", "faces %.0f%s%s" % (r["faces"], detail,
                                                 ("  ·  from %.0f triangles" % r["tris"]) if r["tris"] else "")))
        bits = []
        if r["max_dev"] is not None:
            bits.append("deviation max %.3f mm, mean %s mm" % (r["max_dev"], _n(r["mean_dev"], "%.4f")))
        if r["dvol"] is not None:
            bits.append("volume %+.3f %%" % r["dvol"])
        if r["seconds"] is not None:
            s = r["seconds"]
            bits.append("time %s" % ("%.0f s" % s if s < 120 else "%.0f min %.0f s" % (s // 60, s % 60)))
        if bits:
            rows.append(("", "  ·  ".join(bits)))
        hit = [words for name, _, words in FLAGS if r.get(name)]
        rows.append(("Limits hit", "; ".join(hit) if hit else "none"))
    e = R.get("E")
    if e:
        rows.append(("Error", "%s  ·  message tag %s" % (ERROR_NAMES.get(e["error"], e["error"]),
                                                       "".join(ALPHABET[(e["hash"] >> s) & 31] for s in (10, 5, 0)))))
    if d.get("unknown"):
        rows.append(("", "records this version does not know: " + ", ".join(d["unknown"])))
    if d.get("ok") is False:
        rows.append(("CHECK FAILED", "the code was mistyped or misread: some of the above may be wrong"))
    elif d.get("ok") is None:
        rows.append(("", "the code has no check part: it may be cut short"))
    return rows


def text(code):
    """The code in plain words, as lines."""
    return [("%s: %s" % (k, v)) if k else v for k, v in describe(decode(code))]


# --------------------------------------------------------------------- QR --
def qr_rows(code):
    """The QR symbol of a code as rows of "0"/"1" (1 = dark), without the quiet border, or None
    when the `segno` package is not installed."""
    try:
        import segno
    except ImportError:
        return None
    q = segno.make(code, error="m", micro=False)            # all characters are QR "alphanumeric"
    return ["".join("1" if v else "0" for v in row) for row in q.matrix]


def read_image(path):
    """Find diagnostic codes in a screenshot or photo. Returns the list of codes read (may be
    empty). Needs OpenCV, which the Image -> STEP tab already uses."""
    import cv2
    import numpy as np
    img = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("Could not read that picture.")
    det = cv2.QRCodeDetector()
    found = []

    def look(im):
        try:
            ok, texts, _, _ = det.detectAndDecodeMulti(im)
        except cv2.error:
            ok, texts = False, ()
        for t in (texts if ok else ()):
            if t and PREFIX in _clean(t) and _clean(t) not in found:
                found.append(_clean(t))
        if not found:
            t = det.detectAndDecode(im)[0]
            if t and PREFIX in _clean(t):
                found.append(_clean(t))

    look(img)
    if not found:                                           # small on a big screenshot, or a soft photo
        for k in (2.0, 3.0, 0.5):
            look(cv2.resize(img, None, fx=k, fy=k, interpolation=cv2.INTER_CUBIC if k > 1 else cv2.INTER_AREA))
            if found:
                break
    if not found:
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        look(cv2.cvtColor(cv2.threshold(g, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)[1], cv2.COLOR_GRAY2BGR))
    return found
