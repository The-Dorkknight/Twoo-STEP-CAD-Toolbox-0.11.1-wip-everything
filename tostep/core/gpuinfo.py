# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""
gpuinfo — which graphics cards are in this computer, and what each one needs.

Standard library only, and it never imports PyTorch (that costs 1.5 s and 400 MB). It reads what the
operating system and the Nvidia driver tool say, then works out for each card whether the optional
GPU pack (PyTorch) can use it and which build of PyTorch that is.

Why this is not just "pip install torch" (facts checked October 2026, see docs/guide/gpu.md):
  * Apple Silicon (M1 … M5): the normal PyTorch wheel talks to the GPU through Metal.
  * Nvidia, Turing or newer (RTX 20xx, GTX 16xx and up): the normal wheel works if the driver is recent
    (580 or newer for the CUDA 13 build that PyPI now serves); older drivers use the CUDA 12.6 build.
  * Nvidia, Pascal / Maxwell / Volta (GTX 750 … GTX 10xx, Titan V): the normal wheel has NO code for these
    cards. The card is seen, PyTorch says "CUDA available", and every kernel then fails. They need the
    CUDA 12.6 build, which PyTorch publishes up to version 2.14 only.
  * AMD and Intel built-in graphics: PyTorch has no build this installer can set up for them.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys

# The last PyTorch release that still ships CUDA 12.6 wheels (Maxwell / Pascal / Volta support) is 2.14.
CU126_INDEX = "https://download.pytorch.org/whl/cu126"
CU126_SPEC = ["torch>=2.6,<2.15"]
# The CUDA 13 build that PyPI serves by default needs at least this driver (Nvidia's own figure).
DRIVER_FOR_CUDA13 = 580
# CUDA 12.x builds need at least this driver (525 on Linux, 528 on Windows; 528 covers both).
DRIVER_FOR_CUDA12 = 528


def _run(cmd, timeout=10):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""


def _nvidia_smi():
    p = shutil.which("nvidia-smi")
    if p:
        return p
    if sys.platform.startswith("win"):
        for c in (r"C:\Windows\System32\nvidia-smi.exe",
                  r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe"):
            if os.path.isfile(c):
                return c
    return None


def vendor_of(name):
    n = (name or "").lower()
    if "nvidia" in n or "geforce" in n or "quadro" in n or "tesla" in n or re.search(r"\b(gtx|rtx)\b", n):
        return "NVIDIA"
    if "apple" in n:
        return "Apple"
    if "amd" in n or "radeon" in n or "advanced micro" in n or "ati " in n:
        return "AMD"
    if "intel" in n or "iris" in n or re.search(r"\barc\b", n):
        return "Intel"
    return "Other"


# Compute capability from the card's name, for old drivers whose nvidia-smi cannot say.
_CC_BY_NAME = [
    (r"RTX\s*50\d\d|B[12]00\b|GB10", 12.0), (r"RTX\s*40\d\d|RTX\s*[2-6]000 Ada|L4\b|L40", 8.9),
    (r"H100|H200|GH200", 9.0), (r"RTX\s*30\d\d|RTX\s*A\d{3,4}|\bA(10|16|30|40|100)\b", 8.6),
    (r"RTX\s*20\d\d|GTX\s*16\d\d|Quadro RTX|Titan RTX|\bT4\b|\bT[1-6]\d\d\b", 7.5),
    (r"Titan V\b|V100|Quadro GV100", 7.0),
    (r"GTX\s*10\d\d|GT\s*10\d\d|Titan X|Titan Xp|MX\s*[1-3]\d\d|Quadro P\d|Tesla P(4|40)\b|\bP(4|40)\b", 6.1),
    (r"Tesla P100|\bP100\b", 6.0),
    (r"GTX\s*(9\d\d|750)|GTX\s*Titan X|Quadro M\d|Tesla M\d|MX\s*[1-2]\d\d", 5.2),
]


def cc_from_name(name):
    for pat, cc in _CC_BY_NAME:
        if re.search(pat, name or "", re.I):
            return cc
    return None


def _num(s):
    try:
        return float(str(s).strip())
    except Exception:
        return None


def nvidia_cards():
    """Cards the Nvidia driver tool lists: name, compute capability, driver, memory."""
    exe = _nvidia_smi()
    if not exe:
        return []
    out = _run([exe, "--query-gpu=name,compute_cap,driver_version,memory.total", "--format=csv,noheader,nounits"])
    cards = []
    if out.strip():
        for line in out.strip().splitlines():
            p = [x.strip() for x in line.split(",")]
            if len(p) >= 4:
                cards.append(dict(name=p[0], cc=_num(p[1]), driver=p[2], vram_mb=_num(p[3])))
    else:   # old drivers refuse the compute_cap field
        out = _run([exe, "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader,nounits"])
        for line in out.strip().splitlines():
            p = [x.strip() for x in line.split(",")]
            if len(p) >= 3:
                cards.append(dict(name=p[0], cc=cc_from_name(p[0]), driver=p[1], vram_mb=_num(p[2])))
    return cards


def system_cards():
    """Every display adapter the operating system knows (names only): built-in and separate."""
    names = []
    if sys.platform.startswith("win"):
        out = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                    "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name | ConvertTo-Json -Compress"], 20)
        try:
            v = json.loads(out) if out.strip() else []
            names = [v] if isinstance(v, str) else list(v)
        except Exception:
            names = []
    elif sys.platform == "darwin":
        out = _run(["system_profiler", "SPDisplaysDataType", "-json"], 20)
        try:
            for d in json.loads(out).get("SPDisplaysDataType", []):
                names.append(d.get("sppci_model") or d.get("_name") or "Graphics")
        except Exception:
            names = []
    else:
        out = _run(["lspci", "-mm"])
        for line in out.splitlines():
            if re.search(r'"(VGA compatible controller|3D controller|Display controller)"', line):
                q = re.findall(r'"([^"]*)"', line)
                if len(q) >= 3:
                    names.append((q[1] + " " + q[2]).strip())
        if not names:
            for card in sorted(os.listdir("/sys/class/drm")) if os.path.isdir("/sys/class/drm") else []:
                if re.fullmatch(r"card\d+", card):
                    try:
                        ven = open(f"/sys/class/drm/{card}/device/vendor").read().strip()
                    except Exception:
                        continue
                    names.append({"0x10de": "NVIDIA graphics", "0x1002": "AMD Radeon graphics",
                                  "0x8086": "Intel graphics"}.get(ven, "Graphics adapter " + ven))
    return [n for n in names if isinstance(n, str) and n.strip()]


