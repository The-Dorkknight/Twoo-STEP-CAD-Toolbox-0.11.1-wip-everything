# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty. Research script for paper D18/D19 (2 November 2026).
import sys, os, subprocess, tempfile, numpy as np, trimesh, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', '..'))
from tostep.plugins.mesh2step import quadfit
exe=quadfit.find_quadriflow()
m=trimesh.load(sys.argv[1]); N=int(sys.argv[2]) if len(sys.argv)>2 else 600
d=tempfile.mkdtemp(); src=d+'/in.obj'; dst=d+'/out.obj'
quadfit._write_obj(src,m.vertices,m.faces)
t=time.time()
r=subprocess.run([exe,'-i',src,'-o',dst,'-f',str(N),'-sharp','-seed','0'],capture_output=True,text=True)
print('rc',r.returncode,'%.1fs'%(time.time()-t), r.stdout[-200:])
QV,QF=quadfit._read_obj(dst); QV,QF=quadfit._clean_quads(np.asarray(QV),QF)
print('quads',len(QF),'closed',quadfit._is_closed(QF))
# how well do quad vertices sit on the rim / plane
zs=QV[:,2]; onplane=np.abs(zs+12)<0.05
print('verts on base plane', onplane.sum())
# quads fully on plane
full=[f for f in QF if all(onplane[i] for i in f)]
part=[f for f in QF if 0<sum(onplane[i] for i in f)<4]
print('quads fully on plane',len(full),'straddling',len(part))
# straddling quads: off-plane vertices distance above plane
if part:
    off=[zs[i]+12 for f in part for i in f if not onplane[i]]
    print('straddle off-plane z min/max %.3f %.3f'%(min(off),max(off)))
from collections import Counter
cnt=Counter(sum(onplane[i] for i in f) for f in part); print('straddle on-plane vertex counts',cnt)
adj=0
for f in part:
    o=[onplane[i] for i in f]
    if sum(o)==2 and any(o[k] and o[(k+1)%4] for k in range(4)): adj+=1
print('rim quads with an edge on the rim',adj)
from tostep.plugins.mesh2step import classic as core
mesh=core.load_mesh(sys.argv[1],core.Params(),lambda s:None)
ctx=core.MeshCtx(mesh); tol=max(0.01,5e-4*ctx.diag)
t=time.time()
shape,nq=quadfit.build_shape(mesh,QV,QF,tol)
ref=trimesh.Trimesh(np.array(mesh.vertices),np.array(mesh.faces),process=False)
print('built %.1fs'%(time.time()-t),'sealed',core._sealed(shape),'dev',core.measure_deviation(ref,shape,tol),'tol',tol)
