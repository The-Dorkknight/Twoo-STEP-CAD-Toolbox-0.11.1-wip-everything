# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty. Research script for paper D18/D19 (2 November 2026).
import os
import json, numpy as np, matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
fig,axs=plt.subplots(1,2,figsize=(12.5,5.0),dpi=150)
for ax,(f,name) in zip(axs,[("blobby.stl.cmp.json","Coarse blob (5,120 triangles)"),("knob_plain.stl.cmp.json","Ridged knob (test part)")]):
    R=json.load(open(f)); tol=R[0][6]
    cap=6*tol
    c=np.array([min(r[2],cap) for r in R]); p=np.array([min(r[4],cap) for r in R])
    x=np.arange(len(R))
    ax.bar(x-0.2,p,0.4,color="#7d808a",label="plate filler")
    ax.bar(x+0.2,c,0.4,color="#2e9e57",label="Coons fill (D18)")
    ax.axhline(tol,color="#c0392b",lw=1.5,ls="--")
    ax.text(1.0,tol,"  tolerance\n  %.3f mm"%tol,color="#c0392b",ha="left",va="center",fontsize=9,transform=ax.get_yaxis_transform(),clip_on=False)
    ax.set_ylim(0,cap*1.02)
    nc=int(sum(r[2]<=tol for r in R)); npl=int(sum(r[4]<=tol for r in R))
    tc=sum(r[3] for r in R); tp=sum(r[5] for r in R)
    ax.set_title(f"{name}\nwithin tolerance: Coons {nc}/{len(R)}, plate {npl}/{len(R)} · time {tc:.0f} s against {tp:.0f} s",fontsize=10.5)
    ax.set_xlabel("patch the least-squares fit missed (one bar pair each)"); ax.set_ylabel("worst gap to the mesh, mm")
    ax.set_xticks(x); ax.set_xticklabels([str(i+1) for i in x],fontsize=8)
    ax.spines[['top','right']].set_visible(False)
h,l=axs[0].get_legend_handles_labels()
fig.legend(h,l,frameon=False,loc="lower center",ncol=2,fontsize=10)
fig.text(0.99,0.015,"A bar reaching the top of the chart: no usable face",ha="right",fontsize=8.5,color="#666")
fig.tight_layout(rect=(0,0.06,0.97,1)); fig.savefig("fig-coons-fill.png",facecolor="white")
