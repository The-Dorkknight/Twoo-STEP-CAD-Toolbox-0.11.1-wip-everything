# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""
accel — optional graphics-card support (PyTorch: Apple Metal, Nvidia CUDA, Intel XPU).

Only the v2 fitter's shape-guess scoring (the biggest pure-number step) uses it. Everything else
— building faces, sewing, STEP — is OpenCASCADE, which is CPU-only. If PyTorch isn't installed,
or TOSTEP_GPU isn't "1", nothing here is used and the NumPy path runs.
"""
from __future__ import annotations

import os
import sys

import numpy as np

_state = {"dev": None, "checked": False, "why": ""}


def kernel_test(dev):
    """Run the real thing on the device, not just ask whether it exists: a few plain operations, then the actual
    scoring (planes, cylinders, spheres) on a tiny made-up mesh. Returns "" if all is well, otherwise a sentence
    saying what failed. This catches a card PyTorch can see but cannot run (Nvidia Pascal with a current PyPI build:
    'no kernel image is available') and one that lacks an operation the scoring needs (Apple MPS: no float64)."""
    try:
        import torch
        x = torch.arange(64, dtype=torch.float32, device=dev).reshape(8, 8)
        y = (x @ x.T).sum() + torch.linalg.norm(x, dim=1).sum() + torch.einsum("ij,ij->i", x, x).sum()
        v = float(y.cpu())
        if not (v == v):
            return "the card returned a not-a-number result"
        S, H = 24, 6
        rng = np.random.default_rng(1)
        sV = rng.normal(size=(S, 3, 3))
        sN = rng.normal(size=(S, 3)); sN /= np.linalg.norm(sN, axis=1)[:, None]
        sC, sA, ctol = sV.mean(1), rng.uniform(0.5, 1.5, S), np.full(S, 0.3)
        u = rng.normal(size=(H, 3)); u /= np.linalg.norm(u, axis=1)[:, None]
        rows = {"pln": np.hstack([rng.normal(size=(H, 3)), u]),
                "cyl": np.hstack([rng.normal(size=(H, 3)), u, rng.uniform(1, 3, (H, 1)), np.ones((H, 1))]),
                "sph": np.hstack([rng.normal(size=(H, 3)), rng.uniform(1, 3, (H, 1)), np.ones((H, 1))])}
        for kind, blk in rows.items():
            out = score_blocks(kind, blk, sV, sN, sC, sA, 0.5, ctol, 0.9, 0.9, dev)
            if out.shape != (H,) or not np.all(np.isfinite(out)):
                return f"scoring {kind} on the card gave a bad answer"
        return ""
    except Exception as e:  # noqa
        return (str(e).strip().splitlines() or [type(e).__name__])[0][:240]


def device():
    """torch device to use, or None. Only a device that passed kernel_test() is ever returned; if a card is
    present but fails, the reason is in why() and the NumPy path runs, so a conversion never fails on a GPU."""
    if not _state["checked"]:
        _state["checked"] = True
        flag = os.environ.get("TOSTEP_GPU")
        if flag == "1":
            try:
                import torch
                cand = None
                if torch.cuda.is_available():
                    cand = torch.device("cuda", int(os.environ.get("TOSTEP_GPU_INDEX", "0") or 0))
                elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
                    cand = torch.device("mps")
                elif getattr(torch, "xpu", None) and torch.xpu.is_available():
                    cand = torch.device("xpu")
                if cand is not None:
                    bad = kernel_test(cand)
                    if bad:
                        _state["why"] = f"graphics card found ({cand.type}) but it failed a test: {bad}"
                    else:
                        _state["dev"] = cand
                else:
                    _state["why"] = "PyTorch is installed but sees no usable graphics card"
            except Exception as e:  # noqa
                _state["why"] = f"PyTorch could not start: {e}"
                _state["dev"] = None
        elif flag == "cpu-test":     # for tests: torch on the CPU
            import torch
            _state["dev"] = torch.device("cpu")
    return _state["dev"]


def why():
    """Why device() is None although the GPU was asked for ('' if there is no reason to report)."""
    return _state["why"]


_dev_cache = {"key": None, "refs": None, "t": None}


def _sample_on_device(torch, dev, dt, sV, sN, sC, sA, ctol):
    """The sample of triangles as device tensors. The v2 fitter scores several blocks of guesses against the SAME
    sample (up to 1,500 triangles), so it is converted and uploaded once and reused until a new sample arrives
    (0.11.2: before, every block of 256 guesses re-uploaded all five arrays, a fixed cost on Apple's and Nvidia's
    cards alike that is as big as the maths on a sample this small)."""
    key = (id(sV), id(sN), id(sC), id(sA), id(ctol), str(dev), str(dt))
    if _dev_cache["key"] != key:
        T = lambda a: torch.as_tensor(np.ascontiguousarray(a), dtype=dt).to(dev)
        _dev_cache["t"] = (T(sV), T(sN), T(sC), T(sA), T(ctol))
        _dev_cache["refs"] = (sV, sN, sC, sA, ctol)       # keeps the arrays alive so their ids cannot be reused
        _dev_cache["key"] = key
    return _dev_cache["t"]


def score_blocks(kind, blk, sV, sN, sC, sA, tol, ctol, cos_lim, cos_pl, dev=None, dtype=None, sync=True):
    """Same maths as mesh2step.v2._batch_hypotheses.score for one block of hypotheses;
    returns the supported area per hypothesis (numpy). float32 on GPUs (Metal has no float64).
    sync=False returns the device tensor instead, so several blocks can be queued and read back with ONE
    transfer (see gather()); each read-back waits for the card to finish, which costs more than the maths."""
    import torch
    dev = dev or device()
    dt = dtype or (torch.float64 if dev.type == "cpu" else torch.float32)
    # Convert on the CPU, then move: the Apple (MPS) backend has no float64 at all, so nothing here may ask the
    # device for a float64 tensor, not even to hand the answer back.
    T = lambda a: torch.as_tensor(np.ascontiguousarray(a), dtype=dt).to(dev)
    sV_, sN_, sC_, sA_, ctol_ = _sample_on_device(torch, dev, dt, sV, sN, sC, sA, ctol)
    b = T(blk)
    if kind == "pln":
        o, n = b[:, 0:3], b[:, 3:6]
        on = (o * n).sum(1)
        rc = (torch.einsum("sck,hk->hsc", sV_, n) - on[:, None, None]).abs().amax(2)
        rm = (sC_ @ n.T - on).T.abs()
        ok_n = (sN_ @ n.T).T >= cos_pl
    elif kind == "cyl":
        c, a, r, out_ = b[:, 0:3], b[:, 3:6], b[:, 6], b[:, 7] > 0.5
        d = sV_[None] - c[:, None, None]
        h = torch.einsum("hsck,hk->hsc", d, a)
        rad = torch.linalg.norm(d - h[..., None] * a[:, None, None], dim=3)
        rc = (rad - r[:, None, None]).abs().amax(2)
        dc = sC_[None] - c[:, None]
        hc = torch.einsum("hsk,hk->hs", dc, a)
        radv = dc - hc[..., None] * a[:, None]
        nr = torch.linalg.norm(radv, dim=2)
        rm = (nr - r[:, None]).abs()
        sign = torch.where(out_, 1.0, -1.0).to(dt)[:, None, None]
        dirn = radv / nr.clamp_min(1e-12)[..., None] * sign
        ok_n = torch.einsum("hsk,sk->hs", dirn, sN_) >= cos_lim
    else:
        c, r, out_ = b[:, 0:3], b[:, 3], b[:, 4] > 0.5
        rc = (torch.linalg.norm(sV_[None] - c[:, None, None], dim=3) - r[:, None, None]).abs().amax(2)
        dc = sC_[None] - c[:, None]
        nr = torch.linalg.norm(dc, dim=2)
        rm = (nr - r[:, None]).abs()
        sign = torch.where(out_, 1.0, -1.0).to(dt)[:, None, None]
        dirn = dc / nr.clamp_min(1e-12)[..., None] * sign
        ok_n = torch.einsum("hsk,sk->hs", dirn, sN_) >= cos_lim
    ok = (rc <= tol) & (rm <= ctol_[None]) & ok_n
    out = ok.to(dt) @ sA_
    return out.cpu().double().numpy() if sync else out


def gather(parts):
    """Read back the device tensors made by score_blocks(sync=False) in one transfer; returns one numpy array."""
    import torch
    return torch.cat(list(parts)).cpu().double().numpy()


def numpy_scores(kind, blk, sV, sN, sC, sA, tol, ctol, cos_lim, cos_pl):
    """The same scoring in plain NumPy (a copy of the maths in mesh2step.v2), used only to check the card's answers."""
    if kind == "pln":
        o, n = blk[:, 0:3], blk[:, 3:6]
        rc = np.abs(np.einsum("sck,hk->hsc", sV, n) - (o * n).sum(1)[:, None, None]).max(2)
        rm = np.abs(sC @ n.T - (o * n).sum(1)).T
        ok_n = (sN @ n.T).T >= cos_pl
    elif kind == "cyl":
        c, a, r, out_ = blk[:, 0:3], blk[:, 3:6], blk[:, 6], blk[:, 7] > 0.5
        d = sV[None] - c[:, None, None]
        h = np.einsum("hsck,hk->hsc", d, a)
        rad = np.linalg.norm(d - h[..., None] * a[:, None, None], axis=3)
        rc = np.abs(rad - r[:, None, None]).max(2)
        dc = sC[None] - c[:, None]
        hc = np.einsum("hsk,hk->hs", dc, a)
        radv = dc - hc[..., None] * a[:, None]
        nr = np.linalg.norm(radv, axis=2)
        rm = np.abs(nr - r[:, None])
        dirn = radv / np.maximum(nr, 1e-12)[..., None] * np.where(out_, 1.0, -1.0)[:, None, None]
        ok_n = np.einsum("hsk,sk->hs", dirn, sN) >= cos_lim
    else:
        c, r, out_ = blk[:, 0:3], blk[:, 3], blk[:, 4] > 0.5
        rc = np.abs(np.linalg.norm(sV[None] - c[:, None, None], axis=3) - r[:, None, None]).max(2)
        dc = sC[None] - c[:, None]
        nr = np.linalg.norm(dc, axis=2)
        rm = np.abs(nr - r[:, None])
        dirn = dc / np.maximum(nr, 1e-12)[..., None] * np.where(out_, 1.0, -1.0)[:, None, None]
        ok_n = np.einsum("hsk,sk->hs", dirn, sN) >= cos_lim
    ok = (rc <= tol) & (rm <= ctol[None]) & ok_n
    return ok.astype(float) @ sA


