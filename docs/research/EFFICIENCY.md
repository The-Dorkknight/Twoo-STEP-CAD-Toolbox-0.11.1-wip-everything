# Where the time goes, tab by tab, and what was changed

**WORK IN PROGRESS: MANY FEATURES MAY BE BROKEN** (EVERYTHING to solid 0.8.0-wip, 14 October 2026)

*Working notes from the 0.8.0 efficiency pass, asked for as "investigate any additional efficiencies /
improvements for all these tools". Like sections 12–13 of the [research README](README.md), these are
measurements on this project's own code and test files, not a literature survey. Every number is from the
2-core Linux test machine; a 7th-gen i3 is about the same single-thread speed, an 11th-gen i5 roughly twice as
fast.*

## How it was measured

- **Convert (all engines):** `tools/bench.py` on 11 meshes (bracket, knob, sphere, torus, blob, two blobs plates,
  oval plate, fine plate, coarse and fine tray, a 24,146-triangle turned vase), engines Classic, Ladder and
  Staged, one process per run. The baseline (0.7.1-wip) and the new code ran back to back on a quiet machine;
  every result field except the times was compared.
- **Hotspots:** `cProfile` on Classic for knob, fine tray, blobs plate and vase; `py-spy` on a running Jetwash;
  call-site counters wrapped around the geometry check and the mesher.
- **Jetwash:** the fine tray's Classic STEP (1,927 faces, the slowest case found) and the other ten Classic
  results, old and new code, threshold 0.05 mm, coarseness 2; outputs compared face by face (count, types,
  volume, area).
- **Image → STEP and SCAD → STEP:** the job worker run as the app runs it (`worker.py job.json`), wall time
  including start-up.
- **3D preview, web page, Hardpoints:** timed directly.

## What was found

| Tool | Where the time went | Size of it |
|---|---|---|
| Convert, Classic | The finished solid's geometry check (`BRepCheck_Analyzer`) ran 9 times per conversion, 5 of them on a shape that had not changed since the last check | 0.4–0.8 s each: 2–3 s per part, 9 % of the tray |
| Convert, all engines | Primary-shape detection (RANSAC in `grow_primitives`): each of ~300 guesses per round tested all three corners, the middle and the normal of every triangle, though most guesses fail at the first corner | 26 of 85 s on the blobs plate; 8 of 28 s on the tray |
| Convert, Classic | The surface-to-mesh check of trial NURBS patches meshes each patch. 30 trial patches of the vase had edges up to 12× looser than the mesh step asked for; OpenCascade's mesher spent up to 77 s on one and gave up on others (then retried 3 times coarser) | 97 of 290 s on the vase |
| Jetwash | After one artifact was removed, the "remove everything at once" shortcut was tried again on the whole rest. On the tray it failed every time, 14 times, at 60 s each | 606 of 669 s |
| Jetwash | Each face's area integral computed twice; volumes of the same solid computed again and again | 15 s of 669 |
| Image → STEP, SCAD → STEP | The job worker loaded the mesh converter (trimesh, SciPy) for every job, and the image check re-read its STEP through it | ~1 s of a 2–3 s job |
| 3D preview | Re-reading the result STEP | 6.7 of 12.5 s for the vase's 22 MB result; ≤ 2 s for normal parts |
| SCAD → STEP | OpenSCAD's own run (first run only, cold cache) | 2.5 s first time, 0.2 s after |
| Hardpoints inspect | — | 0.06 s: nothing to gain |
| Web page | 79 kB (26 kB if compressed), served from this computer | nothing to gain |

## What was changed (all results identical)

1. **Geometry check only when the shape changed** (`_build_surface`, `_safe_unify`, the report). The checks that
   repeated an answer already known are skipped; a shape that passed the closed-solid check is reported valid
   without a second full check (the closed-solid check ends with the same test).
