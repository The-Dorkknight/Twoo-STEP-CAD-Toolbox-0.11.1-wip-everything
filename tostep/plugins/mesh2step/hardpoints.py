# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""
hardpoints — the converter's side of the hardpoint STL format (the format itself, and the
pure-Python reader/writer the CAD plugins use, are in hstl.py).

load()  : the input STL carries face labels -> hand the converter its faces ready-made,
          after checking each one against the tolerance (a label is a hint, never trusted
          blindly: a face that doesn't fit what its label says goes through the normal
          detection instead).
save()  : after a normal conversion, write the input mesh again as a hardpoint STL that
          remembers the faces that were found. Triangles are copied byte for byte.
"""
from __future__ import annotations

import struct

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree

from tostep.core import hstl

REC = np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")])
# What the converter does with each labelled face. It has no cone or torus surface of its
# own yet, so those become fitted (NURBS) faces — one smooth face each, which measured far
# better than letting the detector chop them into cylinder and sphere pieces. Unknown and
# unlabelled areas ("") go through the normal detection.
KIND = {hstl.T_PLANE: "plane", hstl.T_CYL: "cyl", hstl.T_SPHERE: "sph", hstl.T_FREE: "free",
        hstl.T_CONE: "free", hstl.T_TORUS: "free", hstl.T_FACET: "facet"}
# (Tried and dropped: also remembering how a freeform face was cut into patches. Reloading
# the patches was about 4x faster on an organic test part but gave a worse result — 20 areas
# left as facets instead of 2 — because the fit depends on how the borders were smoothed on
# the way there. So a freeform face is always laid out afresh.)
MATCH = 0.98                            # share of triangles that must line up with the labelled file


def apply_units(path, prm, log):
    """A hardpoint STL knows its units. If the user left the units at mm, use the file's."""
    doc = _peek(path)
    if doc and doc["units"] != "mm" and prm.scale == 1.0:
        prm.scale = hstl.UNITS[doc["units"]]
        log(f"This STL says it is in {doc['units']} — converting to mm")


def _peek(path):
    try:
        with open(path, "rb") as f:
            return hstl.parse_header(f.read(80))
    except OSError:
        return None


def _groups(key, n):
    """{key value: indices} for an integer array."""
    order = np.argsort(key, kind="stable")
    ks = key[order]
    cuts = np.r_[0, np.flatnonzero(np.diff(ks)) + 1, len(ks)]
    return {int(ks[cuts[i]]): order[cuts[i]:cuts[i + 1]] for i in range(len(cuts) - 1)} if n else {}


def _components(ctx, key):
    a, b = ctx.adj[:, 0], ctx.adj[:, 1]
    same = key[a] == key[b]
    G = coo_matrix((np.ones(int(same.sum())), (a[same], b[same])), shape=(ctx.nF, ctx.nF))
    return connected_components(G, directed=False)


def _majority(group, value, n_groups, n_values):
    cnt = np.bincount(group * n_values + value, minlength=n_groups * n_values).reshape(n_groups, n_values)
    return cnt.argmax(axis=1)


