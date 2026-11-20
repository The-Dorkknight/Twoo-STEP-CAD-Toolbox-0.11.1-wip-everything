"""Area share of a STEP's faces by kind: facets (3-edged planes), planes, round, NURBS/other."""
import os, sys
sys.path.insert(0, os.environ.get("TOSTEP_ROOT", "."))
from tostep.plugins.mesh2step import classic as C
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.TopAbs import TopAbs_FACE, TopAbs_EDGE
import collections
for p in sys.argv[1:]:
    sh = C.read_step(p); acc = collections.Counter(); n = collections.Counter()
    for f in C._explore(sh, TopAbs_FACE):
        f = C._cast("Face", f); t = BRepAdaptor_Surface(f).GetType().name.replace("GeomAbs_", "")
        ne = sum(1 for _ in C._explore(f, TopAbs_EDGE))
        k = "facet" if (t == "Plane" and ne <= 4) else {"Plane": "plane", "Cylinder": "round", "Sphere": "round", "Cone": "round", "Torus": "round", "SurfaceOfRevolution": "turned"}.get(t, "freeform")
        a = C._face_area(f); acc[k] += a; n[k] += 1
    tot = sum(acc.values())
    print(p, " | ".join(f"{k} {n[k]} faces {100*acc[k]/tot:.1f}%" for k in sorted(acc)))
