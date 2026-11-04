**WORK IN PROGRESS: MANY FEATURES MAY BE BROKEN** (0.11.1-wip)

# Long-term support: what it means here

"LTS" in this project is a **set of promises about what stops changing**, made by one person who maintains this in spare
time. It is not a service contract, and it begins at **1.0.0**. Until then (0.11.x and later 0.x) the structure is settled
but the promises below are intentions, and the README says so.

## What is promised from 1.0.0 on (the frozen list)
1. **Plug-in contract 1** ([docs/PLUGIN_CONTRACT.md](docs/PLUGIN_CONTRACT.md)): manifest fields, parameter coercion, `run(job)`, hook names. Additive changes only.
2. **Job kinds** (`convert`, `wash`, `scad`, `image`) and the **diagnostic code format** (`S2S1-…`). Saved codes keep decoding.
3. **The command line**: command names, existing options and their meaning, and the `--machine` protocol (`@@PROGRESS`, `@@DONE`, `@@ERROR`) the CAD plug-ins use. Options may be added, not removed or reinterpreted.
4. **Classic stays the default** on the command line and in the CAD plug-ins. Other engines are opt-in and carry their "experimental" label until the golden benchmark and real use say otherwise.
5. **Results do not get worse.** The golden benchmark ([benchmarks/README.md](benchmarks/README.md)) is a gate: a change that makes a guarded result worse does not ship. A limit is loosened only by a person, in writing, with a reason.
6. **Dependencies are pinned** to the exact versions that passed the gate on each operating system (see below), and the start files install those.
7. **The licence terms** stay PolyForm Noncommercial 1.0.0 for everything written by the author.

## What 1.x will take
- Fixes (wrong results, crashes, security problems), compatibility updates for new Python / FreeCAD / Fusion / OS versions, documentation, test and tool improvements.
- New plug-ins and new options, **opt-in**, labelled experimental until they have earned "stable".
- Dependency updates, one at a time, each through the gate.

## What it does not promise
- **Response time.** Single maintainer, best effort. A fix may take weeks. Security reports go private ([SECURITY.md](SECURITY.md)).
- **Platforms nobody has run.** Linux x86-64 is the tested platform today. See the gates below.
- **Experimental things** (Image → STEP, Staged, v2, Quad patches, Extrude/Loft). They may change or be removed in a minor release with a changelog line.
- **Python versions** other than the 3.11–3.14 window the CAD library supports; the start files install their own 3.12, so this only matters to people running it by hand.
- **Warranty or fitness for any purpose.** Check converted files before relying on them.
- **Support after the author stops.** If the project is abandoned, the README will say so plainly, and the frozen state stays online like [the legacy project](LEGACY.md).

## The gates before 1.0.0 (not yet met)
All of these, written down so nobody has to guess when "work in progress" may end:

| # | Gate | State on 2026-10-29 |
|---|---|---|
| 1 | `tools/golden.py` passes on Linux, macOS and Windows in CI (GitHub-hosted runners), Python 3.12 | Linux x86-64 passes locally. CI workflow written, **not yet run on GitHub**. |
| 2 | The full test suite passes on those three | Linux passes locally. |
| 3 | A person starts the toolbox from the real start file on a real Mac and a real Windows PC, converts a part, and sends the log | **Not done.** |
| 4 | The FreeCAD workbench and the Fusion add-in are run once each in the real program, converting a part | **Not done.** (Stand-in tests only.) |
| 5 | Exact dependency versions that passed 1–2 on each OS are recorded and the start files use them | **Not done**, depends on 1. |
| 6 | `docs/KNOWN_LIMITATIONS.md` is true (every "tested on" line matches what was tested) | Written for 0.11.0-wip. |
| 7 | One full release is made through the publish workflow and downloaded as a user would | **Not done.** |

Gate 3 and 4 need a human at a Mac, a Windows PC, FreeCAD and Fusion. Everything else a machine can check.

## Branches and releases
- `main` is where work happens. Every push runs the tests and the gate (when CI has run once and is trusted).
- At 1.0.0 a branch `lts/1.x` is cut. It takes only fixes cherry-picked from `main`. `main` may then move to 2.0 work.
- Releases: the publish workflow makes one from `tostep/version.py` when a version has none; versions with `wip` are pre-releases; only the newest four releases are kept (tags stay).
- A change to the frozen list needs: a line in [docs/DECISIONS.md](docs/DECISIONS.md), a test that shows what changed, and (for the contract) a new contract number.
