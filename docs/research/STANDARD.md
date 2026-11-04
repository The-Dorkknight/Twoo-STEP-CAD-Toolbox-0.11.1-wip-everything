# Why Standard was so unimpressive, and what Standard is now

**WORK IN PROGRESS: MANY FEATURES MAY BE BROKEN** (EVERYTHING to solid 0.10.2-wip, 24 October 2026)

*Working notes. Asked for, after the ghost cat ([GHOST_CAT.md](GHOST_CAT.md)): "Ok lets rework standard or make
fast standard if standard cant reliably give benifits. Why was it so unimpressive". Every number below is from the
2-core Linux test machine, synthetic test meshes, run through `tools/bench.py` after the fixes in section 2.*

## The short answer

Standard was the app's very first converter (Classic) with its original settings, and **it treated every part the
same way**: hunt for flat faces and cylinders everywhere, then hold every organic patch to engineering tolerance
(0.05 % of the part's size). On a cat that means a cat's cheek has to be within a few hundredths of a millimetre,
big patches can't manage that, so they get split, re-fitted, sent to the slow plate filler and finally given up as
facets. That is the 4 to 10 minutes and the 1,164 facet areas.

Quick (the Ladder engine) looks at the part first for a few milliseconds, skips the searches that can't succeed, and
lets organic areas sit up to 0.3 mm off *inside* a patch while flats, holes and edges stay tight: the author's own
accuracy rule. So its patches pass first time.

Measured over every test mesh, **Classic was hardly ever the better choice**. It was quicker on three small
flat-sided plates (about 5 s against 11 to 13 s, where Quick runs a second fitter as a check), it used fewer faces on
one of them (64 against 89, at 0.102 against 0.076 mm), and it had a closer worst point on the smooth blob (0.057
against Quick's guesstimated 0.108 mm, and Quad patches gets 0.022). Everywhere else it was the same or slower, with
more faces, more facets or a worse fit. So:

- **Standard now picks the engine by the part's shape** (the new *Auto* engine): smooth organic parts go to
  **Quad patches** (within the tight tolerance, no facets, smooth joins), everything else, and any organic part
  Quad patches can't do well, goes the **Quick** way.
- **Fine** now runs that, then the v2 fitter, and keeps the better result (it used to start from Classic).
- **Classic stays** under *Advanced → Surface fitter → Classic*, and is still what the command line and the
  FreeCAD / Fusion plug-ins use unless told otherwise.

## 1. Where the time went on the cat (from its diagnostic codes)

| | Standard (Classic) | Quick (Ladder) |
|---|---|---|
| Route | none (every search run) | "organic": flat and cylinder searches skipped |
| Organic patches held to | ~0.03 mm (the auto tolerance) | 0.3 mm inside, edges tight |
| Time | 4 min 23 s | 81 s |
| NURBS faces | 829 | 467 (bigger patches) |
| Facet areas | 1,164 | 808 |
| Worst point | **1.110 mm** | 0.228 mm |

The 1.110 mm was the odd one out: Standard is the strict preset, so its worst point should have been the
*smallest*. Chasing the same symptom on the test meshes turned up two real defects.

## 2. Two defects behind "far off the mesh"

Two test plates had always come out far off in Classic, and the notes called it "the known cusp problem": the
oval plate 3.66 mm, the blobs plate 3.0 mm. Neither was a cusp.

**The oval plate: a face in the wrong place.** Every face was checked one way only: the face must lie on the
mesh. The 155 mesh points that were 3.6 mm from the result all belonged to *one* region, a cylinder wall: its
face was the right size and lay on the mesh, but round the other side of the oval. Nothing asked whether the
region's own triangles were covered.

**The blobs plate: faces OpenCascade can't draw.** Here the region's face did cover its triangles (to 0.03 mm when
rebuilt on its own). But three cylinder faces in the written STEP could not be triangulated by OpenCascade at all,
at any setting from 0.01 to 0.5 mm, so the deviation measurement saw holes. A program built on OpenCascade, like
FreeCAD, would most likely struggle to draw them too. The checker that looked for such faces (used by Staged only)
meshed each face on its own and coarsely, which found one of the three; meshed the way the deviation is measured
(the whole solid at once, finely) it found all three and a fourth.

**The fix (both engines that build faces, Classic and Ladder):**

1. The write-and-read-back check that only Staged had now runs for Classic and Ladder too (`trip_check`).
2. It looks for round faces that aren't drawn when the whole solid is meshed at the measurement's fineness, and
   rebuilds those regions as exact facets (once face merging has been ruled out as the cause).
3. On the finished solid it measures the working mesh's triangle centres *and corners* against the result. Where a
   point is more than three times the tolerance away (plus the mesh's own chord sag for centres), the region under
   it is rebuilt as facets (`cover_check`). Checking this on the finished solid matters: tried at face-building
   time, it refused half a sphere, which is only right once its partner half has mended the shared seam (the sphere
   went from 2 faces to 2,561).
4. A defect found on the way: meshing a shape a second time can make OpenCascade's mesher refine without end
   (the vase: 0.9 s the first time, over five minutes the second, so the whole conversion timed out at 30 min).
   The checks mesh a copy, and the vase is back to 160 s.

| | Before | After |
|---|---|---|
| Oval plate (Classic) | 141 faces, **3.658 mm** | 1,223 faces, 0.083 mm |
| Blobs plate (Classic) | 824 faces, **3.000 mm** | 2,384 faces, 0.176 mm |
| Sphere, knob, blob, vase, every other test mesh | | the same results |

The price is honest: where a face can't be trusted, its area becomes facets, so the face count goes up. A wrong
face that looks fine is worse than a right one that looks busy.

Whether this was also the cat's 1.110 mm is not known: it needs the cat's mesh on the test machine.

## 3. The test set, three ways

"Out" = worst point more than 5 % over the tolerance the run was held to (Quick holds organic areas to 0.3 mm).
On the coarse CAD meshes most "outs" are the mesh's own chord sag against the true cylinders (up to 5× the
tolerance on the knob, noted in 0.8.0), not a fault of the engine.

**12 CAD-style test meshes** (the vase's Classic run measured separately after the meshing fix):

| | Classic (old Standard) | Quick (Ladder) | **Standard now (Auto)** |
|---|---|---|---|
| Time, all 12 | 305 s | **140 s** | 176 s |
| Faces, all 12 | 15,744 | **1,922** | 2,394 |
| Facet areas | 179 | **22** | 22 |
| Out of tolerance | 9 of 12 | 7 of 12 | 7 of 12 |
| Turned vase | 160 s, 8,656 faces | **8 s, 14 faces** | 8 s, 14 faces |
| Torus | 631 faces, 63 facet areas, 0.160 mm | **9 faces, 0.076 mm** | the same |
| Fine tray | 1,927 faces | **131** | 131 |
| Blob | 24 faces, *0.057* (tolerance 0.028) | 20 faces, 0.108 (guesstimated) | **492 faces, 0.022** |

Auto is Quick on every part except the blob, the one organic part in the set, which it sends to Quad patches.

**5 organic test shapes:**

| | Classic (old Standard) | Quick (Ladder) | **Standard now (Auto)** |
|---|---|---|---|
| Time, all 5 | 521 s | **96 s** | 219 s |
| Faces, all 5 | 1,310 | **773** | 2,412 |
| Facet areas | 52 | 15 | **0** |
| Within the *tight* tolerance | 3 of 5 | n/a (0.3 mm) | **5 of 5** |
| Organic figure, 162,036 triangles | 395 s, 19 faces, 0.015 mm | 7 s, 19 faces, 0.026 mm | **7 s, 57 faces, 0.010 mm** |
| Bumpy ring with a hole | 38 s, 1,234 faces, 52 facet areas, *0.110* | 9 s, 701 faces, 15 facet areas, 0.242 | 125 s, 1,787 faces, **0 facets, 0.028** |
| Finer blob | 42 s, 16 faces, 0.024 | 33 s, 16 faces, 0.024 | **4 s**, 59 faces, 0.017 |
| Peanut (turned) | 37 s, 17 faces | 45 s, 17 faces | 46 s, 17 faces (handed on: turned) |

**The synthetic test ghost** (156,252 triangles, a cut hem and eye tunnels, the closest thing in the test set to
the cat): Quick and Standard-now both 173 s, 3,701 faces, 0.130 mm, 574 facet areas. Quad patches refuses it
("1.0 % of its edge length is sharper than 30°") and it goes the Quick way. Classic with Guesstimate took 228 s
(GHOST_CAT.md); strict Classic took 41 minutes in the 0.4 notes.

## 4. So what does each preset give you now

| Preset | Engine | Pick it when |
|---|---|---|
| **Draft** | Ladder, organic 0.5 mm, big meshes reduced, 1-minute cap | you want *a* solid, now |
| **Quick** | Ladder (routes the part, organic 0.3 mm) | most things; the fastest good result in every test |
| **Standard** | Auto: Quad patches for smooth organics, otherwise Quick | organic parts that must be tight and facet-free; otherwise it *is* Quick |
| **Fine** | Standard, then the v2 fitter, keep the better | narrow details (the blob plates' cusps), time to spare |

**Fine, measured on four parts** (Standard first, then the v2 fitter): the blobs plate 444 faces and 0.108 mm
(Standard alone: 151 faces, 0.191 mm, outside its 0.097 mm tolerance; Fine's rule now takes v2 when the first pass
missed its tolerance and v2 is clearly closer), the knob 69 faces and 0.058 mm, the blob 492 faces and 0.022 mm, the
oval plate 1,189 faces and 0.048 mm; 23 to 83 s each, about twice Standard.

Honest limits of the new Standard:

- **On your cat it will most likely give Quick's result.** The cat's pointy tatters are sharp edges, so Quad patches
  should refuse it, exactly as it refuses the synthetic ghost. Not tested: the cat isn't on the test machine.
- **On busy organic shapes it is slower and makes more faces than Quick** (the bumpy ring 125 s and 1,787 faces
  against 9 s and 701), and the file is bigger (a quad patch is about 16 KB of STEP). What you get for it: tight
  tolerance, no facets, seams within 3°.
- **It needs a quad remesher.** Linux ships QuadriFlow; macOS and Windows get Instant Meshes from the launcher
  (0.10.2). Without either, Standard is Quick.
- Quad patches is still marked experimental, and real scans and sculpts have not been tried.

## Not tested

The cat itself (and so whether its 1.110 mm was one of the two defects above); anything on macOS or Windows; the
FreeCAD and Fusion plug-ins, which still use Classic.
