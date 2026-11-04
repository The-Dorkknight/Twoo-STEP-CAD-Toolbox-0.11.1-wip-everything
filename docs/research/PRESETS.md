# Quality presets for the STL → STEP tab: how they were chosen

**WORK IN PROGRESS: MANY FEATURES MAY BE BROKEN** (EVERYTHING to solid 0.9.0-wip, 14 October 2026)

*Working notes. Asked for: "easy presets, fast / low quality, low medium, medium, high quality and advanced which
opens the full panel. Use whatever preset names are better." Every number is from the project's own test meshes,
run through the app's real job worker (`worker.py`, as the page runs it) on the 2-core Linux test machine.*

## What a preset is

Only a set of values for settings that already exist: the same fields the *Advanced* panel shows. So the
diagnostic code, the log and the result read exactly as if the values had been set by hand, nothing new can
break, and *Standard* is precisely the old default. The table lives in one place, `app/presets.py`; the page and
the command line (`--preset draft|quick|standard|fine`) both read it, and any option given by hand still wins.

| Preset | Settings it sets (everything else at its default) |
|---|---|
| **Draft** | Ladder engine, Guesstimate on with organic tolerance 0.5 mm, reduce meshes over 50,000 triangles first, stop refining after 60 s |
| **Quick** | Ladder engine (its own defaults: routes the part, Guesstimate 0.3 mm on organic and mixed parts) |
| **Standard** (the default) | since 0.10.2 the Auto engine: Quad patches for smooth organic parts, the Ladder engine for everything else ([STANDARD.md](STANDARD.md)); until 0.10.1 nothing: Classic with its usual settings |
| **Fine** | Second pass: Standard's engine (Classic until 0.10.1), then the v2 fitter, the better result kept |
| **Advanced…** | not a preset: shows every setting, starting from the preset chosen before |

The names: *Draft / Quick / Standard / Fine* say what they are for without promising a number; "low medium" and
"high" were dropped as names because they describe a setting, not a use.

## Measured: 12 test meshes (0.9.0; the 0.10.2 run is in STANDARD.md)

Wall time per job (start-up included), faces in the STEP, maximum deviation from the mesh in mm. All 60 results
were closed, valid solids.

| Mesh (triangles) | Draft | Quick | Standard | Fine |
|---|---|---|---|---|
| Blob (9,944) | 5.0 s, 20, 0.113 | 4.8 s, 20, 0.113 | 18.3 s, 24, 0.059 | 43.3 s, 24, 0.059 |
| Blobs plate (6,844) | 49.9 s, 825, 3.000 | 56.7 s, 823, 3.000 | 61.0 s, 823, 3.000 | 118.2 s, **444, 0.108** |
| Bracket (332) | 2.7 s, 8, 0.007 | 2.7 s, 8, 0.007 | 2.9 s, 8, 0.007 | 4.5 s, 8, 0.007 |
| Knob (2,958) | 9.4 s, 944, 0.241 | 11.7 s, 951, 0.129 | 15.1 s, 1,103, 0.129 | 27.9 s, **77, 0.050** |
| Oval plate (4,428) | 16.8 s, 195, 2.076 | 15.9 s, 195, 2.076 | 20.9 s, 141, 3.658 | 35.1 s, **45, 0.083** |
| Coarse plate (1,800) | 9.5 s, 121, 0.078 | 10.3 s, 121, 0.078 | 9.8 s, 121, 0.078 | 22.7 s, 98, 0.076 |
| Fine plate (6,322) | 11.1 s, 62, 0.102 | 9.4 s, 62, 0.102 | 9.3 s, 64, 0.102 | 18.7 s, 64, 0.102 |
| Sphere (5,120) | 4.7 s, 2, 0.045 | 4.2 s, 2, 0.045 | 7.0 s, 2, 0.045 | 11.0 s, 2, 0.045 |
| Torus (4,096) | 6.0 s, 9, 0.076 | 5.9 s, 9, 0.076 | 41.8 s, 526, 0.185 | 69.6 s, 46, 0.188 |
| Coarse tray (1,792) | 10.2 s, 635, 0.235 | 10.5 s, 635, 0.235 | 10.5 s, 635, 0.235 | 21.2 s, **119, 0.079** |
| Fine tray (3,440) | 22.0 s, 1,927, 0.076 | 20.2 s, 1,927, 0.076 | 21.0 s, 1,927, 0.076 | 49.0 s, **124**, 0.076 |
| Vase (24,146) | 11.0 s, 14, 0.025 | 10.9 s, 14, 0.025 | 152.2 s, 8,665, 0.044 | 280.4 s, 8,665, 0.044 |
| **All 12** | **158 s**, 4,762 faces | **163 s**, 4,767 faces | **370 s**, 14,039 faces | **702 s**, 9,716 faces |

