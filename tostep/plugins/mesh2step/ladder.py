# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""
ladder — the EXPERIMENTAL "Ladder" engine's front end: look at the mesh for a few
milliseconds, decide what kind of part it is, and set the converter up so the easy
things are claimed first and the hard leftover is either fitted, guesstimated or kept
as facets within a time budget.

Everything here is computed from the mesh's own exact data (facet normals, areas,
adjacency, dihedral angles) — no rendering. The signals and thresholds come from
docs/research/README.md ("Easy things first"): they were tuned on 26 CAD-exported
test meshes in a sandbox, not on scans, and the classic converter stays the default.

Routes
  prismatic  flats bounded by sharp edges plus walls square to one axis → extruded
             outlines claimed first (Staged roughing, since 0.10.0: on five of seven such
             test parts far fewer faces and a closer fit, same time), then the Classic
             fitter; engines.py runs the plain fitter as well and keeps the better result
             when roughing misses twice the tolerance (one coarse test plate). No Guesstimate
  lathe      nearly all curved area is a surface of revolution about one axis →
             lathe shapes are claimed on the whole mesh BEFORE the flat-face search
             (Staged roughing), then the rest as usual
  organic    no sharp-bounded flats, no lathe axis, almost no sharp edges → the flat
             and cylinder/sphere searches are skipped and Guesstimate is switched on
  mixed      anything else → Classic with the caps (and Guesstimate if allowed)
"""
from __future__ import annotations

import math

import numpy as np

ROUTES = ("auto", "prismatic", "lathe", "organic", "mixed")

# thresholds (see the research notes; margins on the test set in brackets)
FLAT_SHARP_MIN = 0.30     # prismatic: sharp-bounded flat area share        [P ≥ 0.44 / L ≤ 0.26]
FLAT_PLUS_PERP_MIN = 0.75  # prismatic: flats + walls square to an axis     [P ≥ 1.19]
LATHE_MIN = 0.85          # lathe: lathe share of the curved area           [L ≥ 0.91 / P ≤ 0.09 / O ≤ 0.11]
MIXED_ROUGH_MIN = 0.50     # mixed route: roughing (Staged) when the lathe share of curved area reaches this
ORGANIC_FLAT_MAX = 0.05
ORGANIC_LATHE_MAX = 0.50
ORGANIC_SHARP_MAX = 0.02  # length share of edges sharper than 30°          [O < 0.007]
CLUSTER_MIN_AREA = 0.0025  # an exact-normal cluster counts from 0.25 % of the area (1.3 mm knob flats are 0.79 %)
SHARP_BOUNDARY_DEG = 25.0  # a flat is "sharp-bounded" when ≥ half its boundary length is sharper than this
LATHE_BAND = math.sin(math.radians(5.0))   # normal within 5° of the (radial, axial) plane


def _edge_lengths(ctx):
    e = ctx.adj_edges
    return np.linalg.norm(ctx.V[e[:, 0]] - ctx.V[e[:, 1]], axis=1)


def crease_from_histogram(ctx):
    """Sharp-edge threshold from the dihedral-angle histogram: the first valley after the
    exporter's own facet-angle peak. None when there is no clear smooth peak below 30°."""
    ang = ctx.adj_angles()                       # degrees
    nz = ang[ang >= 0.5]
    if len(nz) < 50:
        return None
    h, e = np.histogram(nz, bins=np.arange(0.5, 90.5, 0.5))
    c = 0.5 * (e[1:] + e[:-1])
    hs = np.convolve(h, np.ones(3) / 3, mode="same")
    sm = np.where(c < 30)[0]
    if not len(sm) or hs[sm].max() <= 0.02 * len(nz):
        return None
    ip = sm[np.argmax(hs[sm])]
    lvl = 0.02 * hs[ip]
    for i in range(ip + 1, len(hs)):
        if hs[i] <= lvl:
            return float(c[i])
    return None


def _axis_tests(ctx, a):
    """Area shares about one axis direction: flat (normal along it), perp (normal square to
    it: extrusion walls) and lathe (normal in the radial/axial plane of the best axis line)."""
    N, A, C = ctx.N, ctx.area, ctx.C
    tot = float(A.sum()) or 1.0
    d = N @ a
    flat = float(A[np.abs(d) > 0.9994].sum() / tot)
    perp = float(A[np.abs(d) < 0.035].sum() / tot)
    # axis point p0 (square to a) minimising the tangential normal component n·(a × (c − p0))
    rhs = np.einsum("ij,ij->i", N, np.cross(a, C))
    B = np.cross(N, a)
    w = np.sqrt(A)
    e1 = np.cross(a, [1, 0, 0] if abs(a[0]) < 0.9 else [0, 1, 0]); e1 /= np.linalg.norm(e1); e2 = np.cross(a, e1)
    X = np.c_[B @ e1, B @ e2] * w[:, None]
    sol = np.linalg.lstsq(X, rhs * w, rcond=None)[0]
    p0 = e1 * sol[0] + e2 * sol[1]
    rel = C - p0
    tdir = np.cross(a, rel); tn = np.linalg.norm(tdir, axis=1)
    ok = tn > 1e-9
    tang = np.abs(np.einsum("ij,ij->i", N[ok], tdir[ok] / tn[ok][:, None]))
    lathe = float(A[ok][tang < LATHE_BAND].sum() / tot)
    return {"axis": a, "flat": flat, "perp": perp, "lathe": lathe}


