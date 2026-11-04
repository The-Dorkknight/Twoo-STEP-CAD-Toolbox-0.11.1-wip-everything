<!-- Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight) -->
<!-- Licensed under the PolyForm Noncommercial License 1.0.0. No warranty. -->
<!-- Carried over from the STL2STEP / EVERYTHING to solid 0.10.2 README. Where it says 'app/' or 'tab', read the plugin folder under tostep/plugins/. -->

## What to expect
- **CAD-style parts** (flat faces, holes, rounded edges and corners, chamfers): flat faces
  become true planes; edge rounds and corner balls are split apart and become true
  cylinders and spheres. Every face is checked (valid, right size, on the mesh) and anything
  that fails is kept as exact facets, so the result is always a closed solid.
- **Organic shapes** (sculpts, scans) become smooth fitted patches.
- **Organic shapes with many sharp rims or tunnels** work but are slow and come out partly
  faceted. A 160k-triangle test ghost took 30 min and gave a valid, accurate solid with
  many facets. For those, "Exact facets" mode is quicker if you only need a closed STEP.
- **Preview:** when a conversion finishes, a 3D preview opens in the app (drag to rotate,
  scroll to zoom). Blue = flat, green = cylinder/sphere, light grey = smooth NURBS,
  dark = kept as facets. *Original* shows the input mesh for comparison.

## Experimental options
The default converter is the proven *Classic* one. These are opt-in:
- **Surface fitter → v2**: a newer fitter (adaptive NURBS, stricter checks). Better on some
  CAD parts, worse on others (can give more faces or facets).
- **Surface fitter → Staged**: roughing first — lathe and extruded shapes become exact faces
  before patch fitting (see above). Far fewer faces on turned, round or prismatic parts.
- **Surface fitter → Second pass**: runs Classic, then v2, and keeps whichever came out
  better — it can only improve on Classic, but takes about twice as long.
- **Extrude outline / Loft cross-sections** modes: only for flat-profile or tapered/stepped
  parts. On organic shapes they give a striped mess — use Smooth surfaces for those.
Command line: `--engine staged`, `--engine ladder` (with `--route`, `--budget`, `--max-fills`, `--no-ladder-guess`, `--plane-rule`), `--engine v2`, `--engine best`, `--mode extrude`, `--mode loft`.
