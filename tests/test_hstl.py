# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""Self-check of the hardpoint STL reader/writer. Standard library only:

    python tests/test_hstl.py
"""
import os
import struct
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from tostep.core import hstl  # noqa: E402


def box(size=10.0):
    """A cube the way a CAD program hands it over: each face meshed on its own (its own
    four points), outward-facing."""
    s = size
    c = [(0, 0, 0), (s, 0, 0), (s, s, 0), (0, s, 0), (0, 0, s), (s, 0, s), (s, s, s), (0, s, s)]
    quads = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    coords, tris, face = [], [], []
    for fi, q in enumerate(quads):
        base = len(coords) // 3
        for v in q:
            coords += c[v]
        tris += (base, base + 1, base + 2, base, base + 2, base + 3)
        face += (fi, fi)
    return coords, tris, face, {i: hstl.T_PLANE for i in range(6)}, c


def main():
    coords, tris, face, types, corners = box()
    data, info = hstl.encode(coords, tris, face, types, corners, units="mm", tol=0.01, angle=15, by="selftest")
    assert info["triangles"] == 12 and info["faces"] == 6 and info["watertight"], info
    assert info["hardpoints"] == 8 and info["colours"] <= 3, info
    assert len(data) == 84 + 50 * 12, "must be exactly the size of a plain binary STL"
    assert not data.startswith(b"solid"), "a binary STL header must not start with 'solid'"
    assert any(b > 127 for b in data[84:84 + 128]), "first triangles must mark the file as binary"
    for i in range(12):
        w = struct.unpack_from("<H", data, 84 + 50 * i + 48)[0]
        assert w & 0x80 and not w & 0xC060, hex(w)        # labelled; reserved bits and bit 15 clear

    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "box.stl")
        open(p, "wb").write(data)
        doc = hstl.read(p)
        assert doc and doc["crc_ok"] and doc["header"]["by"] == "selftest" and doc["header"]["units"] == "mm"
        dec = hstl.decode(doc)
        written = [face[i] for i in info["order"]]
        assert len(set(zip(dec["face"], written))) == 6 == len(set(dec["face"])), "faces must come back as written"
        assert dec["type"] == [hstl.T_PLANE] * 6 and len(dec["hardpoints"]) == 8

        bad = bytearray(data)
        bad[84 + 12] ^= 1                                 # move one corner
        open(p, "wb").write(bytes(bad))
        assert hstl.read(p)["crc_ok"] is False and not hstl.is_hardpoint_stl(p), "edited mesh must be refused"

        plain = b"plain".ljust(80, b" ") + data[80:]
        open(p, "wb").write(plain)
        assert hstl.read(p) is None, "a plain STL is not a hardpoint STL"

    got = hstl.transfer_faces(coords, tris, face, coords, tris)
    assert got == face, "label transfer onto the same mesh must be exact"

    # ONE copy of hstl.py lives in the repo; the installer adds it to each CAD plug-in (no drifting copies)
    from tostep.frontends import install
    ref = open(os.path.join(ROOT, "tostep", "core", "hstl.py"), "rb").read()
    for kind in ("FreeCAD", "Fusion"):
        src = install._plugin_src(kind)
        assert src, f"{kind} plug-in folder missing"
        assert not os.path.exists(os.path.join(src, "hstl.py")), f"{kind}: no private copy of hstl.py in the repo"
        with tempfile.TemporaryDirectory() as d:
            dest = os.path.join(d, "TOSTEP")
            install.build_cad_package(src, dest)
            assert open(os.path.join(dest, "hstl.py"), "rb").read() == ref, f"{kind}: hstl.py differs from core"
            assert os.path.isfile(os.path.join(dest, "tostep_bridge.py")), f"{kind}: bridge missing"
    print("hstl self-check passed")


def test_self_check():
    main()


if __name__ == "__main__":
    main()
