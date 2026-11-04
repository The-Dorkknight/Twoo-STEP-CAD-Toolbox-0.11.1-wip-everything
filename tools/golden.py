#!/usr/bin/env python3
# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""
golden — the release gate: convert a fixed set of small parts and fail if the results got worse.

    python tools/golden.py                 # check against benchmarks/golden.json (exit 1 on any regression)
    python tools/golden.py --cases box     # only some cases
    python tools/golden.py --observe       # print what the current code produces (to set new limits by hand)

Why limits and not exact numbers: floating-point results differ in the last digits between operating
systems and library versions. The limits are chosen so that a real regression (an extra face, a bigger
deviation, an open solid) fails and a rounding difference does not. A limit is only ever loosened by a person,
with the reason written in benchmarks/golden.json ("why").

The parts are made here with trimesh, so nothing large is stored in the repository. Each case runs in its own
process (tools/bench.py), exactly like a job does.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import bench  # noqa: E402

GOLDEN = os.path.join(ROOT, "benchmarks", "golden.json")


def make_part(spec, folder):
    """A case's input: a file from the repository, or a mesh built from a recipe."""
    if isinstance(spec, str):
        return os.path.join(ROOT, spec)
    import trimesh
    kind = spec["make"]
    if kind == "box":
        m = trimesh.creation.box(extents=spec.get("size", [40, 30, 20]))
    elif kind == "cylinder":
        m = trimesh.creation.cylinder(radius=spec.get("radius", 10), height=spec.get("height", 30),
                                      sections=spec.get("sections", 96))
    elif kind == "sphere":
        m = trimesh.creation.icosphere(subdivisions=spec.get("subdivisions", 4), radius=spec.get("radius", 15))
    elif kind == "stepped":                                   # two stacked boxes: a stepped block
        a = trimesh.creation.box(extents=[40, 40, 10])
        b = trimesh.creation.box(extents=[20, 20, 10])
        b.apply_translation([0, 0, 10])
        a.apply_translation([0, 0, 0])
        m = trimesh.util.concatenate([a, b])                  # touching, not fused: two closed bodies
    else:
        raise SystemExit(f"unknown part recipe {kind!r}")
    path = os.path.join(folder, spec.get("name", kind) + ".stl")
    m.export(path)
    return path


def judge(row, expect):
    """-> list of sentences saying what is outside the limits (empty = pass)."""
    bad = []
    if "error" in row:
        return [f"the conversion failed: {row['error']}"]
    if expect.get("solid", True) and not row.get("solid"):
        bad.append("the result is not a closed solid")
    if expect.get("valid", True) and not row.get("valid"):
        bad.append("the geometry check found problems")
    lo, hi = expect.get("faces", [0, 10 ** 9])
    if not lo <= row["faces"] <= hi:
        bad.append(f"{row['faces']} faces, expected {lo} to {hi}")
    if row["triangle_regions"] > expect.get("faceted_regions_max", 10 ** 9):
        bad.append(f"{row['triangle_regions']} regions left as facets, at most {expect['faceted_regions_max']}")
    if row["max_dev"] > expect.get("max_dev_max", 10 ** 9):
        bad.append(f"worst deviation {row['max_dev']:.4f} mm, at most {expect['max_dev_max']} mm")
    if row["exact_area_pct"] < expect.get("exact_area_pct_min", 0):
        bad.append(f"only {row['exact_area_pct']}% of the surface is on exact faces, at least "
                   f"{expect['exact_area_pct_min']}%")
    if row["seconds"] > expect.get("seconds_max", 10 ** 9):
        bad.append(f"took {row['seconds']} s, at most {expect['seconds_max']} s (a slow runner? re-run once)")
    return bad


def main():
    ap = argparse.ArgumentParser(description="Release gate: convert fixed parts, fail on regressions.")
    ap.add_argument("--cases", default="", help="comma-separated case names (default: all)")
    ap.add_argument("--observe", action="store_true", help="print the measured numbers; do not judge")
    ap.add_argument("--keep", default="", metavar="DIR", help="keep the STEP files and logs in this folder")
    a = ap.parse_args()
    with open(GOLDEN, encoding="utf-8") as f:
        spec = json.load(f)
    want = set(filter(None, a.cases.split(",")))
    failures = 0
    with tempfile.TemporaryDirectory() as tmp:
        outdir = a.keep or os.path.join(tmp, "out")
        os.makedirs(outdir, exist_ok=True)
        for case in spec["cases"]:
            if want and case["name"] not in want:
                continue
            src = make_part(case["part"], tmp)
            row = bench.run(src, case.get("engine", "classic"), case.get("kwargs", {"mode": "surface"}),
                            outdir, case.get("timeout", 900), label=case["name"])
            if a.observe:
                print(json.dumps({k: row.get(k) for k in ("mesh", "engine", "seconds", "faces", "solid", "valid",
                                                          "triangle_regions", "max_dev", "exact_area_pct", "error")}))
                continue
            bad = judge(row, case["expect"])
            status = "PASS" if not bad else "FAIL"
            print(f"{status}  {case['name']:14s} faces {row.get('faces', '-')!s:>5}  "
                  f"max dev {row.get('max_dev', float('nan')):.4f}  {row.get('seconds', '-')} s")
            for b in bad:
                print("      - " + b)
            failures += bool(bad)
    if failures:
        print(f"\n{failures} case(s) outside their limits. Do not release; see benchmarks/README.md.")
        return 1
    if not a.observe:
        print("\nAll golden cases are inside their limits.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
