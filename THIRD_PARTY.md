# Third-party software

The PolyForm Noncommercial licence in [LICENSE.md](LICENSE.md) covers **only the code written by Matthew Armstrong** in this repository. `tostep/plugins/scad2step/scad2step_core.py` is the SCAD2STEP engine and `tostep/plugins/image2step/imagetosketch` the ImageToSketch tracing library, both also by Matthew Armstrong, copied into this repository. Apart from the QuadriFlow program in `tostep/plugins/mesh2step/bin/linux-x86_64` (see its row), the components below are not included in the repository; the start scripts download them on first run (OpenSCAD is the exception: you install it yourself), and each keeps its own licence (see the linked pages). Twoo STEP CAD Toolbox is not affiliated with or endorsed by any of these projects or by Autodesk, the FreeCAD project, Bambu Lab or Prusa Research.

| Component | Used for | Project page |
|---|---|---|
| Python | runtime (downloaded privately) | <https://www.python.org/> |
| uv | fetches Python and packages | <https://github.com/astral-sh/uv> |
| cadquery-ocp / Open CASCADE Technology | STEP reading/writing, geometry, healing | <https://github.com/CadQuery/OCP> · <https://dev.opencascade.org/> |
| trimesh | mesh loading | <https://github.com/mikedh/trimesh> |
| NumPy, SciPy | numerics | <https://numpy.org/> · <https://scipy.org/> |
| Shapely | 2D outline geometry | <https://github.com/shapely/shapely> |
| NetworkX | graph algorithms (used inside trimesh; the toolbox's own winding repair uses SciPy since 0.9.1) | <https://networkx.org/> |
| rtree (libspatialindex) | spatial index | <https://github.com/Toblerity/rtree> |
| pyfqmr | fast mesh simplification | <https://github.com/Kramer84/pyfqmr-Fast-Quadric-Mesh-Reduction> |
| mapbox-earcut | polygon triangulation | <https://github.com/skogler/mapbox_earcut_python> |
| psutil | CPU / disk activity meters | <https://github.com/giampaolo/psutil> |
| OpenCV (opencv-python-headless) | reading and tracing pictures in the Image → STEP tab | <https://opencv.org/> · <https://github.com/opencv/opencv-python> |
| QuadriFlow (BSD-3; with Eigen MPL2-only, LEMON, MiniSat, pcg32, Intel parallel stable sort, Boost graph headers) | quad layout for the experimental Quad patches engine. **Bundled as an unchanged program** for 64-bit Linux in `tostep/plugins/mesh2step/bin/linux-x86_64/quadriflow`, built from the pinned sources listed with every licence in `tostep/plugins/mesh2step/bin/README.txt`; other systems build it with `tools/build_quadriflow.py` | <https://github.com/hjwdzh/QuadriFlow> |
| Instant Meshes (BSD-3), via pynanoinstantmeshes (BSD; optional: since 0.10.2 the launchers install the ready-built 0.0.3 wheel once where one exists, listed in `requirements-optional.txt`) | quad layout for the Quad patches engine where QuadriFlow is not there | <https://github.com/wjakob/instant-meshes> · <https://pypi.org/project/pynanoinstantmeshes/> |
| segno | draws the QR code of the diagnostic panel (pure Python) | <https://github.com/heuer/segno/> |
| PyTorch (optional GPU pack, installed only on request) | GPU scoring in the v2 fitter | <https://pytorch.org/> |
| OpenSCAD (optional, installed by you, not bundled; only used for the SCAD → STEP tab) | evaluates `.scad` models to a CSG tree; also its own render for the comparison check | <https://openscad.org/> |
| FreeCAD, Autodesk Fusion | optional host applications for the plug-ins | <https://www.freecad.org/> · <https://www.autodesk.com/products/fusion-360> |

Check each project's page for its licence terms before redistributing a bundle that includes it.
