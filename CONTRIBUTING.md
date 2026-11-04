# Contributing

**WORK IN PROGRESS: MANY FEATURES MAY BE BROKEN.** Bug reports, test meshes that convert badly, new plug-ins and pull
requests are welcome. This is a spare-time project with one maintainer: replies may take a while ([LTS_POLICY.md](LTS_POLICY.md)).

## The short rules
- Open an issue first for anything big. Use the bug-report form and attach the log (or the `S2S1-…` diagnostic code).
- By contributing you agree your contribution is licensed under the same [PolyForm Noncommercial License 1.0.0](LICENSE.md) as the project. **Every source file starts with** `Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)` and the PolyForm line; a test checks `.py` and `.sh` files, you check the rest.
- Only contribute code you have the right to contribute. No GPL / AGPL code (it cannot be combined with this licence). Do not paste in code from projects with incompatible licences.
- Say **source-available**, never "open source": the licence is noncommercial.
- Keep **Classic**'s behaviour stable and keep it the default. New ideas go in as opt-in, labelled experimental ([LTS_POLICY.md](LTS_POLICY.md) §1.4).
- Docs say what was **not** tested. Do not write "works on Windows" unless somebody ran it on Windows.

## Where to put things
Read [ARCHITECTURE.md](ARCHITECTURE.md) first; it is short. The four rules (enforced by `tests/test_contract.py`):
1. Plug-in run code never imports a front end.
2. The core does not name plug-ins (one listed exception).
3. A plug-in imports another plug-in only if its manifest lists it in `requires`.
4. A plug-in's web code imports only `Response` and `safe_name` from the server.

- **A new tool** → a new folder under `tostep/plugins/`: [docs/ADDING_A_PLUGIN.md](docs/ADDING_A_PLUGIN.md), starting from `examples/plugin_template/hello`. You do not edit the core.
- **The hardpoint file format** → one source, `tostep/core/hstl.py`. The CAD packages get a copy at install time; do not paste it anywhere.
- **`tostep/plugins/image2step/imagetosketch/`** is a copy of the ImageToSketch library: change it in that project and refresh with `python tools/sync_imagetosketch.py <checkout>`; glue goes in the plug-in, not the copy.
- **`tostep/plugins/scad2step/scad2step_core.py`** is the SCAD2STEP engine, kept as one file; glue goes in `bridge.py`.
- **A change to the frozen list** (contract, job kinds, `S2S1-` codes, `--machine`) needs an entry in [docs/DECISIONS.md](docs/DECISIONS.md) and a test.

## Before you open a pull request
```
python -m pytest tests/test_contract.py     # seconds: structure, manifests, headers
python -m pytest tests                      # about five minutes: everything (OpenSCAD tests skip without OpenSCAD)
python tools/golden.py                      # about a minute: the release gate
```
If you cannot run them, say so; pushing runs them in CI (the **Actions** tab). If the golden gate moves, do not edit the
limits to make it green: say why in the pull request, and the maintainer decides ([benchmarks/README.md](benchmarks/README.md)).

## A mesh that converts badly is a gift
Attach it only if you may share it. Say what you expected. If it is a *kind* of part we have no case for, it becomes a golden case
and the bug stays fixed.
