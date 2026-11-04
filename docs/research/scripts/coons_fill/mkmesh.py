# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty. Research script for paper D18/D19 (2 November 2026).
import os
import trimesh, numpy as np, sys
# 1) blob with a flat-cut base: organic + plane + sharp rim
m = trimesh.creation.icosphere(subdivisions=5)
v = m.vertices.copy()
f = np.sin(3*v[:,0])*np.cos(2*v[:,1])*0.15 + 0.1*np.sin(5*v[:,2]+v[:,0])
v = v*(1+f[:,None])*30
b = trimesh.Trimesh(v, m.faces)
cut = trimesh.intersections.slice_mesh_plane(b, [0,0,1], [0,0,-12], cap=True)
cut.export('blobcut.stl'); print('blobcut', len(cut.faces), cut.is_watertight)
# 2) box with a bumpy organic top (prismatic + organic)
n=60
x=np.linspace(-30,30,n); X,Y=np.meshgrid(x,x)
Z=10+4*np.sin(X/8)*np.cos(Y/7)+2*np.exp(-((X-8)**2+(Y+5)**2)/60)
top=np.c_[X.ravel(),Y.ravel(),Z.ravel()]
bot=np.c_[X.ravel(),Y.ravel(),np.zeros(n*n)]
V=np.vstack([top,bot]); F=[]
idx=lambda i,j,o=0:o+i*n+j
for i in range(n-1):
  for j in range(n-1):
    a,b_,c,d=idx(i,j),idx(i,j+1),idx(i+1,j+1),idx(i+1,j)
    F+= [[a,b_,c],[a,c,d]]
    o=n*n; F+=[[a+o,c+o,b_+o],[a+o,d+o,c+o]]
# walls
ring=[(0,j) for j in range(n)]+[(i,n-1) for i in range(1,n)]+[(n-1,j) for j in range(n-2,-1,-1)]+[(i,0) for i in range(n-2,0,-1)]
for k in range(len(ring)):
  p,q=ring[k],ring[(k+1)%len(ring)]
  a,b_=idx(*p),idx(*q); F+=[[a,a+n*n,b_],[b_,a+n*n,b_+n*n]]
bx=trimesh.Trimesh(V,np.array(F)); trimesh.repair.fix_normals(bx)
bx.export('bumpbox.stl'); print('bumpbox', len(bx.faces), bx.is_watertight)
