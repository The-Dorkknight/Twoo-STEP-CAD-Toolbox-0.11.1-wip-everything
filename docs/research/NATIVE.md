# What does the heavy lifting, and where Python sat in the way

**WORK IN PROGRESS: MANY FEATURES MAY BE BROKEN** (EVERYTHING to solid 0.9.1-wip, 14 October 2026)

*Working notes. Asked for: "check if as many dependencies which do the heavy lifting are bare metal as possible,
if there are any translation inefficiency we can get rid of." Measured on the 2-core Linux test machine (8 GB),
Python 3.11, with the library versions listed below.*

## Short answer

Almost all the heavy lifting already ran in compiled code: OpenCascade (C++) builds, sews, checks, meshes and
writes every solid; numpy / scipy do the arithmetic; GEOS, libspatialindex and OpenCV do the 2D geometry, the
spatial lookups and the pictures. Profiling every stage found two places where Python sat between the program and
that compiled code doing real work:

1. **Fixing the winding of a mesh with flipped triangles** went through trimesh's own repair, which walks the faces
   one pair at a time with networkx, a pure-Python graph library. Replaced by `app/meshfix.py` (scipy's compiled
   search, every flip worked out at once): **28 times faster** on its own (456 s → 16 s over 280 test cases), and a
   162,000-triangle figure with flipped faces converts with *Quick* in **9.4 s instead of 26.1 s**, same result.
2. **Shape guessing on blended rounds** calls numpy's convenience functions (`np.linalg.norm`, `np.cross`, `np.c_`,
   `np.outer`) hundreds of thousands of times on arrays of 3 to 1,500 numbers, where their Python-side argument
   handling costs more than the sums. Rewritten as the same operations in the same order, so **the same bits**:
   the 11-mesh benchmark ran **about 8 % faster** (560 → 515 s; 10 % on the blob plate), every result identical.

Nothing else was worth moving: the rest of the time is inside OpenCascade already.

## The dependencies

| Library | What it does here | Runs as |
|---|---|---|
| **cadquery-ocp 8.0** (OpenCascade) | every B-rep step: faces, sewing, checks, meshing, STEP read / write | C++ (pybind11 bindings) |
| **numpy 2.4** | all array arithmetic | C |
| **scipy 1.17** | KD-trees, sparse graphs (components, shortest paths, now winding), least squares | C / C++ / Fortran |
| **pyfqmr 0.5** | mesh reduction (quadric decimation) | C++ (Cython) |
| **rtree 1.4** | spatial index under trimesh's closest-point queries | C++ (libspatialindex) |
| **shapely 2.1** | 2D outlines and sections | C (GEOS) |
| **mapbox-earcut 2.1** | polygon triangulation | C++ |
| **opencv-python-headless 4.13** | reading pictures, the image tracer, reading QR codes | C++ |
| **psutil 7.2** | memory and core counts | C |
| **trimesh 5.1** | mesh loading, sampling, closest points, repair | Python on numpy (mostly vectorised) |
| **networkx 3.6** | trimesh's graph walks (still used by its hole filling) | pure Python |
| **segno 1.6** | drawing the diagnostic QR code | pure Python (a few hundred bytes, once) |
| **pluggy 1.6** (bundled) | drop-in plug-in calls | pure Python (a handful of calls per job) |

The pure-Python ones are fine where they are except networkx in trimesh's winding repair (fixed, below). Hole
filling still uses networkx, but only on the boundary edges of the holes, a few hundred at most.

## Where the time went (cProfile, Classic engine)

| Mesh | Total | Shape guessing (Python + small numpy calls) | The rest (mostly inside OpenCascade) | Notes |
|---|---|---|---|---|
| Blob plate (6,844 triangles) | 103 s | **68 s** (`grow_primitives`; `_support` 33 s, `_hyp_from_pair` 19 s) | 35 s | 125,000 support tests, 58,000 two-face guesses |
| Fine tray (3,440) | 50 s | 17 s | 33 s | |
| Knob (2,958) | 45 s | 6 s | 39 s | |

Inside the guessing loop, `np.linalg.norm` alone was called 746,000 times on the blob plate (10 s with its
argument handling), `np.cross` 68,000 times (8 s), `np.c_` 53,000 times (2.8 s). Each does a few
nanoseconds of arithmetic and several microseconds of Python. Starting a job also costs about 3.4 s of loading
OpenCascade's compiled modules; that is the price of using it and is paid once per job.

Looked at and left alone, because the time is already compiled code or too small to matter:

- *Reading triangles back out of OpenCascade* (`shape_to_mesh`, the 3D preview): one Python call per node, about
  1 s of 103 s on the blob plate. The meshing itself, inside OpenCascade, is what takes long (on the vase, 115 of
  116 s). A bulk route exists (write the shape as STL and read it with numpy), but it orients triangles differently,
  so the deviation check would not give the same numbers.
- *Closest points on the mesh* (trimesh with rtree, in the deviation check): 2.6 s of 103 s; exact, and a faster
  approximate version would change the reported deviation.
