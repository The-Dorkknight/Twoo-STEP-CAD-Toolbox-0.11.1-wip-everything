**WORK IN PROGRESS: MANY FEATURES MAY BE BROKEN** (0.11.1-wip)

# Adding a tool (a plug-in)

The example in [`examples/plugin_template/hello`](../examples/plugin_template/hello) is a real plug-in that counts the
lines of a text file. It is loaded by the tests (`tests/test_browser.py` drops a file on its tab in a headless browser), so
this page cannot drift from what works.

## Try it now (no install, nothing copied)
```
TOSTEP_PLUGIN_PATH=examples/plugin_template python run_tostep.py plugins                  # it is listed
TOSTEP_PLUGIN_PATH=examples/plugin_template python run_tostep.py hello notes.txt out.txt --skip-blank
TOSTEP_PLUGIN_PATH=examples/plugin_template python run_tostep.py ui                       # a "Hello" tab appears
```
(On Windows use `set TOSTEP_PLUGIN_PATH=examples\plugin_template` first.)

## Make your own in five steps
1. **Copy the folder** `examples/plugin_template/hello` to a new place (for a private tool, anywhere you like and point
   `TOSTEP_PLUGIN_PATH` at its parent; for one that belongs in the toolbox, `tostep/plugins/`). Rename it. The folder name is the package name.
2. **Edit `manifest.toml`**: `id` (= folder name), `name`, `summary`, `kind` and `entry` (`<folder>:run`), the files it
   accepts and writes (`[io]`), and one `[[params]]` block per setting. Every parameter needs a `help` sentence.
   The field list is in [PLUGIN_CONTRACT.md](PLUGIN_CONTRACT.md).
3. **Write `run(job)`** in `__init__.py`: read `job.src`, write `job.out`, call `job.log()` / `job.progress()` now and then,
   return anything with a `.summary()`. Raise `UserError("a sentence")` for problems the person can fix.
4. **Optional: a command line.** Without `cli.py` the command is `tostep <command> input [output]`. A `cli.py` with
   `add_arguments` and `params_from_args` adds options.
5. **Optional: a tab.** `ui/tab.html` (copy the block of ids from the example; change only the tab name) and `ui/tab.js`
   (`hookDrop('<tab>', '.ext'); function runX(){ start('<tab>', '<kind>', {…}) }`).

Run `python run_tostep.py plugins`: a manifest mistake is reported there in a sentence naming the plug-in, the field and the reason.

## Before it goes into `tostep/plugins/`
- `status = "experimental"` until it has tests and has run on a part the author did not make. Say in the manifest `summary` what is untested.
- Add tests next to the others (`tests/test_<id>.py`); `tests/test_contract.py` already checks its manifest, its kind, its command and tab for clashes, its imports, and its source headers.
- Every source file starts with the two licence lines (`Required Notice: Copyright …` and the PolyForm line); a test fails without them.
- If it changes results the golden benchmark covers, run `python tools/golden.py`. If it adds a result worth guarding, add a case ([benchmarks/README.md](../benchmarks/README.md)).
- Write what it does *not* do in `docs/guide/<id>.md`. Honesty is the only documentation standard here.
- If it needs a CAD button, add the `[[cad]]` block **and** the command in both adapters (`tostep/frontends/freecad/TOSTEP/tostep_fc.py`, `fusion/TOSTEP/TOSTEP.py`); the test names what is missing.

## Things that will trip you
- `kind` is frozen once released (it is inside saved diagnostic codes). Pick it carefully.
- A job runs in a **separate process**. Do not rely on module-level state surviving between jobs.
- Output that is not a STEP needs `preview = false` in `[io]`, or the page will try to draw it.
- Parameters arrive **typed and filled in**; do not re-parse strings, and do not assume a key you did not declare exists.
- Do not import the web server (except `Response` and `safe_name`, from your own `web.py`) and do not import another plug-in unless it is in `requires`.
