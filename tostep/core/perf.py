# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""
perf — hardware detection and performance profiles.

Profiles (pick in the app, or "auto"):
  potato    one core, low priority, no GPU            — old / small laptops, big meshes without crashes
  moderate  about half the cores, GPU if available    — typical modern laptop
  extreme   every core, parallel Second pass, GPU     — Apple Silicon Macs, workstations

A profile is just a handful of numbers (see PROFILES); the worker process gets them as
environment variables before NumPy/SciPy load, so the maths libraries use that many threads.
"""
from __future__ import annotations

import importlib.util
import json
import os
import platform
import subprocess
import sys

PROFILE_NAMES = ("auto", "potato", "moderate", "extreme")
DESCRIPTIONS = {
    "potato": "One core at low priority, no graphics card, meshes over 30,000 triangles reduced first (features kept). Slowest, lightest — keeps the computer usable. May grind for hours but finishes: after 2.5 h what is still outside tolerance is kept as facets.",
    "moderate": "About half the cores. Uses the graphics card where supported (v2 fitter only). Finishes within about 40 min: after that, unfinished areas are kept as facets.",
    "extreme": "Every core, runs Classic and v2 side by side in Second pass, and several queued files at once. Uses the graphics card where supported. Same 40 min ceiling as Moderate.",
}


def _ram_gb():
    try:
        import psutil
        return psutil.virtual_memory().total / 2**30
    except Exception:
        try:
            return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30
        except Exception:
            return 8.0


def _physical_cores():
    try:
        import psutil
        n = psutil.cpu_count(logical=False)
        if n:
            return n
    except Exception:
        pass
    return max(1, (os.cpu_count() or 2) // 2)


def apple_silicon():
    return sys.platform == "darwin" and platform.machine().lower() in ("arm64", "aarch64")


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Run in a child process so the server never loads PyTorch itself (1.5 s, 400 MB), and so a PyTorch that
# was installed a moment ago is seen without restarting anything.
_PROBE = r"""
import json
r = {"torch": None, "backend": None, "ok": False, "error": ""}
try:
    import torch
    r["torch"] = torch.__version__
    r["cuda_build"] = getattr(torch.version, "cuda", None)
    d = None
    if torch.cuda.is_available():
        d = torch.device("cuda", 0); r["backend"] = "cuda"; r["device"] = torch.cuda.get_device_name(0)
    elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        d = torch.device("mps"); r["backend"] = "mps"; r["device"] = "Apple GPU"
    elif getattr(torch, "xpu", None) and torch.xpu.is_available():
        d = torch.device("xpu"); r["backend"] = "xpu"; r["device"] = "Intel GPU"
    if d is None:
        r["error"] = "PyTorch is installed but sees no graphics card it can use"
    else:
        from tostep.core import accel
        bad = accel.kernel_test(d)
        r["ok"], r["error"] = (not bad), bad
except Exception as e:
    r["error"] = (str(e).strip().splitlines() or [type(e).__name__])[0][:300]
