# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""Twoo STEP CAD Toolbox commands for FreeCAD.

Convert: selected mesh objects (or mesh files) -> real solids, imported back into the document.
Jetwash: selected shapes (or STEP files) -> cleaned solids.
Import OpenSCAD: a .scad / .csg model -> a solid built from its true geometry (SCAD2STEP engine).
Hardpoint STL: selected solids -> an ordinary STL that also remembers the CAD faces, so it
converts back to a solid without guessing (done here in FreeCAD, no Twoo STEP CAD Toolbox folder needed).
The heavy lifting runs in the portable Twoo STEP CAD Toolbox folder's own Python, in the background,
so FreeCAD stays responsive and a crash there can't take FreeCAD down.
"""
import os
import queue
import re

import FreeCAD
import FreeCADGui
from PySide import QtCore, QtGui

try:
    from PySide import QtWidgets
except ImportError:  # old shim: widgets live in QtGui
    QtWidgets = QtGui

import tostep_bridge as bridge
import hardpoint_fc

HERE = os.path.dirname(os.path.abspath(__file__))
ICONS = os.path.join(HERE, "Resources", "icons")
PARAMS = "User parameter:BaseApp/Preferences/Mod/TOSTEP"
MESH_EXT = (".stl", ".obj", ".ply", ".3mf")
STEP_EXT = (".step", ".stp")


# ----------------------------------------------------------------------------- settings
def prefs():
    return FreeCAD.ParamGet(PARAMS)


def folder(ask=True):
    """The Twoo STEP CAD Toolbox folder, asking the user once if it can't be found."""
    p = prefs()
    f = bridge.find_folder(p.GetString("Folder", ""))
    if f:
        if f != p.GetString("Folder", ""):
            p.SetString("Folder", f)
        return f
    if ask:
        return choose_folder()
    return None


def choose_folder(parent=None):
    start = prefs().GetString("Folder", "") or os.path.expanduser("~")
    f = QtWidgets.QFileDialog.getExistingDirectory(parent or FreeCADGui.getMainWindow(),
                                                   "Choose your Twoo STEP CAD Toolbox folder", start)
    if not f:
        return None
    if not bridge.is_toolbox_folder(f):
        # let people pick the folder that contains it (e.g. the unzipped download)
        inner = os.path.join(f, "Twoo-STEP-CAD-Toolbox")
        if bridge.is_toolbox_folder(inner):
            f = inner
        else:
            _warn("That folder isn't Twoo STEP CAD Toolbox — pick the folder that contains 'tostep' and the "
                  "Twoo STEP CAD Toolbox start files.")
            return None
    prefs().SetString("Folder", f)
    return f


def _exec(dlg):
    return dlg.exec() if hasattr(dlg, "exec") else dlg.exec_()


def _warn(msg, title="Twoo STEP CAD Toolbox"):
    QtWidgets.QMessageBox.warning(FreeCADGui.getMainWindow(), title, msg)


def _ready():
    f = folder()
    if not f:
        return None
    ok, msg = bridge.status(f)
    if not ok:
        _warn(msg)
        return None
    return f


def _safe_name(s):
    return re.sub(r"[^\w\-. ]+", "_", s).strip() or "part"


def _unique(path):
    base, ext = os.path.splitext(path)
    i, p = 2, path
    while os.path.exists(p):
        p = f"{base}_{i}{ext}"
        i += 1
    return p


def _work_dir(f):
    d = os.path.join(f, "runtime", "work", "freecad")
    os.makedirs(d, exist_ok=True)
    return d


def _out_dir(f):
    d = os.path.join(f, "Output")
    os.makedirs(d, exist_ok=True)
    return d


# ----------------------------------------------------------------------------- selection
def _selected(kind):
    """Selected document objects of a kind: 'mesh' (Mesh::Feature) or 'shape' (has a solid shape)."""
    out = []
    for o in FreeCADGui.Selection.getSelection():
        if kind == "mesh" and hasattr(o, "Mesh") and o.Mesh.CountFacets > 0:
            out.append(o)
        elif kind == "shape" and hasattr(o, "Shape") and not o.Shape.isNull() and o.Shape.Faces:
            out.append(o)
    return out


