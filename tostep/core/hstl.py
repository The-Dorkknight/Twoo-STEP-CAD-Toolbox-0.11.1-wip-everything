# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""
hstl — "hardpoint STL": an ordinary binary STL that also says which CAD face every
triangle came from, so a mesh-to-CAD converter doesn't have to guess.

Nothing is added to the file. The labels live in bytes every STL already has and
slicers ignore: the 80-byte header and the 2-byte "attribute" field after each triangle.
The triangles themselves are untouched, the file keeps the exact size of a plain STL and
the extension stays .stl.

File layout (format version 1)
------------------------------
Header, 80 bytes of ASCII padded with spaces:

    HARDPOINT-STL 1 u=mm tol=0.05 ang=15 crc=1a2b3c4d by=freecad

    u    units of the coordinates: mm, in, cm or m
    tol  how far the triangles may be from the true surface, in those units (0 = unknown)
    ang  sharp-edge / meshing angle in degrees (0 = unknown)
    crc  CRC-32 of the triangle data (the 48 bytes of normal + corners of every
         triangle, in file order, attribute fields left out). If it doesn't match, the
         mesh was edited after labelling and the labels must be ignored.
    by   what wrote the file (free text, no spaces)

Attribute field of each triangle, 16 bits, little-endian:

    bits 0-4    face colour 0-31. A FACE is a set of triangles joined edge to edge that
                share a colour; two faces that touch along an edge never share one. (So a
                few colours label any number of faces, the way four colours do a map.)
    bits 5-6    reserved, 0
    bit  7      1 = this triangle is labelled. (It also keeps slicers that sniff the first
                bytes for "is this a text STL?" from misreading simple files.)
    bits 8-10   surface type: 0 unknown, 1 plane, 2 cylinder, 3 sphere, 4 cone, 5 torus,
                6 freeform, 7 keep as facets
    bits 11-13  hardpoints: bit 11 = corner 1 of this triangle is a CAD vertex, bit 12 =
                corner 2, bit 13 = corner 3. A hardpoint is a point where face edges meet
                or an edge changes character (line into arc).
    bit  14     reserved, 0
    bit  15     always 0 (some viewers read bit 15 = 1 as "this field is a colour")

Readers must ignore reserved bits and must fall back to treating the file as a plain STL
when the header doesn't start with HARDPOINT-STL or the crc doesn't match.

Pure standard library, so it runs inside FreeCAD's and Fusion's own Python.
    python hstl.py info part.stl      # what a file contains
"""
from __future__ import annotations

import heapq
import struct
import sys
import zlib

MAGIC = "HARDPOINT-STL"
VERSION = 1
TYPES = ("unknown", "plane", "cylinder", "sphere", "cone", "torus", "freeform", "facets")
T_UNKNOWN, T_PLANE, T_CYL, T_SPHERE, T_CONE, T_TORUS, T_FREE, T_FACET = range(8)
UNITS = {"mm": 1.0, "in": 25.4, "cm": 10.0, "m": 1000.0}
FACE_COLOURS = 32
LABELLED = 0x0080
_REC = struct.Struct("<12fH")


# ------------------------------------------------------------------ attribute field
def pack_word(face_colour, stype=0, corners=0):
    return (face_colour & 0x1F) | LABELLED | ((stype & 0x7) << 8) | ((corners & 0x7) << 11)


def unpack_word(w):
    """-> (labelled, face_colour, surface_type, corner_flags)"""
    return bool(w & LABELLED), w & 0x1F, (w >> 8) & 0x7, (w >> 11) & 0x7


# ------------------------------------------------------------------ header
def make_header(units="mm", tol=0.0, angle=0.0, crc=0, by=""):
    by = "".join(c for c in str(by) if c.isalnum() or c in "-_.")[:20]
    s = f"{MAGIC} {VERSION} u={units} tol={float(tol):.6g} ang={float(angle):.4g} crc={crc & 0xFFFFFFFF:08x}"
    if by:
        s += f" by={by}"
    return s.encode("ascii")[:80].ljust(80, b" ")


def parse_header(h80):
    """-> dict(version, units, tol, angle, crc, by) or None if this isn't a hardpoint STL."""
    try:
        parts = bytes(h80[:80]).decode("ascii", "replace").split()
    except Exception:
        return None
    if len(parts) < 2 or parts[0] != MAGIC:
        return None
    try:
        out = dict(version=int(parts[1]), units="mm", tol=0.0, angle=0.0, crc=None, by="")
    except ValueError:
        return None
    for p in parts[2:]:
        k, _, v = p.partition("=")
        try:
            if k == "u" and v in UNITS:
                out["units"] = v
            elif k == "tol":
                out["tol"] = float(v)
            elif k == "ang":
                out["angle"] = float(v)
            elif k == "crc":
                out["crc"] = int(v, 16)
            elif k == "by":
                out["by"] = v
        except ValueError:
            pass
    return out