def signals(ctx):
    """The routing signals for one mesh (a dict; all shares are of the total area or of the
    total adjacency-edge length)."""
    N, A = ctx.N, ctx.area
    tot = float(A.sum()) or 1.0
    ang = ctx.adj_angles()                       # degrees
    el = _edge_lengths(ctx)
    el_tot = float(el.sum()) or 1.0
    out = {"zero_len": float(el[ang < 0.5].sum() / el_tot),
           "sharp30_len": float(el[ang > 30].sum() / el_tot)}

    # exact-normal clusters (CAD exporters give every facet of a flat face the same normal)
    key = np.round(N, 3)
    u, inv = np.unique(key, axis=0, return_inverse=True)
    inv = inv.reshape(-1)
    acc = np.bincount(inv, weights=A, minlength=len(u)) / tot
    adj = ctx.adj
    ci, cj = inv[adj[:, 0]], inv[adj[:, 1]]
    bnd = ci != cj
    tot_len = np.bincount(np.r_[ci[bnd], cj[bnd]], weights=np.r_[el[bnd], el[bnd]], minlength=len(u))
    shp = bnd & (ang > SHARP_BOUNDARY_DEG)
    sharp_len = np.bincount(np.r_[ci[shp], cj[shp]], weights=np.r_[el[shp], el[shp]], minlength=len(u))
    frac = sharp_len / np.maximum(tot_len, 1e-12)
    big = acc >= CLUSTER_MIN_AREA
    flat_ok = big & (frac >= 0.5)
    out["flat_sharp"] = float(acc[flat_ok].sum())
    out["n_flats"] = int(flat_ok.sum())
    order = np.argsort(-acc)
    peaks = [u[i] / max(np.linalg.norm(u[i]), 1e-12) for i in order[:3] if acc[i] > 0.01]

    # candidate axes: the biggest normal peaks and the eigenvectors of the normal scatter
    M = (N * A[:, None]).T @ N / tot
    _, Vec = np.linalg.eigh(M)
    cands = list(peaks) + [Vec[:, k] for k in range(2, -1, -1)]
    seen, tests = [], []
    for v in cands:
        v = v / max(np.linalg.norm(v), 1e-12)
        if any(abs(v @ s) > 0.995 for s in seen):
            continue
        seen.append(v)
        tests.append(_axis_tests(ctx, v))
    best_perp = max(tests, key=lambda t: t["perp"])
    best_lathe = max(tests, key=lambda t: t["lathe"])
    bl = best_lathe
    out["perp"] = best_perp["perp"]
    out["perp_axis"] = best_perp["axis"]
    out["lathe_axis"] = bl["axis"]
    out["lathe_curved"] = (bl["lathe"] - bl["flat"]) / max(1e-9, 1 - bl["flat"]) if bl["flat"] < 0.99 else 1.0
    out["lathe_curved"] = float(min(1.0, max(0.0, out["lathe_curved"])))
    return out


def route(sig):
    """(route name, one-line reason) from the signals."""
    flat, perp, lathe, sharp = sig["flat_sharp"], sig["perp"], sig["lathe_curved"], sig["sharp30_len"]
    if flat >= FLAT_SHARP_MIN and flat + perp >= FLAT_PLUS_PERP_MIN and lathe < LATHE_MIN:
        return "prismatic", f"sharp-bounded flats {100 * flat:.0f}% + walls square to an axis {100 * perp:.0f}%"
    if lathe >= LATHE_MIN:
        return "lathe", f"{100 * lathe:.0f}% of the curved area is a surface of revolution; flats {100 * flat:.0f}%"
    if flat < ORGANIC_FLAT_MAX and lathe < ORGANIC_LATHE_MAX and sharp < ORGANIC_SHARP_MAX:
        return "organic", (f"flats {100 * flat:.0f}%, lathe {100 * lathe:.0f}%, "
                           f"sharp edges {100 * sharp:.1f}% of edge length")
    return "mixed", f"flats {100 * flat:.0f}%, walls {100 * perp:.0f}%, lathe {100 * lathe:.0f}%"


