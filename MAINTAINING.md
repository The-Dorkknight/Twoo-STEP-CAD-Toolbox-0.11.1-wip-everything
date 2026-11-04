**WORK IN PROGRESS: MANY FEATURES MAY BE BROKEN** (0.11.1-wip)

# Looking after this project

For whoever holds the keys next, including the author in a year, after forgetting all of it. Plain words, in the order
you will need them.

## Ten minutes to oriented
1. Read [README.md](README.md) (what it is), then [ARCHITECTURE.md](ARCHITECTURE.md) (where things live, the four rules).
2. Run it: double-click the start file for your system, or `python run_tostep.py plugins`.
3. Run the checks: `python -m pytest tests` (about five minutes; `tests/test_contract.py` alone takes seconds) and `python tools/golden.py` (about a minute).
4. Skim [docs/DECISIONS.md](docs/DECISIONS.md): the "why" of every odd thing.
5. Skim [docs/KNOWN_LIMITATIONS.md](docs/KNOWN_LIMITATIONS.md): what has never been tested.

## What not to touch without a decision (the frozen list)
Plug-in contract 1 · job kinds · the `S2S1-` diagnostic format · the `--machine` protocol · Classic as the default engine ·
the licence header in every source file · the wording "source-available" (never "open source") · the WIP banner while the
version has `wip`. Full list and why: [LTS_POLICY.md](LTS_POLICY.md).

## The checks, and what each guards
| Command | Guards | Time |
|---|---|---|
| `python -m pytest tests/test_contract.py` | manifests, registry, parameter rules, import layers, headers, the page's assembly, the CAD command promise, command-line flags the CAD bridge uses | seconds |
| `python -m pytest tests/test_cad_adapters.py` | the FreeCAD/Fusion files import and register their commands (stand-ins only) | seconds |
| `python -m pytest tests/test_browser.py` | the generated page in headless Chromium: no script errors, a file dropped on a tab runs a job (needs Playwright; skipped without) | ~15 s |
| `python -m pytest tests` | everything above plus the ported 0.10.2 suite: converters, engines, hardpoints, SCAD (OpenSCAD tests skip without it), images, diagnostic code, jetwash, the web server with real workers | ~5 min |
| `python tools/golden.py` | the **release gate**: nine fixed parts must stay inside written limits | ~1 min |
| `python tools/bench.py a.stl b.stl --engines classic ladder` | numbers for any meshes, to judge a change by | varies |

You do not need a terminal to see these: pushing to GitHub runs them (see `.github/workflows/ci.yml`) and the **Actions** tab of
the repository shows green or red. *(Written, not yet run on GitHub — LTS_POLICY gate 1.)*

## Making a release
1. Change the version in `tostep/version.py` (`0.11.1-wip`, …), and in the two CAD manifests if they changed
   (`tostep/frontends/freecad/TOSTEP/package.xml`, `fusion/TOSTEP/TOSTEP.manifest`).
2. Add a section to [CHANGELOG.md](CHANGELOG.md). The release notes are cut from it; the heading must start `## <version>`.
3. Push to `main`. `.github/workflows/publish.yml` makes the release from `tostep/version.py` if that version has none,
   marks it a pre-release while it contains `wip`, and keeps only the newest four (old tags stay).
4. Download the release zip like a user would, unzip it, run the start file. Gate 7 of the LTS policy is exactly this.

With GitHub Desktop only: commit, push, then watch the **Actions** tab on github.com. You never need a terminal for a release.

## Updating a dependency
1. Change `requirements.txt` (one package at a time).
2. Run the whole suite and `tools/golden.py` on at least Linux. If CI exists for macOS/Windows, wait for it.
3. If any guarded number moves, **stop**: decide whether the new number is better, and write the reason in `benchmarks/golden.json` (`why`) and `docs/DECISIONS.md`, or hold the update back.
4. The Python window is 3.11–3.14 because `cadquery-ocp` ships wheels for those. `launch/portable.sh` and the `.bat` install 3.12.

## Refreshing the copied libraries
`tostep/plugins/image2step/imagetosketch/` is the ImageToSketch library: `python tools/sync_imagetosketch.py <checkout>`; never edit it here.
`tostep/plugins/scad2step/scad2step_core.py` is the SCAD2STEP engine: glue goes in `bridge.py`, not in the engine.
QuadriFlow for Linux ships in `tostep/plugins/mesh2step/bin/`; other systems build it with `tools/build_quadriflow.py` (licences in that folder's README).

## Adding a tool
[docs/ADDING_A_PLUGIN.md](docs/ADDING_A_PLUGIN.md). It is an hour of work for a small tool, because the example runs.

## Weak spots, honestly (read before promising anything)
- **Nothing has run on a real Mac or Windows PC**, nor in a real FreeCAD or Fusion. The start files are the 0.10.2 ones with renamed paths. LTS_POLICY gates 3–4.
- **Classic leaves round parts as many flat strips** (a plain 96-sided cylinder: 98 faces; Standard and Quick get 3). Locked in the gate so it can only improve. Improving Classic's cylinder detection is the single most valuable fix; it must not change Classic's other results.
- **Organic parts with many sharp rims** convert slowly and come out partly faceted; there is an open question about the cat model's 1.110 mm worst point ([docs/research/GHOST_CAT.md](docs/research/GHOST_CAT.md)).
- **Quad patches** were tested on five synthetic shapes only.
- **Image → STEP** was tested on pictures drawn by the test code and on simulated photos, never on a real printed token or a real phone photo.
- **Coupling debt** is listed in [ARCHITECTURE.md](ARCHITECTURE.md) with a fix for each; the biggest is the OpenCascade helpers living in `mesh2step/classic.py`.
- **Dependencies are ranges** until gate 5.
- **One maintainer.** No bus factor. That is why the notes are long.

## If you are taking this over
1. Fork or take the repository; keep the licence and every `Required Notice:` line (the PolyForm licence requires it).
2. Run the checks. Everything should be green on Linux. If not, that is your first task.
3. Do not start with features. Start with LTS gates 3 and 4: find a Mac, a Windows PC, FreeCAD and Fusion and try them. Everything else follows from what breaks.
4. Keep the changelog and the research notes: they are the memory of this project, and the defensive publication
   ([docs/research/SOLID_EVIDENCE.md](docs/research/SOLID_EVIDENCE.md)) is what keeps the methods free for everyone.
