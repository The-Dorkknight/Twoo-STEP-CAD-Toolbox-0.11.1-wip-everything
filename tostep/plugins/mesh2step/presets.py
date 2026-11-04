# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""
presets — the quality presets of the STL → STEP tab, in one table for the page and the command line.

"pic" picks the little drawing on the button: flat faces are exact in every preset; organic areas are
"loose" (up to 0.5 mm off), "near" (up to 0.3 mm off), "fit" (within tolerance, exact facets where a
patch cannot fit) or "best" (two fitters tried, the better result kept).
"pq" is the pair of red / green LEDs the page shows on the button (load on the computer, quality of
the result, 0 to 1), on the same scale as the LEDs on every other setting.

A preset is only a set of values for settings that already exist (the same fields the Advanced
panel shows), so the diagnostic code, the log and the result read exactly as if those values had
been set by hand. "Advanced" is not a preset: it shows every setting, starting from the preset that
was chosen before.

    python run_tostep.py convert part.stl --preset draft        (any other option still overrides it)

The numbers in "measured" come from docs/research/PRESETS.md (12 test meshes, a 2-core laptop).
"""
from __future__ import annotations

DEFAULT = "standard"

# The page's own defaults (what every preset starts from). Keys are the page's setting names.
BASE = {"mode": "surface", "engine": "classic", "tol": "0", "plane_angle": "3", "crease_angle": "30",
        "patch_size": "30", "max_bend": "60", "primitives": True, "memory_guard": True, "guess": False,
        "organic_tol": "0.3", "decimate": "0", "smooth": "0", "budget_s": "0"}

PRESETS = [
    {"id": "draft", "pic": "loose", "pq": [0.12, 0.55], "name": "Draft", "tag": "fastest",
     "line": "Quick, but looser: organic areas may sit up to 0.5 mm off, meshes over 50,000 triangles are "
             "reduced first (outlines kept) and refining stops after a minute. Flat faces, holes and edges stay exact.",
     "set": {"engine": "ladder", "guess": True, "organic_tol": "0.5", "decimate": "50000", "budget_s": "60"}},
    {"id": "quick", "pic": "near", "pq": [0.25, 0.75], "name": "Quick", "tag": "fast",
     "line": "Looks at the part's shape first (turned, flat-sided, organic) and claims the easy faces before "
             "anything slow. In the tests about twice as fast as Standard, often with fewer faces. Organic "
             "areas may sit up to 0.3 mm off.",
     "set": {"engine": "ladder"}},
    {"id": "standard", "pic": "fit", "pq": [0.5, 0.85], "name": "Standard", "tag": "recommended",
     "line": "Picks the engine by the part's shape. Smooth organic parts become quad patches: within the "
             "tolerance, no facets, smooth joins. Everything else goes the Quick way. Slower than Quick on busy "
             "organic shapes, the same elsewhere.",
     "set": {"engine": "auto"}},
    {"id": "fine", "pic": "best", "pq": [1.0, 0.95], "name": "Fine", "tag": "slowest",
     "line": "Converts twice (Standard, then the v2 fitter) and keeps the better result. In the tests it kept "
             "narrow details the others smoothed over (the cusps of two test plates).",
     "set": {"engine": "best"}},
]
BY_ID = {p["id"]: p for p in PRESETS}


def settings(preset_id):
    """The full page settings of a preset (BASE with the preset's values on top)."""
    p = BY_ID.get(preset_id)
    if p is None:
        raise ValueError("unknown preset %r (choose from %s)" % (preset_id, ", ".join(BY_ID)))
    return dict(BASE, **p["set"])


def cli_defaults(preset_id):
    """The same preset as defaults for `run_tostep.py convert part.stl ...` (argparse destinations)."""
    s = settings(preset_id)
    return {"mode": s["mode"], "engine": s["engine"], "tol": float(s["tol"]),
            "plane_angle": float(s["plane_angle"]), "crease_angle": float(s["crease_angle"]),
            "patch_size": float(s["patch_size"]) / 100.0, "max_bend": float(s["max_bend"]),
            "no_primitives": not s["primitives"], "no_memory_guard": not s["memory_guard"],
            "guess": float(s["organic_tol"]) if s["guess"] else 0.0, "decimate": int(s["decimate"]),
            "smooth": int(s["smooth"]), "budget": float(s["budget_s"])}


def for_page():
    """What the page needs: the presets with their full settings, and the default."""
    return {"default": DEFAULT, "presets": [dict(p, settings=settings(p["id"])) for p in PRESETS]}
