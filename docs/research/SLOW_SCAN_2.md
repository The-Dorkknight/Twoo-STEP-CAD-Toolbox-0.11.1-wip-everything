<!-- Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight) -->
<!-- Licensed under the PolyForm Noncommercial License 1.0.0. No warranty. -->

# The scan that was still slow: closed scans, silent steps and power settings (research note, 19 November 2026)

**Asked because** the real S99 scan (620,944 triangles, 31 MB) was *still very slow* after the 18 November scan work
([SCAN_CLEANUP.md](SCAN_CLEANUP.md)): "even on turbo it runs initially at 40 percent cpu, then after 30 min idles at 15
percent (i5 9th gen)". The screenshot showed 31 min 37 s of running, 20 % progress, and the last log line 21 min 24 s
old (it was "6 cylinder/sphere surface(s) recognised"). The log said: Route mixed (flats 0 %, walls 61 %, lathe 56 %),
*Guesstimate: reduced 620,924 → 90,356 triangles in 351.5 s*. All times below: the 2-core Linux test machine.

## Why the 18 November changes did nothing for it
Everything in D20 (clean-up, light denoise, plain pre-reduction) started only when the clean-up had **acted**. The
real scan is **one closed piece** (620,944 triangles and 310,472 vertices: Euler's V = F/2 + 2 for a closed surface),
so `scanclean` returned at once, `scan_cleaned` stayed False, and the guesstimate reduction ran on all 620,000
triangles (351 s on the author's laptop) before the noisy mesh was fitted.

## A closed, noisy stand-in
`s99/v_clean.stl`: the stand-in scan after clean-up, 648,638 triangles, one closed piece, 0.04 mm noise. Same command
as the author's: Quick, Extreme profile.

| Run | What happened |
|---|---|
| 0.11.2 code | the feature-keeping reduction took 58 s and could not stay closed; plain reduction to 60,000; patch layout still going at 4 min (1,782 patches and counting). Stopped. |
| **this build** | prep 12 s; guesstimate reduction **17 s** (150,000 → 84,752); patch layout finished at 98 s; fitting passes to 171 s; the rest is the repair loop below; **15.9 min in all**, closed, valid, 9,702 faces (98 flat, 113 round, 233 NURBS patches, 427 areas kept as facets), mean deviation 0.026 mm, maximum 0.20 mm (the Guesstimate allowance for freeform areas is 0.3 mm) |

## Telling noise from crisp CAD, and why the denoise comes before the reduction
After 3 Taubin passes, how far did the points move?

| Mesh | median | 99 % | 99.9 % | largest |
|---|---|---|---|---|
| the noisy scan (tolerance 0.085 mm) | **0.054 mm** | 0.118 | 0.142 | 0.669 |
| a clean 917,504-triangle box | **0.0000 mm** | 0.002 | 0.062 | 0.075 |

Flat areas of a clean mesh do not move at all, noise moves every point, so **the median is the test**: under a tenth
of the tolerance, the points are put back exactly. (The largest move cannot tell them apart: the box's edges move
0.075 mm.)

Plain quadric reduction to 150,000 triangles of the noisy scan:

| Before it | Result |
|---|---|
| nothing | **not closed**: 16 non-manifold edges; `fill_holes` does not close it |
| 3 Taubin passes | closed, 3.3 s, surface moved 0.004 mm typically, 0.030 mm at most |
| 3 passes, each point's move capped at 0.043 mm (half the tolerance) | not closed |
| 3 passes, capped at 0.09 mm | not closed |

A cap on the move (the first idea, to make the denoise safe for CAD) breaks the reduction; the median test makes it safe
instead. A reduction that still comes out open is repaired (triangles on non-manifold edges cut, the holes closed) and
kept if that closes it.

## Where the time went (new in 0.11.3: *Where the time went* at the end of a run)
Of the 15.9 minutes: **checking the solid (write and read back) 10 min 46 s (68 %)**, dividing curved areas into
patches 1 min 8 s, fitting surface patches 1 min 8 s, building faces and sewing 1 min 3 s, the other 1 min 49 s.
The check is up to six rounds. Each merges faces, writes the whole STEP, reads it back, meshes the whole shape and tests
every round face, then makes the 1 to 3 faces it found into facets ("2 region(s) didn't survive being written to STEP and
read back", then 3, then 1, then 3). One round took 3 to 4 minutes at this size.

**That is also why the processor sits at 15 %.** Reducing the mesh, patch layout, face building, sewing, writing and
reading STEP are single-thread work (OpenCascade is not multi-threaded here, and NumPy has nothing to spread). The
first minutes use several threads (the reduction, matrix work: 40 %); then one thread. On an 8-thread i5, one busy thread
is 12.5 %. No profile, Turbo included, changes that.

## An experiment that failed: a time cap on the repair rounds
To bound the loop, the rounds were given 15 % of the time budget (at least 4 minutes). The run stopped them at 6 min 45 s
("no more rounds; what they found has been made facets") and went on to *Verifying*, where it then hung: more than 6
minutes inside OpenCascade's mesher (`BRepMesh_IncrementalMesh`, called from the deviation measurement), where the
uncapped run took 28 s. A face that the whole-shape mesher cannot finish is exactly what a round finds, and a round
that is not run does not find the next one. **Reverted**; a comment at the loop says why. A faster run that can hang, or
write a file that other CAD programs cannot draw, is worse than a slow one.

## The progress line while OpenCascade has the interpreter lock
A heartbeat thread in the worker (a line when the log has been quiet for 30 s) worked between OpenCascade calls ("still
checking the solid (writing and re-reading the STEP) (0:01 in this step, 5:55 in all)") but printed nothing during the
long calls: OpenCascade's Python binding does not release the lock. So the worker writes its stage name and detail to a
small file (atomically, the detail at most once a second) and the **web server, another process**, reads it with every
poll of the page and adds the processor's clock and the power findings. The progress line now reads, for example,
"now: building faces and sewing them into a solid (4:12 in this step) · CPU 3.9 of 4.1 GHz".

## Power settings (what is read, what is reported)
`tostep/core/powerinfo.py`, read-only, every fact best effort:

| What | Linux | Windows | macOS |
|---|---|---|---|
| on battery | psutil, or `/sys/class/power_supply` | psutil | psutil |
| power mode | `powerprofilesctl get` (`power-saver` is reported) | `powercfg /getactivescheme` | `pmset -g` Low Power Mode |
| energy preference | `scaling_driver`, `energy_performance_preference` (`power`, `balance_power` are reported) | | |
| turbo boost | `intel_pstate/no_turbo`, `cpufreq/boost` | | |
| cap | `intel_pstate/max_perf_pct` below 100 | | |
| clock | psutil or `scaling_cur_freq` against `cpuinfo_max_freq` | psutil | psutil |

The governor name `powersave` is reported only for drivers other than `intel_pstate` and `amd-pstate`, where it is the
driver's normal dynamic mode, not a stuck clock. A low clock counts only while the processor is busy (about a minute in;
idle, every laptop runs at a fraction of its top speed). **Not tested on the author's laptop**; the logic is tested with
made-up readings (`tests/test_power_and_dense.py`). A 9th-generation i5 laptop at 100 % for half an hour often lowers
its own clock to stay cool; the clock in the progress line shows that when it happens.

## Still not done
- **The repair loop.** A cheaper test, before the first full round, that finds the faces that cannot be drawn, so that
  fewer rounds are needed (the check meshes the whole shape, facets included, because meshing a face alone can succeed
  where meshing it inside its shell fails). Not tried.
- **Noise still decides how much comes out as real surfaces**: 427 of the areas here are facets. Denoising inside face
  groups, never across them (paper D25), is the next idea.
- **Not run on the real scan.** The author's laptop has the numbers that count: the log now ends with *Where the time
  went*, so one run on the real S99 file says where its time goes.

## Scripts
`scripts/scan_cleanup/` (from 0.11.2) and `scripts/scan_prep/`: `prep_variants.py` (the four reduction variants),
`move_stats.py` (the displacement table).
