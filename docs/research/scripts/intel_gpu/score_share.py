# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty. Research script (11 November 2026): share of a v2 run spent in shape scoring, processor only.
import sys, os, time, cProfile, pstats, io
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', '..'))
os.environ["TOSTEP_GPU"]="0"; os.environ.setdefault("TOSTEP_BUDGET","0")
from tostep.plugins.mesh2step import engines
src=sys.argv[1]
pr=cProfile.Profile(); t=time.time(); pr.enable()
rep=engines.convert(src,"o.step",{},"v2",log=lambda s:None)
pr.disable(); dt=time.time()-t
st=pstats.Stats(pr); st.sort_stats("cumulative")
tot=0
for (f,l,n),v in st.stats.items():
    if n=="score" and f.endswith("v2.py"): tot+=v[3]
print(f"{os.path.basename(src)}: total {dt:.1f}s, score() {tot:.1f}s = {100*tot/dt:.0f}%  faces={rep.n_faces}")