def export_mesh(obj, path):
    """Write a mesh object to STL in global coordinates (placement of parents included)."""
    m = obj.Mesh.copy()
    try:
        m.Placement = obj.getGlobalPlacement()
    except Exception:
        m.Placement = obj.Placement
    pts, facets = m.Topology          # Topology applies the placement
    coords = []
    for v in pts:
        coords += (v.x, v.y, v.z)
    tris = []
    for t in facets:
        tris += t
    return bridge.write_binary_stl(path, coords, tris, 1.0)


# ----------------------------------------------------------------------------- dialogs
class Options(QtWidgets.QDialog):
    def __init__(self, title, what, washing=False, files=False):
        super().__init__(FreeCADGui.getMainWindow())
        self.setWindowTitle(title)
        p = prefs()
        lay = QtWidgets.QFormLayout(self)
        info = QtWidgets.QLabel(what)
        info.setWordWrap(True)
        lay.addRow(info)

        self.units = QtWidgets.QComboBox()
        self.units.addItems(["mm", "inches", "cm", "m"])
        self.units.setCurrentText(p.GetString("Units", "mm"))
        self.units.setToolTip("Units the mesh file was modelled in. Meshes already in FreeCAD are mm.")
        if files and not washing:
            lay.addRow("Mesh file units", self.units)

        if washing:
            self.threshold = QtWidgets.QDoubleSpinBox()
            self.threshold.setDecimals(4)
            self.threshold.setRange(0.0001, 10.0)
            self.threshold.setSingleStep(0.01)
            self.threshold.setSuffix(" mm")
            self.threshold.setValue(p.GetFloat("Threshold", 0.05))
            self.threshold.setToolTip("Anything thinner or smaller than this is treated as junk.")
            lay.addRow("Threshold", self.threshold)
            self.coarse = QtWidgets.QComboBox()
            self.coarse.addItems(["1 — gentle (near-zero junk only)",
                                  "2 — normal (slivers, micro-steps, thin fins, specks)",
                                  "3 — aggressive (also tiny holes, fillets, chamfers)"])
            self.coarse.setCurrentIndex(max(0, min(2, p.GetInt("Coarseness", 2) - 1)))
            lay.addRow("Coarseness", self.coarse)
        else:
            self.mode = QtWidgets.QComboBox()
            self.mode.addItems(bridge.MODE_NAMES)
            m = p.GetString("Mode", "surface")
            self.mode.setCurrentIndex(bridge.MODES.index(m) if m in bridge.MODES else 0)
            lay.addRow("Mode", self.mode)
            self.axis = QtWidgets.QComboBox()
            self.axis.addItems(["z", "y", "x"])
            self.axis.setCurrentText(p.GetString("Axis", "z"))
            self.axis.setToolTip("Extrude / loft: the direction the part is pushed through")
            lay.addRow("Outline axis", self.axis)
            self.tol = QtWidgets.QDoubleSpinBox()
            self.tol.setDecimals(3)
            self.tol.setRange(0.0, 10.0)
            self.tol.setSingleStep(0.01)
            self.tol.setSuffix(" mm")
            self.tol.setSpecialValueText("auto")
            self.tol.setValue(p.GetFloat("Tolerance", 0.0))
            lay.addRow("Fit tolerance", self.tol)
            self.reduce = QtWidgets.QSpinBox()
            self.reduce.setRange(0, 5000000)
            self.reduce.setSingleStep(10000)
            self.reduce.setSpecialValueText("off")
            self.reduce.setSuffix(" triangles")
            self.reduce.setValue(p.GetInt("Reduce", 0))
            self.reduce.setToolTip("Simplify big meshes first. Flat faces, holes and sharp edges are "
                                   "found first and kept exact.")
            lay.addRow("Reduce to", self.reduce)

        self.hide = QtWidgets.QCheckBox("Hide the original")
        self.hide.setChecked(p.GetBool("HideOriginal", True))
        if not files:
            lay.addRow(self.hide)

        row = QtWidgets.QHBoxLayout()
        self.folder = QtWidgets.QLabel(prefs().GetString("Folder", ""))
        self.folder.setWordWrap(True)
        b = QtWidgets.QPushButton("Change…")
        b.clicked.connect(self._pick)
        row.addWidget(self.folder, 1)
        row.addWidget(b)
        lay.addRow("Twoo STEP CAD Toolbox folder", row)

        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addRow(bb)
        self.washing = washing

    def _pick(self):
        f = choose_folder(self)
        if f:
            self.folder.setText(f)

    def save(self):
        p = prefs()
        p.SetBool("HideOriginal", self.hide.isChecked())
        p.SetString("Units", self.units.currentText())
        if self.washing:
            p.SetFloat("Threshold", self.threshold.value())
            p.SetInt("Coarseness", self.coarse.currentIndex() + 1)
        else:
            p.SetString("Mode", bridge.MODES[self.mode.currentIndex()])
            p.SetString("Axis", self.axis.currentText())
            p.SetFloat("Tolerance", self.tol.value())
            p.SetInt("Reduce", self.reduce.value())


