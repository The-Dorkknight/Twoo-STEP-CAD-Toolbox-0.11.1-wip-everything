<!-- Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight) -->
<!-- Licensed under the PolyForm Noncommercial License 1.0.0. No warranty. -->
<!-- Carried over from the STL2STEP / EVERYTHING to solid 0.10.2 README. Where it says 'app/' or 'tab', read the plugin folder under tostep/plugins/. -->

## Front panel, activity LEDs and effort LEDs
![Front panel](../images/front-panel.webp)

The top bar is styled like an 80s/90s PC case. Left to right: tabs, a CPU % display and VU bar, a red **DISK** LED, a green **RUN** heartbeat that blinks every second while a job is running (amber if it has been quiet for 45 s), and a 3-position **LOCK / NORM / TURBO** key switch that is purely decorative for now.

Every setting has two little LED stacks: **red = how hard it works your computer**, **green = quality of the result**. Red high + green high means the computer will drag but the output will be as good as it gets. They are rough guides computed from the value, not measurements, and update as you change a setting.

## Smooth sketch (optional, in Extrude outline mode)
![Smooth sketch preview](../images/smooth-sketch.webp)

*Extrude outline* normally redraws a part's outline as straight segments, so an oval hole becomes dozens of tiny flats. Tick **Smooth sketch** and each loop is redrawn as the simplest shapes that stay within the outline tolerance: straight **lines**, true **arcs** and **circles**, **ellipses**, and **splines** with as few control points as possible. Press *Preview outline* to see it; red dots mark where two pieces meet. Command line: `--mode extrude --smooth-sketch`.

On a test plate with rounded corners, a slot, a D-shaped hole, a hex hole, a round hole, an ellipse and a freeform "bean" hole, the result has 25 faces, the same as the original CAD model (94 without Smooth sketch at the automatic tolerance, 319 at 0.02 mm). The ellipse comes back as one true ellipse and the bean as one closed spline (11 control points at the automatic tolerance, 33 at 0.02 mm).

Limits: it only applies to *Extrude outline* (not Loft, not Smooth surfaces). A loop that can't be fitted within tolerance stays as straight segments, and the log says so. Noisy or scanned outlines come out in more pieces. A very coarse round fillet (steps over 25°) is read as corners. The plug-ins don't offer it yet.

## Is it a 3D scan? (new in 0.11.4)
A mesh over 100,000 triangles whose small triangles are tilted a few degrees at random is a 3D scan; a CAD export or a clean
model is flat or smooth to a fraction of a degree. Before it converts such a file the page asks: **Yes, treat it as a scan**
(smooth the fuzz off while keeping real edges, thin the mesh out, guesstimate freeform areas), **No, it's CAD: use Classic**, or
Cancel. *Advanced → Is it a 3D scan?* answers it once and for all. A scan that is not a turned or flat-sided part is converted
as in 0.11.3. Details and measurements: `docs/research/SCAN_EDGES.md`.

## 3D scans (new 18 November 2026)
Scans bring things a CAD export doesn't: floating fragments, bits of the turntable, holes the scanner couldn't
see, and noise. **Scan clean-up** (Advanced; automatic when the mesh isn't one closed piece) removes loose pieces,
cuts edges shared by three or more triangles and fills the holes. Since 0.11.3 a **dense mesh counts as a scan even when it is one closed piece**
(over 150,000 triangles, which is what a real scan is): it is denoised lightly (3 passes, only if there is noise to
remove; a clean dense mesh is left exactly as it was; set *Denoise iterations* to change it) and reduced to 150,000
triangles before anything else, provided that moves the surface by no more than half the tolerance. *Scan clean-up:
off* skips all of it. Use **Draft** or **Quick** for scans: on a 653,000-triangle stand-in scan Draft took 7.7 minutes
(65 flat and 390 round faces recognised, worst gap 0.085 mm). If the scan stands on a turntable plate, choose
*Keep only the largest piece*. A noisy scan still comes out partly as exact facets. Details:
[SCAN_CLEANUP.md](../research/SCAN_CLEANUP.md).

## Guesstimate organic areas (optional, off by default)
![Settings with Guesstimate switched on](../images/guesstimate.webp)

![The ghost test model: input mesh, Classic with Guesstimate, Staged with Guesstimate](../research/images/ghost-before-after.webp)

Tick **Guesstimate organic areas (fast)** in Settings when the freeform parts of a model only need to be roughly right. *Organic tolerance* (default 0.3 mm) is how far a freeform surface may sit from the mesh inside a patch. **Flat faces, round holes (cylinders), spheres and all edges keep the normal tight tolerance.** With it on, dense meshes are first reduced (staying within a third of the normal tolerance, checked, and kept closed) and the slow patch filler is skipped. Command line: `--guess` or `--guess 0.3`. Works with the Classic and Staged fitters; the FreeCAD and Fusion plug-ins don't offer it yet.

Measured on a 160,000-triangle organic test model (2-core machine): 41 min → 4.3 min, 107 MB → 28 MB STEP, 40,484 → 9,239 faces, max deviation 0.105 → 0.211 mm. It does **not** reduce the number of areas left as triangles (513 vs 450 regions); that needs the boundary work planned next. Parts made only of flats and round features come out identical with it on or off.