def geometry_crc(facets, n):
    """CRC-32 over the 48 geometry bytes of each 50-byte triangle record."""
    mv = memoryview(facets)
    crc = 0
    for i in range(n):
        crc = zlib.crc32(mv[50 * i:50 * i + 48], crc)
    return crc & 0xFFFFFFFF


# ------------------------------------------------------------------ reading
def read(path):
    """Read a hardpoint STL.

    -> None for anything that isn't one (plain STL, text STL, other file), else
       dict(header=..., n=triangles, facets=bytes (50 per triangle), crc_ok=bool)."""
    try:
        with open(path, "rb") as f:
            head = f.read(84)
            if len(head) < 84:
                return None
            hdr = parse_header(head[:80])
            if hdr is None:
                return None
            n = struct.unpack("<I", head[80:84])[0]
            facets = f.read()
    except OSError:
        return None
    if len(facets) != 50 * n:
        return None
    ok = hdr["version"] == VERSION and hdr["crc"] is not None and hdr["crc"] == geometry_crc(facets, n)
    return dict(header=hdr, n=n, facets=facets, crc_ok=ok)


def words(doc):
    """The attribute field of every triangle of a read() result."""
    f = doc["facets"]
    return [f[50 * i + 48] | (f[50 * i + 49] << 8) for i in range(doc["n"])]


def is_hardpoint_stl(path):
    d = read(path)
    return bool(d and d["crc_ok"])


def _weld_exact(facets, n):
    """Triangle corner ids, corners with the same three float32 values being one point."""
    ids, tris = {}, []
    for i in range(n):
        o = 50 * i + 12
        t = []
        for k in range(3):
            key = bytes(facets[o + 12 * k:o + 12 * k + 12])
            t.append(ids.setdefault(key, len(ids)))
        tris.append(tuple(t))
    return tris, len(ids)


