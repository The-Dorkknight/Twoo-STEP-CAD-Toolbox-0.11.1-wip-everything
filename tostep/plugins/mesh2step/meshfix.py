# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""
meshfix — trimesh.repair.fix_normals without the pure-Python graph walk (0.9.1).

trimesh fixes the winding of a mesh by walking its faces one pair at a time with networkx
(a pure-Python graph library) and a few small numpy calls per pair: about 15 s for a
160,000-triangle mesh with flipped faces on the 2-core test machine. This does the same walk
with scipy's compiled breadth-first search and works out every flip at once, so it takes a
fraction of a second. On any mesh whose faces can be wound consistently (every real part)
the answer is exactly trimesh's: the first face of each connected piece, in trimesh's own
order, keeps its direction and every other face is made to agree with it. Meshes that cannot
be wound consistently at all (a Möbius strip, faces glued along more than one edge) have no
right answer; there the faces that get flipped may differ from trimesh's.

The check for inside-out bodies afterwards is trimesh's own (`fix_inversion`).
"""
from __future__ import annotations

import numpy as np


def _flips(faces, adj, adj_edges):
    """Which faces to reverse so that neighbours run their shared edge in opposite directions."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import breadth_first_order, connected_components

    nF = len(faces)
    a, b = adj[:, 0], adj[:, 1]
    u, v = adj_edges[:, 0], adj_edges[:, 1]

    def runs_forward(fi):                               # does face fi hold the edge as u -> v ?
        f = faces[fi]
        return (((f[:, 0] == u) & (f[:, 1] == v)) | ((f[:, 1] == u) & (f[:, 2] == v))
                | ((f[:, 2] == u) & (f[:, 0] == v)))

    same = runs_forward(a) == runs_forward(b)           # same direction: one of the two must turn

    # one root per connected piece: its first face in the order trimesh meets them (the
    # adjacency pairs read row by row), so the same face keeps its direction as in trimesh
    g = coo_matrix((np.ones(len(a)), (a, b)), shape=(nF, nF)).tocsr()
    _, label = connected_components(g, directed=False)
    nodes = adj.ravel()
    _, first = np.unique(label[nodes], return_index=True)
    roots = nodes[first]

    # one search over all pieces at once, from an extra node joined to every root
    k = len(roots)
    rows = np.concatenate([a, np.full(k, nF)])
    cols = np.concatenate([b, roots])
    g = coo_matrix((np.ones(len(rows)), (rows, cols)), shape=(nF + 1, nF + 1)).tocsr()
    order, pred = breadth_first_order(g, nF, directed=False, return_predecessors=True)

    # the flip across each tree edge, looked up from the adjacency pairs
    key = np.minimum(a, b) * (nF + 1) + np.maximum(a, b)
    ukey, ui = np.unique(key, return_index=True)
    usame = same[ui]
    child = order[1:].astype(np.int64)             # scipy gives int32: the keys below need 64 bits
    parent = pred[child].astype(np.int64)
    tree_same = np.zeros(len(child), bool)
    inner = parent != nF
    ck = np.minimum(child[inner], parent[inner]) * (nF + 1) + np.maximum(child[inner], parent[inner])
    tree_same[inner] = usame[np.searchsorted(ukey, ck)]

    # a face is reversed if its parent ended up reversed XOR the edge between them says so;
    # the search order guarantees every parent is settled before its children
    flip = [False] * (nF + 1)
    for c, p, s in zip(child.tolist(), parent.tolist(), tree_same.tolist()):
        flip[c] = flip[p] != s
    return np.array(flip[:nF], bool)


def fix_winding(mesh):
    """In place, like trimesh.repair.fix_winding."""
    if mesh.is_winding_consistent:
        return
    adj = np.asarray(mesh.face_adjacency, dtype=np.int64).reshape(-1, 2)
    if not len(adj):
        return
    faces = mesh.faces.view(np.ndarray).copy()
    edges = np.asarray(mesh.face_adjacency_edges, dtype=np.int64).reshape(-1, 2)
    flip = _flips(faces, adj, edges)
    if flip.any():
        faces[flip] = faces[flip][:, ::-1]
        mesh.faces = faces


def fix_normals(mesh, multibody=False):
    """In place, like trimesh.repair.fix_normals: consistent winding, then outward normals."""
    import trimesh
    fix_winding(mesh)
    trimesh.repair.fix_inversion(mesh, multibody=multibody)