What it shows:

- **Quick is about 2.3× faster than Standard** over the set and gives a third of the faces. It is much faster on
  turned parts (vase 152 → 11 s, torus 42 → 6 s); on flat-sided parts it is the same as Standard (it uses the same
  fitter there). It is less exact on the organic blob (0.113 against 0.059 mm: Guesstimate at 0.3 mm), as it says.
- **Fine is about 1.9× slower than Standard** and the best on six of the twelve: the v2 fitter won on the trays,
  the knob, the coarse plate and both blob plates. It is the only preset that kept the narrow cusps of the two
  blob plates (3.0 → 0.11 mm and 3.7 → 0.08 mm; see EFFICIENCY.md). On the vase it kept Classic's result (8,665
  faces); Quick gives 14 faces there, so Fine is not always the one with fewest faces.
- **Draft is Quick, looser.** On these parts it was as fast as Quick (158 against 163 s in all) and only less exact
  where Guesstimate's 0.5 mm showed (knob 0.241 against 0.129 mm). Its reduction step and one-minute refining cap
  only act on big or awkward meshes; see below.

## Big meshes

| Mesh | Draft | Quick | Standard |
|---|---|---|---|
| Dense organic blob (162,036 triangles) | 11.1 s, 19 faces, 0.027 mm | 9.9 s, 19 faces, 0.027 mm | not re-run (the 0.4 notes: about 40 min on a similar 160,000-triangle figure) |
| Dense tray (220,160 triangles) | **not finished in 25 min** at 0.9.0; a bug in the flat-face reduction, fixed in 0.10.0: now 571 s, 25,759 faces, 0.0505 mm, closed and valid (IMPROVEMENTS_0_10.md) | not finished in 40 min (0.10.0: no reduction in Quick) | not run |

Also measured, and *not* made a preset: **Exact facets** (every triangle kept, flat areas merged), the obvious
"fastest, lowest quality" candidate. It is not fast: 32 s for the blob (9,882 faces), 54 s for the vase (10,329
faces), 129 s and 17,089 faces for the dense blob, 428 s for the dense tray, against 5 to 11 s for Quick. It stays
in Advanced → Mode for when every triangle must be kept. A tighter Standard (patch size 20 %, plane angle 2°)
was tried for *Fine* too: 367 s in all, no better than Standard, so not used.

## The buttons

Each preset is a large button with the same red / green LED bars as every other setting (red = load on the
computer, green = quality of the result; set from the table above, not measured per part) and a small drawing of
what it does: on the left a flat face (exact in every preset), on the right an organic bump with the mesh in grey
and the STEP in blue: *Draft* a smooth surface up to 0.5 mm off (the shaded band), *Quick* 0.3 mm off,
*Standard* on the mesh with a stretch of exact facets where a patch would not fit, *Fine* on the mesh all the way.
The drawings are illustrations, not measurements of the part loaded.

The dense tray was the honest limit of Draft at 0.9.0. 0.10.0 found why: the reduction to 50,000 triangles was
being thrown away (a bug in the flat-face retriangulation), so the full mesh was fitted, and the one-minute cap
never fired after a reduction (the start time was lost). With both fixed Draft finishes it in 571 s as a closed,
valid solid within tolerance (25,759 faces: the checkpoint and the final assembly of that many faces are where
the time goes). Exact facets does that part in 428 s with 2,759 faces, so on a dense flat-sided mesh *Exact
facets* is still the better choice; making Draft stop every stage at a time limit is a change to the converter,
not to the preset, and is left for later.

## Not tested

- Windows and macOS; real 3D scans; meshes from other exporters than the test set's.
- Draft's 60 s refining cap on a part that reaches it (no test part did).