def components(tris, keys):
    """Group triangles joined edge to edge that have the same key. -> group id per triangle."""
    parent = list(range(len(tris)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    seen = {}
    for i, (a, b, c) in enumerate(tris):
        for e in ((a, b) if a < b else (b, a), (b, c) if b < c else (c, b), (a, c) if a < c else (c, a)):
            for j in seen.setdefault(e, []):
                if keys[j] == keys[i]:
                    ra, rb = find(i), find(j)
                    if ra != rb:
                        parent[ra] = rb
            seen[e].append(i)
    roots = {}
    return [roots.setdefault(find(i), len(roots)) for i in range(len(tris))]


def decode(doc):
    """Reference reader: rebuild the faces of a read() result.

    -> dict(face=[face id per triangle, -1 = not labelled], type=[surface type per face],
            hardpoints=[(x, y, z), ...])"""
    n, f = doc["n"], doc["facets"]
    w = words(doc)
    tris, _ = _weld_exact(f, n)
    face = components(tris, [(x & 0x1F) if x & LABELLED else -1 - i for i, x in enumerate(w)])
    votes = {}
    for i, x in enumerate(w):
        if x & LABELLED:
            votes.setdefault(face[i], {}).setdefault((x >> 8) & 7, 0)
            votes[face[i]][(x >> 8) & 7] += 1
    types = [max(votes[g].items(), key=lambda kv: kv[1])[0] if g in votes else T_UNKNOWN
             for g in range(max(face) + 1 if face else 0)]
    hard = set()
    for i, x in enumerate(w):
        for k in range(3):
            if x & (1 << (11 + k)):
                hard.add(struct.unpack_from("<3f", f, 50 * i + 12 + 12 * k))
    face = [g if w[i] & LABELLED else -1 for i, g in enumerate(face)]
    return dict(face=face, type=types, hardpoints=sorted(hard))


# ------------------------------------------------------------------ writing
def colour(n, pairs, limit):
    """Give n things a number 0..limit-1 so that no pair shares one. -> list, or None if
    `limit` numbers aren't enough. (Smallest-last ordering: needs few colours on surfaces.)"""
    nb = [set() for _ in range(n)]
    for a, b in pairs:
        if a != b:
            nb[a].add(b)
            nb[b].add(a)
    deg = [len(s) for s in nb]
    heap = [(d, i) for i, d in enumerate(deg)]
    heapq.heapify(heap)
    gone = [False] * n
    order = []
    while heap:
        d, i = heapq.heappop(heap)
        if gone[i] or d != deg[i]:
            continue
        gone[i] = True
        order.append(i)
        for j in nb[i]:
            if not gone[j]:
                deg[j] -= 1
                heapq.heappush(heap, (deg[j], j))
    col = [-1] * n
    for i in reversed(order):
        used = {col[j] for j in nb[i]}
        c = 0
        while c in used:
            c += 1
        if c >= limit:
            return None
        col[i] = c
    return col


class _Welder:
    """Merge points closer than `tol` (CAD kernels give the two faces along an edge points
    that differ in the last digits)."""

    def __init__(self, tol):
        self.tol = tol
        self.h = max(tol * 4.0, 1e-12)
        self.exact = {}
        self.cells = {}
        self.pts = []

    def _near(self, p):
        h, t2 = self.h, self.tol * self.tol
        cx, cy, cz = int(p[0] // h), int(p[1] // h), int(p[2] // h)
        for dx in (0, -1, 1):
            for dy in (0, -1, 1):
                for dz in (0, -1, 1):
                    for j in self.cells.get((cx + dx, cy + dy, cz + dz), ()):
                        q = self.pts[j]
                        if (q[0] - p[0]) ** 2 + (q[1] - p[1]) ** 2 + (q[2] - p[2]) ** 2 <= t2:
                            return j
        return -1

    def find(self, p, tol):
        """Index of the welded point nearest to p within tol, or -1."""
        j = self.exact.get(p)
        if j is not None:
            return j
        if getattr(self, "_coarse_tol", None) != tol:
            self._coarse_tol, self._coarse, h = tol, {}, max(tol * 2.0, 1e-12)
            for k, q in enumerate(self.pts):
                self._coarse.setdefault((int(q[0] // h), int(q[1] // h), int(q[2] // h)), []).append(k)
        h = max(tol * 2.0, 1e-12)
        cx, cy, cz = int(p[0] // h), int(p[1] // h), int(p[2] // h)
        best, bd = -1, tol * tol
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for k in self._coarse.get((cx + dx, cy + dy, cz + dz), ()):
                        q = self.pts[k]
                        d = (q[0] - p[0]) ** 2 + (q[1] - p[1]) ** 2 + (q[2] - p[2]) ** 2
                        if d <= bd:
                            best, bd = k, d
        return best

    def add(self, p):
        j = self.exact.get(p)
        if j is not None:
            return j
        j = self._near(p)
        if j < 0:
            j = len(self.pts)
            self.pts.append(p)
            h = self.h
            self.cells.setdefault((int(p[0] // h), int(p[1] // h), int(p[2] // h)), []).append(j)
        self.exact[p] = j
        return j


def _f32(x):
    return struct.unpack("<f", struct.pack("<f", x))[0]


def encode(coords, tris, tri_face, face_type, hard_points=(), scale=1.0, units="mm", tol=0.0,
           angle=0.0, by="", weld_tol=None):
    """Build a hardpoint STL in memory.

    coords      flat [x0, y0, z0, x1, ...] (any number of duplicate points is fine)
    tris        flat [a0, b0, c0, a1, ...] indices into coords
    tri_face    for each triangle, the number of the CAD face it belongs to (-1 = none)
    face_type   {face number: surface type 0-7} (missing = unknown)
    hard_points [(x, y, z), ...] CAD vertices, same coordinates as coords
    scale       multiply all coordinates (Fusion works in cm: pass 10 for a file in mm)

    -> (file bytes, info dict). info["order"][k] is the input triangle written at position k
       (triangles that collapse to a line when points are welded are left out)."""
    n_in = len(tris) // 3
    pts = [(_f32(coords[i] * scale), _f32(coords[i + 1] * scale), _f32(coords[i + 2] * scale))
           for i in range(0, len(coords) - 2, 3)]
    if weld_tol is None:
        lo = [min(p[k] for p in pts) for k in range(3)] if pts else [0, 0, 0]
        hi = [max(p[k] for p in pts) for k in range(3)] if pts else [0, 0, 0]
        diag = sum((hi[k] - lo[k]) ** 2 for k in range(3)) ** 0.5
        weld_tol = max(2e-7 * diag, 1e-9)
    w = _Welder(weld_tol)
    vid = [w.add(p) for p in pts]
    V = w.pts

    T, F, order = [], [], []
    for i in range(n_in):
        a, b, c = vid[tris[3 * i]], vid[tris[3 * i + 1]], vid[tris[3 * i + 2]]
        if a == b or b == c or a == c:
            continue                                  # collapsed to a line by welding
        T.append((a, b, c))
        F.append(tri_face[i])
        order.append(i)
    n = len(T)

    # edges: who uses each one, and in which direction
    edges = {}
    for i, (a, b, c) in enumerate(T):
        for u, v in ((a, b), (b, c), (c, a)):
            edges.setdefault((u, v) if u < v else (v, u), []).append((i, u < v))
    open_e = sum(1 for e in edges.values() if len(e) == 1)
    bad_e = sum(1 for e in edges.values() if len(e) > 2 or (len(e) == 2 and e[0][1] == e[1][1]))

    # faces -> colours
    faces = sorted({f for f in F if f >= 0})
    fidx = {f: k for k, f in enumerate(faces)}
    fpairs = set()
    for e in edges.values():
        for x in range(len(e)):
            for y in range(x + 1, len(e)):
                i, j = e[x][0], e[y][0]
                if F[i] < 0 or F[j] < 0:
                    continue
                if F[i] != F[j]:
                    fpairs.add((fidx[F[i]], fidx[F[j]]))
    fcol = colour(len(faces), fpairs, FACE_COLOURS)
    if fcol is None:
        raise ValueError(f"this part has more than {FACE_COLOURS} faces that all touch each other — "
                         "the format can't label it")

    # hardpoints -> corner flags
    hard, missed = set(), 0
    for hp in hard_points:
        j = w.find((_f32(hp[0] * scale), _f32(hp[1] * scale), _f32(hp[2] * scale)), weld_tol * 50.0)
        if j >= 0:
            hard.add(j)
        else:
            missed += 1

    # a labelled triangle goes first: its attribute byte marks the file as binary for
    # slicers that sniff the first bytes
    first = next((i for i in range(n) if F[i] >= 0), 0)
    if first:
        for arr in (T, F, order):
            arr[0], arr[first] = arr[first], arr[0]

    body = bytearray(50 * n)
    for i, (a, b, c) in enumerate(T):
        pa, pb, pc = V[a], V[b], V[c]
        ux, uy, uz = pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2]
        vx, vy, vz = pc[0] - pa[0], pc[1] - pa[1], pc[2] - pa[2]
        nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
        ln = (nx * nx + ny * ny + nz * nz) ** 0.5 or 1.0
        if F[i] >= 0:
            word = pack_word(fcol[fidx[F[i]]], face_type.get(F[i], T_UNKNOWN),
                             (a in hard) | ((b in hard) << 1) | ((c in hard) << 2))
        else:
            word = 0
        _REC.pack_into(body, 50 * i, nx / ln, ny / ln, nz / ln, *pa, *pb, *pc, word)
    crc = geometry_crc(body, n)
    data = make_header(units, tol, angle, crc, by) + struct.pack("<I", n) + bytes(body)
    info = dict(triangles=n, dropped=n_in - n, points=len(V), faces=len(faces),
                colours=(max(fcol) + 1 if fcol else 0), hardpoints=len(hard),
                hardpoints_missed=missed,
                open_edges=open_e, bad_edges=bad_e, watertight=(open_e == 0 and bad_e == 0 and n > 0),
                order=order,
                types={TYPES[t]: sum(1 for f in faces if face_type.get(f, 0) == t) for t in range(8)
                       if any(face_type.get(f, 0) == t for f in faces)})
    return data, info


def write(path, *args, **kw):
    data, info = encode(*args, **kw)
    with open(path, "wb") as f:
        f.write(data)
    return info


def _tri_d2(p, a, b, c):
    """Squared distance from point p to triangle abc."""
    abx, aby, abz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    acx, acy, acz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    apx, apy, apz = p[0] - a[0], p[1] - a[1], p[2] - a[2]
    d1 = abx * apx + aby * apy + abz * apz
    d2 = acx * apx + acy * apy + acz * apz
    if d1 <= 0 and d2 <= 0:
        return apx * apx + apy * apy + apz * apz
    bpx, bpy, bpz = p[0] - b[0], p[1] - b[1], p[2] - b[2]
    d3 = abx * bpx + aby * bpy + abz * bpz
    d4 = acx * bpx + acy * bpy + acz * bpz
    if d3 >= 0 and d4 <= d3:
        return bpx * bpx + bpy * bpy + bpz * bpz
    vc = d1 * d4 - d3 * d2
    if vc <= 0 and d1 >= 0 and d3 <= 0:
        v = d1 / (d1 - d3)
        q = (a[0] + v * abx, a[1] + v * aby, a[2] + v * abz)
    else:
        cpx, cpy, cpz = p[0] - c[0], p[1] - c[1], p[2] - c[2]
        d5 = abx * cpx + aby * cpy + abz * cpz
        d6 = acx * cpx + acy * cpy + acz * cpz
        if d6 >= 0 and d5 <= d6:
            return cpx * cpx + cpy * cpy + cpz * cpz
        vb = d5 * d2 - d1 * d6
        if vb <= 0 and d2 >= 0 and d6 <= 0:
            w = d2 / (d2 - d6)
            q = (a[0] + w * acx, a[1] + w * acy, a[2] + w * acz)
        else:
            va = d3 * d6 - d5 * d4
            if va <= 0 and (d4 - d3) >= 0 and (d5 - d6) >= 0:
                w = (d4 - d3) / ((d4 - d3) + (d5 - d6))
                q = (b[0] + w * (c[0] - b[0]), b[1] + w * (c[1] - b[1]), b[2] + w * (c[2] - b[2]))
            else:
                den = va + vb + vc
                if den == 0:
                    return apx * apx + apy * apy + apz * apz
                v, w = vb / den, vc / den
                q = (a[0] + abx * v + acx * w, a[1] + aby * v + acy * w, a[2] + abz * v + acz * w)
    return (p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 + (p[2] - q[2]) ** 2


def transfer_faces(src_coords, src_tris, src_face, dst_coords, dst_tris):
    """Label the triangles of one mesh from another mesh of the same shape: each triangle
    takes the face number of the labelled triangle that its middle point lies on (nearest
    by true point-to-triangle distance, so a triangle next to a face border still gets its
    own face). Used when a CAD program can only give a watertight mesh for the whole body
    and separate, slightly different meshes per face."""
    ns = len(src_tris) // 3
    if ns == 0:
        return [-1] * (len(dst_tris) // 3)
    P = [(src_coords[i], src_coords[i + 1], src_coords[i + 2]) for i in range(0, len(src_coords) - 2, 3)]
    T = [(P[src_tris[3 * i]], P[src_tris[3 * i + 1]], P[src_tris[3 * i + 2]]) for i in range(ns)]
    lo = [min(p[k] for p in P) for k in range(3)]
    hi = [max(p[k] for p in P) for k in range(3)]
    diag = sum((hi[k] - lo[k]) ** 2 for k in range(3)) ** 0.5 or 1.0
    h = diag / max(8.0, min(64.0, (ns / 4.0) ** 0.5))
    grid = {}
    for i, (a, b, c) in enumerate(T):
        r = [(int(min(a[k], b[k], c[k]) // h), int(max(a[k], b[k], c[k]) // h)) for k in range(3)]
        for x in range(r[0][0], r[0][1] + 1):
            for y in range(r[1][0], r[1][1] + 1):
                for z in range(r[2][0], r[2][1] + 1):
                    grid.setdefault((x, y, z), []).append(i)
    out = []
    for i in range(0, len(dst_tris) - 2, 3):
        a, b, c = 3 * dst_tris[i], 3 * dst_tris[i + 1], 3 * dst_tris[i + 2]
        p = ((dst_coords[a] + dst_coords[b] + dst_coords[c]) / 3.0,
             (dst_coords[a + 1] + dst_coords[b + 1] + dst_coords[c + 1]) / 3.0,
             (dst_coords[a + 2] + dst_coords[b + 2] + dst_coords[c + 2]) / 3.0)
        cx, cy, cz = int(p[0] // h), int(p[1] // h), int(p[2] // h)
        best, bd, seen, r = -1, float("inf"), set(), 0
        while r <= 70:
            for dx in range(-r, r + 1):
                for dy in range(-r, r + 1):
                    for dz in range(-r, r + 1):
                        if max(abs(dx), abs(dy), abs(dz)) != r:
                            continue
                        for j in grid.get((cx + dx, cy + dy, cz + dz), ()):
                            if j not in seen:
                                seen.add(j)
                                d = _tri_d2(p, *T[j])
                                if d < bd:
                                    bd, best = d, j
            # everything within r*h of p (measured to the walls of the searched block) is covered
            reach = min(p[0] - (cx - r) * h, (cx + r + 1) * h - p[0], p[1] - (cy - r) * h,
                        (cy + r + 1) * h - p[1], p[2] - (cz - r) * h, (cz + r + 1) * h - p[2])
            if best >= 0 and bd <= reach * reach:
                break
            r += 1
        out.append(src_face[best] if best >= 0 else -1)
    return out


# ------------------------------------------------------------------ command line
def describe(path):
    d = read(path)
    if d is None:
        return f"{path}: not a hardpoint STL (a plain STL, or not an STL at all)"
    h = d["header"]
    lines = [f"{path}: hardpoint STL version {h['version']}, {d['n']} triangles, units {h['units']}, "
             f"written by {h['by'] or 'unknown'}"]
    if not d["crc_ok"]:
        lines.append("  The triangles were changed after the labels were written — labels will be ignored.")
        return "\n".join(lines)
    r = decode(d)
    nf = len(r["type"])
    kinds = {}
    for t in r["type"]:
        kinds[TYPES[t]] = kinds.get(TYPES[t], 0) + 1
    unl = sum(1 for g in r["face"] if g < 0)
    lines.append(f"  {nf} faces (" + ", ".join(f"{v} {k}" for k, v in sorted(kinds.items())) + "), "
                 f"{len(r['hardpoints'])} hardpoints" + (f", {unl} unlabelled triangles" if unl else ""))
    if h["tol"]:
        lines.append(f"  meshed to {h['tol']:g} {h['units']}" + (f", {h['angle']:g} degrees" if h["angle"] else ""))
    return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "info":
        print(describe(sys.argv[2]))
    else:
        print("usage: python hstl.py info part.stl")
