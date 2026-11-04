**WORK IN PROGRESS: MANY FEATURES MAY BE BROKEN** (0.11.1-wip)

# Architecture

A small core, five plug-ins, and front ends that are built from the plug-ins' manifests. This page says where
everything lives, which way dependencies are allowed to point, and, honestly, where they still point the wrong way.

```
run_tostep.py  /  python -m tostep  /  tostep (console script)
        │
        ▼
┌────────────────────────────── tostep/frontends ──────────────────────────────┐
│ cli.py            web/server.py + shell + per-plugin fragments     install.py │
│ (commands from    (tabs, forms, preview, queue, diagnostic)    (CAD plug-ins, │
│  manifests)                                                    menu entries)  │
│                    freecad/TOSTEP   fusion/TOSTEP   cad_shared/bridge.py      │
│                    (hand-written adapters over the command line)              │
└───────────────────────────────────────┬──────────────────────────────────────┘
                                        │ asks the registry, starts jobs
                                        ▼
┌──────────────────────────────── tostep/core ─────────────────────────────────┐
│ registry.py   manifest.py   params.py   job.py   worker.py   cliutil.py      │
│ perf.py (profiles, GPU pack)  diagcode.py  preview3d.py  hstl.py  accel.py   │
└───────────────────────────────────────┬──────────────────────────────────────┘
                                        │ imports a plug-in only by manifest entry
                                        ▼
┌─────────────────────────────── tostep/plugins ───────────────────────────────┐
│ mesh2step    jetwash    hardpoint    scad2step    image2step                  │
│ each: manifest.toml + package with run(job)  [+ cli.py, web.py, ui/]         │
└──────────────────────────────────────────────────────────────────────────────┘
```

## The rules

1. **A plug-in's run code never imports front ends.** It gets a `Job` and returns a report. Its `web.py` (the page
   adapter: routes, job preparation) may use the web server's public helpers (`Response`, `safe_name`) and nothing else
   from there. Enforced by `tests/test_contract.py` (`Layers`).
2. **The core never names a plug-in.** It asks the registry (*"which plug-in runs kind `wash`?"*). The one exception is
   listed under *Known couplings*.
3. **A plug-in may import another one only if its manifest says `requires = [...]`.** The registry refuses circles and
   missing requirements, and marks that plug-in unusable instead of failing everything. Today `jetwash` and `hardpoint`
   require `mesh2step`; the test fails if an undeclared import appears.
4. **A job runs in its own process** (`python -E -s -u -m tostep.core.worker spec.json`, cwd = the toolbox folder).
   Cancel kills the process. Crashes, out-of-memory and runaway fitting cannot take the web server down.
5. **Front ends talk to jobs through two text protocols only**: the worker's `@@{json}` lines (progress, log, summary,
   error, result numbers) and the command line's `--machine` lines (`@@PROGRESS n`, `@@DONE path`, `@@ERROR message`).
   The second one is what the FreeCAD and Fusion plug-ins use. Both are frozen by contract 1.
6. **The diagnostic code format is frozen.** `S2S1-…` codes people saved with 0.10.x must still decode. Job kinds
   (`convert`, `wash`, `scad`, `image`) are inside those codes, so they are frozen too (a test checks them).
7. **Classic stays the default** on the command line and in the CAD plug-ins. New engines are opt-in and labelled
   experimental until they have earned otherwise on the golden benchmark.

## Where things live

| Path | What |
|---|---|
| `run_tostep.py` | What the start files run. A script's own folder is first on the module path, which is how `tostep` is found without installing anything. |
| `tostep/version.py` | The version and product name. One place. |
| `tostep/core/registry.py` | Finds plug-ins (built in, plus folders in `TOSTEP_PLUGIN_PATH`), checks them, keeps their errors. |
| `tostep/core/manifest.py`, `params.py` | Reads `manifest.toml`; turns what a form or command line sends into typed values. |
| `tostep/core/job.py`, `worker.py` | The `Job` object, `UserError`, the `@@` protocol, the one-job process. |
| `tostep/core/perf.py`, `accel.py`, `gpuinfo.py` | Performance profiles, thread counts, priority; the optional GPU pack: which cards exist and what each needs (`gpuinfo`, no PyTorch import), the install steps and states (`perf`), the card maths and its real-operation test (`accel`). |
| `tostep/core/diagcode.py` | The diagnostic code (encode, decode, QR text). |
| `tostep/core/hstl.py` | The hardpoint-STL reader/writer. **One copy**; the installer adds it to each CAD plug-in. |
| `tostep/plugins/<id>/` | One tool. `manifest.toml`, a package with `run(job)`, optional `cli.py`, `web.py`, `ui/`. |
| `tostep/frontends/web/` | The generic server (routes, jobs, queue, upload, download, preview, diagnostic) and the page shell. |
| `tostep/frontends/cli.py` | Command dispatch: a command comes from a manifest; a bare file name means *convert*. |
| `tostep/frontends/install.py` | FreeCAD / Fusion installers, Linux menu entry, right-click entries, the browser opener. |
| `tostep/frontends/cad_shared/bridge.py` | The one bridge both CAD plug-ins use to run the toolbox as a subprocess. |
| `tostep/frontends/freecad/TOSTEP`, `fusion/TOSTEP` | The two CAD adapters (hand-written; see below). |
| `launch/portable.sh`, the three start files | Private Python 3.12 via uv, libraries, then `run_tostep.py`. |
| `tests/` | The 0.10.2 suite, ported, plus `test_contract.py` and `test_cad_adapters.py`. |
| `tools/`, `benchmarks/` | Release gate (`golden.py`), `bench.py`, packaging, tracer sync. |

