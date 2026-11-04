# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty. Research script for paper D18/D19 (2 November 2026).
import sys, time, json, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', '..'))
os.environ.setdefault("TOSTEP_BUDGET", "0")
from tostep.plugins.mesh2step import engines
src, engine, gf = sys.argv[1], sys.argv[2], sys.argv[3]
extra = json.loads(sys.argv[4]) if len(sys.argv) > 4 else {}
out = f"out_{os.path.basename(src)[:-4]}_{engine}_{gf.replace('+','_')}.step"
lines=[]
t=time.time()
rep = engines.convert(src, out, {"gap_fill": gf, **extra}, engine, log=lambda s: lines.append(s))
dt=time.time()-t
for l in lines:
    if 'gap fill' in l or 'Faces in STEP' in l or 'route' in l.lower(): print('  ', l)
print(f"RESULT {src} {engine} {gf}: {dt:.1f}s faces={rep.n_faces} maxdev={rep.max_dev:.4f} tol={rep.tol:.4g} solid={rep.is_solid} valid={rep.is_valid}")