print(json.dumps(r))
"""
_probe_cache = {}


def probe_torch(refresh=False):
    """What the GPU pack (PyTorch) can do right now: {"torch": version|None, "backend", "ok", "error", ...}.
    One quick check if PyTorch is not installed; a short child process if it is. Cached until refresh=True."""
    if "p" in _probe_cache and not refresh:
        return _probe_cache["p"]
    if importlib.util.find_spec("torch") is None:
        p = {"torch": None, "backend": None, "ok": False, "error": ""}
    else:
        try:
            out = subprocess.run([sys.executable, "-E", "-s", "-c", _PROBE], capture_output=True, text=True,
                                 timeout=180, cwd=ROOT, env=dict(os.environ, PYTHONNOUSERSITE="1"))
            p = json.loads(out.stdout.strip().splitlines()[-1])
        except Exception as e:  # noqa
            p = {"torch": "?", "backend": None, "ok": False, "error": "could not check PyTorch: " + str(e)[:200]}
    _probe_cache["p"] = p
    return p


def gpu_backend():
    """cuda / mps / xpu if the GPU pack is installed AND a real test on the card passed, else None."""
    p = probe_torch()
    return p["backend"] if p.get("ok") else None


_cards_cache = {}


def gpu_cards(refresh=False):
    """Every graphics card found (see gpuinfo), each with a "state" for the page:
         ready    the GPU pack is installed and a real test on this card passed
         install  the GPU pack can be installed for it
         fix      a GPU pack is installed but does not work for this card (reinstalling the right build helps)
         no       nothing this program can do for it (the "why" says what)"""
    if "c" not in _cards_cache or refresh:
        from tostep.core import gpuinfo
        _cards_cache["c"] = gpuinfo.list_gpus(apple_silicon())
    p = probe_torch(refresh)
    out = []
    for c in _cards_cache["c"]:
        c = dict(c)
        plan = c.get("plan")
        kind = plan["kind"] if plan else None
        if not plan:
            c["state"] = "no"
        elif p.get("ok") and p.get("backend") == kind:
            c["state"] = "ready"
        elif p.get("torch"):
            c["state"] = "fix"
            c["detail"] = p.get("error") or "the installed PyTorch cannot use this card"
        else:
            c["state"] = "install"
        out.append(c)
    return out


def gpu_capable_hardware():
    """A short name for the card the GPU pack could use (even if the pack isn't installed), else None."""
    for c in gpu_cards():
        if c.get("plan"):
            return c["name"] + {"mps": " (Metal)", "cuda": " (CUDA)"}.get(c["plan"]["kind"], "")
    return None


def detect(cards=True):
    """Hardware facts. cards=False skips listing the graphics cards (slow on Windows) — a conversion only needs
    to know whether the GPU pack works."""
    ram = _ram_gb()
    d = {"logical": os.cpu_count() or 1, "physical": _physical_cores(), "ram_gb": round(ram, 1),
         "apple_silicon": apple_silicon(), "gpu_backend": gpu_backend(),
         "gpu_hardware": gpu_capable_hardware() if cards else None, "platform": sys.platform,
         "machine": platform.machine()}
    return d


def auto_profile(hw=None):
    hw = hw or detect()
    if hw["ram_gb"] < 12 or hw["physical"] < 4:
        return "potato"
    if (hw["physical"] >= 8 and hw["ram_gb"] >= 16) or (hw["apple_silicon"] and hw["ram_gb"] >= 16 and hw["physical"] >= 8):
        return "extreme"
    return "moderate"


def settings(profile, hw=None, gpu_wanted=True):
    """Concrete numbers for a profile name ('auto' resolves first)."""
    hw = hw or detect(cards=False)
    name = auto_profile(hw) if profile in (None, "", "auto") else profile
    if name not in DESCRIPTIONS:
        name = "moderate"
    logical, phys, ram = hw["logical"], hw["physical"], hw["ram_gb"]
    # budget_s: after this many seconds a conversion stops refining and keeps what is still
    # outside tolerance as exact facets (the file is always finished, never abandoned);
    # max_fills: most slow plate fills per conversion. Potato may grind for hours but must
    # not run out of memory; a mid-range laptop should finish anything in under an hour.
    if name == "potato":
        s = dict(threads=1, jobs=1, parallel_engines=False, gpu=False, nice=10, budget_s=9000, max_fills=20,
                 tri_ceiling=30000)
    elif name == "moderate":
        s = dict(threads=max(2, logical // 2), jobs=1, parallel_engines=False, gpu=True, nice=5,
                 budget_s=2400, max_fills=60)
    else:
        s = dict(threads=logical, jobs=max(1, min(4, phys // 4, int(ram // 8))),
                 parallel_engines=ram >= 12, gpu=True, nice=0, budget_s=2400, max_fills=120)
    s["gpu"] = bool(s["gpu"] and gpu_wanted and hw["gpu_backend"])
    s["name"] = name
    return s


def auto_limits():
    """(budget_s, max_fills) of the profile "auto" would pick for this computer, from memory
    and core count only — no graphics-card probing (that loads PyTorch when the GPU pack is
    installed: 1.5 s and 400 MB a conversion has no use for)."""
    hw = {"ram_gb": _ram_gb(), "physical": _physical_cores(), "logical": os.cpu_count() or 1,
          "apple_silicon": apple_silicon(), "gpu_backend": None, "gpu_hardware": None}
    s = settings(auto_profile(hw), hw, gpu_wanted=False)
    return int(s.get("budget_s", 0)), int(s.get("max_fills", 0))


def worker_env(s, base=None):
    """Environment for a worker process running with settings s."""
    env = dict(base if base is not None else os.environ)
    t = str(max(1, int(s["threads"])))
    for k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS",
              "NUMEXPR_NUM_THREADS", "BLIS_NUM_THREADS"):
        env[k] = t
    env["TOSTEP_NICE"] = str(int(s["nice"]))
    env["TOSTEP_GPU"] = "1" if s["gpu"] else "0"
    env["TOSTEP_PARALLEL_ENGINES"] = "1" if s["parallel_engines"] else "0"
    env["TOSTEP_BUDGET"] = str(int(s.get("budget_s", 0)))
    env["TOSTEP_MAX_FILLS"] = str(int(s.get("max_fills", 0)))
    env["TOSTEP_TRI_CEILING"] = str(int(s.get("tri_ceiling", 0)))     # Potato only: bigger meshes are reduced first
    return env


def apply_nice():
    n = int(os.environ.get("TOSTEP_NICE", "0") or 0)
    if n <= 0:
        return
    try:
        if sys.platform.startswith("win"):
            import ctypes
            ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(),
                                                    0x4000 if n < 10 else 0x40)   # below normal / idle
        else:
            os.nice(n)
    except Exception:
        pass


def _private_uv():
    """The uv that set up this private Python: runtime/<cpu-type>/uv, found from sys.executable."""
    d = os.path.dirname(os.path.abspath(sys.executable))
    for _ in range(8):
        for name in ("uv", "uv.exe"):
            c = os.path.join(d, name)
            if os.path.isfile(c):
                return c
        nd = os.path.dirname(d)
        if nd == d:
            break
        d = nd
    return None


def _installer_base():
    uv = _private_uv()
    if uv:
        return [uv, "pip", "{verb}", "--python", sys.executable, "--system", "--break-system-packages"], True
    return [sys.executable, "-m", "pip", "{verb}", "--break-system-packages"], False


def gpu_pack_steps(card_name=None):
    """What installing GPU support does, as (steps, note, card): steps is a list of command lines (an uninstall
    of a PyTorch that cannot use the card, then the right install), or None with a sentence saying why not.

    The private Python is "managed by uv", which refuses plain installs; the start scripts install the normal
    libraries with --system --break-system-packages, so this does the same."""
    cards = gpu_cards()
    card = None
    for c in cards:
        if c.get("plan") and (card_name is None or c["name"] == card_name):
            card = c
            break
    if card is None:
        why = [c["why"] for c in cards if c.get("why")]
        return None, ("No graphics card here can use the GPU pack. " + " ".join(why)).strip(), None
    plan = card["plan"]
    base, is_uv = _installer_base()
    inst = [x.replace("{verb}", "install") for x in base]
    if plan.get("index"):
        inst += ["--index-url", plan["index"]]
    inst += plan["spec"]
    steps = []
    if card["state"] == "fix":
        un = [x.replace("{verb}", "uninstall") for x in base]
        if not is_uv:
            un = [sys.executable, "-m", "pip", "uninstall", "-y", "torch"]
        else:
            un = [x for x in un if x != "--break-system-packages"] + ["torch"]
        steps.append(un)
    steps.append(inst)
    note = f"{plan['size']}; {card['why']}"
    return steps, note, card


def gpu_pack_command():
    """Compatibility: (install command, note) for the first usable card, or (None, reason)."""
    steps, note, _ = gpu_pack_steps()
    return (steps[-1] if steps else None), note


def gpu_test():
    """A real test of the GPU pack in a child process: card, whether kernels run, CPU time against card time.
    Returns the dict accel.selftest() makes, plus "ran": False when it could not even start."""
    try:
        out = subprocess.run([sys.executable, "-E", "-s", "-m", "tostep.core.accel"], capture_output=True, text=True,
                             timeout=300, cwd=ROOT, env=dict(os.environ, PYTHONNOUSERSITE="1", TOSTEP_GPU="1"))
        r = json.loads(out.stdout.strip().splitlines()[-1])
        r["ran"] = True
        return r
    except Exception as e:  # noqa
        return {"ran": False, "ok": False, "error": "could not run the test: " + str(e)[:200]}


def gpu_test_text(r):
    """The result of gpu_test() as plain sentences."""
    if not r.get("ok"):
        return "Graphics card test FAILED: " + (r.get("error") or "unknown reason")
    s = f"Graphics card test passed: {r.get('device')} ({r.get('backend')}), PyTorch {r.get('torch')}."
    if r.get("numpy_s") and r.get("gpu_s"):
        s += (f" Scoring a test block took {r['gpu_s']:.3f} s on the card and {r['numpy_s']:.3f} s on the processor"
              f" ({r['numpy_s'] / max(r['gpu_s'], 1e-9):.1f}x). Only the v2 fitter's shape scoring uses the card.")
    return s


def gpu_pack_check():
    """After installing: one line for the log (and the page's cache is refreshed)."""
    probe_torch(refresh=True)
    return gpu_test_text(gpu_test())


if __name__ == "__main__":
    hw = detect()
    print(json.dumps(hw, indent=1))
    print("auto ->", auto_profile(hw), settings("auto", hw))