2. **Primary-shape support test in steps** (`_support`): first corner, then the second and third on the faces still
   in the running, then the middle and the normal. Same answer as testing everything at once (checked call by
   call: 14,575 calls on two parts, 0 differences), a few times faster for wrong guesses.
3. **Loose-edged trial patches meshed with a minimum element size up front** (`shape_to_mesh`). A single face whose
   edges carry a tolerance more than 5× the mesh step gets the same remedy the code already used for runaway faces
   (minimum size 2× the step) before the mesher can stall on it. On the slowest captured patch: 64 s → 0.8 s.
4. **Jetwash: the all-at-once shortcut is tried once per body.** After it fails, features are removed one at a time
   as before. Also: each face's area integral once, volumes remembered, and a progress line every 20 s
   ("still washing: 6 removed, 30 left alone, 41 tried so far (95 s)") instead of minutes of silence.
5. **Image and SCAD jobs no longer load the mesh converter**; the image tab's closed-solid check reads its STEP with
   OpenCascade alone.

### Convert: before and after (quiet machine, same 11 meshes)

| Mesh (triangles) | Classic | Ladder | Staged |
|---|---|---|---|
| Vase (24,146) | 298.7 → **163.6 s** | 10.0 → 9.5 s | 15.4 → 15.5 s |
| Torus (4,096) | 49.4 → 39.8 s | 5.8 → 4.5 s | 5.5 → 5.6 s |
| Blobs plate (6,844) | 63.3 → 59.4 s | 64.1 → 58.9 s | 78.6 → 74.2 s |
| Fine tray (3,440) | 28.7 → 21.0 s | 30.0 → 21.3 s | 18.3 → 11.7 s |
| Oval plate (4,428) | 25.0 → 19.8 s | 18.8 → 14.3 s | 19.7 → 15.5 s |
| Blob (9,944) | 20.7 → 19.0 s | 2.6 → 2.8 s | 31.2 → 29.6 s |
| Knob (2,958) | 16.9 → 17.2 s | 13.8 → 11.1 s | 13.8 → 11.2 s |
| Coarse tray (1,792) | 12.2 → 10.0 s | 14.9 → 8.4 s | 12.2 → 8.8 s |
| Fine plate (6,322) | 10.5 → 7.8 s | 9.3 → 8.0 s | 19.2 → 16.0 s |
| Sphere (5,120) | 4.9 → 4.9 s | 2.7 → 2.2 s | 5.2 → 4.8 s |
| Bracket (332) | 0.5 → 0.7 s | 0.5 → 0.7 s | 0.5 → 0.5 s |
| **All 33 runs** | | | **923 → 698 s (−24 %)** |

All 33 results are the same as before in faces, face types, share of exact surface, triangle regions, maximum and
mean deviation, STEP size, closed and valid. (Times under a second, and differences of a few tenths, are noise.)
One mistake was caught by this comparison and fixed before release: a variable of the new check-skipping code
had the same name as one in the write-and-read-back loop of the roughing engines, which changed the Ladder
vase by 0.04 mm³; with the fix it is identical again.

### Jetwash: before and after

| File (faces) | Before | After | Result |
|---|---|---|---|
| Fine tray, Classic STEP (1,927) | 669 s | **140 s** | identical: 1,912 faces (1,901 planes, 9 cylinders, 2 spheres), volume 27,018.759599 mm³, area 13,046.064619 mm² |
| Ten other Classic results (8 – 8,665 faces) | 0.1 – 45 s | the same | identical (face count, kept candidates, volume to 6 decimals) |

The removed-feature count can read differently (13 instead of 15 for the tray): the old code counted a later
all-at-once removal of three features as three, the new one removes them one by one and some vanish with their
neighbour. The washed solid is the same. In principle the shortcut could have succeeded on a later try where
it failed first, giving a different (still valid) result; on these 11 files it never did.

### Image and SCAD jobs

| Job (worker, wall time) | Before | After |
|---|---|---|
| Image → STEP, icon, 2 mm solid | 3.1–3.5 s | 2.1 s |
| SCAD → STEP, test cube with hole | 2.4–3.0 s | 1.4–1.7 s |

