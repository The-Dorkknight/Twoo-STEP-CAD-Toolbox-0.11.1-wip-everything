# The 0.10.0 pass: quality and speed of every tool except Hardpoints

**WORK IN PROGRESS: MANY FEATURES MAY BE BROKEN** (EVERYTHING to solid 0.10.0-wip, 21 October 2026)

*Working notes. Asked for: "research other ways to improve quality and/or performance of all tools except
hardpoints", plus two ideas to mull over (slicing with a 0.0001 mm nozzle; a wireframe filled with surfaces for
organic shapes). Every number is from the 2-core Linux test machine (8 GB), the same 12 test meshes as the earlier
notes, measured with `tools/bench.py` on the old and the new code back to back on a quiet machine; results are
compared field by field, not only by time.*

## Short answer

Five real defects were found by profiling and tracing, all in the STL → STEP converter, and two were the kind that
quietly spoil results rather than slow them down:

1. **Results were not repeatable** (the vase: 28 or 30 facet areas from one run to the next). OpenCascade's plate
   filler chains the loose outline edges it is given through a map hashed on *memory addresses*; which edge it
   started from, and whether it then asked a null vertex for its point, changed with where things landed in
   memory. It now gets the outline as one chained wire of its own. Three runs: identical.
2. **A face's repair damaged its neighbours.** OpenCascade's face repair widens outline vertices to make a wire
   close on a surface that misses them, and on a *shared* outline that widening reached the faces next door: on
   the knob one vertex went from 0.03 mm to 4.7 mm of tolerance, after which every check let a neighbouring face
   that was 1.3 mm off through; in other places the same accident turned good patches into facets. Fitted and
   flat faces are now built on their own copy of the outline. Classic on the knob: 1,103 faces and 0.129 mm →
   963 faces and 0.058 mm.
3. **Draft never finished on a dense flat-sided mesh** (the 220,000-triangle tray): its reduction had a bug in the
   flat-face retriangulation (two straight outline stretches meeting at one corner fanned the same triangle twice;
   zero-area triangles from collinear points could not become faces), so the reduction was thrown away and the
   full mesh fitted. Fixed. The 60 s cap of that preset also never fired after a reduction (the start time was
   lost when the mesh was replaced). Fixed.
4. **Jetwash** tried every candidate at once, once, and defeaturing cost grows faster than the number of faces: on
   the tray that one attempt (81 faces) cost 38 s and failed. Batches of 16, and the cheap volume test before the
   slow validity check: 102 → 63 s, same result on all 12 files.
5. **Quick on extruded parts** used the plain fitter where the Staged roughing is far better (noted in 0.8.0 and
   left for a decision). The prismatic route now roughs first, with the plain fitter as a measured fallback.
   Quick's total faces over the 12 meshes 4,767 → 1,574; its worst deviation 3.0 → 0.19 mm.

The SCAD → STEP and Image → STEP tabs were profiled and left alone: their time is in OpenSCAD and in ImageToSketch,
not in this app's own code (numbers below). The two ideas are weighed at the end.

## Benchmark: 12 meshes, three engines, old against new

Faces, maximum deviation from the mesh (mm) and wall time; every result a closed, valid solid in both runs.
Classic's and Staged's results change only where defect 2 reached them; Quick's change by design (defect 5).

