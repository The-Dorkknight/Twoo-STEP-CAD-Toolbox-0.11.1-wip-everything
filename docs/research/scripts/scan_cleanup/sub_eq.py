# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty. Research script (18 November 2026).
import sys, numpy as np, time
import os; sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', '..'))
import trimesh
from scipy.sparse import coo_matrix
from tostep.plugins.mesh2step import classic
m=classic.load_mesh(sys.argv[1], classic.Params(), lambda s:None)
ctx=classic.MeshCtx(m); g=classic._Graph(ctx)
def old(ids,nF):
    loc=-np.ones(nF,dtype=np.int64); loc[ids]=np.arange(len(ids))
    mm=(loc[g.a]>=0)&(loc[g.b]>=0); n=len(ids)
    return coo_matrix((g.w[mm],(loc[g.a[mm]],loc[g.b[mm]])),shape=(n,n)).tocsr()
rng=np.random.default_rng(0); worst=0; to=tn=0
for k in range(30):
    c=ctx.C[rng.integers(ctx.nF)]; ids=np.argsort(np.linalg.norm(ctx.C-c,axis=1))[:rng.integers(50,20000)]; rng.shuffle(ids)
    t=time.time(); A=old(ids,ctx.nF); to+=time.time()-t
    t=time.time(); B=g.sub(ids,ctx.nF); tn+=time.time()-t
    worst=max(worst, abs(A-B).max() if A.nnz else 0)
print(f'{ctx.nF} faces: max difference {worst}, old {to/30*1000:.1f} ms/call, new {tn/30*1000:.1f} ms/call')