## How a job flows (web)

1. The page POSTs `/api/run {kind, file, params}`. `App.start_job` finds the plug-in for `kind` in the registry and
   lets the plug-in's `web.prepare_job` adjust parameters, if it has one.
2. A free slot is acquired (the performance profile says how many jobs may run at once); the job's spec is written
   to a JSON file and a worker process is started.
3. The worker coerces the parameters with the manifest, builds the `Job`, calls the plug-in's `run(job)` and prints
   `@@` lines. The server turns them into the job's progress, log, summary and diagnostic data.
4. The page polls `/api/job/<id>`; downloads, the 3D preview and the diagnostic panel read the finished job.

The command line does steps 1–3 in one process (no worker) and prints the same log.

## CAD adapters are hand-written, on purpose

The web page and the command line are *generated* from manifests. The FreeCAD workbench and Fusion add-in are not:
they run inside programs we cannot start in this repository's tests, and a generator would have to be as large as the
two adapters it replaced. Instead they are kept honest by tests: every `[[cad]]` command in a manifest must exist in both
adapters under the id `TOSTEP_<Id>`, and every command line the bridge builds must be accepted by the real parser
(`tests/test_contract.py`, `tests/test_cad_adapters.py`). **Neither adapter has been run in a real FreeCAD or Fusion.**

## Known couplings (the wrong-way arrows)

These are real and deliberate to leave alone in 0.11; each has a fix, listed so nobody has to rediscover it.

| Coupling | Why it is there | The fix, when there is a golden-gate green light |
|---|---|---|
| `core/preview3d.py`, `jetwash/jetwash.py` import OpenCascade helpers from `plugins/mesh2step/classic.py` (`read_step`, `_cast`, `_explore`, `_S`) | The helpers were written inside the converter in 0.1 and everything grew on them | Move them to `tostep/core/occ.py`; `classic.py` imports them back. A move, no behaviour change. |
| `web/server.py` knows the *hardpoint STL beside the result* feature (`labelled_saved`, `what=labels`) | It was a mesh2step option that grew a download button | Let a plug-in report extra output files through its result; the server lists them. |
| Environment variables carry the time ceiling to the converter (`TOSTEP_BUDGET`, `TOSTEP_MAX_FILLS`) | `perf.py` decides the ceiling, `engines.convert` reads it | Pass them in `Job.params` instead. |
| `image2step/imagetosketch/` is a **copy** of the ImageToSketch library | It is also a product of its own | `tools/sync_imagetosketch.py` refreshes it; never edit it here. |
| `scad2step/scad2step_core.py` is a copy of the SCAD2STEP engine | Same | Edit glue in `scad2step/bridge.py`, not the engine. |
| Python 3.11–3.14 only | The CAD library (`cadquery-ocp`) ships wheels for those | The start files install 3.12 themselves; `cli.py` says so if run with another. |
| Dependencies are ranges, not pins | They worked; the exact versions were never recorded on Mac/Windows | CI records the tested set per OS; pin once those runs exist (see `LTS_POLICY.md`). |
| `web/app.js` treats the tabs `conv` and `wash` specially (on cancel/reset; only `conv` offers *Start later*) | The page grew from a one-tool page | A `[web] flags` table in the manifest: an additive contract-1 change when a third plug-in needs it (decision D12). |

## What was removed from 0.10.2

The drop-in system (pluggy hooks, `dropins/` folder, install-from-zip, `dropin_glue`, `plugkit`, `hooks`) is gone: its
job is done by plug-ins in the repository plus `TOSTEP_PLUGIN_PATH`, and it was the largest single source of "how does
this connect" questions. The standalone ImageToSketch and SCAD2STEP *apps* are not part of this repository; their
libraries are, as plug-ins. See [docs/DECISIONS.md](docs/DECISIONS.md).
