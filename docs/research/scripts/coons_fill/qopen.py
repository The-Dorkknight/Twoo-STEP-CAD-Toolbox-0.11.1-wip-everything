# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty. Research script for paper D18/D19 (2 November 2026).
import os, sys, numpy as np, trimesh, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', '..'))
from tostep.plugins.mesh2step import quadfit
m=trimesh.load('blobcut.stl')
n=m.face_normals
flat=(n[:,2]<-0.999)&(m.triangles_center[:,2]<-11.9)
o=m.submesh([np.where(~flat)[0]],append=True)
print('open part',len(o.faces),'watertight',o.is_watertight, 'flat tris',flat.sum())
print('remesher',quadfit.remesher_name())
t=time.time()
QV,QF=quadfit.quad_layout(o.vertices,o.faces,400)
QV,QF=quadfit._clean_quads(QV,QF)
print('quads',len(QF),'closed',quadfit._is_closed(QF),'%.1fs'%(time.time()-t))
from collections import Counter
c=Counter()
for f in QF:
    for k in range(4):
        a,b=f[k],f[(k+1)%4]; c[(min(a,b),max(a,b))]+=1
bd=[e for e,v in c.items() if v==1]
bv=np.unique(np.array(bd).ravel()) if bd else []
print('boundary edges',len(bd),'boundary verts z range', (QV[bv][:,2].min(),QV[bv][:,2].max()) if len(bv) else None, 'any >2 uses',sum(v>2 for v in c.values()))
