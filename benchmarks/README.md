**WORK IN PROGRESS: MANY FEATURES MAY BE BROKEN** (0.11.1-wip)

# The golden benchmark

`python tools/golden.py` converts nine small parts and fails (exit code 1) if any result got worse than the limits written in
[`golden.json`](golden.json). It is the **release gate** ([LTS_POLICY.md](../LTS_POLICY.md) §1.5): a change that makes a guarded
result worse does not ship. It takes about a minute on a normal PC and stores no large files: the parts are generated with
trimesh each time.

## What it checks, per case
| Limit | Fails when |
|---|---|
| `solid` / `valid` | the output is not a closed, valid solid |
| `faces` [min, max] | the face count leaves the range (too many = lost the merge; too few = merged what should stay apart) |
| `faceted_regions_max` | more regions fell back to flat facets than allowed |
| `max_dev_max` (mm) | the worst point is further from the input mesh than allowed |
| `exact_area_pct_min` | less of the surface is an exact CAD face (plane, cylinder, sphere …) than required |
| `seconds_max` | it got slower than allowed (loose: a slow CI machine must not fail a good change) |

## Why limits and not exact numbers
Floating-point results differ in the last digits between operating systems and library versions. The limits are chosen so a real
regression (an extra face, a bigger deviation, an open solid) fails and a rounding difference does not.

## The cases
Each case in `golden.json` has a `why`: what that part guards. Read the file; it is the list. One is a **known weakness locked in
as it is today**: `cylinder` (Classic leaves a 96-sided cylinder as 98 flat strips, so its face limit is 3 to 98). The limit can
only be tightened, and the two cases after it show Standard and Quick already get 3 faces.

## Adding a case (do this every time a bug is fixed)
1. Run `python tools/golden.py --observe` (or `--cases yourcase`) to see what the current code produces. Add the part's recipe
   to `golden.json` (a trimesh `make` recipe, or a path to a small file in `tests/data`).
2. Write limits a little looser than what you see on the *good* result, and write the `why`: which bug this prevents.
3. Run the gate; commit the case with the fix. The case is the memory of the bug.

## Loosening a limit
Only a person does this, in the same change, with the reason written in `why` and an entry in
[`docs/DECISIONS.md`](../docs/DECISIONS.md) if it is a result getting worse on purpose (for example, trading a little accuracy for a
big speed-up). A limit is **never** loosened just to make CI green.

## What it does not cover
Large organic scans (the ghost-cat class of problem) and anything that needs a real photo. Those are in
[`docs/research`](../docs/research). Nine parts are not a proof; they are a tripwire.

## Where it was recorded
`recorded_with` in `golden.json`: version 0.11.0-wip on Linux x86-64, Python 3.13, with the package versions listed there. Not yet
recorded on macOS or Windows ([LTS_POLICY.md](../LTS_POLICY.md) gates 1 and 5).