| Mesh | Classic before → after | Quick (Ladder) before → after | Staged before → after |
|---|---|---|---|
| Blob | 24 faces, 0.059, 11.9 s → 24, 0.057, 8.6 s | 20, 0.113, 1.9 s → 20, 0.108, 1.7 s | 24, 0.059, 18 s → 24, 0.057, 15 s |
| Blobs plate | 823, 3.000, 36 s → 824, 3.000, 29 s | 823, **3.000**, 38 s → **121, 0.191**, 40 s | 121, 0.191, 48 s → 121, 0.191, 42 s |
| Bracket | 8, 0.007 → same | 8, 0.007 → same | 8, 0.007 → same |
| Knob | **1,103, 0.129**, 10.6 s → **963, 0.058**, 16 s | 951, 0.129, 8.8 s → 963, 0.058, 24 s | 951, 0.129, 8.9 s → 950, **0.398**, 8.7 s |
| Oval plate | 141, 3.658, 10.6 s → same | 195, **2.076**, 8.8 s → **25, 0.112**, 28 s | 18, 0.081, 11 s → 18, 0.081, 9 s |
| Coarse plate | 121, 0.078, 4.8 s → same | 121, 0.078, 4.8 s → 121, 0.078, 13 s | 39, 0.142, 9.5 s → 39, 0.142, 9.0 s |
| Fine plate | 64, 0.102, 5.0 s → same | 62, 0.102, 4.7 s → 41, 0.076, 9 s | 43, 0.076, 10 s → 43, 0.076, 9 s |
| Sphere | 2, 0.045 → same | 2, 0.045 → same | 2, 0.045 → same |
| Torus | 526, 0.185, 28 s → 631, 0.160, 23 s | 9, 0.076, 3.3 s → same | 9, 0.076, 3.8 s → same |
| Coarse tray | 635, 0.235, 6.5 s → same | 635, **0.235**, 6.1 s → **119, 0.079**, 5.8 s | 119, 0.079, 5.8 s → same |
| Fine tray | 1,927, 0.076, 14.6 s → same | 1,927, 0.076, 13.6 s → **131**, 0.076, 7.4 s | 131, 0.076, 6.5 s → 131, 0.076, 7.7 s |
| Vase | 8,665, 0.044, 116 s → 8,656, 0.044, 164 s | 14, 0.025, 6.6 s → same | 14, 0.025, 12 s → same |
| **All 12** | 14,039 faces, 249 s → 13,996 faces, 282 s | **4,767 faces, 98 s → 1,574 faces, 141 s** | 1,479 faces, 138 s → 1,478 faces, 124 s |

Reading it:

- **Classic** is better on the two parts where defect 2 had bitten (knob, torus) and the same elsewhere; the
  vase is slower (116 → 164 s) because plate fills that used to fail by accident now run to the end, and two more
  of its areas come out as patches instead of facets (28 → 26). The 3.0 and 3.66 mm on the two blob plates are
  the known cusp problem of the Classic fitter (EFFICIENCY.md); the Fine preset keeps those cusps.
- **Quick** gives a third of the faces it did, and its worst case went from 3 mm off to 0.19 mm. It is slower on
  the three parts where the roughed result missed twice the tolerance and the plain fitter was run as well (oval
  9 → 28 s, coarse plate 5 → 13 s, knob 9 → 24 s); on the fine tray it is twice as fast.
- **Staged** on the knob is the one result that got worse (0.129 → 0.398 mm): a plate fill bridges a narrow groove
  of the knob and passes its own checks (the groove's bottom belongs to a neighbouring region, which the fill's
  check does not sample). Before, that same face was refused by the *accident* of defect 2. The result's note says
  "16 times the tolerance away, near x 0.2, y 10.0, z 9.5 mm"; Quick runs the plain fitter in that case and keeps
  its 0.058 mm result. A fill check that samples the neighbours' vertices too is the proper fix, left for later.

## 1. Results were not repeatable

Every per-region decision of three Classic runs on the vase was traced and diffed. The first difference was always
inside OpenCascade's plate filler: `BRepFill_Filling.Build()` on the *same* edges and the same constraint points
(hashes checked) succeeded in one process and threw `Standard_NullObject("BRep_Tool:: TopoDS_Vertex hasn't
gp_Pnt")` in another, always consistently within one process. Six attempts in one process all agreed, so it was
not a race; two processes with `PYTHONHASHSEED=0` still disagreed, so it was not Python's hashing. Variants tried
in a failing process: the same edges in reverse order, succeeded; private copies of the edges (vertices not
shared), always failed; the edges taken from a wire built with `BRepBuilderAPI_MakeWire`, always succeeded. So it
is the filler's own chaining of loose edges, which goes through OpenCascade's `TopTools` maps, hashed on the
address of each shape's underlying object; a different memory layout gives a different chaining order and, on an
outline with a very short edge (0.03 mm here), sometimes a null vertex.

Fix (`stl2step_core.Builder.nurbs_face_fill`): the outline edges are chained end to end by the app
(`_wire_order`, by their end points, each edge kept as given), edges shorter than the filler's own 3D tolerance
are left out, and the filler gets a **private copy** of that chain (`_own_wire_edges`) because it alters the edges
it is given. The surface it produces is the same as before when the old code happened to succeed (G0 error
1.43e-14 in both). Three traced runs of the vase after the fix: identical in every decision; the benchmark's vase
row repeats.

## 2. A face's repair damaged its neighbours

