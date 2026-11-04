# Quad patches: a wireframe first, then surfaces between the wires (0.10.1)

**WORK IN PROGRESS: MANY FEATURES MAY BE BROKEN** (EVERYTHING to solid 0.10.1-wip, 22 October 2026)

*Working notes. Asked for: "Lets do the quadriflow and private outlines", after the sketch at the end of
IMPROVEMENTS_0_10.md. Everything measured on the 2-core Linux test machine; the organic test shapes are the
project's blob and 162,000-triangle figure plus three made for this note (a finer blob, a bumpy ring with a hole,
a peanut), all synthetic.*

## What was built

An experimental fifth engine, **Quad patches** (`app/quadfit.py`; `--engine quad`; *Advanced → Surface fitter →
Quad patches* on the page):

1. **Quad layout.** QuadriFlow (Huang, Zhou, Niessner, Shewchuk, Guibas, SGP 2018; BSD-3) lays the mesh out as a
   coarse all-quad mesh that follows its curvature. It runs as its own program (`app/bin/<system>-<machine>/`),
   so a crash in it cannot take the app down. Where it is not there, Instant Meshes (Jakob et al. 2015, BSD-3,
   the method QuadriFlow builds on) is used through the `pynanoinstantmeshes` package if that is installed; its
   quad-dominant result is split once into all quads.
2. **Wires.** Every quad corner is moved onto the mesh; every quad edge becomes one cubic B-spline drawn through
   seven points of the mesh along it, all on the same uniform parameters (so every curve has the same knots).
3. **Patches.** Every quad is filled with a Coons surface from its four curves (OpenCascade's
   `GeomFill_BSplineCurves`): untrimmed, no plate solve, and two patches meet exactly along the curve they share.
4. **Interior fit.** A Coons surface only knows its edges, so the inside of every patch is fitted to the mesh:
   two knots are inserted each way (the surface does not change), and the inner control points are found by least
   squares against the nearest mesh points of a grid of surface points, the boundary control points held. All
   patches are solved together in one sparse system that also asks, softly, for the two control points either
   side of every shared edge to lie on one line through it, which is the condition for neighbouring patches to
   meet without a crease. A patch the fit made worse than its plain Coons surface goes back to it.
5. **Refinement.** The result is measured against the mesh. If it is further away than the tolerance, the next
   quad layout is made finer by the rate the error fell between the last two layouts, up to 6,000 quads, eight
   layouts or the profile's time ceiling; the closest result is kept.