- *The two-face guess's 3 × 2 least-squares solve* (`np.linalg.lstsq`, 5 s of 103 s): a closed formula would be
  faster but not bit-identical, so the guesses (and possibly the result) could change. Left for a decision.
- *Jetwash*: one OpenCascade call per face already (see EFFICIENCY.md).

## 1. The winding repair (`app/meshfix.py`)

trimesh's `fix_winding` returns at once when the triangles already agree (most files), so a clean STL was never
slow. A file with flipped triangles (common from some exporters and scanners) went the slow way: networkx builds a
Python graph of the faces, and each of the ~240,000 neighbour pairs of a 160,000-triangle mesh is checked with a few
small numpy calls. `meshfix` builds the same graph as a scipy sparse matrix, runs one compiled breadth-first search
over all pieces (an extra node joined to the first face of each piece), works out for every neighbour pair at once
whether their shared edge runs the same way, and settles each face from its parent in one pass over plain lists.
trimesh's own check for inside-out bodies runs afterwards, unchanged.

**Same answer?** The first face of each piece keeps its direction, as in trimesh, and on a mesh that can be wound
consistently the rest follows from it, so the result is unique. Compared face for face with trimesh on all 12 test
meshes, the 162,000-triangle figure and the knob from the tests, each with 0, 1, 30, 50 and 90 % of the faces
flipped, with holes punched in, doubled into two bodies (one inside out), with and without trimesh's per-body
option: **all 196 cases identical.** Where they differ: a mesh made of **separate pieces of very different size** with
broken winding. For any piece smaller than half the mesh, trimesh's choice of starting face comes from the order of
a Python set (a side effect of how networkx views part of a graph), so which way round it turns that piece is
effectively arbitrary; meshfix always starts from the piece's first face in the file. Of 84 such made-up cases (a
small ball added beside each test part, three ways), 16 came out the other way round, all with trimesh's per-body
option off (with it on, every inside-out body is turned outward anyway). Neither answer is more right: such a file has no
"right" winding to recover.

| | trimesh | meshfix |
|---|---|---|
| all 280 cases above, total | 456.8 s | 16.0 s |
| 162,036-triangle figure, 30 % flipped, whole *Quick* conversion | 26.1 s | **9.4 s** (same 19 faces, 0.0267 mm) |
| knob, 30 % flipped, *Quick* / *Standard* | 12.7 / 15.1 s | 11.6 / 15.8 s (same results; small mesh, noise) |

## 2. The shape-guessing helpers (`app/stl2step_core.py`, `app/stl2step_core_v2.py`)

`_row_norms(x)` is `np.sqrt(np.add.reduce(x * x, axis=-1))`, which is exactly what `np.linalg.norm(x, axis=...)`
computes once its argument checks are done; the cross product, the 3 × 2 matrix and the vector lengths in
`_hyp_from_pair` are written out the way numpy evaluates them; `np.outer(a, b)` became `a[:, None] * b`. Checked
three ways:

- 2,000 random arrays of every size and scale: bit-identical (`tests/test_meshfix.py` keeps a short version).
- **Call by call**: full conversions of the blob plate, the fine tray and the knob (Classic) and the coarse tray
  (v2) with the old functions run next to the new ones on every call: about 900,000 calls, every answer bit-identical.
- **The benchmark** (11 meshes × Classic, Ladder, Staged, through `tools/bench.py`, old and new code back to back on
  the quiet machine): all 35 comparable results identical in every field except time.

| Engine (11 meshes) | Before | After |
|---|---|---|
| Classic (10 meshes, vase left out, see below) | 201.4 s | 189.9 s |
| Ladder | 149.4 s | 138.4 s |
| Staged | 209.5 s | 186.4 s |
| **All 35 runs** | **560.3 s** | **514.7 s (−8 %)** |

Single runs vary by a few percent on this machine, so read the per-mesh gains (blob plate 60 → 54 s, oval Staged 18
→ 14 s) as approximate.

## Found on the way: the vase's Classic result varies from run to run

*Found and fixed in 0.10.0 (IMPROVEMENTS_0_10.md): OpenCascade's plate filler chained the loose outline edges through a map hashed on memory addresses. The paragraph below is left as it was written.*

The vase is left out of the table above because its *Classic* result is not repeatable, **with the old code as
well**: three runs of the old code and two of the new gave either 28 or 30 regions kept as exact facets (always
8,665 faces in all, 0.044 mm), and 165 to 245 s. The runs part at the first refining pass (3 or 4 patches found
outside tolerance), after a stage whose time also swings (66 s or 134 s). Not found yet; candidates are
OpenCascade's parallel meshing in the patch checks and the memory test that changes how much is checked when the
computer is short of memory. The Ladder and Staged results for the vase were the same every time. Left for a later
pass.

## Not tested

- Windows and macOS; other numpy versions than 2.4 (the bit-for-bit argument holds for numpy's current
  `linalg.norm`; an older numpy computing it another way would give the same values to the last bit or two).
- Real scans with broken winding (the flipped meshes here were made by flipping random faces of good ones).