Found while checking the first fix: with the filler now behaving, the knob came out as 951 faces with a 1.3 mm
error, where the old code gave 1,103 faces and 0.129 mm. Tracing vertex tolerances through the run showed that
`ShapeFix_Face`, run on every fitted face in `Builder._trim`, had raised the tolerance of two *shared* outline
vertices to 4.74 and 4.36 mm while repairing a least-squares surface that missed its outline (its own fit was
2.8 mm off and was refused anyway). Those vertices are shared with the neighbouring faces, and every later check
(`BRepCheck`, `_face_ok`, the sewing) accepts anything within a vertex's tolerance, so a neighbouring face 1.3 mm
off passed as valid. The old code had the same side effect; the 1,103-face result was the accident falling the
other way (that face refused, and good patches next to the widened vertices refused with it, which is where many
of the knob's 882 facets came from).

Fix: `Builder.wire(loop, own=True)` gives a face its own deep copy of the outline (`BRepBuilderAPI_Copy` of the
whole wire, so the copy still shares its vertices inside); `_trim` (fitted surfaces) and `plane_face` use it. The
repair then widens only the copy. Since a private outline hides the gap from everyone else, `_trim` also
**refuses a face whose repair had to widen the outline by more than twice the tolerance** (twice: the tolerance
the assembly's sewing closes anyway). Exactly the tolerance was tried first: it refused good patches too (the
organic blob 24 → 46 faces, 0.06 → 0.13 mm; the torus 526 → 1,132 faces; Classic 15 % slower in all), so twice it
is.

Round faces (`prim_face`) keep the shared outline: the two halves of a sphere only build because the first half's
repair fixes the shared seam for the second (with private outlines the sphere came out as a shell of two identical
halves, volume 0.11 mm³). On the knob a private outline for its one cylinder face would have let the ridged grip
fit as **61 faces** instead of 963 (the cylinder's repair is what widens the vertices the grip's patches share), so
there is a real gain left here: private outlines for round faces that do not wrap a closed surface's seam.

The v2 fitter has always built every face on its own copies (its `fresh_wire`); Classic now does the same for
fitted and flat faces.

## 3. Draft on a dense flat-sided mesh

`simplify_keep_features` (the reduction behind Draft's "meshes over 50,000 triangles are reduced first") rebuilds
each flat face from its outline alone with earcut, then puts back the outline points that earcut drops on straight
stretches by fanning the triangle along the stretch. Two defects on the 220,000-triangle tray:

- where two straight stretches met at one corner triangle, that triangle was fanned twice from the original
  triangle list, leaving 8 open edges per face (16 in all), so the stitched mesh was not closed and the whole
  reduction was discarded ("couldn't keep the mesh closed — using the full mesh instead");
- earcut also emits zero-area triangles from collinear outline points; they survived the area check (they add
  nothing) and could not become faces later, so the faceted fallback had gaps and had to sew.

`_retriangulate_flat` now fans one stretch at a time on the current triangles, drops zero-area triangles before
fanning (the fan puts their points back with real triangles), and checks at the end that the new triangles have
exactly the outline's edges and no zero-area triangle, or returns None (the region is then reduced with the
outline-locked simplifier instead). The tray: reduced to a closed 48,400-triangle mesh in 0.8 s, 0 zero-area
triangles, 48,400 of 48,400 facets buildable; the volume unchanged to 0.01 mm³.

The time budget: `convert` rebuilds its mesh context when the mesh is replaced (reduction, Guesstimate, Staged),
and the start time lived on the old context, so `budget_s` counted from the rebuild, never from the start. Carried
over now (`tests/test_repeatable.py` checks every rebuild).

Draft on the tray, measured: see the big-mesh table below.

## 4. Jetwash: batches, and the cheap test first

`cProfile` on the fine tray's Classic STEP (1,927 faces, 71 candidates): 63 s of 99 s in `BRepAlgoAPI_Defeaturing`,
of which one call, the old "everything at once" attempt over all 81 candidate faces, took 38 s and failed; 16 s in
`_accept` (a full `BRepCheck_Analyzer` of every trial result, 0.15 s each, before the 0.05 s volume test that
rejected most of them); 5.6 s in candidate detection (14 rounds). Defeaturing time against the number of faces
removed, same solid: 1 face 0.11 s, 2 → 0.54, 4 → 1.1, 8 → 2.1, 16 → 4.8, 32 → 14.5, 64 → 34.6, 81 → 38.2 s: about
0.3 s a face up to 16, then much more. So a batch is only worth it when it succeeds, and a big batch that fails
costs more than its features would one at a time.

Now: batches of up to 16 faces (`FAST_FACES`) while they succeed; the first failure switches to one at a time;
four single successes in a row switch batches back on. `_accept` does the volume test first. Fine tray 102 → 63 s,
vase 46 → 40 s, all 12 test files identical in faces, volume and features removed; a made-up box with 24 tiny
bosses is washed in 0.4 s either way (`tests/test_jetwash.py`).

## 5. Quick on extruded parts

EFFICIENCY.md had measured that Staged beats Classic on six of seven extruded-profile test parts and left the
Ladder's prismatic route unchanged for lack of evidence. The table above is that evidence (the blobs plate 823 faces
and 3.0 mm → 121 and 0.19; the oval 195 and 2.1 → 25 and 0.11; the coarse tray 635 and 0.24 → 119 and 0.08; the
fine tray 1,927 → 131). The prismatic route now sets `rough=True`; `--no-ladder-rough` keeps the plain fitter.
The one part where roughing is worse, the coarse plate (0.142 against 0.078 mm), is caught by a new rule in
`engines.py`: when the roughed result is more than twice the tolerance from the mesh, on the prismatic or the
lathe route, the plain fitter is run too and the better result kept (lower deviation; a valid solid first). That
rule also rescues the knob (lathe route) from the plate-fill problem described under Staged above. Cost: the plain
run on top, only on those parts (oval +19 s, coarse plate +8 s, knob +15 s).