def _driver_major(d):
    try:
        return int(str(d).split(".")[0])
    except Exception:
        return None


def plan_for(card):
    """What the GPU pack would be for this card.

    Returns (plan, why):  plan = {"kind", "index", "spec", "size"} or None;  why = one sentence.
    """
    v = card["vendor"]
    if v == "Apple":
        return ({"kind": "mps", "index": None, "spec": ["torch"], "size": "about 80 MB"},
                "Uses the Apple graphics chip through Metal.")
    if v == "NVIDIA":
        cc, drv = card.get("cc"), _driver_major(card.get("driver"))
        if cc is None:
            return None, ("Could not tell which generation this Nvidia card is. Update the Nvidia driver, "
                          "then press Look again.")
        if cc < 5.0:
            return None, ("Too old: PyTorch no longer builds for Kepler-generation Nvidia cards (compute "
                          f"capability {cc:g}).")
        if drv is not None and drv < DRIVER_FOR_CUDA12:
            return None, (f"The Nvidia driver ({card.get('driver')}) is too old for any current PyTorch. "
                          f"Update it to {DRIVER_FOR_CUDA12} or newer, then press Look again.")
        if cc < 7.5:
            gen = "Pascal" if 6.0 <= cc < 7.0 else ("Volta" if cc < 7.5 and cc >= 7.0 else "Maxwell")
            return ({"kind": "cuda", "index": CU126_INDEX, "spec": CU126_SPEC, "size": "about 2.5 GB"},
                    f"A {gen}-generation card: PyPI's normal PyTorch has no code for it, so this installs the "
                    "CUDA 12.6 build instead (PyTorch publishes that up to version 2.14).")
        if cc >= 10.0 and drv is not None and drv < DRIVER_FOR_CUDA13:
            return None, (f"A Blackwell-generation card needs Nvidia driver {DRIVER_FOR_CUDA13} or newer "
                          f"(you have {card.get('driver')}). Update it, then press Look again.")
        if drv is not None and drv < DRIVER_FOR_CUDA13:
            return ({"kind": "cuda", "index": CU126_INDEX, "spec": CU126_SPEC, "size": "about 2.5 GB"},
                    f"Your driver ({card.get('driver')}) is older than the {DRIVER_FOR_CUDA13} that PyPI's "
                    "newest PyTorch wants, so this installs the CUDA 12.6 build, which works with it.")
        return ({"kind": "cuda", "index": None, "spec": ["torch"], "size": "about 2.5 GB"},
                "Uses the Nvidia card through CUDA.")
    if v == "AMD":
        return None, ("Not used. PyTorch's AMD (ROCm) builds support only some separate Radeon cards and the "
                      "newest Ryzen AI chips, not the built-in Radeon graphics of older Ryzen laptops, and this "
                      "installer does not set ROCm up. The Nvidia card, if there is one, is the one that counts.")
    if v == "Intel":
        return None, ("Not used. Intel graphics need a different PyTorch build (XPU) that this installer does "
                      "not set up.")
    return None, "Not used: no PyTorch build this installer can set up for it."


def list_gpus(apple_silicon=False):
    """All graphics cards found, each with vendor, plan (or None) and a sentence saying why."""
    cards = []
    smi = nvidia_cards()
    for c in smi:
        c["vendor"] = "NVIDIA"
        cards.append(c)
    for n in system_cards():
        vend = vendor_of(n)
        if vend == "NVIDIA" and smi:
            continue            # already listed, with more detail
        c = dict(name=n, vendor=vend, cc=None, driver=None, vram_mb=None)
        if vend == "NVIDIA":
            c["problem"] = ("An Nvidia card is installed but its driver tool (nvidia-smi) did not answer. "
                            "Install or repair the Nvidia driver from nvidia.com, restart the computer, then press Look again.")
        cards.append(c)
    if apple_silicon and not any(c["vendor"] == "Apple" for c in cards):
        cards.append(dict(name="Apple GPU", vendor="Apple", cc=None, driver=None, vram_mb=None))
    # Apple Silicon shows as "Apple M5" etc.; make sure the vendor is right even if the name lacks "apple"
    for c in cards:
        if apple_silicon and re.match(r"apple\b|m\d", c["name"], re.I):
            c["vendor"] = "Apple"
    seen, out = set(), []
    for c in cards:
        key = (c["vendor"], c["name"])
        if key in seen:
            continue
        seen.add(key)
        if c.get("problem"):
            c["plan"], c["why"] = None, c["problem"]
        else:
            c["plan"], c["why"] = plan_for(c)
        out.append(c)
    return out


def best_card(cards):
    """The card the GPU pack should be installed for: the first one with a plan, Nvidia/Apple first."""
    ok = [c for c in cards if c.get("plan")]
    return ok[0] if ok else None


if __name__ == "__main__":
    for c in list_gpus(sys.platform == "darwin"):
        print(json.dumps(c, indent=1))
