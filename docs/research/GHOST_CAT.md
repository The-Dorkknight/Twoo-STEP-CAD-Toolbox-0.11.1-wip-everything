# Field report: the ghost cat that stretched

**WORK IN PROGRESS: MANY FEATURES MAY BE BROKEN** (EVERYTHING to solid 0.10.2-wip, 23 October 2026)

*Working notes. The first conversions of a real, downloaded model, on a real Mac, by a real human. Everything before
this was synthetic test shapes on a 2-core Linux sandbox, so this is roughly the moment the lab rat met the
outside world.*

## The patient

**"Halloween Cat Stretching"**, one of *A Trio of Halloween Ghost Cats* by **OddPlasticity** on Printables. It is
a third-party model: it was only converted as a test, and its files are not in this repository or anywhere in the
project. 91,676 triangles of pure organic: a ghost-sheet cat mid-stretch, with a fringed hem of pointy tatters, two
eyes, and not a single flat face or round hole anywhere. In other words: everything the converter used to be worst
at, in one cat.

## The machine

An Apple **M5 MacBook**, no GPU support (the converter doesn't use one anyway: OpenCascade is CPU-only). The app
picked the **Extreme** profile with 10 threads.

## The results (read off the diagnostic codes)

Each run's diagnostic panel was photographed with a phone, and the app's own reader (`stl2step.py diag <picture>`)
read the QR codes straight off the photos: the first time the diagnostic code has been read from a real photo of a
real screen rather than a simulated one. All three photos read first time, at an angle, with the screen's moire and glare.

| Preset (what it ran) | Time | Faces | of which NURBS | Facet areas | Max / mean deviation | Notes |
|---|---|---|---|---|---|---|
| **Standard** (Classic, strict) | 4 min 23 s | 17,197 | 829 | 1,164 | **1.110** / 0.0111 mm | checkpoint written; some faces failed the check |
| **Quick** (Ladder → organic route) | **81 s** | 11,864 | 467 | 808 | 0.228 / 0.0214 mm | organic areas guesstimated automatically |
| **Draft** (Ladder, Guesstimate 0.5 mm, reduced to 50k, 1 min cap) | **79 s** | **11,117** | 427 | 734 | 0.271 / 0.0234 mm | |

Every result: a closed solid, geometry check valid.

Plus the first report, before the diagnostic codes were sent: a **Standard** run that took **about 10 minutes** and
gave a **50 MB** STEP, opened in Fusion. (That is longer than the 4 min 23 s above. Whether it was the same cat,
an earlier attempt or included the file opening isn't recorded, so the table keeps them apart.)

## What the numbers say

**1. "So few faces on something pure organic" is the NURBS count, and it's real.** The smooth body of the cat is a
few hundred big NURBS patches: 829 in Standard, 427 in Draft. On a 91,676-triangle organic mesh that is roughly one
real surface per 110–215 triangles. That is the part that looks so good in Fusion.

**2. "10× the faces" is the facets.** The face *total* is dominated by facets: single flat triangles kept where a
patch failed. In Draft, 427 NURBS sit next to 10,082 facet triangles (the preview's count), so the total is about
26 times the number of real surfaces. The facets are small in *area* (the eyes, the hem's tatters, some spots on the
body) but huge in *number*, because a facet area is one face per triangle.

Compared with each other, Draft actually has **35 % fewer faces than Standard** (11,117 against 17,197), not more,
and so does Quick (11,864). Draft's patches are just bigger, because Guesstimate lets an organic patch sit up to
0.5 mm from the mesh inside while its edges stay tight.

**3. The 50 MB is the facets too.** From 141 earlier benchmark runs, a STEP file costs about **2.4 KB per facet**,
**16 KB per NURBS face**, plus about 0.4 MB of overhead (a least-squares fit over the runs). Standard's result:
16,368 non-NURBS faces × 2.4 KB + 829 × 16 KB + 0.4 MB ≈ **54 MB**. That is a good match for the 50 MB file. The
829 beautiful patches are about 13 MB of it; the facets are about 40 MB.

**4. The fast presets are not just faster, here they are better.** Quick is 3.2 times faster than Standard, has 31 %
fewer faces, and its *worst* point is 0.228 mm from the mesh, against Standard's **1.110 mm**. Draft is the same
story at 0.271 mm. Guesstimate's promise is "organic areas within the organic tolerance" (0.3 mm by the author's
own rule for organic shapes, 0.5 mm in Draft), and both met it.

**5. Standard's 1.110 mm is a bug-shaped number.** Standard does not guesstimate: every face is meant to be within
the automatic tolerance (0.05 % of the part's size), and anything that misses is supposed to become facets. A worst
point more than a millimetre away, with a mean of 0.011 mm, means one small spot is far off: a face that passed
the checks and shouldn't have, or a hole in the face network filled by a big facet. It is the first thing to chase
on this model (see "next" below). The mean says the rest of the cat is excellent.

**6. Why the eyes, the tatters and the spots.** The Classic pipeline cuts the mesh into regions, fits a NURBS
patch to each and *trims* it to the region's outline. Where the outline is a tight zig-zag (pointy tatters,
small eye rims, little bumps) the trimmed patch either misses the tolerance or fails the validity check, and the
region falls back to facets. The hem looks stepped in Fusion because the edge of a facet area follows the mesh's
own triangle edges.

**7. A small preview quirk.** The Draft preview shows blue "flat" patches (607 of them), while the report says
"flat 0". The preview calls any planar face with other than three straight edges "flat"; in Draft, where the mesh was
reduced first, some facet areas most likely come out as flat polygons of a few merged triangles, which the preview
paints blue. They are facets, not real flat faces. The report's count is the right one.

## Is this the most advanced free STL → STEP app?

Honest answer: **possibly, and nobody has checked.** What the research for this project found:

- **FreeCAD** (free) turns a mesh into a shape with one face per triangle, then can merge coplanar ones: a valid
  solid, but a facet solid.
- **Fusion's personal-use licence** (free) offers only the *faceted* conversion; *Prismatic* (planes and
  cylinders) needs a paid licence, according to Autodesk's own forum.
- **mesh2step** and the earlier STL2STEP by boromyr (MIT, free) recognise planes and simple shapes and keep the
  rest as triangles.
- Tools that fit **organic NURBS** to a mesh are paid: Geomagic Design X, SpaceClaim's Auto Skin, Fusion's paid
  conversion, Rhino's QuadRemesh → SubD → NURBS route, MeshToCAD.

So among free tools, the research found nothing else that fits organic NURBS to a mesh within a tolerance and keeps
the exact shapes exact. But that is "found nothing", not "measured everything": no side-by-side benchmark against
the others has been run, and the paid tools publish no numbers at all. "Possibly the most advanced" stays
*possibly* until someone runs the same cat through all of them.

## For comparison: the synthetic test ghost, then and now

The project's own test ghost (a synthetic 156,252-triangle ghost with a wavy hem and two eye tunnels, made by
script, not a real model) through Classic with Guesstimate at 0.3 mm on the 2-core Linux test machine:

| | Time | Faces | Worst point | STEP |
|---|---|---|---|---|
| 3 October 2026 (0.4-era) | 262 s | 9,239 | 0.211 mm | 28.3 MB |
| 23 October 2026 (0.10.2) | **228 s** | **7,839** | **0.113 mm** | **23.7 MB** |

Same mesh, same settings: 13 % faster, 15 % fewer faces and the worst point nearly halved, from the fixes since
(repeatable plate fills, private outlines, the faster winding repair). 592 areas are still facets, which is the
same story as the cat. (A strict Standard run was started too and stopped unfinished to deliver these notes.)

## Next

- **Find the 1.110 mm spot.** Needs the mesh itself on the test machine (a file you downloaded can be attached to
  a chat for testing; it stays out of the repository).
- **Quad patches on the cat.** On an M-series Mac the Quad patches engine had no quad remesher (QuadriFlow ships
  for Linux only), so it would have handed the cat to Classic. From 0.10.2 the Mac, Windows and Linux launchers
  install **Instant Meshes** (`pynanoinstantmeshes`, BSD, a ready-built ~1 MB wheel) once, as an optional extra.
  Prediction, *not tested*: the cat's pointy tatters may count as sharp edges (the engine refuses parts with more
  than 0.2 % of their edge length sharper than 30°, because it would round them over); if so, it will say so in the
  log and hand the cat to Classic. The synthetic test ghost, with its cut hem, is refused that way (1.0 %).
- **A hybrid**: quad patches for the organic body, Classic for the sharp tatters, meeting on shared curves. Not
  built; described as an idea in [SOLID_EVIDENCE.md](SOLID_EVIDENCE.md) section 6.
- **The time.** On this M5 Quick and Draft finished in under a minute and a half, well inside the "11th-gen i5 in
  45 minutes" target. Standard's 4 min 23 s (or 10 min) too. Nothing yet on the target i3.

*Running tally: one real cat converted, zero real cats harmed.*
