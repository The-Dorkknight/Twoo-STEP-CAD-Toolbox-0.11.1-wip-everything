**WORK IN PROGRESS: MANY FEATURES MAY BE BROKEN** (0.11.1-wip)

# Plug-in contract, version 1

A plug-in is a **folder** that holds a `manifest.toml` and a Python **package** with a `run(job)` function. The core reads
the manifest without importing the plug-in's code, so listing plug-ins, building the command line, the web page and
the CAD buttons never costs the seconds it takes to load NumPy and OpenCascade.

Contract 1 is **frozen**: within 1.x a change may only *add* an optional field or an optional hook. Anything that would
make a working plug-in stop working is contract 2, and the core will speak both for as long as there is a plug-in on 1.
`tests/test_contract.py` is the executable version of this page.

A worked example that is run by the tests: [`examples/plugin_template/hello`](../examples/plugin_template/hello).
Step-by-step: [ADDING_A_PLUGIN.md](ADDING_A_PLUGIN.md).

## Where plug-ins come from
1. The folders under `tostep/plugins/` that hold a `manifest.toml` (built in).
2. Folders inside any directory named in `TOSTEP_PLUGIN_PATH` (separated like `PATH`). A plug-in there cannot replace a
   built-in one: a repeated id, job kind, command or tab is refused and the first one wins.

A plug-in that is broken, or needs one that is missing, is **skipped and reported** (`tostep plugins`, the Install
tab); it never stops the others. A plug-in from `TOSTEP_PLUGIN_PATH` is Python code that runs with your rights, like
any program you install; there is no sandbox ([SECURITY.md](../SECURITY.md)).

## manifest.toml

```toml
contract = 1                      # required. This core speaks 1.
id = "jetwash"                    # required. [a-z][a-z0-9_]{1,31}. Equals the folder name.
name = "Jetwash"                  # required. Shown to people.
version = "0.10.2"                # required. x.y.z (a -wip suffix is fine).
status = "stable"                 # required. "stable" | "experimental"
summary = "…one sentence…"        # required.
kind = "wash"                     # optional. Makes this a job-running plug-in. [a-z][a-z0-9_]{0,15}. FROZEN once released.
entry = "tostep.plugins.jetwash:run"   # required when there is a kind. "dotted.module:function".
requires = ["mesh2step"]          # optional. Plug-in ids that must be usable.
```

**`kind` is frozen** because it is written inside diagnostic codes that people keep. Never rename one; add a new one.

### `[io]` — what a job takes and gives
```toml
[io]
inputs = [".step", ".stp"]        # extensions the web page accepts (lower-case, with the dot). Empty = anything.
output = "{stem}_clean.step"      # output file name; must contain {stem} (the input's name without extension)
reject = "Jetwash needs a STEP"   # the sentence shown when the wrong kind of file is dropped
mesh_preview = true               # the 3D preview may show the input beside the result (false: no "Original" button)
preview = true                    # the result is a STEP the preview can show (false: no preview, no diagnostic panel)
chain = true                      # the result is offered as the input of the next tool ("last converted STEP")
raw_params = false                # true: the plug-in checks its own parameters; the core passes them through untouched
```

### `[cli]` — a command line command
```toml
[cli]
command = "wash"                  # tostep wash …   (unique)
hooks = "tostep.plugins.jetwash.cli"   # optional module; see Command-line hooks below
```

### `[web]` — a tab on the page
```toml
[web]
tab = "wash"                      # [a-z][a-z0-9]{0,11}, unique. Becomes the ids p-wash, t-wash.
title = "Jetwash"                 # the tab's button
order = 50                        # left to right; ties break by id
fragment = "ui/tab.html"          # required with [web]. The tab's markup, relative to the plug-in folder.
script = "ui/tab.js"              # optional
style = "ui/tab.css"              # optional
routes = "tostep.plugins.jetwash.web"  # optional module; see Web hooks below
```

### `[[cad]]` — a button in FreeCAD and Fusion
```toml
[[cad]]
id = "jetwash"                    # the command is TOSTEP_Jetwash
label = "Jetwash STEP"
tooltip = "Clean slivers … out of a STEP file."
input = "shape"                   # mesh | step | scad | shape | picture | none
frontends = ["freecad", "fusion"] # optional; default both
```
The CAD adapters are hand-written ([ARCHITECTURE.md](../ARCHITECTURE.md)). A `[[cad]]` entry is a **promise** that both
adapters have the command; `tests/test_contract.py` fails if one is missing.

