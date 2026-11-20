TITLE
Twoo STEP CAD Toolbox: STL to STEP converter (free, offline)

SUMMARY (paste into the Summary box)

**Turn your STL into a real CAD part.**

Ever tried to remix an STL and found it's just thousands of tiny triangles? You can't click a face, move a hole or make it 2 mm wider. Twoo STEP CAD Toolbox turns that mesh into a **STEP file**, the format CAD programs actually understand. Flat bits become real flat faces, holes become real cylinders, curvy bits become smooth surfaces. Open it in Fusion, FreeCAD, Onshape or SolidWorks and edit away.

**What it can do**

* **STL → STEP** (also OBJ, PLY, 3MF), with four speeds: Draft, Quick, Standard, Fine
* **Photo → STEP**: a photo of a flat part becomes a solid of the thickness you choose (experimental)
* **OpenSCAD → STEP**, with the Customizer sliders still working
* **Jetwash**: cleans slivers and tiny junk faces out of a messy STEP
* **3D scans**: cleans up specks, holes and fuzz first. A scanned soldering iron went from 3 hours to under 8 minutes.
* **3D preview** of the result, and a self-check that tells you how close it is to your mesh

**Free for non-commercial use.** Hobby, school and your own prints are all fine. Selling it or using it in a business needs written permission (ask on GitHub). Licence: PolyForm Noncommercial 1.0.0.

No account and no cloud. It runs on your own computer, and your files never leave it.

PRINT SETTINGS / POST-PRINTING (Thingiverse asks for these; paste this into "How I designed this" instead)

**Get it**

1. Go to https://github.com/The-Dorkknight/twoo-step-cad-toolbox
2. Click **Code → Download ZIP** and unzip it anywhere
3. Double-click the start file: `(Windows).bat`, `(Mac).command` (right-click → Open the first time) or `linux.sh`
4. The first start sets itself up (it needs the internet just this once), then it opens in your browser
5. Drop in an STL and press **Convert**

To uninstall, delete the folder.

**Honest bit:** this is a work in progress made by one person, and it's only been tested on Linux so far. If it misbehaves on Windows or Mac, please tell me on GitHub. Always check a converted part before you rely on it.

The files attached here are a sample: a test STL and the STEP the toolbox made from it, so you can see the difference in your own CAD program.

TAGS
stl to step, step file, cad, converter, remix, fusion 360, freecad, openscad, 3d scan, reverse engineering
