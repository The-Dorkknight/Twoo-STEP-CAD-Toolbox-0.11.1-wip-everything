# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty. Research script for paper D18/D19 (2 November 2026).
import os
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch
rungs=[("1  Plane","flat faces","built","#2e9e57","#2e9e57"),
("2  Cylinder · cone · sphere","holes and rounds","built (cones: Staged only)","#349f5b","#2e9e57"),
("3  Revolve (lathe)","vases, domes, bodies of revolution","built · Staged, Ladder","#4aa267","#2e9e57"),
("4  Extrude","plates, prisms, stepped parts","built · Staged, Ladder (freeform outlines)","#66a873","#2e9e57"),
("5  Fillet pieces","rolling-ball blends: cylinder, torus, sphere","partly","#8aab80","#b07a1e"),
("6  Four-sided NURBS patch","organic skin; no trimming needed","built · Quad patches engine, whole organic\nparts only (Standard preset; D8, D9)","#a3a68c","#2e9e57"),
("7  Coons fill","from its border; inside fitted to mesh","built · experimental, opt-in: Advanced →\nGap filler (D18, v1.3)","#999999","#b07a1e"),
("8  Plate solver","slow; for odd-shaped holes","built (the default gap filler)","#7d808a","#2e9e57"),
("9  Triangles","always works, always last","built","#3c3f48","#2e9e57")]
fig,ax=plt.subplots(figsize=(13.2,6.9),dpi=150)
ax.set_xlim(0,13.2); ax.set_ylim(0,6.9); ax.axis('off')
fig.patch.set_facecolor('white')
ax.text(0.5,6.62,"The claim ladder: each rung must prove itself against the full-resolution mesh, or hand its region down",fontsize=13.5,weight='bold',color='#1c1f26',va='center')
ax.text(0.5,6.3,"Status in Twoo STEP CAD Toolbox 0.11.1-wip and later (paper version 1.3, 2 November 2026)",fontsize=10.5,color='#5a5f6b',va='center')
y=5.75
for i,(t,sub,st,c,sc) in enumerate(rungs):
    x=0.5+0.14*i
    ax.add_patch(FancyBboxPatch((x,y-0.26),4.0,0.5,boxstyle="round,pad=0.0,rounding_size=0.08",fc=c,ec='none'))
    ax.text(x+0.17,y+0.06,t,fontsize=13.5,weight='bold',color='white',va='center')
    ax.text(x+0.17,y-0.15,sub,fontsize=10.2,color='white',va='center')
    ax.text(5.85,y,st,fontsize=11,weight='bold',color=sc,va='center',linespacing=1.15)
    y-=0.66
ax.annotate("",xy=(11.9,0.35),xytext=(11.9,6.0),arrowprops=dict(arrowstyle='-|>',color='#5c6170',lw=2,mutation_scale=18))
ax.text(11.6,3.2,"tried first  →  last resort",rotation=90,fontsize=11,color='#5c6170',va='center',ha='center')
ax.text(11.3,3.2,"exact and cheap  →  approximate and slow",rotation=90,fontsize=11,color='#5c6170',va='center',ha='center')
fig.savefig("claim-ladder-v1.3.png",bbox_inches='tight',facecolor='white')