## 6. Joining facets without sewing

On the 48,400-facet reduced tray, timed step by step: building the triangle faces 3 s, **sewing 69 s**,
`ShapeFix_Shell` 14 s, shell → solid (shell and solid fixes) 33 s, `ShapeFix_Shape` on the solid 21 s, the
sealed check 8.5 s, writing 14 s. Facet faces already share their edges by construction, so the sewing is only
needed where fitted faces meet them. `_join_or_sew` now puts the faces straight into one shell and, when every
edge is shared (`BRep_Tool.IsClosed`) and the solid seals, skips the sewing; otherwise it sews as before. Since
fitted and flat faces now carry their own outline copies (defect 2), this helps the fully faceted paths (the
faceted fallback, the checkpoint of a mesh whose exact faces are all facets) and costs nothing elsewhere (the
closed test is instant and false). Making the direct join work with fitted faces too would need the repaired
faces to hand their widened-but-acceptable outline back to the shared edges; not attempted.

## 7. Smaller things

- The point-on-surface projector (`GeomAPI_ProjectPointOnSurf`) was built afresh for every one of the 400 points
  the plate filler measures per attempt; built once per surface and reused (`_projector`) it gives the same
  numbers (1,800 points compared, identical) in a tenth to a third less time on plate surfaces. The faster
  projection algorithms were measured and rejected: OpenCascade's grid-tree extrema were up to 5 mm wrong on these
  surfaces, `ShapeAnalysis_Surface` up to 1.6 mm.
- Found and left: the vase's Classic run spends 25 s in the filler's own measuring loop (now less) and 34 s inside
  the filler; a closed-form solve could replace the 3 × 2 least squares in the shape guesser (5 s of 103 s on the
  blobs plate) but is not bit-identical.

## SCAD → STEP and Image → STEP

- **SCAD**: the example bracket, 4.8 s: 0.3 s building the geometry here, the rest in OpenSCAD (its CSG export,
  its verification render, and the first `openscad --version` probe, 3.3 s cold, 0.02 s warm). A stress model
  (minkowski, rotate_extrude, $fn 96): 21 s, of which 0.3 s here and 18 s in OpenSCAD's own render that the result
  is compared with. The comparison is what gives the "Volume: STEP vs OpenSCAD render" line and runs after the STEP
  is written. Nothing to change in this app's code; a time cap on the comparison render would be the next step if
  it ever matters.
- **Image**: a calibration-sheet photo with the empty-sheet photo, 1.8 s to convert and 1.1 s to preview, nearly
  all in ImageToSketch's marker detection (`calsheet.locate` 2.3 s of 2.8, `_refine` 12 ms per marker in Python).
  That library is the ImageToSketch project's; a note for it: the preview and the conversion each detect the
  markers again, so a cache keyed on the file and the settings would save a second per conversion.

## Big meshes

