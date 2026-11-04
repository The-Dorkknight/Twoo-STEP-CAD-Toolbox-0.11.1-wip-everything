**WORK IN PROGRESS: MANY FEATURES MAY BE BROKEN** (0.11.1-wip)

# The legacy project: STL2STEP → EVERYTHING to solid

Twoo STEP CAD Toolbox is the long-term-support successor of a project that had three names in one repository:
**STL2STEP** (a mesh converter), then **EVERYTHING to solid** (the same converter plus hardpoint STLs, OpenSCAD,
pictures and Jetwash). Its last version is **0.10.3-wip** (0.10.2 plus the graphics-card fix described in [CHANGELOG.md](CHANGELOG.md)). It is frozen, deliberately, as a finished dead end:

> **<https://github.com/The-Dorkknight/stl2step>** — read it, download it, run it. It will not change again except to
> point here.

## Why it was ended instead of carried on

It had grown by adding things next to each other: a converter that was also a web server that was also an installer for
two CAD programs that was also home to a plug-in system, with the same helper files copied into three places and tests
that read the source code of other files. Every new idea cost more than the last. The honest read, written down while the
author still remembered why: the *methods* were good (see [SOLID_EVIDENCE](docs/research/SOLID_EVIDENCE.md)) and the
*shape* had stopped being maintainable. So the methods came here, in a smaller shape, and the shape stayed there.

## What came across, and how we know it is the same
| Thing | State here |
|---|---|
| The converter, Jetwash, SCAD→STEP, hardpoints, Image→STEP | Moved file for file into plug-ins. Imports were rewritten mechanically; the algorithms were not touched. On the test knob the new layout writes a STEP of the same size and face count as 0.10.2 (69 faces, byte-for-byte the same length). |
| The 0.10.2 test suite | Ported. Web tests run against the new generic server with real worker processes. |
| The diagnostic code (`S2S1-…`) | Unchanged, and frozen. Codes people saved from 0.10.x still decode here. |
| Research notes, write-ups, the SOLID defensive publication | Copied verbatim into [`docs/research`](docs/research). |
| The full changelog | [`docs/history/CHANGELOG-stl2step-0.10.2.md`](docs/history/CHANGELOG-stl2step-0.10.2.md), unedited. |
| The feature guides | Split out of the old README into [`docs/guide`](docs/guide). |

## What did not come across
- **The drop-in plug-in system** (pluggy hooks, a `dropins/` folder, install-from-zip). Its job is done by plug-ins in the
  repository plus `TOSTEP_PLUGIN_PATH`. Its design notes and the skill that described it are kept in [`docs/history`](docs/history) and [`docs/research/PLUGIN_SYSTEM.md`](docs/research/PLUGIN_SYSTEM.md).
- **Stand-alone apps** for single tools. The libraries are plug-ins here.
- **The git history of the old repository.** It stays in the old repository. This one starts from the 0.10.2 files
  with their history written up in the changelog and notes, because the only copy this repository was built from was a
  release zip, which carries no commits. Use the legacy repository for `git blame`.
- **Old settings.** Environment variables, command ids and preference names were renamed; see [docs/MIGRATING_FROM_STL2STEP.md](docs/MIGRATING_FROM_STL2STEP.md).

## Using both
Both can sit side by side: they use different folder names, different environment variables (`STL2STEP_*` against
`TOSTEP_*`) and different FreeCAD/Fusion add-in names. Remove the old FreeCAD workbench / Fusion add-in folder before installing the new one if you do not want two sets of buttons doing the same job.

## How to cite
"Twoo STEP CAD Toolbox (successor of STL2STEP / EVERYTHING to solid), Matthew Armstrong, source-available under PolyForm Noncommercial 1.0.0."
For the methods themselves, cite the defensive publication ([docs/research/SOLID_EVIDENCE.md](docs/research/SOLID_EVIDENCE.md)).
