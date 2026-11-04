# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
# Twoo STEP CAD Toolbox workbench for FreeCAD. The converter itself runs in the portable Twoo STEP CAD Toolbox folder.
import os
import FreeCAD
import FreeCADGui


class TOSTEPWorkbench(FreeCADGui.Workbench):
    MenuText = "Twoo STEP CAD Toolbox"
    ToolTip = ("Turn meshes and OpenSCAD models into real solids (planes, cylinders, NURBS), clean junk "
               "out of STEP files, and export STLs that remember their CAD faces")

    def __init__(self):
        import tostep_bridge
        self.__class__.Icon = os.path.join(os.path.dirname(tostep_bridge.__file__),
                                           "Resources", "icons", "tostep.svg")

    def Initialize(self):
        import tostep_fc
        tostep_fc.register()
        cmds = ["TOSTEP_Convert", "TOSTEP_Scad", "TOSTEP_Jetwash", "TOSTEP_Hardpoint", "TOSTEP_Settings"]
        self.appendToolbar("Twoo STEP CAD Toolbox", cmds)
        self.appendMenu("Twoo STEP CAD Toolbox", cmds)

    def Activated(self):
        pass

    def Deactivated(self):
        pass

    def ContextMenu(self, recipient):
        self.appendContextMenu("Twoo STEP CAD Toolbox", ["TOSTEP_Convert", "TOSTEP_Scad", "TOSTEP_Jetwash", "TOSTEP_Hardpoint"])

    def GetClassName(self):
        return "Gui::PythonWorkbench"


FreeCADGui.addWorkbench(TOSTEPWorkbench())
