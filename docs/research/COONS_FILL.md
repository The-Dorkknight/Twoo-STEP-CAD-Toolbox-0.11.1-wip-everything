# Coons gap filler and the per-region hybrid (2 November 2026)

Research note for paper disclosures **D18** (built, opt-in) and **D19** (measured, not built). The question was
whether the two unbuilt rungs of the claim ladder, *four-sided patch* and *Coons fill*, are worth building inside the
ordinary pipeline, and whether a hybrid of quad patches and exact faces is in reach.

First, a correction. The claim-ladder drawing (research README, paper Figure 3 up to version 1.2) said "not yet" for
four-sided NURBS patches and Coons fill. That was out of date: the Quad patches engine (D8, `quadfit.py`) has made one
four-sided Coons patch per quad, its inside fitted to the mesh, since 0.10.x, and it is what the Standard preset uses
for smooth organic parts. What was really missing:

1. Coons fill as the gap filler *inside* Classic / Staged / Ladder, in place of the plate filler (research README
   table, row 3). **Built now, opt-in.**
2. The per-region hybrid: quad patches on organic regions, exact faces elsewhere. **Measured, not built.**

Everything below was measured on a 2-core Linux machine (part of it while another conversion ran on the second core).
The scripts are in [scripts/coons_fill/](scripts/coons_fill/); the test parts are made by `mkmesh.py`, plus
`tests/data/knob_plain.stl` and the coarse blob of `tests/test_coons_fill.py`.

## 1. Coons gap filler (D18)

Code: `classic.py`, `Builder.nurbs_face_coons`, with `_corner_candidates`, `_sides`, `_coons_grid`, `_trim_pc`;
setting `Params.gap_fill` (`plate` default, `coons+plate`, `coons`, `off`), Advanced → *Gap filler*, `--gap-fill`.

### The route to something that works

| Try | Result | What changed next |
|---|---|---|
| Corners at the four sharpest turns, sides interpolated through 13 points, trimmed by the outline with OpenCascade's own projection | 0 of 16 knob patches within tolerance; most faces refused | Score candidate corner sets instead |
| Corner sets scored by the plain Coons surface's distance to the mesh | 1 of 16; trimming still failed on most | Look at why |
| Why trimming failed | the face builder's curves on the surface were thousands of mm off | Make our own curves on the surface |
| Our own curves (12 points per edge projected and interpolated) | trims, but the outline was 0.1 to 1.5 mm off the surface | Look at the sides |
| Sides through 13 points | missed the blob's outline by up to 0.53 mm | Fit sides to all outline points |
| Sides approximated separately | Coons patches of up to 60 x 100 control points; the fit used 5 GB and was killed | Same uniform knots for all four sides |
| Shared knots, fitted by least squares, ends pinned | sides within half the tolerance with 6 to 26 control points | |
| Fit against the patch's own triangles only | stuck at about 2 mm: on the coarse blob the smoothed outline runs up to 2 mm outside them | Two rings of neighbours |
| Untrimmed faces (their own edges as the outline) | 8 of 13 blob patches within tolerance, but the assembly could not join them: **2,688 faces** | Trimmed faces need a surface larger than the outline |
| Extend the fitted surface (GeomLib) | OCP cannot return GeomLib's in-place result | |
| Extend by resampling and approximating again | 0.02 to 0.26 mm drift | Grow the outline instead |
| **Outline grown outwards along the mesh, Coons on that, inside fitted, trimmed by the real outline with our own curves** | works; numbers below | shipped, opt-in |

### Measured

Every patch the least-squares fit missed, both fillers tried on the same patch (`cmp.py`; raw rows in
`scripts/coons_fill/*.cmp.json`):

| Part | Patches | Within tol.: Coons | Within tol.: plate | Time Coons | Time plate |
|---|---|---|---|---|---|
| Coarse blob, 5,120 triangles, tol 0.063 mm | 13 | 8 | 2 | 15 s | 6 s |
| Ridged knob, tol 0.025 mm | 16 | 6 | 4 | 19 s | 8 s |

Whole parts (`run1.py`; `final_classic.txt`), Classic engine, all closed and valid:

| Part | plate (default) | Coons | Coons, then plate |
|---|---|---|---|
| Coarse blob | 26 faces, 0.141 mm, 13.1 s | 21 faces, 0.145 mm, 20.3 s | 21 faces, 0.145 mm, 23.1 s |
| Ridged knob | 69 faces, 0.058 mm, 14.4 s | 78 faces, 0.058 mm, 20.8 s | 69 faces, 0.058 mm, 30.7 s |
| Blob cut flat | 17 faces, 0.083 mm | same | same (no patch needed a filler) |
| Bumpy box | 13 faces, 0.076 mm | same | same (no patch needed a filler) |
| Cat-like figure, 82k triangles | 29,961 faces (facet fallback), 0.014 mm, 706 s | not run | same result, 871 s: Coons used on 1 of 57 patches, 183 s spent on it |

### Conclusion

Better patch by patch (14 of 29 against 6), and the two fillers fail on different patches. Not cheaper: about twice
the time per patch (the corner search and the fit's nearest-point searches). On the fine cat-like mesh it only cost time (+165 s for one patch). Whole parts barely moved, because their
worst gap is set by outlines and other faces. **So it ships opt-in and the plate filler stays the default.** Worth
trying next: run it only on patches the plate filler misses (the reverse order), and measure on real scans.

## 2. Per-region hybrid (D19)

Test part: the blob cut flat at z = -12 mm (`blobcut.stl`, 15,338 triangles).

| Layout | Result |
|---|---|
| Flat base removed, QuadriFlow on the open rest (`qopen.py`) | 332 quads; the open edge pulled in to 3.9 to 8.1 mm above the cut |
| Same with Instant Meshes | 1,620 quads; open edge from 4.0 mm below to 4.9 mm above the cut |
| Closed mesh, QuadriFlow `-sharp` (`qsharp.py`) | 597 quads: 176 on the base, 56 on the rim, of which 32 have an edge along the rim and 24 cross it corner-wise; Coons patches built on it: 0.379 mm off (tol 0.049), not closed |

Conclusion: neither remesher gives the layout a hybrid needs. The next step would be repairing the 24 crossing rim
quads (snap the odd corner along the rim, or split the quad along it) and then merging the quads that lie on one
exact surface into one exact face. That is a research project of its own, not an evening's work; it is written up as
a method in D19 so it stays free to use.