Same STEP and the same summary; the job's own "Time:" line now includes OpenCascade's start-up (0.7 → 1.5 s for
the icon), because that is no longer loaded before the job's clock starts.

## Improvements that are not about speed

- **A warning when the STEP misses part of the mesh.** On the blobs plate and the oval plate (both engines Classic
  and Ladder) the result is closed and valid but up to 3.0 and 3.7 mm off the mesh: the narrow cusps where two
  round edges meet are bridged over (measured: 47 of 20,000 mesh points more than 0.5 mm from the STEP, none the
  other way round). The deviation was in the report, but nothing pointed at it. The report now adds, when the
  maximum deviation is more than 10× the tolerance: *"in one place the STEP is 31 times the tolerance away from
  the mesh, near x 146.8, y 70.7, z 3.0 mm …"*. 10×, not less: on the knob a coarse mesh's own chord sag against
  the true cylinders reaches 5×, which is not a fault.
- **A hint for turned parts.** When Classic converts a part that the Ladder engine's 0.1 s router calls a lathe
  part (and it has over 2,000 triangles), the log says the experimental Ladder engine was up to 17× faster on such
  parts in the tests (vase 164 s vs 9.5 s after this pass; torus 40 vs 4.5 s), with as few or fewer faces. Classic
  carries on unchanged; nothing is switched for the user.

## Found, measured, not changed (for a decision)

- **Staged is better than Classic on every extruded-profile test part but one.** Fine tray: 131 faces, 0.076 mm,
  11.7 s against Classic's 1,927 faces, 21 s. Coarse tray: 119 faces, 0.079 mm against 635 faces, 0.235 mm. Blobs
  plate: 121 faces, **0.19 mm** against 823 faces, **3.0 mm** (the cusps are kept). Oval: 18 faces, 0.081 mm against
  141 faces, 3.7 mm. The exception, from the earlier notes and re-run now: the coarse plate, 39 faces but 0.142 mm
  (2× its tolerance) against Classic's 121 faces, 0.078 mm. The Ladder router sends all of these to its
  "prismatic" route, which uses the Classic fitter. A rule "prismatic with curved walls → rough first" would
  bring Staged's results to Ladder; one counter-example in seven parts is not enough to switch it yet. Classic
  stays the default (standing rule).
- **The 3D preview re-reads the result STEP** (6.7 s of 12.5 s on the vase's 22 MB file, under 2 s on usual parts).
  The worker already holds that shape when it finishes; it could write the preview's triangles beside the STEP.
  Not done: it would make every job slower by the meshing time, to save time only when the preview is opened, and
  the preview opens on its own only after a conversion.
- **The v2 fitter has its own copy of the mesher call** (`stl2step_core_v2.shape_to_mesh`); change 3 was not made
  there (v2 is experimental and was not in the benchmark).
- **Start-up:** OpenCascade takes 0.8 s to load and the mesh libraries another 0.7 s, once per job (each job is its
  own process so that Cancel can always stop it). Keeping a warm worker would save that per job but makes
  cancelling harder; not worth it for jobs that take tens of seconds.
- **Parallel defeaturing in Jetwash** (`SetRunParallel`): measured 59.6 → 58.1 s on the all-at-once case, the same
  result. Too small to matter on 2 cores; not switched on.
- **Jetwash on a body with dozens of candidates** is still slow (140 s for the tray): each failed one-at-a-time
  removal is a full OpenCascade defeaturing of the whole solid. Removing features in a local box and stitching
  back would be faster but is a different algorithm with its own risks.

## Not tested

- Windows and macOS; real 3D-printed or scanned parts (the meshes are the project's test set).
- Jetwash on STEP files from other CAD programs (all 11 inputs were this app's own results).
- The loose-edged meshing remedy on the v2 fitter, and on parts outside the test set: it can change which trial
  patch wins on such a part, which on these 11 meshes it never did.