def choose(ctx, prm, log):
    """Decide the route (unless the user forced one) and set the converter up for it.
    Returns (route, signals)."""
    sig = signals(ctx)
    forced = prm.route not in ("", "auto")
    r, why = (prm.route, "chosen by you") if forced else route(sig)
    log(f"Route: {r} ({why})")
    # the histogram crease and the density-independent strip test are for CAD-like parts; on
    # the organic test figure they made 48 small flats where the released rules make 14
    cad_like = r in ("prismatic", "lathe")
    if cad_like:
        prm.strip_rule = "sag"
    if cad_like and prm.ladder_crease and prm.crease_angle == type(prm).crease_angle:
        c = crease_from_histogram(ctx)
        if c is not None:
            prm.crease_angle = float(min(30.0, max(10.0, c)))
            if prm.crease_angle < 29.5:
                log(f"Sharp-edge angle set to {prm.crease_angle:.0f}° from the mesh's own edge-angle histogram")
    guess = prm.ladder_guess and prm.mode != "faceted"
    if r == "prismatic":
        prm.rough = bool(getattr(prm, "ladder_rough", True))
        if prm.rough:
            log("  extruded outlines are claimed first (roughing); the plain fitter is tried too if that misses the tolerance")
        if prm.plane_rule == "classic":
            # flat borders judged by length: keeps the facets of a big fillet or a coarse
            # cylinder from passing as flats (on the test set this only ever lost the 1.3 mm
            # flats of a lathe part, which takes the lathe route)
            prm.plane_rule = "length"
    elif r == "lathe":
        prm.rough = True
        prm.lathe_first = True
    elif r == "organic":
        prm.rough = False
        prm.skip_planes = True
        prm.fit_primitives = False
        if guess and not prm.organic_tol:
            prm.organic_tol = prm.ladder_organic_tol
    else:
        # mixed: when at least half the curved area turns about one axis there are lathe
        # shapes worth claiming first (Staged roughing); on the 160,000-triangle test figure
        # (73 %) that gave 3,271 faces in 268 s against 14,217 faces in 411 s without it.
        # One part's evidence; below a half the Classic fitter is used.
        prm.rough = sig["lathe_curved"] >= MIXED_ROUGH_MIN
        if prm.rough:
            log("  lathe shapes are claimed first (roughing), the rest is fitted afterwards")
        if guess and not prm.organic_tol:
            prm.organic_tol = prm.ladder_organic_tol
    if prm.organic_tol and r in ("organic", "mixed"):
        log(f"  problem areas will be guesstimated (freeform patches may be up to {prm.organic_tol:.3g} mm off)")
    log("  " + estimate(r, ctx.nF, bool(prm.organic_tol)))
    return r, sig


# seconds per triangle measured on the 2-core test machine (docs/research/README.md), per route;
# a 7th-gen i3 is about the same single-thread speed, an 11th-gen i5 roughly twice as fast
_RATE = {"prismatic": (0.5e-3, 2.5e-3), "lathe": (0.3e-3, 1.0e-3), "organic": (0.25e-3, 1.0e-3),
         "mixed": (1.5e-3, 6e-3)}


def estimate(route, n_tris, guess):
    """A rough time band for the log, so a long run is expected rather than discovered."""
    lo, hi = _RATE.get(route, _RATE["mixed"])
    if route == "mixed" and not guess:
        hi *= 2.5
    a, b = lo * n_tris, hi * n_tris

    def fmt(t):
        return f"{t:.0f} s" if t < 90 else (f"{t / 60:.0f} min" if t < 5400 else f"{t / 3600:.1f} h")
    return (f"rough estimate for this part: {fmt(max(a, 1))} to {fmt(max(b, 2))} on a 2-core laptop "
            "(an 11th-gen i5 about half that); the profile's time ceiling applies")


def claim_lathe_first(ctx, tol, log):
    """Lathe route: find the surfaces of revolution on the WHOLE mesh before any flat-face
    search, and remember which triangles they own (find_planes then leaves those alone and
    _layout_regions builds them as exact faces)."""
    from tostep.plugins.mesh2step import rough
    ids = np.arange(ctx.nF)
    found, _ = rough.find_revolves(ctx, ids, tol, max_shapes=4)
    if not found:
        log("  no lathe surface found on the whole mesh — continuing the usual way")
        return None
    lab = -np.ones(ctx.nF, dtype=np.int64)
    prims = []
    for k, (faces, prim) in enumerate(found):
        lab[faces] = k
        prims.append(prim)
    claimed = lab >= 0
    share = float(ctx.area[claimed].sum() / max(float(ctx.area.sum()), 1e-12))
    log(f"  {len(prims)} lathe surface(s) claimed first, covering {100 * share:.0f}% of the surface")
    # Only the triangles are remembered (so the flat-face search leaves them alone); the
    # shapes themselves are fitted again per smooth component by the Staged roughing, which
    # gave cleaner faces than carrying the whole-mesh fit through.
    ctx.claimed = claimed
    return claimed
