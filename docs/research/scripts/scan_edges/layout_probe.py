"""Run the Ladder conversion up to the patch layout and say why components were split."""
import os, sys, time, collections, pickle
sys.path.insert(0, os.environ.get("TOSTEP_ROOT", "."))
import numpy as np
from tostep.plugins.mesh2step import classic as C, engines

src = sys.argv[1]
kw = dict(engine="ladder", ladder=True, budget_s=2400)
prm = engines._params(C, {**kw})
why = collections.Counter()
orig_layout = C.layout_patches
def layout(ctx, graph, comp_ids, max_area, max_bend, need_disk=True):
    stack=[comp_ids]; n=0
    while stack:
        ids=stack.pop()
        if len(ids)<=1: continue
        d = (not need_disk) or C._is_disk(ctx, ids); c = C._cone(ctx, ids) <= max_bend; a = ctx.area[ids].sum() <= max_area
        if d and c and a: n+=1; continue
        why[("disk" if not d else "")+("cone" if not c else "")+("area" if not a else "")]+=1
        parts=C._split(ctx, graph, ids, 2 if a else int(min(8,max(2,np.ceil(ctx.area[ids].sum()/max_area)))))
        if len(parts)<=1: n+=1; continue
        stack.extend(parts)
    return orig_layout(ctx, graph, comp_ids, max_area, max_bend, need_disk)
C.layout_patches = layout
stop = {}
def fake_build(ctx, label, planes, tol, prm, L, rep, full=None, regions0=None, check=None):
    g = C._Graph(ctx)
    t=time.time()
    regs = C._layout_regions(ctx, g, label, planes, tol, prm, L, (prm.patch_size*ctx.diag)**2)
    kinds = collections.Counter(r["type"] for r in regs)
    sizes = [len(r["faces"]) for r in regs if r["type"]=="nurbs"]
    print("LAYOUT", round(time.time()-t,1), "s", dict(kinds), "median tris/patch", np.median(sizes), "split reasons", dict(why), flush=True)
    # component stats: euler char and boundaries
    comps = collections.defaultdict(list)
    for f in range(ctx.nF):
        pass
    pickle.dump(dict(V=ctx.V, F=ctx.F, regs=[(r["type"], r["faces"]) for r in regs], label=label, comp=ctx.face_comp), open(sys.argv[2], "wb"))
    raise SystemExit(0)
C._build_surface = fake_build
C.convert(src, "probe.step", prm, log=lambda s: print(s, flush=True))