| Mesh and preset | Before | After |
|---|---|---|
| Dense tray, 220,160 triangles, **Draft** | not finished in 25 min (PRESETS.md) | **571 s**, closed valid solid, 25,759 faces (126 planes, 66 round, 44 NURBS, 102 areas as facets), max 0.0505 mm (tolerance 0.052): reduced to 48,400 triangles, 12 swept surfaces claimed, the 60 s cap fired after the checkpoint, 2 passes; the checkpoint (a plain faceted solid, 198 s) and the final assembly (300 s) are where the time goes |
| Dense tray, **Quick** (no reduction: the full 220,000 triangles) | not run | **not finished in 40 min** (stopped by the harness while still fitting; no checkpoint yet). Draft is the preset for a mesh this dense; Quick's own reduction is only the memory guard's |
| Organic figure, 162,036 triangles, **Quick** | 9.9 s, 19 faces, 0.027 mm (PRESETS.md) | 7.5 s, 19 faces, 0.027 mm (same) |
| Dense tray, **Quick**, on a pretend 2 GB computer (1.2 GB free) | | the memory guard reduced it to 131,074 triangles; the checkpoint alone took 697 s (sewing 131,000 facets), and the run was stopped by the harness at 50 min, still fitting; no crash, peak memory within the pretend limit |

The last two rows are the honest limit found: without Draft's reduction a 220,000-triangle mesh with a big
curved area is still a very long job on this machine or a smaller one, within the ceiling but far past "fast".
Joining fitted faces without sewing (section 6) is what would cut the checkpoint's 697 s; routing a mesh this
dense to Draft's reduction automatically is the cheaper answer and is not done yet.

## The two ideas

**Slice the STL as a hollow object with a 0.0001 mm nozzle and feed the layers in.** Not harmful, but it adds
nothing: what a slicer gives at each layer is the mesh cut by a plane, which the app computes exactly in one call
(that is the *Loft cross-sections* mode of `profile2d`). A slicer turns that exact cut into G-code polylines (arcs
gone, points offset by half a nozzle, however small, each layer's path closed by the slicer's own rules) and then
the app would have to recover the curve from the polyline again. Everything the slicer "sees" (where material is,
inside against outside) the mesh already says. What SLICER.md recommended borrowing still stands: the slicer's
rules for *where* to put layers (adaptive layer height from the curvature), not the layers themselves.

**A wireframe first, then surfaces filled between the wires, for organic shapes.** This one is real: commercial
reverse-engineering tools work this way for organic surfaces (lay a coarse network of curves over the mesh,
ideally a quad layout that follows the curvature directions; make every curve a spline that lies on the mesh;
fill each four-sided cell with a Coons or Gordon surface, so the patches are untrimmed and meet exactly along the
shared curves). This app already has half of it: its plate-fill fallback *is* "fill a wireframe of boundary
curves", and the first research report recommended four-sided untrimmed patches. What is missing is the quad
layout, which is the hard part (a cross field on the mesh, its singularities, then the layout). Costed sketch for
an experimental fourth engine:

1. A coarse quad mesh of the organic area (a few hundred quads) from a cross-field quadrangulator: QuadriFlow is
   BSD-3-licensed (allowed by the standing rule, unlike anything GPL) and takes a triangle mesh and a target face
   count; it would need building for Windows and macOS too, or a Python port of its field optimisation.
2. Every quad edge projected onto the original mesh as a polyline, then a spline through those points (the app's
   `_chain_curve` already does this for region borders).
3. Each quad filled with a Coons surface from its four curves (`GeomFill_BSplineCurves` in OpenCascade, no
   trimming, no plate solve), neighbours sharing the edge curves, so the shell closes without sewing.
4. Deviation measured against the mesh as now; a quad whose Coons surface misses the tolerance is split in four and
   refilled (the usual refinement), or the mesh's own vertices inside it are fitted by the existing least-squares
   patch with the quad's curves as the boundary.

Where it would win: smooth organic parts (the blob, the figure) would come out as a few hundred untrimmed patches
with G0 continuity along every seam instead of trimmed least-squares patches and facets, and the slow parts of
today's fitter (plate fills, sewing, face repair) would mostly not run. Where it would not: anything with sharp
edges and flats, which the Ladder already routes to exact shapes, and parts whose quad layout the field solver
gets wrong (a mesh with holes or thin fins). Measured here only in the sense that every piece it needs exists:
nothing of it is built, and it should start as an opt-in engine like v2 did.

## Not tested

- Windows and macOS; real scans and downloads (the meshes are the project's synthetic test set).
- The lathe-route fallback on a turned part where roughing misses by *less* than twice the tolerance (nothing in
  the test set does).
- Jetwash's batches on a file where a batch of 16 succeeds and the next fails in the middle (the test files
  either succeed in batches or fail in singles).
