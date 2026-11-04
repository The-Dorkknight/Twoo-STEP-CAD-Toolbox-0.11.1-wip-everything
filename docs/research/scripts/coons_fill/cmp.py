# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty. Research script for paper D18/D19 (2 November 2026).
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', '..'))
os.environ["TOSTEP_FILL_COMPARE"]="1"; os.environ.setdefault("TOSTEP_BUDGET","0")
from tostep.plugins.mesh2step import engines, classic
src=sys.argv[1]; eng=sys.argv[2] if len(sys.argv)>2 else "classic"
rep=engines.convert(src,"cmp.step",{"gap_fill":"off"},eng,log=lambda s:None)
rows=classic._DEBUG.get("fill_compare",[])
print(f"{'tris':>6} {'lsq':>8} {'coons':>8} {'t':>5} {'plate':>8} {'t':>5}  tol")
for n,dl,dc,tc,dp,tp,tol in rows:
    print(f"{n:6d} {dl:8.4f} {dc:8.4f} {tc:5.2f} {dp:8.4f} {tp:5.2f}  {tol:.4f}")
import numpy as np
R=np.array([r[:6] for r in rows],float)
if len(R):
  tol=rows[0][6]
  for k,nm in ((1,'lsq'),(2,'coons'),(4,'plate')):
    print(nm,'within tol:',int((R[:,k]<=tol).sum()),'/',len(R),' median',np.median(np.minimum(R[:,k],99)))
  print('coons<=plate', int((R[:,2]<=R[:,4]).sum()), ' time coons',R[:,3].sum().round(1),' plate',R[:,5].sum().round(1))
import json
json.dump(rows, open(os.path.basename(src)+".cmp.json","w"))