def selftest():
    """Used by `run_tostep.py gpu --test`: which card, does it work, and is it any faster than the CPU path?
    Returns a dict (json-friendly). Times a block of plane and cylinder hypotheses scored against synthetic
    triangles: the same maths the v2 fitter uses, nothing else."""
    import time
    r = {"torch": None, "backend": None, "device": None, "ok": False, "error": "", "numpy_s": None, "gpu_s": None,
         "max_difference": None}
    try:
        import torch
    except Exception as e:  # noqa
        r["error"] = "PyTorch (the GPU pack) is not installed: " + str(e).splitlines()[0]
        return r
    r["torch"] = torch.__version__
    cand = None
    if torch.cuda.is_available():
        cand = torch.device("cuda", int(os.environ.get("TOSTEP_GPU_INDEX", "0") or 0))
        r["backend"], r["device"] = "cuda", torch.cuda.get_device_name(cand)
        cc = torch.cuda.get_device_capability(cand)
        r["capability"] = f"{cc[0]}.{cc[1]}"
        r["built_for"] = list(torch.cuda.get_arch_list())
    elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        cand, r["backend"], r["device"] = torch.device("mps"), "mps", "Apple GPU (Metal)"
    elif getattr(torch, "xpu", None) and torch.xpu.is_available():
        cand, r["backend"], r["device"] = torch.device("xpu"), "xpu", "Intel GPU"
    elif os.environ.get("TOSTEP_GPU") == "cpu-test":     # tests: same code path, torch on the CPU
        cand, r["backend"], r["device"] = torch.device("cpu"), "cpu", "CPU (test)"
    if cand is None:
        r["error"] = ("PyTorch is installed but sees no graphics card it can use. "
                      + ("This PyTorch build has no CUDA part." if getattr(torch.version, "cuda", None) is None and sys.platform != "darwin" else ""))
        return r
    bad = kernel_test(cand)
    if bad:
        r["error"] = bad
        if "no kernel image" in bad:
            r["error"] += (" — this PyTorch build has no code for your card's generation. "
                           "Use 'Reinstall the right build' in the Graphics card box (it picks the right build).")
        return r
    rng = np.random.default_rng(7)
    S, H = 4000, 512
    sV = rng.normal(size=(S, 3, 3))
    sN = rng.normal(size=(S, 3)); sN /= np.linalg.norm(sN, axis=1)[:, None]
    sC = sV.mean(1)
    sA = rng.uniform(0.1, 1.0, S)
    n = rng.normal(size=(H, 3)); n /= np.linalg.norm(n, axis=1)[:, None]
    ctol = np.full(S, 0.2)
    rows = {"pln": np.hstack([rng.normal(size=(H, 3)), n]),
            "cyl": np.hstack([rng.normal(size=(H, 3)), n, rng.uniform(0.5, 2, (H, 1)), np.ones((H, 1))]),
            "sph": np.hstack([rng.normal(size=(H, 3)), rng.uniform(0.5, 2, (H, 1)), np.ones((H, 1))])}
    worst, t_np, t_gpu = 0.0, 0.0, 0.0
    try:
        for kind, blk in rows.items():
            t0 = time.perf_counter()
            ref = numpy_scores(kind, blk, sV, sN, sC, sA, 0.5, ctol, 0.9, 0.9)
            t_np += time.perf_counter() - t0
            score_blocks(kind, blk, sV, sN, sC, sA, 0.5, ctol, 0.9, 0.9, cand)       # warm-up (kernel / shader loading)
            t0 = time.perf_counter()
            got = score_blocks(kind, blk, sV, sN, sC, sA, 0.5, ctol, 0.9, 0.9, cand)
            t_gpu += time.perf_counter() - t0
            # float32 on a card can flip a borderline triangle in or out; allow a small share of the total area
            worst = max(worst, float(np.abs(got - ref).max()) / max(1.0, float(ref.max())))
        r["numpy_s"], r["gpu_s"], r["max_difference"] = round(t_np, 4), round(t_gpu, 4), worst
        r["ok"] = worst <= 0.02 if cand.type != "cpu" else worst <= 1e-9
        if not r["ok"]:
            r["error"] = f"the card's answers differ from the processor's by {worst:.1%} of the area (float32 rounding should be far smaller)"
    except Exception as e:  # noqa
        r["error"] = (str(e).strip().splitlines() or [type(e).__name__])[0][:240]
    return r


if __name__ == "__main__":
    import json as _json
    print(_json.dumps(selftest()))
