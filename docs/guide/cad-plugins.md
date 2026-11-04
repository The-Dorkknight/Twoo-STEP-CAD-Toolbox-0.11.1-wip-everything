<!-- Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight) -->
<!-- Licensed under the PolyForm Noncommercial License 1.0.0. No warranty. -->

# FreeCAD and Fusion plug-ins
**Easiest:** start Twoo STEP CAD Toolbox, open the **Install** tab and click *Install FreeCAD plugin*
(or *Install Fusion add-in*). It finds every FreeCAD on the computer (normal install,
AppImage, Flatpak, Snap, 1.0 and 1.1 settings folders), copies the workbench in and tells
it where this folder is. Restart FreeCAD and pick **Twoo STEP CAD Toolbox** from the workbench list.
(It won't appear in FreeCAD's Addon Manager — that only lists add-ons it downloaded itself.)
Linux: *Add Twoo STEP CAD Toolbox to the app menu* makes it start like any other app.
*Add right-click entries* puts **Convert to STEP** (for meshes and OpenSCAD models) and **Live-link** (for a `.scad`) in
Linux Mint's Nemo, GNOME Files' Scripts menu or Windows' Send to menu (`install files`, undo with `install files --remove`).
Command line: `./twoo-step-cad-toolbox-linux.sh install` (or `install freecad`, `install fusion`, `install menu`, `install files`).

**Linux: the app opened FreeCAD instead of a browser?** Some programs claim web links.
Twoo STEP CAD Toolbox now skips those and opens Firefox/Chrome/Chromium; the address is also printed in
the terminal window if you want to paste it yourself.

Manual install, if you prefer:
The two CAD plug-ins are thin front ends (FreeCAD workbench, Fusion add-in; Fusion's free Personal Use licence runs add-ins too): they send the mesh to this
Twoo STEP CAD Toolbox folder, it does the conversion in the background, and the solid comes back into your model.
**Double-click the Twoo STEP CAD Toolbox start file once first** so its setup is done; the plug-ins won't download anything themselves.
They look for this folder in your home folder, Applications, Desktop, Documents and Downloads, and ask where it is if it isn't there. Results are also saved in `Output/`.

The two plug-ins share one bridge file and one hardpoint-STL reader, which live in the toolbox, so the repository folders alone are not complete. For a manual copy, build the ready folders first:

    python run_tostep.py install freecad        # or fusion: does it all for you, or
    python tools/build_cad_packages.py          # writes dist/cad/FreeCAD/TOSTEP and dist/cad/Fusion/TOSTEP

**FreeCAD (0.20 or newer)**
1. Copy `dist/cad/FreeCAD/TOSTEP` into FreeCAD's `Mod` folder:
   - Linux: `~/.local/share/FreeCAD/Mod/` (for FreeCAD 1.0+ AppImage/Flatpak the same path usually works; in FreeCAD, `Macro → Macros…` shows the user folder, and `Mod` sits next to it)
   - macOS: `~/Library/Application Support/FreeCAD/Mod/`
   - Windows: `%APPDATA%\FreeCAD\Mod\`
2. Restart FreeCAD and pick the **Twoo STEP CAD Toolbox** workbench.
   - **Mesh → STEP solid**: converts the selected mesh object(s). With nothing selected it asks for mesh files. The solid is added as `<name>_solid` and the mesh is hidden.
   - **OpenSCAD → STEP solid**: pick a `.scad` / `.csg`; it is rebuilt as real geometry and added as a solid.
   - **Jetwash (clean STEP)**: cleans the selected solid(s), or STEP files you pick. Result: `<name>_clean`.
   - **Export hardpoint STL**: saves the selected solid(s) as a hardpoint STL (done inside FreeCAD, no toolbox folder needed).
   - **Twoo STEP CAD Toolbox folder…**: choose where the toolbox is.
   Progress shows in a dialog with Cancel; the full log is in View → Panels → Report view.

**Fusion**
1. Copy `dist/cad/Fusion/TOSTEP` somewhere permanent (e.g. next to this folder).
2. In Fusion: **Utilities → Add-Ins** (or Shift+S) → **Add-Ins** tab → the **+** → choose that `TOSTEP` folder → select it → **Run**. Tick *Run on Startup* to keep it.
3. The buttons are under **Utilities → Add-Ins**:
   - **Mesh → STEP solid**: select mesh bodies (or choose a mesh file), set options, OK. The STEP is imported into the same component and the mesh is hidden.
   - **OpenSCAD → STEP solid**: choose a `.scad` / `.csg`; the solid is imported.
   - **Jetwash STEP**: choose a STEP file (defaults to the last converted one); the cleaned result is imported.
   - **Export hardpoint STL**: select solid bodies and save them as a hardpoint STL.
   Fusion works in cm internally; the add-in converts to mm for you.

**What is and is not tested:** the two plug-ins are loaded against stand-in modules by `tests/test_cad_adapters.py` (they import, register the commands the plug-in manifests promise, and build the same command lines the toolbox accepts). They have **not** been run in a real FreeCAD or Fusion.
The Image → STEP plug-in has no CAD command.

**The toolbox folder moved or was updated?** Run the installer again (Install tab, or `install freecad` / `install fusion`): it rewrites the small note that tells the plug-in where the folder is.

