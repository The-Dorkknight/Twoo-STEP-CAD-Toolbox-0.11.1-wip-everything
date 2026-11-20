# scripts: edge-keeping smoothing and the scan call (0.11.4)

Run from the repository root with `TOSTEP_ROOT=. python3 docs/research/scripts/scan_edges/<script>`.

| Script | What it does |
|---|---|
| `mkclean.py` | makes clean dense meshes that must **not** be called scans: a dense box, a dense cylinder, a marching-cubes rounded box and ghost |
| `mkorg.py` | makes the organic stand-in scan (bent axis, oval moulded grip, finger dips, flat window) with its noise-free `truth.stl` |
| `jitter.py` | the scan test's measure: the typical angle between neighbouring triangles, for each file given |
| `flagtest.py` | what the scan call says about each file, and how far the smoothing would move a clean one |
| `bnf.py`, `evalden.py` | the first bilateral-normal-filter experiment against the noise-free shape (the table in SCAN_EDGES.md) |
| `layout_probe.py`, `nstats.py` | run the converter up to the patch layout and say why it split (disk / cone / area), and the normal statistics of the mesh it laid out |
| `shares.py` | area share of a result's faces by kind (facets, planes, round, turned, freeform) |

The stand-in files are made by the scripts; none is stored in the repository.
