#!/usr/bin/env python3
# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""
bench — measure the converter on a set of meshes so changes can be judged by numbers.

    python tools/bench.py part1.stl part2.stl --engines classic staged --out bench.csv
    python tools/bench.py ghost.stl --engines staged --guess 0.3 --timeout 3600

One row per (mesh, engine): seconds in each stage, faces by type, regions left as
triangles, share of the surface that ended up on exact (flat / round / lathe) faces,
deviation from the mesh, STEP size and peak memory. Each run is its own process.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

STAGES = [("load", "Loaded"), ("route", "Route:"), ("planes", "flat region"), ("shapes", "smooth curved component"),
          ("fit", "Pass 1"), ("build", "Building faces"), ("write", "Writing STEP"),
          ("verify", "Verifying"), ("end", "Faces in STEP")]

CHILD = r'''
import sys, json, time, os, resource
sys.path.insert(0, sys.argv[1])
src, out, engine, kw = sys.argv[2], sys.argv[3], sys.argv[4], json.loads(sys.argv[5])
from tostep.plugins.mesh2step import engines, classic as core
t0 = time.time(); lines = []
def log(s):
    for l in str(s).splitlines(): lines.append((round(time.time() - t0, 2), l))
rep = engines.convert(src, out, kw, engine, log=log)
# surface area by face type in the written file
from OCP.BRepAdaptor import BRepAdaptor_Surface, BRepAdaptor_Curve
from OCP.GProp import GProp_GProps
from OCP.BRepGProp import BRepGProp
from OCP.TopAbs import TopAbs_EDGE
shape = core.read_step(out)
area = {"flat": 0.0, "round": 0.0, "lathe": 0.0, "nurbs": 0.0, "triangle": 0.0}
count = dict.fromkeys(area, 0)
for f in core._explore(shape, core.TopAbs_FACE):
    face = core._cast("Face", f)
    t = BRepAdaptor_Surface(face).GetType().name
    g = GProp_GProps(); core._S(BRepGProp, "SurfaceProperties")(face, g); a = abs(g.Mass())
    if t.endswith("Plane"):
        es = [core._cast("Edge", e) for e in core._explore(face, TopAbs_EDGE)]
        straight = all(BRepAdaptor_Curve(e).GetType().name.endswith("Line") for e in es)
        k = "triangle" if (len(es) == 3 and straight) else "flat"
    elif t.endswith(("Cylinder", "Sphere", "Cone", "Torus")): k = "round"
    elif t.endswith(("Revolution", "Extrusion")): k = "lathe"
    else: k = "nurbs"
    area[k] += a; count[k] += 1
tot = sum(area.values()) or 1.0
res = dict(ok=rep.ok, seconds=round(time.time() - t0, 1), faces=rep.n_faces, solid=rep.is_solid, valid=rep.is_valid,
           triangle_regions=rep.n_faceted_regions, max_dev=rep.max_dev, mean_dev=rep.mean_dev, tol=getattr(rep, "tol", 0.0),
           input_tris=rep.n_input_tris, step_mb=round(os.path.getsize(out) / 1e6, 2),
           peak_mb=round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 if sys.platform != "darwin" else 1048576)),
           count=count, area_pct={k: round(100 * v / tot, 1) for k, v in area.items()}, log=lines)
print("@@RESULT" + json.dumps(res))
'''


def stage_seconds(log):
    marks = []
    for key, needle in STAGES:
        t = next((t for t, l in log if needle in l), None)
        if t is not None:
            marks.append((key, t))
    out = {}
    for (k, t), (_, t2) in zip(marks[:-1], marks[1:]):
        out[k] = round(t2 - t, 1)
    return out


def run(src, engine, kw, outdir, timeout, label=None):
    name = os.path.splitext(os.path.basename(src))[0]
    label = label or engine
    out = os.path.join(outdir, f"{name}__{label.replace(':', '_').replace('=', '-').replace(',', '_')}.step")
    t0 = time.time()
    try:
        p = subprocess.run([sys.executable, "-c", CHILD, ROOT, src, out, engine, json.dumps(kw)],
                           capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"mesh": name, "engine": label, "error": f"timeout after {timeout} s"}
    line = next((l for l in p.stdout.splitlines() if l.startswith("@@RESULT")), None)
    engine = label
    if line is None:
        return {"mesh": name, "engine": engine, "error": (p.stderr.strip().splitlines() or ["failed"])[-1][:200],
                "seconds": round(time.time() - t0, 1)}
    r = json.loads(line[8:])
    lg = r.pop("log")
    with open(os.path.splitext(out)[0] + ".log", "w", encoding="utf-8") as fh:
        fh.write("\n".join(f"{t:8.1f}  {l}" for t, l in lg))
    st = stage_seconds(lg)
    cnt, ar = r.pop("count"), r.pop("area_pct")
    row = {"mesh": name, "engine": engine, **r}
    row.update({f"n_{k}": v for k, v in cnt.items()})
    row["exact_area_pct"] = round(ar["flat"] + ar["round"] + ar["lathe"], 1)
    row["triangle_area_pct"] = ar["triangle"]
    row.update({f"s_{k}": v for k, v in st.items()})
    return row


def main():
    ap = argparse.ArgumentParser(description="Benchmark the converter on a set of meshes.")
    ap.add_argument("meshes", nargs="+")
    ap.add_argument("--engines", nargs="+", default=["classic"],
                    help="engine names; options after a colon, e.g. ladder:ladder_guess=false,max_settle=5")
    ap.add_argument("--guess", type=float, default=0.0, help="organic tolerance in mm (0 = off)")
    ap.add_argument("--out", default="bench.csv")
    ap.add_argument("--outdir", default="bench_out")
    ap.add_argument("--timeout", type=int, default=7200)
    a = ap.parse_args()
    os.makedirs(a.outdir, exist_ok=True)
    rows = []
    for src in a.meshes:
        for eng in a.engines:
            kw = {"mode": "surface"}
            if a.guess > 0:
                kw["organic_tol"] = a.guess
            base, _, opts = eng.partition(":")
            for item in filter(None, opts.split(",")):
                k, _, v = item.partition("=")
                kw[k] = json.loads(v) if v not in ("", None) else True
            row = run(src, base, kw, a.outdir, a.timeout, label=eng)
            rows.append(row)
            if "error" in row:
                print(f"{row['mesh']:16s} {eng:8s} ERROR {row['error']}", flush=True)
            else:
                print(f"{row['mesh']:16s} {eng:8s} {row['seconds']:7.1f}s  faces {row['faces']:6d}  "
                      f"exact {row['exact_area_pct']:5.1f}%  triangles {row['triangle_area_pct']:5.1f}% "
                      f"({row['triangle_regions']} regions)  max dev {row['max_dev']:.4f}  "
                      f"{row['step_mb']} MB  peak {row['peak_mb']} MB  valid {row['valid']} solid {row['solid']}", flush=True)
    keys = []
    for r in rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    with open(a.out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys); w.writeheader(); w.writerows(rows)
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
