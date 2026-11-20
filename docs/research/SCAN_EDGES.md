<!-- Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight) -->
<!-- Licensed under the PolyForm Noncommercial License 1.0.0. No warranty. -->

# Straightening the straight triangles: edge-keeping smoothing, a better "is it a scan?" test, and what it did to clean CAD (research note, 19 November 2026)

**Asked because** the real S99 scan converted in 15 minutes (Moderate, Standard) but came out as 6 flat faces, 5 round
faces, 472 freeform patches and 610 facet regions, worst point 2.09 mm. Then, from the author: *"assuming everything is a
scan and straightening already straight triangles could cause [trouble]: please run the ghost and box benchmarks to see,
apply anything useful learned; perhaps a dialog that something looks like a 3D scan, accept / reject and go Classic."*
All times: the 2-core Linux test machine, Standard preset, Moderate profile, unless it says otherwise.

## 1. Why a scan came out as hundreds of patches
The patch layout splits every smooth area until each patch is a disk that bends less than 60 degrees. On the iron
stand-in (150,000 triangles after reduction) it made **1,514 freeform patches, median 15 triangles each**, and the
reasons were: bend too wide 1,290 times, not a disk 23 times. Noise is why: 0.04 mm of noise on 0.5 mm triangles tilts
neighbours by about **10 degrees** (median 9.8), 11 % of the edges look sharper than the 30-degree crease, and every
one of those cuts the surface. The same thing is what makes the scan slow and the result facets.

## 2. Smoothing that keeps edges (`edgesmooth.py`)
Bilateral normal filtering (Zheng et al. 2011) and the vertex update (Sun et al. 2007), in NumPy/SciPy, no new
dependency: each triangle's direction is averaged only with neighbours that already point nearly the same way, then the
points are moved to agree. Tried on the 150,000-triangle iron stand-in against the noise-free shape it was made from:

| Setting | neighbouring triangles apart (median) | mean distance to the true shape | time |
|---|---|---|---|
| as fitted (no smoothing) | 9.8° | 0.0131 mm | |
| **σ 0.25, 5 passes (chosen)** | **1.1°** | **0.0067 mm** | 2.4 s |
| σ 0.35, 20 passes | 1.4° | 0.0157 mm | 4.4 s |
| σ 0.50, 10 passes | 1.8° | 0.0266 mm | 3.1 s |

Stronger settings smooth more and move the surface *away* from the truth, so the gentle one is the default. Two
additions came from the benchmarks:

* **Pinned points.** On a clean dense box the smoothing moved a corner by **2.4 tolerances**. Points where the filtered
  directions still differ by more than 40 degrees (real edges and corners) are now never moved: the box's largest move
  is **0.10 tolerance**, and the scan is still smoothed (0.0080 mm to the truth against 0.0067 unpinned). Pinning on the
  *unfiltered* directions did not work: noise alone pinned a tenth of a scan's points and the result stayed rough
  (median 2.0°).
* **A turned-over triangle is a fold:** its points are put back.

