**WORK IN PROGRESS: MANY FEATURES MAY BE BROKEN** (0.11.1-wip)

# Decisions

One entry per decision that someone will later ask "why?" about. Newest last. Format: what was decided, why, what it
costs, and what would change our mind. Dates are when it was decided.

## D1 · 2026-10-29 · End STL2STEP as a dead end; start Twoo STEP CAD Toolbox as a new repository
**Decided:** 0.10.3 (0.10.2 plus the graphics-card fix) is frozen as the last version of STL2STEP / EVERYTHING to solid. The new repository is the long-term
line and points to the old one ([LEGACY.md](../LEGACY.md)).
**Why:** the structure had stopped being cheap to change (see LEGACY.md). A clean start with the same algorithms is cheaper than an in-place rewrite that must keep a drop-in system, three copies of two files, and tests that read other files' source.
**Costs:** the old git history stays in the old repository; this one starts from the 0.10.2 files (the only copy available was a release zip). Two names in the world.
**Would change if:** the new structure turns out not to be cheaper to change. The test: adding a plug-in should take an hour ([ADDING_A_PLUGIN.md](ADDING_A_PLUGIN.md)).

## D2 · One repository, a small core, apps as plug-ins; standalone apps sidelined
**Why:** nobody should download 5 GB (three copies of OpenCascade) to use one tool, and one copy of every shared file cannot drift.
**Costs:** one release carries everything; a plug-in cannot be updated alone.
**Would change if:** a plug-in grows its own audience and release rhythm. The contract makes splitting one out mechanical.

## D3 · Manifests, not code, describe a plug-in
**Why:** the web page, the command line and (by test) the CAD buttons can be built from data without importing NumPy and OpenCascade, a broken plug-in can be skipped with a sentence, and a new tool is a folder.
**Costs:** the manifest is a second place to keep in step with the code. `tests/test_contract.py` checks the drift most likely to happen (manifest defaults against the converter's own).
**Format:** TOML, parsed by the standard library `tomllib` (Python 3.11+), so no dependency.

## D4 · The drop-in system was dropped
**Why:** pluggy hooks, a `dropins/` folder, install-from-zip, a bundled loader and a glue file touching the page, the server and the command line were the largest source of "how does this connect" questions. Plug-ins in the repository plus `TOSTEP_PLUGIN_PATH` do the same job with one mechanism.
**Costs:** a person who wants a private tool needs to point an environment variable at a folder instead of clicking *Install*.
**Would change if:** real users ask for install-from-zip. It can be added on top of the registry without changing the contract.

## D5 · Environment variables are `TOSTEP_*`
**Why:** the prefix names the product; the old and new can run side by side. Nothing reads `STL2STEP_*` any more (a test checks).
**Costs:** scripts that set the old names must change ([MIGRATING_FROM_STL2STEP.md](MIGRATING_FROM_STL2STEP.md)).

## D6 · The CAD adapters are hand-written and checked by tests, not generated
**Why:** they run inside programs we cannot start here. A generator would be as large as the two files it replaced, and untestable for the same reason.
**What keeps them honest:** `[[cad]]` entries are promises; tests fail if either adapter lacks the command or if the bridge builds a command line the real parser rejects. They share one bridge and one hardpoint reader, added at install time.
**Costs:** they have **never run in a real FreeCAD or Fusion**. That is LTS gate 4.

## D7 · Classic stays the default; new engines are opt-in
**Why:** Classic is the engine every earlier result was measured with. Defaults that change break people's work.
**The cost, measured:** Classic leaves a plain 96-sided cylinder as 98 flat strips; Standard (Auto) and Quick (Ladder) make it 3 true faces. This is locked in `benchmarks/golden.json` as "known weakness, can only get better" and is the first thing to fix, as a Classic improvement, not a default change.
**Would change if:** the gate shows Standard no worse than Classic on every guarded part *and* real users' parts. Then the default can move at a major version.

## D8 · Version 0.11.0-wip; 1.0.0 is gated on real-machine runs
**Why:** calling something long-term-support that has never started on a Mac or Windows PC would be a claim we cannot back. The gates are in [LTS_POLICY.md](../LTS_POLICY.md).

## D9 · A golden benchmark of small generated parts is the release gate
**Why:** a gate must run in a minute on any machine and not need large files in the repository. Parts are made with trimesh at run time; limits (not exact numbers) are stored with the reason for each.
**Costs:** nine parts do not cover everything. The ghost-cat class of problem (large organic scans) is not in the gate; it is in the research notes. Add a case whenever a bug is fixed: the case is the memory of the bug.

## D10 · Dependencies stay ranges until the gate has run on every OS
**Why:** pinning versions never tried on macOS or Windows would pin a guess. CI will record the versions that pass and the start files will use those (gate 5).

## D11 · `kind`, the diagnostic format and `--machine` are frozen first
**Why:** they are written down outside the program (in codes people keep, in the CAD plug-ins installed on other computers). Everything else can be fixed in the next release; these cannot.

## D12 · Page tabs are plug-ins' markup, but a few page behaviours still know `conv` and `wash`
**Known wart:** `app.js` treats the tabs `conv` and `wash` specially when a job is cancelled or reset, and only `conv` offers *Start later*. It works, it is small, and moving it into the manifests (a `[web] flags` table) is a contract-1 additive change when a third plug-in needs it.

## D13 · The Image → STEP plug-in stays experimental
**Why:** never tried on a real printed token or a real phone photo. It is shipped because it is useful and honest about that, not because it is ready.

## D14 · 2026-10-29 · The name is "Twoo STEP CAD Toolbox"
**Decided:** by the author: a play on *to*, *two* and *too*, representing the all-in-one approach. Display name `Twoo STEP CAD Toolbox`, repository `twoo-step-cad-toolbox`, start files `Twoo STEP CAD Toolbox (Mac).command` / `(Windows).bat` / `twoo-step-cad-toolbox-linux.sh`. (It was briefly "To STEP CAD Toolbox" in drafts.)
**Deliberately not renamed:** the Python package `tostep`, the environment variables `TOSTEP_*`, the CAD add-in id `TOSTEP`, the `tostep` console command and the diagnostic format. They are short, they are in saved diagnostic codes and CAD installs, and renaming them would break exactly what LTS_POLICY.md freezes.