# ----------------------------------------------------------------------------- runner
_RUNNING = []   # keeps controllers (and their timers) alive


class Runner(QtCore.QObject):
    """Runs a list of jobs one after another with a progress dialog.

    Each item: dict(args=[...], label=str, source=obj-or-None, name=str).
    Worker-thread callbacks go through a queue that a QTimer drains on the GUI thread."""

    def __init__(self, f, items, suffix, hide):
        super().__init__()
        self.f, self.items, self.suffix, self.hide = f, list(items), suffix, hide
        self.q = queue.Queue()
        self.job = None
        self.done, self.failed = [], []
        self.n = len(self.items)
        self.dlg = QtWidgets.QProgressDialog("Starting…", "Cancel", 0, 100, FreeCADGui.getMainWindow())
        self.dlg.setWindowTitle("Twoo STEP CAD Toolbox")
        self.dlg.setMinimumDuration(0)
        self.dlg.setMinimumWidth(460)
        self.dlg.setAutoClose(False)
        self.dlg.setAutoReset(False)
        self.dlg.canceled.connect(self.cancel)
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.cancelled = False
        _RUNNING.append(self)

    def start(self):
        self.dlg.show()
        self.timer.start(100)
        self.next()

    def next(self):
        if self.cancelled or not self.items:
            return self.finish()
        self.cur = self.items.pop(0)
        i = self.n - len(self.items)
        self.prefix = f"[{i}/{self.n}] {self.cur['label']}\n" if self.n > 1 else f"{self.cur['label']}\n"
        self.dlg.setLabelText(self.prefix + "starting…")
        self.dlg.setValue(0)
        FreeCAD.Console.PrintMessage(f"Twoo STEP CAD Toolbox: {self.cur['label']}\n")
        q = self.q
        self.job = bridge.Job(self.f, self.cur["args"],
                              on_line=lambda s: q.put(("line", s)),
                              on_progress=lambda p: q.put(("progress", p)),
                              on_finish=lambda ok, r: q.put(("finish", (ok, r)))).start()

    def cancel(self):
        self.cancelled = True
        if self.job:
            self.job.cancel()

    def poll(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "line":
                    FreeCAD.Console.PrintLog(f"Twoo STEP CAD Toolbox: {val}\n")
                    if not val.startswith(" "):
                        FreeCAD.Console.PrintMessage(f"  {val}\n")
                    self.dlg.setLabelText(self.prefix + val[:90])
                elif kind == "progress":
                    self.dlg.setValue(int(val))
                elif kind == "finish":
                    self.finished_one(*val)
        except queue.Empty:
            pass

    def finished_one(self, ok, result):
        if ok:
            try:
                self.done.append(self.import_step(result))
            except Exception as e:  # noqa
                self.failed.append(f"{self.cur['label']}: saved {result} but couldn't import it ({e})")
        elif not self.cancelled:
            self.failed.append(f"{self.cur['label']}: {result}")
        self.next()

    def import_step(self, path):
        import Part
        doc = FreeCAD.ActiveDocument or FreeCAD.newDocument()
        shape = Part.Shape()
        shape.read(path)
        if len(shape.Solids) == 1:
            shape = shape.Solids[0]
        obj = doc.addObject("Part::Feature", _safe_name(self.cur["name"] + self.suffix).replace(" ", "_"))
        obj.Label = self.cur["name"] + self.suffix
        obj.Shape = shape
        src = self.cur.get("source")
        if src is not None:
            try:
                if hasattr(obj, "ViewObject") and obj.ViewObject and src.ViewObject:
                    obj.ViewObject.ShapeColor = getattr(src.ViewObject, "ShapeColor", obj.ViewObject.ShapeColor)
                if self.hide and src.ViewObject:
                    src.ViewObject.Visibility = False
            except Exception:
                pass
        doc.recompute()
        FreeCAD.Console.PrintMessage(f"Twoo STEP CAD Toolbox: added '{obj.Label}' (file: {path})\n")
        return obj

    def finish(self):
        self.timer.stop()
        was_cancelled = self.cancelled
        try:
            self.dlg.canceled.disconnect(self.cancel)   # closing the dialog emits canceled
        except Exception:
            pass
        self.dlg.close()
        self.cancelled = was_cancelled
        if self in _RUNNING:
            _RUNNING.remove(self)
        try:
            FreeCADGui.SendMsgToActiveView("ViewFit")
        except Exception:
            pass
        if self.failed:
            _warn("\n\n".join(self.failed) + "\n\nThe Report view has the full log.")
        elif self.cancelled:
            FreeCAD.Console.PrintWarning("Twoo STEP CAD Toolbox: cancelled\n")


# ----------------------------------------------------------------------------- commands
class ConvertCmd:
    def GetResources(self):
        return {"Pixmap": os.path.join(ICONS, "convert.svg"), "MenuText": "Mesh → STEP solid",
                "ToolTip": "Turn the selected mesh(es) — or mesh files if nothing is selected — into "
                           "real solids: planes, cylinders, spheres and NURBS surfaces. A hardpoint "
                           "STL file is rebuilt from the faces it remembers."}

    def IsActive(self):
        return True

    def Activated(self):
        f = _ready()
        if not f:
            return
        meshes = _selected("mesh")
        files = []
        if not meshes:
            files, _ = QtWidgets.QFileDialog.getOpenFileNames(
                FreeCADGui.getMainWindow(), "Meshes to convert", prefs().GetString("LastDir", ""),
                "Meshes (*.stl *.obj *.ply *.3mf);;All files (*)")
            if not files:
                return
            prefs().SetString("LastDir", os.path.dirname(files[0]))
        what = (f"{len(meshes)} selected mesh object(s)" if meshes else
                f"{len(files)} file(s): " + ", ".join(os.path.basename(x) for x in files[:4]) +
                (" …" if len(files) > 4 else ""))
        dlg = Options("Twoo STEP CAD Toolbox — mesh to solid", what, files=bool(files))
        if _exec(dlg) != QtWidgets.QDialog.Accepted:
            return
        dlg.save()
        f = _ready()
        if not f:
            return
        p = prefs()
        mode, tol, red = p.GetString("Mode", "surface"), p.GetFloat("Tolerance", 0.0), p.GetInt("Reduce", 0)
        items, work, out = [], _work_dir(f), _out_dir(f)
        for o in meshes:
            src = os.path.join(work, _safe_name(o.Name) + ".stl")
            try:
                export_mesh(o, src)
            except Exception as e:  # noqa
                _warn(f"Couldn't export {o.Label}: {e}")
                continue
            dst = _unique(os.path.join(out, _safe_name(o.Label) + ".step"))
            items.append(dict(args=bridge.convert_args(src, dst, mode, "mm", tol, red, p.GetString("Axis", "z")),
                              label=o.Label, source=o, name=o.Label))
        for x in files:
            name = os.path.splitext(os.path.basename(x))[0]
            dst = _unique(os.path.join(out, _safe_name(name) + ".step"))
            items.append(dict(args=bridge.convert_args(x, dst, mode, p.GetString("Units", "mm"), tol, red, p.GetString("Axis", "z")),
                              label=os.path.basename(x), source=None, name=name))
        if items:
            Runner(f, items, "_solid", p.GetBool("HideOriginal", True)).start()


class JetwashCmd:
    def GetResources(self):
        return {"Pixmap": os.path.join(ICONS, "jetwash.svg"), "MenuText": "Jetwash (clean STEP)",
                "ToolTip": "Remove slivers, micro-steps, thin fins, specks and tiny holes from the "
                           "selected solid(s) — or STEP files if nothing is selected."}

    def IsActive(self):
        return True

    def Activated(self):
        f = _ready()
        if not f:
            return
        shapes = _selected("shape")
        files = []
        if not shapes:
            files, _ = QtWidgets.QFileDialog.getOpenFileNames(
                FreeCADGui.getMainWindow(), "STEP files to clean", prefs().GetString("LastDir", ""),
                "STEP (*.step *.stp *.STEP *.STP);;All files (*)")
            if not files:
                return
            prefs().SetString("LastDir", os.path.dirname(files[0]))
        what = (f"{len(shapes)} selected object(s)" if shapes else
                f"{len(files)} file(s): " + ", ".join(os.path.basename(x) for x in files[:4]))
        dlg = Options("Twoo STEP CAD Toolbox — Jetwash", what, washing=True, files=bool(files))
        if _exec(dlg) != QtWidgets.QDialog.Accepted:
            return
        dlg.save()
        p = prefs()
        th, co = p.GetFloat("Threshold", 0.05), p.GetInt("Coarseness", 2)
        items, work, out = [], _work_dir(f), _out_dir(f)
        for o in shapes:
            src = os.path.join(work, _safe_name(o.Name) + "_in.step")
            try:
                o.Shape.exportStep(src)
            except Exception as e:  # noqa
                _warn(f"Couldn't export {o.Label}: {e}")
                continue
            dst = _unique(os.path.join(out, _safe_name(o.Label) + "_clean.step"))
            items.append(dict(args=bridge.wash_args(src, dst, th, co), label=o.Label, source=o, name=o.Label))
        for x in files:
            name = os.path.splitext(os.path.basename(x))[0]
            dst = _unique(os.path.join(out, _safe_name(name) + "_clean.step"))
            items.append(dict(args=bridge.wash_args(x, dst, th, co), label=os.path.basename(x),
                              source=None, name=name))
        if items:
            Runner(f, items, "_clean", p.GetBool("HideOriginal", True)).start()


class ScadCmd:
    def GetResources(self):
        return {"Pixmap": os.path.join(ICONS, "scad.svg"), "MenuText": "OpenSCAD → STEP solid",
                "ToolTip": "Pick an OpenSCAD model (.scad or .csg). It is rebuilt as real CAD geometry — "
                           "true cylinders, spheres and swept surfaces, exact booleans — and added to the "
                           "document as a solid. Needs OpenSCAD installed (not for .csg files)."}

    def IsActive(self):
        return True

    def Activated(self):
        f = _ready()
        if not f:
            return
        p = prefs()
        files, _ = QtWidgets.QFileDialog.getOpenFileNames(
            FreeCADGui.getMainWindow(), "OpenSCAD models", p.GetString("LastDir", ""),
            "OpenSCAD (*.scad *.csg);;All files (*)")
        if not files:
            return
        p.SetString("LastDir", os.path.dirname(files[0]))
        items, out = [], _out_dir(f)
        for x in files:
            name = os.path.splitext(os.path.basename(x))[0]
            dst = _unique(os.path.join(out, _safe_name(name) + ".step"))
            items.append(dict(args=bridge.scad_args(x, dst, p.GetString("ScadUnits", "mm")),
                              label=os.path.basename(x), source=None, name=name))
        Runner(f, items, "", False).start()


class HardpointDialog(QtWidgets.QDialog):
    def __init__(self, what):
        super().__init__(FreeCADGui.getMainWindow())
        self.setWindowTitle("Twoo STEP CAD Toolbox — export hardpoint STL")
        p = prefs()
        lay = QtWidgets.QFormLayout(self)
        info = QtWidgets.QLabel(
            what + "\n\nThe file is an ordinary STL — slicers see the same triangles as a normal "
            "export. Its spare bytes also record which triangles belong to which CAD face, what kind "
            "of surface each face is, and where the corners are, so Twoo STEP CAD Toolbox can turn it back into "
            "a solid without guessing.")
        info.setWordWrap(True)
        lay.addRow(info)
        self.dev = QtWidgets.QDoubleSpinBox()
        self.dev.setDecimals(3)
        self.dev.setRange(0.001, 5.0)
        self.dev.setSingleStep(0.01)
        self.dev.setSuffix(" mm")
        self.dev.setValue(p.GetFloat("HpDeviation", 0.05))
        self.dev.setToolTip("How far the triangles may be from the true surface. Smaller = smoother "
                            "curves and a bigger file.")
        lay.addRow("Surface deviation", self.dev)
        self.ang = QtWidgets.QDoubleSpinBox()
        self.ang.setDecimals(0)
        self.ang.setRange(1.0, 45.0)
        self.ang.setSuffix(" °")
        self.ang.setValue(p.GetFloat("HpAngle", 15.0))
        self.ang.setToolTip("Largest angle between neighbouring triangles on a curve.")
        lay.addRow("Angle", self.ang)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addRow(bb)

    def save(self):
        p = prefs()
        p.SetFloat("HpDeviation", self.dev.value())
        p.SetFloat("HpAngle", self.ang.value())


class HardpointCmd:
    def GetResources(self):
        return {"Pixmap": os.path.join(ICONS, "hardpoint.svg"), "MenuText": "Export hardpoint STL",
                "ToolTip": "Save the selected solid(s) as an STL that slicers read as usual, and that "
                           "also remembers the CAD faces and corners so it converts back to a solid "
                           "without guessing."}

    def IsActive(self):
        return True

    def Activated(self):
        objs = _selected("shape")
        if not objs:
            _warn("Select one or more solid objects first (in the model tree or the 3D view).")
            return
        what = (f"'{objs[0].Label}'" if len(objs) == 1 else f"{len(objs)} objects, saved together in one file")
        dlg = HardpointDialog(what)
        if _exec(dlg) != QtWidgets.QDialog.Accepted:
            return
        dlg.save()
        p = prefs()
        start = os.path.join(p.GetString("LastDir", ""), _safe_name(objs[0].Label) + ".stl")
        path, _ = QtWidgets.QFileDialog.getSaveFileName(FreeCADGui.getMainWindow(), "Save hardpoint STL",
                                                        start, "STL (*.stl)")
        if not path:
            return
        if not path.lower().endswith(".stl"):
            path += ".stl"
        p.SetString("LastDir", os.path.dirname(path))
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            info = hardpoint_fc.export([hardpoint_fc.global_shape(o) for o in objs], path,
                                       p.GetFloat("HpDeviation", 0.05), p.GetFloat("HpAngle", 15.0))
        except Exception as e:  # noqa
            QtWidgets.QApplication.restoreOverrideCursor()
            _warn(f"Couldn't export: {e}")
            return
        QtWidgets.QApplication.restoreOverrideCursor()
        text = hardpoint_fc.summary(info)
        FreeCAD.Console.PrintMessage(f"Twoo STEP CAD Toolbox: saved {path}\n  " + text.replace("\n", "\n  ") + "\n")
        QtWidgets.QMessageBox.information(FreeCADGui.getMainWindow(), "Twoo STEP CAD Toolbox", f"Saved {path}\n\n{text}")


class SettingsCmd:
    def GetResources(self):
        return {"Pixmap": os.path.join(ICONS, "settings.svg"), "MenuText": "Twoo STEP CAD Toolbox folder…",
                "ToolTip": "Choose where the portable Twoo STEP CAD Toolbox app is"}

    def IsActive(self):
        return True

    def Activated(self):
        f = choose_folder()
        if f:
            ok, msg = bridge.status(f)
            QtWidgets.QMessageBox.information(FreeCADGui.getMainWindow(), "Twoo STEP CAD Toolbox",
                                              f"Using {f}\n\n" + ("Ready." if ok else msg))


_registered = False


def register():
    global _registered
    if _registered:
        return
    FreeCADGui.addCommand("TOSTEP_Convert", ConvertCmd())
    FreeCADGui.addCommand("TOSTEP_Jetwash", JetwashCmd())
    FreeCADGui.addCommand("TOSTEP_Scad", ScadCmd())
    FreeCADGui.addCommand("TOSTEP_Hardpoint", HardpointCmd())
    FreeCADGui.addCommand("TOSTEP_Settings", SettingsCmd())
    _registered = True