def load(path, scale, ctx, tol, log):
    """-> None (no usable labels) or dict(label, planes, comps, hard, summary)."""
    doc = hstl.read(str(path))
    if doc is None:
        return None
    if not doc["crc_ok"]:
        log("This STL has face labels, but its triangles were changed after the labels were "
            "written — ignoring the labels and detecting faces as usual.")
        return None
    arr = np.frombuffer(doc["facets"], REC)
    if len(arr) == 0:
        return None
    cen = arr["v"].astype(np.float64).mean(axis=1) * scale
    d, idx = cKDTree(cen).query(ctx.C)
    hit = d <= 0.25 * tol
    if hit.mean() < MATCH:
        log(f"This STL has face labels, but only {100 * hit.mean():.0f}% of the cleaned-up mesh still "
            "lines up with them — ignoring the labels and detecting faces as usual.")
        return None
    w = arr["a"].astype(np.int64)[idx]
    ok = hit & ((w & hstl.LABELLED) != 0)
    fcol = np.where(ok, w & 0x1F, -1)
    stype = np.where(ok, (w >> 8) & 7, 0)

    nreg, reg = _components(ctx, fcol)
    rtype = _majority(reg, stype, nreg, 8)
    labelled = np.zeros(nreg, bool)
    labelled[reg[ok]] = True

    label = -np.ones(ctx.nF, dtype=np.int64)
    planes, comps = [], []
    counts = {}
    demoted = 0
    for r, ids in _groups(reg, ctx.nF).items():
        t = int(rtype[r]) if labelled[r] else hstl.T_UNKNOWN
        if t == hstl.T_PLANE:
            vids = np.unique(ctx.F[ids])
            pts = ctx.V[vids]
            o = pts.mean(axis=0)
            n = np.linalg.svd(pts - o, full_matrices=False)[2][-1]
            if (ctx.N[ids] * ctx.area[ids][:, None]).sum(axis=0) @ n < 0:
                n = -n
            if len(vids) >= 3 and float(np.abs((pts - o) @ n).max()) <= 1.5 * tol:
                label[ids] = len(planes)
                planes.append((o, n))
                counts["flat"] = counts.get("flat", 0) + 1
                continue
            demoted += 1
            t = hstl.T_UNKNOWN
        kind = KIND.get(t, "")
        comps.append(dict(ids=ids, kind=kind))
        name = hstl.TYPES[t] if t not in (hstl.T_UNKNOWN, hstl.T_FACET) else (
            "kept as facets" if kind == "facet" else "freeform" if kind == "free" else "unlabelled")
        counts[name] = counts.get(name, 0) + 1

    # hardpoints -> vertices of the cleaned mesh
    hard = set()
    fl = (arr["a"].astype(np.int64) >> 11) & 7
    pts = [arr["v"][(fl >> k) & 1 == 1, k, :] for k in range(3)]
    pts = np.unique(np.vstack(pts), axis=0).astype(np.float64) * scale if any(len(p) for p in pts) else np.zeros((0, 3))
    if len(pts):
        dv, vi = cKDTree(ctx.V).query(pts)
        hard = {int(v) for v, dd in zip(vi, dv) if dd <= tol}

    by = doc["header"]["by"] or "another program"
    log(f"Face labels found in this STL (written by {by}): "
        + ", ".join(f"{v} {k}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1]))
        + f"; {len(hard)} hardpoints. Using them instead of guessing the faces.")
    if demoted:
        log(f"  {demoted} face(s) labelled flat aren't flat within tolerance — detecting those normally")
    return dict(label=label, planes=planes, comps=comps, hard=hard)


# ------------------------------------------------------------------ saving
def _source_facets(path):
    """The input's triangles as STL records: byte-exact for a binary STL, rebuilt otherwise."""
    try:
        with open(path, "rb") as f:
            head = f.read(84)
            if len(head) == 84:
                n = struct.unpack("<I", head[80:84])[0]
                body = f.read()
                if len(body) == 50 * n and not head[:5].lower() == b"solid":
                    return np.frombuffer(body, REC).copy(), True
    except OSError:
        pass
    import trimesh
    m = trimesh.load(path, force="mesh", process=False)
    tri = np.asarray(m.triangles, dtype=np.float32)
    arr = np.zeros(len(tri), dtype=REC)
    arr["v"] = tri
    t64 = tri.astype(np.float64)
    nrm = np.cross(t64[:, 1] - t64[:, 0], t64[:, 2] - t64[:, 0])
    ln = np.linalg.norm(nrm, axis=1)
    arr["n"] = (nrm / np.maximum(ln, 1e-300)[:, None]).astype(np.float32)
    return arr, False


def _edge_pairs(tri_v):
    """Pairs of triangles sharing an edge. tri_v: (n, 3) welded vertex ids."""
    n = len(tri_v)
    e = np.concatenate([tri_v[:, [0, 1]], tri_v[:, [1, 2]], tri_v[:, [2, 0]]])
    e.sort(axis=1)
    owner = np.tile(np.arange(n), 3)
    key = e[:, 0].astype(np.int64) * (int(tri_v.max()) + 1) + e[:, 1]
    order = np.argsort(key, kind="stable")
    ks, ow = key[order], owner[order]
    same = ks[1:] == ks[:-1]
    return ow[:-1][same], ow[1:][same]


def save(src_path, dst_path, scale, ctx, regions, tol, angle, log):
    """Write src's mesh as a hardpoint STL labelled with the faces of this conversion."""
    if not regions:
        log("Labelled STL not saved: this conversion ended as plain facets, so there are no faces to remember.")
        return None
    arr, exact = _source_facets(str(src_path))
    if len(arr) == 0:
        return None
    lab = -np.ones(ctx.nF, dtype=np.int64)
    coarse_of, type_of, keys = [], [], {}
    for ri, r in enumerate(regions):
        lab[r["faces"]] = ri
        if r["type"] == "plane":
            key, t = ("pl", ri), hstl.T_PLANE
        elif r["type"] == "prim":
            key, t = ("pr", id(r["prim"])), (hstl.T_CYL if r["prim"][0] == "cyl" else hstl.T_SPHERE)
        else:
            key = ("c", r["comp"]) if r.get("comp") is not None else ("x", ri)
            t = hstl.T_FREE if r["type"] == "nurbs" else hstl.T_FACET
        coarse_of.append(keys.setdefault(key, len(keys)))
        type_of.append(t)
    coarse_of, type_of = np.array(coarse_of), np.array(type_of)

    cen = arr["v"].astype(np.float64).mean(axis=1) * scale
    d, idx = cKDTree(ctx.C).query(cen)
    share = float((d <= max(3.5 * tol, 1e-9)).mean())
    if share < MATCH:
        log(f"Labelled STL not saved: the mesh was reduced or repaired before fitting, so only "
            f"{100 * share:.0f}% of the file's triangles could be matched to a face.")
        return None
    reg = lab[idx]
    have = reg >= 0
    face = np.where(have, coarse_of[np.maximum(reg, 0)], -1)
    stype = np.where(have, type_of[np.maximum(reg, 0)], 0)

    # welded corners of the file's own triangles (exact float32 match, as slicers weld)
    _, inv = np.unique(arr["v"].reshape(-1, 3), axis=0, return_inverse=True)
    tri_v = np.asarray(inv).ravel().reshape(-1, 3)
    a, b = _edge_pairs(tri_v)
    m = (face[a] != face[b]) & (face[a] >= 0) & (face[b] >= 0)
    fcol = hstl.colour(len(keys), set(zip(face[a][m].tolist(), face[b][m].tolist())), hstl.FACE_COLOURS)
    if fcol is None:
        log("Labelled STL not saved: too many faces all touch each other for the format to label.")
        return None
    fcol = np.array(fcol)
    # hardpoints: points where three or more faces meet
    vf = np.unique(np.c_[tri_v.ravel(), np.repeat(face, 3)], axis=0)
    vf = vf[vf[:, 1] >= 0]
    cnt = np.bincount(vf[:, 0], minlength=int(tri_v.max()) + 1)
    hard = cnt >= 3
    corners = (hard[tri_v] * np.array([1, 2, 4])).sum(axis=1)

    word = np.where(have, (fcol[np.maximum(face, 0)] & 0x1F) | hstl.LABELLED | (stype << 8) | (corners << 11),
                    0).astype("<u2")
    arr["a"] = word
    if len(arr) and not have[0] and have.any():
        k = int(np.argmax(have))                 # a labelled triangle goes first (see hstl.encode)
        arr[[0, k]] = arr[[k, 0]]
    body = arr.tobytes()
    units = next((u for u, s in hstl.UNITS.items() if abs(s - scale) < 1e-9), "mm")
    head = hstl.make_header(units, tol / scale, angle, hstl.geometry_crc(body, len(arr)), "tostep")
    with open(dst_path, "wb") as f:
        f.write(head + struct.pack("<I", len(arr)) + body)
    log(f"Saved labelled STL: {dst_path} ({len(keys)} faces, {int(hard.sum())} hardpoints"
        + ("" if exact else "; the input wasn't a binary STL, so it was rewritten as one") + ")")
    return str(dst_path)


def inspect(path):
    """What an STL file is, for the interface: a hardpoint STL with valid labels, one whose
    triangles were edited after labelling (labels ignored), or a plain mesh."""
    import os
    size = os.path.getsize(path)
    out = {"labelled": False, "valid": False, "size": size, "triangles": None, "version": None,
           "units": None, "tol": None, "angle": None, "by": "", "faces": 0, "kinds": {},
           "hardpoints": 0, "unlabelled": 0, "message": ""}
    with open(path, "rb") as f:
        head = f.read(84)
    binary = len(head) >= 84 and 84 + 50 * int.from_bytes(head[80:84], "little") == size
    if binary:
        out["triangles"] = int.from_bytes(head[80:84], "little")
    d = hstl.read(path)
    if d is None:
        if not binary:
            out["message"] = ("This is not a binary STL (a text STL, or another mesh format). It converts as a "
                              "plain mesh; hardpoint labels only travel in binary STL files.")
        else:
            out["message"] = ("A plain STL: no face labels, so the converter finds the flat, round and freeform "
                              "faces itself. Convert it with 'Make a hardpoint STL' to keep what it finds.")
        return out
    h = d["header"]
    out.update(labelled=True, triangles=d["n"], version=h["version"], units=h["units"], tol=h["tol"],
               angle=h["angle"], by=h["by"])
    if not d["crc_ok"]:
        out["message"] = ("This file has hardpoint labels, but its triangles were changed after they were written "
                          "(or it is a newer format version). The labels will be ignored.")
        return out
    r = hstl.decode(d)
    kinds = {}
    for t in r["type"]:
        kinds[hstl.TYPES[t]] = kinds.get(hstl.TYPES[t], 0) + 1
    out.update(valid=True, faces=len(r["type"]), kinds=kinds, hardpoints=len(r["hardpoints"]),
               unlabelled=sum(1 for g in r["face"] if g < 0))
    out["message"] = ("A hardpoint STL with valid labels: the converter uses its faces instead of guessing, "
                      "checking each one against the tolerance first.")
    return out
