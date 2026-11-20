# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty. Research script (18 November 2026), see ../../SCAN_CLEANUP.md.
import sys, numpy as np, trimesh, time
truth=trimesh.load('truth.stl'); St,_=trimesh.sample.sample_surface(truth,30000,seed=3)
m=trimesh.load('v_clean.stl')
def err(mm):
    _,d,_=trimesh.proximity.closest_point(mm,St)
    S,_=trimesh.sample.sample_surface(mm,30000,seed=4); _,d2,_=trimesh.proximity.closest_point(truth,S)
    a=np.r_[d,d2]; return f"median {np.median(a):.3f}  99% {np.percentile(a,99):.3f}  max {a.max():.3f} mm"
print('noisy        ', err(m))
for it in (3,10):
    d=m.copy(); trimesh.smoothing.filter_taubin(d,lamb=0.5,nu=-0.53,iterations=it); print(f'taubin {it:2d}    ', err(d))
tol=0.0853
for it in (3,10):
    t=time.time(); d=m.copy(); V0=d.vertices.copy()
    trimesh.smoothing.filter_taubin(d,lamb=0.5,nu=-0.53,iterations=it)
    D=d.vertices-V0; n=np.linalg.norm(D,axis=1); cap=tol/3
    s=np.minimum(1.0,cap/np.maximum(n,1e-12)); d.vertices=V0+D*s[:,None]
    print(f'capped {it:2d}    ', err(d), f'({time.time()-t:.1f}s)')
