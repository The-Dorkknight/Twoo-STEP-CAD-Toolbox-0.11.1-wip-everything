# Twoo STEP CAD Toolbox: turn your STL into a real CAD part

**Title (paste into the title box):** Twoo STEP CAD Toolbox: STL to STEP converter (free, offline)

**Summary (the short line under the title):** Turn printable meshes, OpenSCAD files and even photos of flat parts into smooth, editable STEP solids. Free for non-commercial use, runs offline on Windows, Mac and Linux.

---

## What is this?

You found the perfect bracket. You want to make it 2 mm wider. You open the STL in your CAD program and… it's 24,000 tiny triangles. You can't click a face. You can't move a hole. You can't do anything except cry gently.

**Twoo STEP CAD Toolbox fixes that.** Drop in an STL and it hands you back a **STEP file**: the format CAD programs actually understand. Flat bits come out as real flat faces, holes as real cylinders, curvy bits as smooth surfaces. Open it in Fusion, FreeCAD, Onshape, SolidWorks or whatever you use, and edit away.

*(The picture on top: a vase that was 24,146 triangles comes out as 14 real surfaces.)*

## Why would I want that?

- **Remix things properly.** Widen it, shorten it, add a mounting hole, fillet an edge.
- **Fix models whose maker lost the original CAD file.**
- **Measure stuff.** Click a hole, get its exact diameter.
- **Send parts to a machine shop or a CNC.** They want STEP, not STL.
- **Bring old OpenSCAD designs into normal CAD.**

## What's in the box

- 🔺 **STL → STEP** (also OBJ, PLY and 3MF). Four speeds: **Draft**, **Quick**, **Standard** and **Fine**. Standard is a good place to start.
- 📷 **Photo → STEP.** Snap a photo of a flat part (a gasket, a bracket, a cookie cutter shape), type how thick it is, and get a solid. *(Still experimental.)*
- 🧩 **OpenSCAD → STEP.** Your .scad model rebuilt as proper CAD, with the Customizer sliders still working.
- 🚿 **Jetwash.** Got a STEP full of slivers and tiny junk faces? It scrubs them out, and undoes any clean-up that would damage the part.
- 📌 **Hardpoint STL.** A special STL that remembers which CAD face each triangle came from, so converting it back is exact, not guesswork.
- 🔍 **3D preview.** Spin the result before you download it. Blue means flat, green means round.
- ✅ **It checks its own homework.** Every result is read back in and measured against your mesh, so you see how close it is (usually a few hundredths of a millimetre).

## Got a 3D scan?

Scans are messy: floating specks, holes the scanner couldn't see, bits of the turntable and a fine layer of fuzz. The toolbox now cleans all that up first. On a scanned soldering iron the conversion went from **over three hours** to **under eight minutes**. Scans are still the hardest job it does, so expect some areas to stay faceted.

## Is it free?

**Yes, for non-commercial use.** Hobby projects, your own prints, school, tinkering: go for it. Selling it, or using it inside a business, needs the author's written permission (open an issue on GitHub to ask). The full licence is the PolyForm Noncommercial License 1.0.0, included in the download.

No account, no cloud, no upload. Everything happens on your own computer, so your files never leave it.

## How do I get it?

1. Go to **https://github.com/The-Dorkknight/twoo-step-cad-toolbox**
2. Click the green **Code** button, then **Download ZIP**, and unzip it anywhere.
3. Double-click the start file for your computer:
   - **Windows:** `Twoo STEP CAD Toolbox (Windows).bat`
   - **Mac:** `Twoo STEP CAD Toolbox (Mac).command` (the first time, right-click it and choose **Open**)
   - **Linux:** `twoo-step-cad-toolbox-linux.sh`
4. The first start sets itself up (it needs the internet just this once). Then it opens in your web browser.
5. Drop in an STL and press **Convert**.

To uninstall it, delete the folder. Nothing else is installed.

**Old laptop?** There's a "Potato" setting that keeps things gentle. A graphics card helps on big organic parts, but isn't needed.

## Works with your CAD program

- **Fusion** and **FreeCAD:** one-click add-ins from the app's Install tab.
- **SolidCraft** (the new open-source Fusion-style CAD app): grab a selected mesh body, convert it, and send the solid straight back.
- Anything else that opens STEP files (which is nearly everything).

## Honest bit

This is a **work in progress**, built by one person. It's tested on **Linux**. Windows, Mac and the CAD add-ins haven't been tried on real machines yet, so if something breaks, please tell me on GitHub. The result is a solid made of surfaces, not a model with its design history (no sketches or extrude steps to edit). Always check a converted part before you rely on it.

## Pictures

1. STL in, real CAD out
2. Three test parts: the STL, the default way and the newer "Staged" way
3. Blobby, sculpted shapes work too
4. The 3D preview
5. Pick a speed
6. A finished conversion, with its self-check
7. 3D scans: from three hours to eight minutes
8. More tools in the box

## Tags (pick up to the site's limit)

stl to step, step, cad, converter, mesh, remix, fusion 360, freecad, openscad, 3d scan, reverse engineering, tool, software

*Made by Matthew Armstrong (The-Dorkknight). Feedback, bug reports and "it worked!" photos are all very welcome.*
