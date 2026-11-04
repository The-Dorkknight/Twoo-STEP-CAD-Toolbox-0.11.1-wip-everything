# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty. Research script for paper D18/D19 (2 November 2026).
import os, sys, subprocess, tempfile, numpy as np, trimesh, matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', '..'))
from tostep.plugins.mesh2step import quadfit
m=trimesh.load('blobcut.stl'); exe=quadfit.find_quadriflow()
d=tempfile.mkdtemp(); quadfit._write_obj(d+'/i.obj',m.vertices,m.faces)
subprocess.run([exe,'-i',d+'/i.obj','-o',d+'/o.obj','-f','600','-sharp','-seed','0'],capture_output=True)
QV,QF=quadfit._read_obj(d+'/o.obj'); QV,QF=quadfit._clean_quads(np.asarray(QV),QF)
on=np.abs(QV[:,2]+12)<0.05
cols=[];polys=[]
n_al=n_x=0
for f in QF:
    o=[on[i] for i in f]; s=sum(o)
    if s==4: c="#c9ccd3"
    elif s==0: c="#9fc9ae"
    elif s==2 and any(o[k] and o[(k+1)%4] for k in range(4)): c="#2e9e57"; n_al+=1
    else: c="#e5392e"; n_x+=1
    polys.append(QV[f]); cols.append(c)
fig=plt.figure(figsize=(12,5.2),dpi=150)
for k,(el,az,title) in enumerate([(-28,-60,"seen from below: the flat base"),(14,-60,"from the side")]):
    ax=fig.add_subplot(1,2,k+1,projection='3d')
    pc=Poly3DCollection(polys,facecolors=cols,edgecolors="#40444d",linewidths=0.25)
    ax.add_collection3d(pc)
    lo,hi=QV.min(0),QV.max(0); c=(lo+hi)/2; r=(hi-lo).max()/2*0.62
    ax.set_xlim(c[0]-r,c[0]+r);ax.set_ylim(c[1]-r,c[1]+r);ax.set_zlim(c[2]-r,c[2]+r)
    ax.view_init(el,az); ax.set_axis_off(); ax.set_title(title,fontsize=11)
from matplotlib.patches import Patch
fig.legend(handles=[Patch(color="#c9ccd3",label="quad lying on the flat base"),Patch(color="#2e9e57",label=f"rim quad with an edge along the sharp rim ({n_al})"),
                    Patch(color="#e5392e",label=f"rim quad crossing the rim corner-wise ({n_x})"),Patch(color="#9fc9ae",label="organic quad")],
           loc="lower center",ncol=2,frameon=False,fontsize=10)
fig.suptitle(f"QuadriFlow with -sharp on the blob cut flat at the bottom: {len(QF)} quads",fontsize=12.5)
fig.tight_layout(rect=(0,0.1,1,0.95)); fig.savefig("fig-hybrid-rim.png",facecolor="white")
print(len(QF),n_al,n_x)
