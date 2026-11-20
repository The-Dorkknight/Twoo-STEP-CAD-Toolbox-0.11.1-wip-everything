<!-- Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight) -->
<!-- Licensed under the PolyForm Noncommercial License 1.0.0. No warranty. -->

# 3D scans: why they were so slow, and what was done (research note, 18 November 2026)

**Asked because** a 3D scan of a Sequre S99 soldering iron (Thingiverse, "Sequre S99 3d scan", alonym, 27 April 2026;
620,924 triangles) took **185 min 56 s on Draft** on a Linux laptop and came out as 46,725 faces (871 faceted
regions, 10 flat, 10 round, no NURBS), worst gap 2.03 mm. The result preview then sat on "Loading…" for 10+
minutes.

Thingiverse can't be reached from the machine these tests ran on, so a **stand-in scan** was made with the same
problems (`scripts/scan_cleanup/mkscan.py`): a pen-shaped soldering-iron body (tip cone, grip ribs, a flat display
window), 0.2 mm marching-cubes triangles (653,119 of them), 0.04 mm scanner noise, three holes, 40 floating
fragments and a turntable plate. All times: 2-core Linux machine.

## What the app already did before converting
Merge duplicate points, drop broken and duplicate triangles, collapse tiny edges, fix facing, fill small holes
(trimesh's filler handles only 3- and 4-edge holes), optional Taubin denoise (off by default), optional reduction
(only Draft turned it on). Nothing removed loose pieces or non-manifold edges, and noise was left alone.

## What made it slow (each one measured)

| Cause | Evidence |
|---|---|
| Loose fragments and the turntable plate | each fragment becomes its own curved area (836 on a first stand-in); the plate made the route "prismatic" instead of "lathe" |
| Noise | the surface fell apart into **3,192–3,227** separate curved areas; the denoised copy had **1** |
| Patch layout cost per split grew with the **whole** mesh | each split masked all ~1 million triangle pairs: 9.9 ms per call on 648,000 triangles; the run stopped after 38 min of CPU, silent for 30 min, still splitting |
| The feature-keeping reduction failed on the noisy scan | "couldn't keep the mesh closed — using the full mesh instead": Draft's 50,000 became 620,000 |
| The safety checkpoint is built from every triangle | 60,000 facets 80 s, 119,000 facets 346 s; at full size it ran for most of the 30-minute cap |
| No sign of life | 30 minutes without a single log line |

## What changed (0.11.2)

1. **Scan clean-up** (`scanclean.py`, Advanced → *Scan clean-up*, `--scan-cleanup`; on automatically when a mesh
   isn't one closed piece): drops loose pieces (open ones under 2 % of the largest piece's area, anything under
   0.1 % or 20 triangles; small closed parts such as screws stay), cuts triangles on non-manifold edges, fills each
   hole (flat in its own best plane, or a fan) up to a quarter of the part's size across. *Keep only the largest
   piece* is there for a scan stood on its plate. Stand-in: 973 fragments gone, 2 holes filled, closed, **5 s**.
2. **Light denoise for scans:** a dense mesh (over 100,000 triangles) that needed clean-up gets 3 Taubin passes.
   Against the noise-free shape: noise median 0.018 → 0.009 mm, 99th percentile 0.071 → 0.061 mm. 10 passes went
   too far (99th percentile 0.118 mm: edges rounded); capping each point's move helped less.
3. **Plain pre-reduction of scans:** a cleaned scan over 150,000 triangles is reduced by plain quadric reduction
   first (3 s; the surface moved 0.004 mm typically, 0.031 mm at most).
4. **Plain fallback** when the feature-keeping reduction can't stay closed (it did that on the scan): the same count
   by plain quadric reduction, kept only if it stays closed, its movement logged.
5. **Quick reduces too:** meshes over 60,000 triangles are reduced first, as Draft does with 50,000.
6. **Patch layout** reads each sub-graph from its own rows: identical result (checked on 30 random face sets), 6×
   faster per call (9.9 → 1.7 ms).
7. **Heartbeat** during patch layout: a line every 20 s ("still dividing curved areas into patches: N so far…").
8. **Checkpoint** only up to 60,000 triangles, and not at all with a time budget under 10 minutes (Draft).
9. **Result preview** built in the background as soon as a job finishes, and kept for the last four jobs.
   Previewing a 48,000-face, 119 MB result here took 50 s (38 s of it reading the STEP back); it now starts while
   you read the log.

## Measured on the stand-in scan

| Run | Time | Result |
|---|---|---|
| Quick, before | stopped after 38 min CPU, unfinished | silent for 30 min in patch layout |
| Quick, clean-up + faster layout only | stopped at 12.6 min, unfinished | 23,500 patches and counting: noise |
| Quick, clean-up + plain reduction to 60,000, no denoise | 13.0 min | closed, valid, 60,000 facets, worst gap 0.18 mm |
| Draft, clean-up + denoise, no pre-reduction | 16.7 min | closed, valid, 48,151 faces (84 flat, 1,077 faceted areas), 0.075 mm |
| **Quick, all changes** | **8.4 min** | closed, valid, worst gap **0.052 mm**, but all 59,428 faces are facets (two regions didn't sew, so the whole surface fell back) |
| **Draft, all changes** | **7.7 min** | closed, valid, 17,672 faces: **65 flat, 390 round**, 2 NURBS, 805 faceted areas; worst gap 0.085 mm |
| Clean-up + 10 Taubin passes + plain reduction to 60,000, then Quick (older code) | 4.4 min | closed, valid, 18,635 faces: 19 flat, 14 round, 13 NURBS, 13 faceted areas; worst gap 0.158 mm |

On the real file, on the laptop: 185 min 56 s before. Not re-run yet.

## What is still not good

- A scan still comes out largely as facets: the noise that is left (and the reduction) defeats the surface fitting in
  many small areas. The heavier denoise in the last row gave far more real surfaces in 4.4 min, but rounds sharp
  edges by up to about 0.3 mm. A **feature-aware denoise** (bilateral normal filtering: smooth within a face group,
  never across a sharp edge) is the obvious next step, and it is what "face groups first" means in practice.
- The 2.03 mm worst gap on the real scan was probably fragments and holes; untested until it is re-run.

## Libraries looked at (for repair)

| Library | Licence | Verdict |
|---|---|---|
| trimesh (already used) | MIT | used: pieces, edges, Taubin |
| pyfqmr (already used) | MIT | used: plain quadric reduction |
| mapbox-earcut (already used) | ISC | used: hole triangulation |
| manifold3d | Apache-2.0 | robust booleans, needs a manifold input already: not a repair tool |
| fast-simplification | MIT | another quadric reducer: nothing pyfqmr doesn't do |
| Open3D | MIT | denoise, Poisson rebuild; a ~400 MB dependency |
| pymeshfix (MeshFix) | AGPL-3.0 | best-known repair, but AGPL: not mixed into a PolyForm project |
| pymeshlab | GPL-3.0 | same |
| meshlib | own licence (free for non-commercial use only) | not used |

## Scripts
`scripts/scan_cleanup/`: `mkscan.py` (the stand-in), `mktruth.py` (its noise-free shape), `denoise.py` (the denoise
comparison), `sub_eq.py` (old and new sub-graph equal, and their timing).
