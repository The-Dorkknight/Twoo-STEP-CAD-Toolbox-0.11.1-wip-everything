# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty. Research script (18 November 2026), see ../../SCAN_CLEANUP.md.
import numpy as np, trimesh, sys
from skimage import measure
rng=np.random.default_rng(1)
h=0.2  # voxel mm -> dense like a scan
x=np.arange(-14,14,h); y=np.arange(-14,14,h); z=np.arange(-5,175,h)
X,Y,Z=np.meshgrid(x,y,z,indexing='ij')
R=np.hypot(X,Y)
# radius profile: tip cone, collar, grip with ribs, body, flat display window
r=np.interp(Z,[0,22,25,30,32,90,100,170],[0.8,1.6,4.5,6.5,9.0,9.5,10.5,10.5])
rib=(Z>34)&(Z<85); r=r+np.where(rib,0.5*(np.sin(Z*2.2)>0.6),0)
d=R-r
d=np.where((Z>105)&(Z<150),np.maximum(d,X-7.0),d)   # flat display window face
d=np.maximum(d,Z-168); d=np.maximum(d,-Z+0.0)
v,f,_,_=measure.marching_cubes(d,0,spacing=(h,h,h))
v+=np.array([x[0],y[0],z[0]])
m=trimesh.Trimesh(v,f,process=True)
# scanner noise
m.vertices+=rng.normal(0,0.04,m.vertices.shape)
# holes: delete triangles in a few patches
c=m.triangles_center; keep=np.ones(len(m.faces),bool)
for p in [(0,10.4,140),(-9,0,60),(0,-4,10)]:
    keep&=np.linalg.norm(c-np.array(p),axis=1)>1.6
m.update_faces(keep); m.remove_unreferenced_vertices()
# floating junk (scanner spray / turntable bits)
junk=[trimesh.creation.icosphere(1,radius=rng.uniform(0.2,0.8)).apply_translation(rng.uniform([-20,-20,0],[20,20,170])) for _ in range(40)]
plate=trimesh.creation.box((60,60,1.0)).apply_translation((0,0,-8))  # bit of turntable
m=trimesh.util.concatenate([m]+junk+[plate])
m.export(sys.argv[1]); print(len(m.faces),'tris; watertight',m.is_watertight,'bodies',len(m.split(only_watertight=False)))
