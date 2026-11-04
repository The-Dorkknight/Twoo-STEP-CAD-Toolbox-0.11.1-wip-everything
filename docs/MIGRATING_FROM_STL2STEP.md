**WORK IN PROGRESS: MANY FEATURES MAY BE BROKEN** (0.11.1-wip)

# Migrating from STL2STEP / EVERYTHING to solid 0.10.x

Nothing about the conversions changed; names did. The two can live side by side.

## Start files and commands
| Was | Is |
|---|---|
| `EVERYTHING to solid (Mac).command` | `Twoo STEP CAD Toolbox (Mac).command` |
| `EVERYTHING to solid (Windows).bat` | `Twoo STEP CAD Toolbox (Windows).bat` |
| `everything-to-solid-linux.sh` | `twoo-step-cad-toolbox-linux.sh` |
| `python app/stl2step.py part.stl` | `python run_tostep.py part.stl` (or `tostep part.stl` if installed with pip) |
| `… wash`, `scad`, `image`, `hardpoints`, `diag`, `install` | the same, unchanged |
| `… dropins …` | removed (see D4 in [DECISIONS.md](DECISIONS.md)); use `TOSTEP_PLUGIN_PATH` |
| `app/requirements.txt`, `app/requirements-optional.txt` | `requirements.txt`, `requirements-optional.txt` at the top |
| `runtime/`, `Output/` | the same names, inside the new folder |

## Environment variables
| Was | Is |
|---|---|
| `STL2STEP_WORK`, `STL2STEP_OUTPUT` | `TOSTEP_WORK`, `TOSTEP_OUTPUT` |
| `STL2STEP_NOPAUSE` | `TOSTEP_NOPAUSE` |
| `STL2STEP_BUDGET`, `STL2STEP_MAX_FILLS` | `TOSTEP_BUDGET`, `TOSTEP_MAX_FILLS` |
| `STL2STEP_BEST_FIRST`, `STL2STEP_PARALLEL_ENGINES`, `STL2STEP_GPU`, `STL2STEP_NICE` | `TOSTEP_BEST_FIRST`, `TOSTEP_PARALLEL_ENGINES`, `TOSTEP_GPU`, `TOSTEP_NICE` |
| `STL2STEP_QUADRIFLOW`, `STL2STEP_QUAD_*` | `TOSTEP_QUADRIFLOW`, `TOSTEP_QUAD_*` |
| `STL2STEP_DROPINS` | removed |
| — | `TOSTEP_PLUGIN_PATH` (new): folders that hold extra plug-ins |

## CAD programs
| Was | Is |
|---|---|
| FreeCAD workbench `STL2STEP` (folder `Mod/STL2STEP`, preferences `Mod/STL2STEP`) | `TOSTEP` (folder `Mod/TOSTEP`). Settings are **not** carried over: it will ask for the toolbox folder once, unless installed from the Install tab, which writes it down. |
| Commands `STL2STEP_Convert/Scad/Jetwash/Hardpoint/Settings` | `TOSTEP_Convert/Scad/Jetwash/Hardpoint/Settings` (matters only if you bound them to keys or toolbars) |
| Fusion add-in `STL2STEP` | `TOSTEP`, with a new add-in id, so Fusion treats it as a different add-in. Stop and remove the old one if you do not want two sets of buttons. |
| `plugins/FreeCAD/STL2STEP`, `plugins/Fusion/STL2STEP` folders for hand copying | `python tools/build_cad_packages.py` writes `dist/cad/FreeCAD/TOSTEP` and `dist/cad/Fusion/TOSTEP` (the two share one bridge and one hardpoint reader, added at build time) |

## Things that carry over exactly
- Diagnostic codes `S2S1-…` from any 0.10.x job still decode (`run_tostep.py diag CODE`).
- Hardpoint STL files: the format ([HARDPOINT_STL.md](../HARDPOINT_STL.md)) is unchanged. New files say `by=tostep` in their header where old ones said `by=stl2step`; readers ignore it.
- STEP outputs: the same engines with the same settings give the same results (checked on the test knob).

## Things that reset
- The browser remembers the first-start notice and the chosen preset under new names, so the notice appears once more and the preset returns to Standard.
- Anything stored in `app/dropins/` is not read.