### `[[params]]` — the settings
```toml
[[params]]
name = "threshold"
type = "float"                    # float | int | bool | str | choice | path
default = 0.05                    # required (choice: one of choices; nullable: may be omitted by sending a blank)
help = "mm: anything thinner … counts as junk."   # required. It is the documentation.
choices = ["1","2","3"]           # choice only
min = 0.0001                      # hints for forms; the core does not clamp
max = 10.0
group = "…"                       # form grouping hint
advanced = false                  # hide behind "Advanced"
nullable = false                  # a blank value means None, not the default
```

**Coercion** (`tostep/core/params.py`) is the same for every plug-in, every front end:

| You send | You get |
|---|---|
| nothing, or a blank | the default (`None` if nullable) |
| a number that is not one | an error naming the parameter |
| `nan`, `inf` | an error |
| a choice that is not listed | the default |
| a bool as text (`"1"`, `"true"`, `"yes"`, `"on"`) | `True` |
| keys the manifest does not declare | passed through untouched |

## The code side: `run(job)`
```python
def run(job):                 # called in a worker process (web) or in-process (command line)
    job.log("starting")       # a line of the log
    job.progress(0.5)         # 0 … 1
    # read job.src, write job.out, use job.params (typed, as declared) and job.scale (mm per input unit)
    return report             # anything with .summary() -> str; .ok is honoured when present
```
`Job` fields: `plugin`, `src`, `out`, `params`, `kind`, `scale`, `log`, `progress`. Raise `UserError("a sentence")`
(`tostep.core.job`) for a problem the person can fix; it is shown without a traceback. Any other exception is shown with its
message and logged with the traceback.

A report may also carry the numbers the diagnostic code records (`tostep.core.diagcode.facts`); a plug-in that does not
simply has no diagnostic panel content.

### Command-line hooks (all optional)
`CLI_HELP`; `add_arguments(parser)`; `defaults_for(argv) -> dict | None` (e.g. a `--preset`); `params_from_args(args, output) -> dict`;
`check(report)`; `after_run(args, job, report, lines, machine)`; or `main(argv)` to take over the whole command line.
`--machine` and `--pause` are added for you. `--machine` prints `@@PROGRESS n`, `@@DONE path`, `@@ERROR message` — **a frozen
interface**: the CAD plug-ins parse it.

### Web hooks (all optional; `routes` module)
`register(router)` adds routes (`router.get/post(pattern, fn)`, `fn(app, req)` returning a dict, `(dict, status)` or a `Response`);
`prepare_job(app, ctx, params) -> params` adjusts parameters before the job starts;
`page_vars() -> {token: value}` fills `__TOKENS__` in your fragment/script; `open_path(app, path)`; `shutdown()`.
Import only `Response` and `safe_name` from `tostep.frontends.web.server` (a test checks you use no private names).

The tab's markup follows one convention the page's `start()` and `watch()` rely on: for a tab `T` the ids `d-T` (drop box),
`f-T` (file name), `b-T` (go button), `x-T` (cancel), `al-T`, `bar-T`, `log-T`, `res-T`. Copy the block from the example.

## The worker's protocol (`tostep.core.worker`)
One job per process: `python -E -s -u -m tostep.core.worker spec.json`, `spec.json = {"plugin", "src", "out", "params"}`,
working directory = the toolbox folder. It prints one JSON object per line, prefixed `@@`:
`{"p": 0.4}` progress · `{"l": "text"}` log · `{"s": "summary"}` result · `{"e": "message"}` error · `{"d": {…}}` result numbers.
Cancelling is killing the process.

## What a plug-in author can rely on
- The manifest fields above, and their meaning, for as long as the core accepts `contract = 1`.
- Typed parameters exactly as declared (unless `raw_params`).
- `job.out`'s folder exists; the file is yours to write. A job that returns without writing `job.out` is an error.
- Your package is imported as `<package>`: built-in ones as `tostep.plugins.<id>`, others by their folder name (the folder's
  parent is put on the module path).

## What it cannot rely on
- Running on the main thread, or in the same process as other jobs.
- The `tostep.core` modules other than `job`, `params`, `cliutil`, `diagcode.facts`, `perf` — they are the core's own and may change in a minor version.
- Anything in another plug-in unless it is in `requires`.

## Changing the contract
1. Additive (new optional manifest field or hook): minor version of the core, documented here, covered by a test.
2. Breaking: `contract = 2`, the core reads both, the old one is supported until every shipped plug-in has moved,
   [LTS_POLICY.md](../LTS_POLICY.md) says how long.
