<!-- Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight) -->
<!-- Licensed under the PolyForm Noncommercial License 1.0.0. No warranty. -->
<!-- Carried over from the STL2STEP / EVERYTHING to solid 0.10.2 README. Where it says 'app/' or 'tab', read the plugin folder under tostep/plugins/. -->

## Troubleshooting
| Problem | What to do |
|---|---|
| macOS says the `.command` file can't be opened | Right-click → Open → Open, or System Settings → Privacy & Security → Open Anyway. |
| First start is slow | It is downloading a private Python and libraries (~500 MB, a few minutes). Later starts are instant. |
| Browser doesn't open | Copy the `http://127.0.0.1:…` address printed in the terminal window into a browser. |
| Linux opened FreeCAD instead of a browser | Fixed: Twoo STEP CAD Toolbox skips programs that claim web links. The address is also printed in the terminal. |
| Twoo STEP CAD Toolbox isn't in FreeCAD's Addon Manager | Expected. It is a workbench: choose it in the workbench dropdown after installing from the Install tab and restarting FreeCAD. |
| *Install GPU pack* fails with "managed by uv and should not be modified" | Fixed in this build (the installer now uses the same switches as the first-run setup). Update, then click it again. |
| The graphics card is listed but greyed out or "does not work" | Press **Test it** in the *Graphics card* box: it says what failed. A GTX 10-series card needs the CUDA 12.6 build (the box offers *Reinstall the right build*); an old Nvidia driver, or an AMD / Intel card, cannot be used. See [gpu.md](gpu.md). |
| Result has black (facet) patches | Those areas couldn't be fitted within tolerance and are kept as exact triangles so the solid stays closed. Try a larger tolerance, or *Exact facets* mode. |
| Out of memory on a big mesh | Leave "Protect memory" on, or set "Reduce to N triangles". |
| SCAD → STEP says OpenSCAD was not found | Install it from openscad.org, or type its location (the program or the AppImage) into the box on the tab and press *Use this*. `.csg` files convert without it. |
| A hardpoint STL's labels were ignored | The log says why: the triangles were edited after the labels were written, or it is a newer format version. Re-export it from the CAD program, or convert it as a plain STL. |
| Image → STEP says the picture tracer needs OpenCV | This version added one library. Close the app and start it again with its start file: it sees that the list of libraries changed and installs what is missing (needs the internet once). |
| Image → STEP says "No scale token found" | The token must be the black square with one clipped and one rounded corner, flat on a light surface, fully in view, not touching the part, not in glare. Or choose another way to set the scale. |
| A traced part comes out a few percent too big | The part is thicker than the token and the camera distance is not known: type the part's height under *Part's top above the token*, and if the log says the picture has no lens data, the *Camera distance* too. |
| The live link doesn't update | It needs a file chosen from disk (not dropped in), and it watches the file and its `include`/`use` libraries. Save in OpenSCAD with F2. |
| Start over | Delete the `runtime/` folder. |
