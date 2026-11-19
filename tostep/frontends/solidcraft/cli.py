# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""`tostep solidcraft`: the SolidCraft link on the command line.

  tostep solidcraft                       the selected mesh bodies in SolidCraft -> solids, put back in
  tostep solidcraft --body Scan           one body by name
  tostep solidcraft --file part.stl       convert a file here and insert the solid into SolidCraft
  tostep solidcraft --status              is a SolidCraft listening?

Each body is exported from SolidCraft as STL, converted by this toolbox exactly as `tostep convert`
does (its own process, so a crash or a memory spike can't take SolidCraft or this link down), and the
STEP is inserted as a new component named "<body> solid". The mesh body is left as it was.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time

from tostep.frontends.solidcraft.link import Link, SolidCraftError, status

CLI_HELP = "take mesh bodies out of a running SolidCraft, convert them, put the solids back"
HERE = os.path.dirname(os.path.abspath(__file__))
TOOLBOX = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))


def convert(src, dst, preset="standard", extra=(), log=print):
    """Run `run_tostep.py convert` in its own process, passing its lines through. Returns True if the
    STEP was written. A heartbeat line every 30 s of silence shows it is still working (a 7th-gen
    laptop can sit on one big step for minutes)."""
    cmd = [sys.executable, os.path.join(TOOLBOX, "run_tostep.py"), "convert", src, dst, "--preset", preset,
           *extra]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
                         encoding="utf-8", errors="replace")
    t0 = last = time.time()
    import threading
    alive = {"on": True}

    def beat():
        nonlocal last
        while alive["on"]:
            time.sleep(1)
            if alive["on"] and time.time() - last > 30:
                log(f"  … still converting ({int(time.time() - t0)} s so far)")
                last = time.time()
    threading.Thread(target=beat, daemon=True).start()
    try:
        for line in p.stdout:
            last = time.time()
            log("  " + line.rstrip())
        p.wait()
    finally:
        alive["on"] = False
    return p.returncode == 0 and os.path.isfile(dst) and os.path.getsize(dst) > 0


def bodies_to_solids(sc, bodies, preset="standard", extra=(), workdir=None, log=print):
    """For each body name: export STL, convert, insert the STEP. Returns [(body, reply or error)]."""
    workdir = workdir or tempfile.mkdtemp(prefix="tostep-solidcraft-")
    done = []
    for b in bodies:
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in b) or "body"
        stl = os.path.join(workdir, safe + ".stl")
        step = os.path.join(workdir, safe + ".step")
        log(f"{b}: exporting from SolidCraft…")
        try:
            sc.export_stl(b, stl)
        except SolidCraftError as e:
            log(f"{b}: {e}")
            done.append((b, e))
            continue
        if not os.path.isfile(stl):
            e = SolidCraftError(f"SolidCraft said it exported {b}, but {stl} is not there")
            log(f"{b}: {e}")
            done.append((b, e))
            continue
        log(f"{b}: converting ({preset})…")
        if not convert(stl, step, preset, extra, log):
            e = SolidCraftError(f"the conversion of {b} failed (its log is above)")
            log(f"{b}: {e}")
            done.append((b, e))
            continue
        log(f"{b}: inserting the solid into SolidCraft…")
        try:
            rep = sc.insert_step(step, f"{b} solid")
            log(f"{b}: done, component '{(rep or {}).get('component_name', b + ' solid')}'")
            done.append((b, rep))
        except SolidCraftError as e:
            log(f"{b}: {e}  (the STEP is kept at {step})")
            done.append((b, e))
    return done


def main(argv):
    ap = argparse.ArgumentParser(prog="tostep solidcraft", description=__doc__.split("\n\n")[0] + "\n\n" +
                                 "\n".join(__doc__.split("\n")[2:7]),
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--body", action="append", help="a body by name (repeat for more); default: the selected bodies")
    ap.add_argument("--file", help="convert this mesh file and insert the solid, instead of taking a body")
    ap.add_argument("--status", action="store_true", help="only check that a SolidCraft is listening")
    ap.add_argument("--preset", default="standard", choices=["draft", "quick", "standard", "fine"])
    ap.add_argument("--port", type=int, help="SolidCraft's control port (default: the one it wrote, else 27878)")
    ap.add_argument("--token", help="the control token (default: read from SolidCraft's token file)")
    ap.add_argument("--keep", metavar="FOLDER", help="keep the STL and STEP files in this folder")
    a, extra = ap.parse_known_args(argv)          # anything else goes to `tostep convert` as it is
    if a.status:
        ok, text = status(a.port, a.token)
        print(text)
        return 0 if ok else 1
    workdir = None
    if a.keep:
        os.makedirs(a.keep, exist_ok=True)
        workdir = a.keep
    try:
        with Link(a.port, a.token) as sc:
            if a.file:
                if not os.path.isfile(a.file):
                    print(f"No such file: {a.file}")
                    return 1
                workdir = workdir or tempfile.mkdtemp(prefix="tostep-solidcraft-")
                stem = os.path.splitext(os.path.basename(a.file))[0]
                step = os.path.join(workdir, stem + ".step")
                print(f"{a.file}: converting ({a.preset})…")
                if not convert(a.file, step, a.preset, extra):
                    print("The conversion failed (its log is above).")
                    return 1
                rep = sc.insert_step(step, stem)
                print(f"Inserted into SolidCraft as '{(rep or {}).get('component_name', stem)}'.")
                return 0
            bodies = a.body or sc.selected_bodies()
            if not bodies:
                print("Nothing to convert: select one or more mesh bodies in SolidCraft (or give --body NAME).")
                return 1
            res = bodies_to_solids(sc, bodies, a.preset, extra, workdir)
    except SolidCraftError as e:
        print(e)
        return 1
    bad = [b for b, r in res if isinstance(r, Exception)]
    print(f"\n{len(res) - len(bad)} of {len(res)} bodies converted and inserted." +
          (f" Not done: {', '.join(bad)}." if bad else ""))
    return 1 if bad else 0