**Which parts it takes:** closed meshes with no real flat faces (the Ladder's sharp-bounded flat share under 5 %),
almost no sharp edges (under 0.2 % of edge length sharper than 30°) and not a turned shape (under 85 % of the
curved area about one axis, which the Quick preset makes as exact surfaces of revolution). Anything else is handed
to Classic, with the reason in the log.

## Measured: organic shapes

Faces, maximum and mean deviation from the mesh (mm), facet areas, wall time; the tolerance is the app's default
(0.05 % of the part's diagonal). Seam angle: the angle between the two faces' normals, sampled along every shared
edge (`median / 95th percentile / worst`).

| Shape (triangles; tolerance) | Quad patches | Classic | Quick (Ladder) |
|---|---|---|---|
| Blob (9,944; 0.028) | **492 faces, 0.022 / 0.004, 0 facets, 38 s; seams 0.05 / 0.19 / 1.1°** | 24 faces, *0.057* / 0.011, 8 s; seams 0.42 / 1.2 / 2.2° | 20 faces, *0.108* (guesstimated), 1.4 s; seams 0.59 / 2.1 / 2.8° |
| Finer blob (20,480; 0.036) | **59 faces, 0.017 / 0.004, 3.6 s; seams 0.01 / 0.07 / 0.19°** | 16 faces, 0.024 / 0.005, 42 s; seams 0.17 / 0.57 / 0.78° | 16 faces, 0.030, 32 s; seams 0.19 / 0.69 / 0.85° |
| Bumpy ring with a hole (9,216; 0.038) | **1,787 faces, 0.028 / 0.004, 0 facets, 124 s; seams 0.07 / 0.31 / 3.2°** | 1,044 faces, *0.144* / 0.015, **50 facet areas**, 31 s; seams 4.3 / 17 / 32° | 701 faces, *0.242*, 15 facet areas, 8 s; seams 4.7 / 17 / 27° |
| Organic figure (162,036; 0.029) | **57 faces, 0.010 / 0.002, 7.3 s; seams 0.01 / 0.07 / 0.23°** | 19 faces, 0.015 / 0.003, **421 s**; seams 0.04 / 0.15 / 0.25° | 19 faces, 0.027, 6.4 s; seams 0.25 / 1.2 / 2.1° |
| Peanut (20,480) | handed to Classic: "97 % of its curved area turns about one axis" | 17 faces, 0.041, 38 s | 17 faces, 0.041 |
| Knob (2,958) | handed to Classic: "0.8 % of its edge length is sharper than 30°" | 69 faces, 0.058 | 69 faces, 0.058 |

*Italic*: outside the tolerance. Every result was a closed, valid solid.

Reading it:

- **Quad patches is the only engine within tolerance on every organic shape**, never leaves facets, and has the
  smoothest seams everywhere (on the bumpy ring the others have creases of 17 to 32°, from facet areas and
  trimmed patches meeting at an angle; quad patches 3°).
- **It uses more faces** where the shape is busy (the blob 492 against 24, the ring 1,787 against 1,044): every
  face is a simple four-sided patch, where Classic's few faces are big trimmed patches that miss the tolerance on
  these shapes.
- **Speed depends on the shape**: on smooth, finely meshed parts it is the fastest (the figure 7 s against
  Classic's 421 s; the finer blob 4 s against 42 s); on the coarse blob and the ring it is slower (38 s and 124 s),
  because the error falls slowly with the number of quads there and the layout is refined several times. Most of
  that time is the fit's nearest-point searches and the solve.
- **Memory**: 0.4 to 0.75 GB on these shapes. A deliberately spiky blob needed 6,746 quads (0.049 mm, just outside
  its 0.044 mm tolerance, 296 s) and 1.4 GB. The first version kept a dense 0.5 MB matrix per patch and factorised
  the whole system directly, and was stopped by the out-of-memory guard at 6 GB on that blob; all patches now share
  one small matrix (every edge curve has the same parameters) and the joint system is solved by conjugate
  gradients without being formed.

## How the parts were chosen

- **Interior fit**: plain Coons patches needed 5,921 quads to reach the blob's tolerance (0.025 mm, 86 s). With
  the inside of each patch fitted to the mesh: 347 quads, 16 s; with two inserted knots each way: 117 quads, 9 s.
- **Seams**: fitted independently, the blob's patches met at up to 20° (median 2.4°): fine on the finely meshed
  figure (max 2.1°), visible on coarse meshes. A rigid rule that lines up the control points either side of every
  edge cost too much accuracy (5,116 quads and still outside the tolerance). Solved together with a soft seam
  condition: weight 1 gave seams up to 3.3° at 570 quads, **weight 3 up to 1.1° at 492 quads** (kept), weight 10
  up to 11° (the fit fought the condition) at 1,226 quads. Four or six inserted knots instead of two gave the same
  seams with more time.
- **Nearest points**: trimesh's exact search was 8 of 13 s; a KD-tree of triangle centres with the 16 nearest
  triangles tried is used for the fit (equal to the exact search to 1e-6 mm on the test blob); the final
  deviation is still measured with the converter's exact search.
- **Which parts**: the ridged knob passed the Ladder's organic test (2 % sharp edges) and came out 0.52 mm off
  (its ridges rounded over); the threshold is 0.2 %. A sphere and a torus came out at 55 and 221 faces where the
  Quick preset gives 2 and 9 exact faces, so turned shapes are handed over too.
- **The engine is opt-in.** Classic stays the default (standing rule); the presets are unchanged.

## QuadriFlow in the app

QuadriFlow has no installable package for macOS and only an old third-party one for Linux and Windows (a prebuilt
2020 library of unknown build), so it is built from its own source: `tools/build_quadriflow.py` fetches the pinned
QuadriFlow commit, an Eigen mirror commit and Boost 1.86's headers (SHA-256 checked) from GitHub, builds with
`BUILD_FREE_LICENSE=ON` (Eigen's MPL2 parts only) and puts the program in `app/bin/<system>-<machine>/`. A build
for 64-bit Linux ships in the repository (`app/bin/linux-x86_64/quadriflow`, 2.4 MB; the GCC C++ runtime built in
under its runtime exception, the system's C library used as it is, so it needs glibc 2.38 or newer: Ubuntu 24.04,
Linux Mint 22 and later). Every licence and the exact sources are in `app/bin/README.txt`. The build tool was run
here from an empty folder: it built a program giving byte-for-byte the same quad layout as the hand build.

All QuadriFlow's parts are under permissive licences (BSD-3, MIT, Boost, Apache 2.0, MPL2), in line with the
project's rule against GPL code. Instant Meshes' package (`pynanoinstantmeshes`, BSD) has wheels for 64-bit Linux,
Windows and Apple-silicon macOS; it is not installed by default. On the blob it gave 480 quads at 0.024 mm in
50 s, seams up to 1.5° (QuadriFlow: 492 quads, 0.022 mm, 46 s, seams up to 1.1°, with the same fitting; both measured before the faster solver).

## Private outlines for round faces (the other half of the request)

IMPROVEMENTS_0_10.md left one lead: fitted and flat faces got private outline copies in 0.10.0, round faces did
not, because the two halves of a sphere only build on a shared seam. Now a round face is built on its own copy of
the outline too, **unless its whole outline runs along other round faces** (the sphere's halves, a cylinder band
between two others). The ridged knob, whose dome is a sphere patch, goes from **963 faces to 69** (0.058 mm either
way); the full 12-mesh benchmark against 0.10.0 is below.

| 12 test meshes, 0.10.0 → 0.10.1 | Classic | Quick (Ladder) | Staged |
|---|---|---|---|
| Knob | 963 faces, 6 facet areas → **69 faces**, 4 facet areas, 0.058 mm both, 16 → 11 s | 963 → **69 faces**, 24 → 15 s | 950 → **60 faces**, 0.398 → 0.400 mm, 9 → 4 s |
| The other 11 | the same results (faces, facet areas, deviation) | the same | the same |
| All 12 | 13,996 → **13,102 faces**, 282 → 278 s | 1,574 → **680 faces**, 141 → 129 s | 1,478 → **588 faces**, 124 → 108 s |

Staged on the knob is still 0.40 mm off (the plate fill that bridges a groove, IMPROVEMENTS_0_10.md); Quick keeps
handing that part to the plain fitter, which gives 0.058 mm.

## Not tested

- Real scans and sculpts (the organic shapes here are synthetic); meshes with holes in them (open meshes are
  handed over); QuadriFlow on Windows and macOS (the build tool's Windows and macOS paths were not run).
- Instant Meshes on Windows and macOS.
- Very large layouts (over 2,000 quads) for memory on a small computer.