## 3. "Is it a scan?" (`scandetect.py`)
0.11.3 asked how far three Taubin passes move the points (median over a tenth of the tolerance). That called **two clean
organic meshes scans** (a marching-cubes ghost and the iron's noise-free twin), because any smoothing pass shrinks a
curved surface a little. The measure that separates them is the typical angle between neighbouring triangles, ignoring
real creases (30° and up):

| Mesh | triangles | typical angle |
|---|---|---|
| clean box (subdivided) | 786,000 | 0.00° |
| clean cylinder | 524,000 | 0.00° |
| clean ghost, marching cubes | 313,000 | 0.09° |
| clean iron stand-in (truth) | 634,000 – 651,000 | 0.00 – 0.06° |
| rounded box, marching cubes | 59,000 | 0.00° (p90 2.9°) |
| **iron stand-in scan, 0.04 mm noise** | 649,000 | **13.1°** |
| **organic stand-in scan** | 635,000 | **13.2°** |
| **the 653,000-triangle S99-like scan** | 653,000 | **13.3°** |

Over 100,000 triangles and 2° or more is a scan (or: over 100,000 and the clean-up had to remove loose pieces or fill
holes). Nothing clean came within a factor of 20. *Not measured:* a real scan with much less noise, and the real S99
file, which is why the page asks.

## 4. The dialog
Before a conversion of a file over 4 MB the page asks the server (`/api/scancheck`, a few seconds at 650,000
triangles). If it looks like a scan: **"Yes, treat it as a scan"** (the scan rules), **"No, it's CAD: use Classic"**
(`scan_mode = cad`, engine Classic), or Cancel. *Advanced → Is it a 3D scan?* sets the same thing for good
(`--scan-mode auto|scan|cad` on the command line). Clean CAD and small files are never asked about.

## 5. What the benchmarks found (and fixed)
* **A crash that had nothing to do with scans.** The golden gate's `stepped` case (two small boxes, 12 triangles each)
  failed with `IndexError`: the 0.11.2 clean-up threw away every piece with under 20 triangles as a "speck", both boxes
  included. A piece is a speck now only if it is tiny in area, or a handful of triangles **and** under 2 % of the
  biggest; if nothing is kept the biggest stays. All nine golden cases pass.
* **A plain reduction of every dense mesh cost a clean box five faces.** The 786,000-triangle box converted in 38 s to
  6 faces on 0.11.1; the 0.11.3 pre-reduction made it 15 s and **11 faces** (4 NURBS, 1 facet). The reduction now runs
  for scans only; the box is back to 6 faces, 38 s.
* **A reload.** The Standard preset looks at a part with the quad-patch engine and then hands it to Ladder, and both
  loaded and prepared the same file (about 15 s twice on the stand-in). The prepared mesh is kept for the second call
  (same file, same settings, a copy).
* **Organic scans are better left as they were.** The bent organic stand-in scan took **307 s, 8,487 faces** on 0.11.3.
  With the smoothing and the scan route it took **over 18 minutes and the run did not finish** (46,516 faces; the
  process was gone after "measuring the result"). The smoothing makes the router call it a turned part (lathe 54 %)
  and that route claims 37 swept faces, 19 of them wrong. So a scan whose route is **not** lathe or prismatic goes back
  to the mesh as it was before the smoothing, and the 0.11.3 route: **339 s, 8,239 faces, worst point 0.244 mm**
  (0.267 on 0.11.3).

## 6. Where it stands on the soldering-iron stand-in
Same part, Standard, Moderate:

| Build | Time | Faces | Facets (area) | Worst point |
|---|---|---|---|---|
| 0.11.3, run alone | 23.5 min, then every curved area written as facets | 78,547 | all curved | 0.058 mm |
| this build, scan route without the second reduction | 6.1 min | 7,099 | 5.1 % | 0.073 mm |
| this build, crease forced to 30° | 8.8 min | 28,082 | | 0.132 mm |
| this build, a variant that reduced the smoothed mesh again | 16 min | 18,750 | | 0.041 mm |
| this build, final code (the rules of section 5) | **did not finish in 37 minutes**: stuck in the first round's "which faces cannot be drawn" test, one OpenCascade meshing call over the whole shape at 0.2 × tolerance (`_faces_not_drawn`, found with py-spy) | | | |

The spread (6 to 16 minutes) is the write-and-read-back repair loop, which is still 40 to 75 % of the run and still not
bounded (the one attempt to cap it hung, SLOW_SCAN_2.md). The face counts and facet areas are what moved.

One run of four hung where the others took 6 to 16 minutes, and it hung in the same kind of place as the capped-rounds
experiment of SLOW_SCAN_2.md: a whole-shape OpenCascade meshing call with no time limit. **The scan route is faster on
average and its face count is far better, but it is not safe from that hang, and neither is 0.11.3.** The real S99 scan
on 0.11.3 finished (15 minutes).

## 7. Not done
* **Bound the drawability test.** Give `_faces_not_drawn` the same runaway protection `shape_to_mesh` has (a minimum
  edge size when edges carry a large tolerance), or mesh in a child process that can be stopped. Not tried: the hang is
  one run in four and each run is 15 to 35 minutes.
* The write-and-read-back loop. A cheaper first test for the faces that cannot be drawn.
* A real scan with little noise, and the real S99 file with this build.
* Whether a mixed scan (flats and rounds) would do better with the smoothing if only its turned parts were claimed.

Scripts: `docs/research/scripts/scan_edges/`.
