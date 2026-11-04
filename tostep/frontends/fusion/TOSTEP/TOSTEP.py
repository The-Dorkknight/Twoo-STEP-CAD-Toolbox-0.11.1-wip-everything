# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""Twoo STEP CAD Toolbox add-in for Autodesk Fusion (works on the free Personal Use licence too).

Mesh → STEP solid: exports the selected mesh bodies (or a mesh file), converts them with the
portable Twoo STEP CAD Toolbox app in the background, and imports the STEP back into the design.
Jetwash: cleans a STEP file (slivers, micro-steps, thin fins, specks) and imports the result.
Hardpoint STL: saves solid bodies as an ordinary STL that also remembers the CAD faces and
corners, so it converts back to a solid without guessing (done here in Fusion; the portable
app isn't needed for it).
"""
import json
import os
import re
import sys
import threading
import time
import traceback

import adsk.core
import adsk.fusion

try:
    from . import tostep_bridge as bridge
    from . import hardpoint_fusion
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import tostep_bridge as bridge
    import hardpoint_fusion

HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(HERE, "resources")
EVENT = "TOSTEP_JobEvent"
PANELS = ["SolidScriptsAddinsPanel"]                       # Utilities → Add-Ins
EXTRA_PANELS = ["ParaMeshModifyPanel", "MeshModifyPanel"]  # Mesh tab, if these ids exist
CMDS = {
    "TOSTEP_Convert": ("Mesh → STEP solid", "Turn mesh bodies into real solids: planes, cylinders, "
                         "spheres and NURBS surfaces.", "convert"),
    "TOSTEP_Scad": ("OpenSCAD → STEP solid", "Rebuild an OpenSCAD model (.scad or .csg) as real CAD "
                      "geometry (true cylinders, spheres, swept surfaces, exact booleans) and import it. "
                      "Needs OpenSCAD installed (not for .csg).", "scad"),
    "TOSTEP_Jetwash": ("Jetwash STEP", "Clean slivers, micro-steps, thin fins, specks and tiny holes "
                         "out of a STEP file and import the result.", "jetwash"),
    "TOSTEP_Hardpoint": ("Export hardpoint STL", "Save solid bodies as an STL that slicers read as usual "
                           "and that also remembers the CAD faces and corners, so it converts back to a "
                           "solid without guessing.", "hardpoint"),
}

app = adsk.core.Application.get()
ui = app.userInterface
_handlers = []
_event = None
_active = None     # the running Batch, if any


# ----------------------------------------------------------------------------- settings
def _settings_path():
    p = os.path.join(HERE, "settings.json")
    try:
        with open(p, "a"):
            pass
        return p
    except OSError:
        return os.path.join(os.path.expanduser("~"), ".tostep_fusion.json")


def load_settings():
    d = dict(folder="", mode="surface", axis="z", units="mm", tol=0.0, reduce=0, hide=True,
             threshold=0.05, coarseness=2, last=None)
    try:
        with open(_settings_path(), encoding="utf-8") as fh:
            d.update(json.load(fh))
    except Exception:
        pass
    return d


def save_settings(d):
    try:
        with open(_settings_path(), "w", encoding="utf-8") as fh:
            json.dump(d, fh, indent=1)
    except Exception:
        pass


def _safe(s):
    return re.sub(r"[^\w\-. ]+", "_", s or "").strip() or "part"


def _unique(path):
    base, ext = os.path.splitext(path)
    i, p = 2, path
    while os.path.exists(p):
        p = f"{base}_{i}{ext}"
        i += 1
    return p


def _dirs(folder):
    work = os.path.join(folder, "runtime", "work", "fusion")
    out = os.path.join(folder, "Output")
    os.makedirs(work, exist_ok=True)
    os.makedirs(out, exist_ok=True)
    return work, out


# ----------------------------------------------------------------------------- mesh export
def export_mesh_body(mb, path):
    """Write a MeshBody to binary STL in mm, in its component's own coordinates."""
    pm = mb.mesh                                   # PolygonMesh: the body's own data (cm)
    coords = list(pm.nodeCoordinatesAsDouble)
    tris = list(pm.triangleNodeIndices)
    q = list(pm.quadNodeIndices)
    for i in range(0, len(q), 4):
        a, b, c, d = q[i:i + 4]
        tris += (a, b, c, a, c, d)
    try:                                           # n-gons: fan them
        counts = list(pm.nodeCountPerPolygon)
        poly = list(pm.polygonNodeIndices)
        k = 0
        for n in counts:
            for j in range(1, n - 1):
                tris += (poly[k], poly[k + j], poly[k + j + 1])
            k += n
    except Exception:
        pass
    if not tris:                                   # fall back to the display mesh
        dm = mb.displayMesh
        coords = list(dm.nodeCoordinatesAsDouble)
        tris = list(dm.nodeIndices)
    return bridge.write_binary_stl(path, coords, tris, 10.0)   # Fusion works in cm


# ----------------------------------------------------------------------------- background batch
class Batch:
    """Runs jobs one after another on a worker thread; everything that touches Fusion
    happens in the custom-event handler on the main thread."""

    def __init__(self, folder, items, hide):
        self.folder, self.items, self.hide = folder, items, hide
        self.n = len(items)
        self.job = None
        self.cancelled = False
        self.errors, self.added = [], 0
        self.progress = ui.createProgressDialog()
        self.progress.isCancelButtonShown = True
        self.progress.cancelButtonText = "Cancel"
        self.progress.isBackgroundTranslucent = False

    def fire(self, **d):
        app.fireCustomEvent(EVENT, json.dumps(d))

    def start(self):
        self.progress.show("Twoo STEP CAD Toolbox", "Starting…", 0, 100, 0)
        threading.Thread(target=self._work, daemon=True).start()
        threading.Thread(target=self._ticker, daemon=True).start()

    def _ticker(self):                       # lets the main thread notice Cancel while quiet
        while not getattr(self, "finished", False):
            time.sleep(0.5)
            self.fire(kind="tick")

    def _work(self):
        for i, it in enumerate(self.items):
            if self.cancelled:
                break
            head = (f"[{i + 1}/{self.n}] " if self.n > 1 else "") + it["label"]
            self.fire(kind="line", text=head)
            done = threading.Event()
            res = {}

            def fin(ok, r, res=res, done=done):
                res.update(ok=ok, r=r)
                done.set()
            self.job = bridge.Job(self.folder, it["args"],
                                  on_line=lambda s, h=head: self.fire(kind="line", text=h + " — " + s.strip()),
                                  on_progress=lambda p: self.fire(kind="progress", value=p),
                                  on_finish=fin).start()
            done.wait()
            self.fire(kind="result", index=i, ok=res["ok"], result=res["r"])
        self.finished = True
        self.fire(kind="end")

    # main thread ----------------------------------------------------------------
    def handle(self, d):
        if self.progress.wasCancelled and not self.cancelled:
            self.cancelled = True
            if self.job:
                self.job.cancel()
        k = d.get("kind")
        if k == "line":
            self.progress.message = d["text"][:110].replace("%", " pct")
        elif k == "progress":
            self.progress.progressValue = int(d["value"])
        elif k == "result":
            it = self.items[d["index"]]
            if d["ok"]:
                self._import(it, d["result"])
            elif not self.cancelled:
                self.errors.append(f"{it['label']}: {d['result']}")
        elif k == "end":
            self._end()

    def _import(self, it, path):
        try:
            design = adsk.fusion.Design.cast(app.activeProduct)
            if not design:
                raise RuntimeError("no design is open")
            target = it.get("component") or design.rootComponent
            opts = app.importManager.createSTEPImportOptions(path)
            if not app.importManager.importToTarget(opts, target):
                raise RuntimeError("Fusion refused the import")
            self.added += 1
            if self.hide and it.get("body") is not None:
                try:
                    it["body"].isLightBulbOn = False
                except Exception:
                    pass
            s = load_settings()
            s["last"] = path
            save_settings(s)
        except Exception as e:  # noqa
            self.errors.append(f"{it['label']}: saved to {path}, but importing failed ({e}). "
                               f"You can insert that file yourself.")

    def _end(self):
        global _active
        self.finished = True
        try:
            self.progress.hide()
        except Exception:
            pass
        _active = None
        if self.errors:
            ui.messageBox("\n\n".join(self.errors), "Twoo STEP CAD Toolbox")
        elif self.cancelled:
            ui.messageBox("Cancelled.", "Twoo STEP CAD Toolbox")


class JobEventHandler(adsk.core.CustomEventHandler):
    def notify(self, args):
        try:
            if _active:
                _active.handle(json.loads(args.additionalInfo))
        except Exception:
            ui.messageBox("Twoo STEP CAD Toolbox:\n" + traceback.format_exc())


# ----------------------------------------------------------------------------- command dialogs
def _ready_folder(folder):
    f = bridge.find_folder(folder)
    if not f:
        return None, ("Twoo STEP CAD Toolbox folder not found. Download Twoo STEP CAD Toolbox, unzip it, double-click its "
                      "start file once, then choose its folder here.")
    ok, msg = bridge.status(f)
    return (f if ok else None), msg


def _pick_file(title, filt):
    fd = ui.createFileDialog()
    fd.title, fd.filter, fd.isMultiSelectEnabled = title, filt, False
    return fd.filename if fd.showOpen() == adsk.core.DialogResults.DialogOK else ""


class CreatedHandler(adsk.core.CommandCreatedEventHandler):
    def __init__(self, kind):
        super().__init__()
        self.kind = kind

    def notify(self, args):
        try:
            s = load_settings()
            cmd = args.command
            cmd.isExecutedWhenPreEmpted = False
            inp = cmd.commandInputs
            if self.kind == "hardpoint":
                sel = inp.addSelectionInput("bodies", "Solid bodies", "Select the solid bodies to export")
                sel.addSelectionFilter("SolidBodies")
                sel.setSelectionLimits(1, 0)
                t = inp.addFloatSpinnerCommandInput("hp_tol", "Surface deviation mm", "", 0.001, 5.0, 0.01,
                                                    float(s.get("hp_tol", 0.05)))
                t.tooltip = "How far the triangles may be from the true surface. Smaller = smoother curves, bigger file."
                a = inp.addFloatSpinnerCommandInput("hp_ang", "Angle (degrees)", "", 1.0, 45.0, 1.0,
                                                    float(s.get("hp_ang", 15.0)))
                a.tooltip = "Largest angle between neighbouring triangles on a curve."
                inp.addTextBoxCommandInput(
                    "hp_info", "", "The file is an ordinary STL: slicers see the same triangles as a normal "
                    "export. Its spare bytes also record which triangles belong to which CAD face, the kind "
                    "of surface, and the corners.", 4, True)
                h2 = ExecuteHandler(self.kind)
                cmd.execute.add(h2)
                _handlers.append(h2)
                return
            if self.kind == "convert":
                sel = inp.addSelectionInput("meshes", "Mesh bodies", "Select mesh bodies to convert")
                sel.addSelectionFilter("MeshBodies")
                sel.setSelectionLimits(0, 0)
                inp.addStringValueInput("file", "…or a mesh file", "").isReadOnly = True
                inp.addBoolValueInput("pickfile", "Choose mesh file…", False, "", False)
                dd = inp.addDropDownCommandInput("units", "File units", adsk.core.DropDownStyles.TextListDropDownStyle)
                for u in ("mm", "inches", "cm", "m"):
                    dd.listItems.add(u, u == s["units"], "")
                dd.isVisible = False
                dm = inp.addDropDownCommandInput("mode", "Mode", adsk.core.DropDownStyles.TextListDropDownStyle)
                for key, name in zip(bridge.MODES, bridge.MODE_NAMES):
                    dm.listItems.add(name, s["mode"] == key, "")
                da = inp.addDropDownCommandInput("axis", "Outline axis (extrude/loft)", adsk.core.DropDownStyles.TextListDropDownStyle)
                for ax in ("z", "y", "x"):
                    da.listItems.add(ax, s.get("axis", "z") == ax, "")
                t = inp.addFloatSpinnerCommandInput("tol", "Tolerance mm (0 = auto)", "", 0.0, 10.0, 0.01, float(s["tol"]))
                t.tooltip = "How far the surfaces may stray from the mesh. 0 picks it from the mesh."
                r = inp.addIntegerSpinnerCommandInput("reduce", "Reduce to (0 = off)", 0, 5000000, 10000, int(s["reduce"]))
                r.tooltip = "Simplify big meshes first; flat faces, holes and sharp edges are kept exact."
                inp.addBoolValueInput("hide", "Hide original mesh", True, "", bool(s["hide"]))
            elif self.kind == "scad":
                inp.addStringValueInput("file", "OpenSCAD model", "").isReadOnly = True
                inp.addBoolValueInput("pickfile", "Choose .scad / .csg…", False, "", False)
                dd = inp.addDropDownCommandInput("units", "What one OpenSCAD unit means", adsk.core.DropDownStyles.TextListDropDownStyle)
                for un in ("mm", "inches", "cm", "m"):
                    dd.listItems.add(un, un == s.get("scad_units", "mm"), "")
            else:
                last = s.get("last") if s.get("last") and os.path.isfile(s["last"]) else ""
                inp.addStringValueInput("file", "STEP file", last).isReadOnly = True
                inp.addBoolValueInput("pickfile", "Choose STEP file…", False, "", False)
                inp.addFloatSpinnerCommandInput("threshold", "Threshold mm", "", 0.0001, 10.0, 0.01, float(s["threshold"])
                                                ).tooltip = "Anything thinner or smaller than this is junk."
                dc = inp.addDropDownCommandInput("coarse", "Coarseness", adsk.core.DropDownStyles.TextListDropDownStyle)
                for i, name in enumerate(("1 gentle", "2 normal", "3 aggressive (holes, fillets, chamfers)"), 1):
                    dc.listItems.add(name, i == int(s["coarseness"]), "")
            inp.addStringValueInput("folder", "Twoo STEP CAD Toolbox folder", bridge.find_folder(s["folder"]) or "").isReadOnly = True
            inp.addBoolValueInput("pickfolder", "Choose Twoo STEP CAD Toolbox folder…", False, "", False)
            f, msg = _ready_folder(s["folder"])
            inp.addTextBoxCommandInput("status", "", "" if f else msg, 3, True).isVisible = not f

            h1 = ChangedHandler(self.kind)
            cmd.inputChanged.add(h1)
            h2 = ExecuteHandler(self.kind)
            cmd.execute.add(h2)
            _handlers.extend([h1, h2])
        except Exception:
            ui.messageBox("Twoo STEP CAD Toolbox:\n" + traceback.format_exc())


class ChangedHandler(adsk.core.InputChangedEventHandler):
    def __init__(self, kind):
        super().__init__()
        self.kind = kind

    def notify(self, args):
        try:
            i = args.input
            inp = args.inputs
            if i.id == "pickfile":
                i.value = False
                if self.kind == "convert":
                    p = _pick_file("Mesh file", "Meshes (*.stl;*.obj;*.ply;*.3mf);;All files (*.*)")
                elif self.kind == "scad":
                    p = _pick_file("OpenSCAD model", "OpenSCAD (*.scad;*.csg);;All files (*.*)")
                else:
                    p = _pick_file("STEP file to clean", "STEP (*.step;*.stp);;All files (*.*)")
                if p:
                    inp.itemById("file").value = p
                    if self.kind == "convert":
                        inp.itemById("units").isVisible = True
            elif i.id == "pickfolder":
                i.value = False
                fd = ui.createFolderDialog()
                fd.title = "Choose your Twoo STEP CAD Toolbox folder"
                if fd.showDialog() == adsk.core.DialogResults.DialogOK:
                    f = fd.folder
                    if not bridge.is_toolbox_folder(f) and bridge.is_toolbox_folder(os.path.join(f, "Twoo-STEP-CAD-Toolbox")):
                        f = os.path.join(f, "Twoo-STEP-CAD-Toolbox")
                    s = load_settings()
                    s["folder"] = f
                    save_settings(s)
                    inp.itemById("folder").value = f
                    ok, msg = _ready_folder(f)
                    st = inp.itemById("status")
                    st.text = "" if ok else msg
                    st.isVisible = not ok
        except Exception:
            ui.messageBox("Twoo STEP CAD Toolbox:\n" + traceback.format_exc())


class ExecuteHandler(adsk.core.CommandEventHandler):
    def __init__(self, kind):
        super().__init__()
        self.kind = kind

    def _hardpoint(self, args):
        inp = args.command.commandInputs
        s = load_settings()
        s["hp_tol"] = inp.itemById("hp_tol").value
        s["hp_ang"] = inp.itemById("hp_ang").value
        sel = inp.itemById("bodies")
        bodies = []
        for k in range(sel.selectionCount):
            b = adsk.fusion.BRepBody.cast(sel.selection(k).entity)
            if b:
                bodies.append(b.nativeObject or b)       # component coordinates, like the mesh export
        if not bodies:
            ui.messageBox("Select one or more solid bodies.", "Twoo STEP CAD Toolbox")
            return
        fd = ui.createFileDialog()
        fd.title, fd.filter = "Save hardpoint STL", "STL (*.stl)"
        fd.initialFilename = _safe(bodies[0].name) + ".stl"
        if s.get("hp_dir") and os.path.isdir(s["hp_dir"]):
            fd.initialDirectory = s["hp_dir"]
        if fd.showSave() != adsk.core.DialogResults.DialogOK:
            return
        path = fd.filename
        if not path.lower().endswith(".stl"):
            path += ".stl"
        info = hardpoint_fusion.export(bodies, path, float(s["hp_tol"]), float(s["hp_ang"]))
        s["hp_dir"] = os.path.dirname(path)
        save_settings(s)
        ui.messageBox(f"Saved {path}\n\n" + hardpoint_fusion.summary(info), "Twoo STEP CAD Toolbox")

    def notify(self, args):
        global _active
        try:
            if self.kind == "hardpoint":
                self._hardpoint(args)
                return
            if _active:
                ui.messageBox("Twoo STEP CAD Toolbox is still working on the previous job.", "Twoo STEP CAD Toolbox")
                return
            inp = args.command.commandInputs
            s = load_settings()
            f, msg = _ready_folder(inp.itemById("folder").value or s["folder"])
            if not f:
                ui.messageBox(msg, "Twoo STEP CAD Toolbox")
                return
            s["folder"] = f
            work, out = _dirs(f)
            file = inp.itemById("file").value
            items = []
            if self.kind == "convert":
                s["mode"] = bridge.MODES[inp.itemById("mode").selectedItem.index]
                s["axis"] = inp.itemById("axis").selectedItem.name
                s["units"] = inp.itemById("units").selectedItem.name
                s["tol"] = inp.itemById("tol").value
                s["reduce"] = inp.itemById("reduce").value
                s["hide"] = inp.itemById("hide").value
                sel = inp.itemById("meshes")
                for k in range(sel.selectionCount):
                    body = adsk.fusion.MeshBody.cast(sel.selection(k).entity)
                    if not body:
                        continue
                    native = body.nativeObject or body   # component coordinates
                    src = os.path.join(work, f"{_safe(body.name)}_{k}.stl")
                    export_mesh_body(native, src)
                    dst = _unique(os.path.join(out, _safe(body.name) + ".step"))
                    items.append(dict(label=body.name, body=body, component=native.parentComponent,
                                      args=bridge.convert_args(src, dst, s["mode"], "mm", s["tol"], s["reduce"], s["axis"])))
                if file:
                    name = os.path.splitext(os.path.basename(file))[0]
                    dst = _unique(os.path.join(out, _safe(name) + ".step"))
                    items.append(dict(label=os.path.basename(file), args=bridge.convert_args(
                        file, dst, s["mode"], s["units"], s["tol"], s["reduce"], s["axis"])))
                if not items:
                    ui.messageBox("Select one or more mesh bodies, or choose a mesh file.", "Twoo STEP CAD Toolbox")
                    return
            elif self.kind == "scad":
                s["scad_units"] = inp.itemById("units").selectedItem.name
                if not file:
                    ui.messageBox("Choose a .scad or .csg file.", "Twoo STEP CAD Toolbox")
                    return
                name = os.path.splitext(os.path.basename(file))[0]
                dst = _unique(os.path.join(out, _safe(name) + ".step"))
                items.append(dict(label=os.path.basename(file), args=bridge.scad_args(file, dst, s["scad_units"])))
            else:
                s["threshold"] = inp.itemById("threshold").value
                s["coarseness"] = inp.itemById("coarse").selectedItem.index + 1
                if not file:
                    ui.messageBox("Choose a STEP file to clean.", "Twoo STEP CAD Toolbox")
                    return
                name = os.path.splitext(os.path.basename(file))[0]
                dst = _unique(os.path.join(out, _safe(name) + "_clean.step"))
                items.append(dict(label=os.path.basename(file),
                                  args=bridge.wash_args(file, dst, s["threshold"], s["coarseness"])))
            save_settings(s)
            _active = Batch(f, items, bool(s.get("hide", True)))
            _active.start()
        except Exception:
            ui.messageBox("Twoo STEP CAD Toolbox:\n" + traceback.format_exc())


# ----------------------------------------------------------------------------- add-in lifecycle
def run(context):
    global _event
    try:
        try:
            app.unregisterCustomEvent(EVENT)
        except Exception:
            pass
        _event = app.registerCustomEvent(EVENT)
        h = JobEventHandler()
        _event.add(h)
        _handlers.append(h)
        for cid, (name, tip, res) in CMDS.items():
            cd = ui.commandDefinitions.itemById(cid)
            if cd:
                cd.deleteMe()
            cd = ui.commandDefinitions.addButtonDefinition(cid, name, tip, os.path.join(RES, res))
            ch = CreatedHandler(res)
            cd.commandCreated.add(ch)
            _handlers.append(ch)
            for pid in PANELS + EXTRA_PANELS:
                panel = ui.allToolbarPanels.itemById(pid)
                if panel and not panel.controls.itemById(cid):
                    panel.controls.addCommand(cd)
    except Exception:
        ui.messageBox("Twoo STEP CAD Toolbox failed to start:\n" + traceback.format_exc())


def stop(context):
    global _active
    try:
        if _active:
            _active.cancelled = True
            if _active.job:
                _active.job.cancel()
            _active = None
        for cid in CMDS:
            for pid in PANELS + EXTRA_PANELS:
                panel = ui.allToolbarPanels.itemById(pid)
                c = panel.controls.itemById(cid) if panel else None
                if c:
                    c.deleteMe()
            cd = ui.commandDefinitions.itemById(cid)
            if cd:
                cd.deleteMe()
        if _event:
            _event.remove(_handlers[0])
        app.unregisterCustomEvent(EVENT)
        _handlers.clear()
    except Exception:
        ui.messageBox("Twoo STEP CAD Toolbox stop:\n" + traceback.format_exc())
