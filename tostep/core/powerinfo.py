# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""
powerinfo (0.11.3) - is the computer's own power setting holding the processor back?

A laptop on battery, in a power-saving mode, with turbo boost off, or running hot can finish a
conversion several times slower than the same laptop plugged in on "performance". The converter
cannot change that (and should not), but it can say so: one line at the start, and the processor's
clock speed in the sign-of-life lines, so a slow run says why.

Everything is best effort and read-only: a missing file, tool or permission just leaves that fact out.

    snapshot()      -> dict of what could be read
    advice(snap)    -> plain sentences about anything that is slowing the computer down (possibly none)
    clock_text()    -> "CPU 3.1 of 4.1 GHz" or "" (cheap enough for a sign-of-life line)
"""
from __future__ import annotations

import glob
import os
import re
import subprocess
import sys

_CPUFREQ = "/sys/devices/system/cpu/cpu0/cpufreq/"


def _read(path):
    try:
        with open(path) as fh:
            return fh.read().strip()
    except OSError:
        return None


def _run(cmd, timeout=3):
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return out.stdout if out.returncode == 0 else ""
    except Exception:  # noqa
        return ""


def _freq_mhz():
    """(average current, highest allowed) in MHz over the cores, or (None, None)."""
    try:
        import psutil
        f = psutil.cpu_freq()
        if f and f.max:
            return float(f.current), float(f.max)
    except Exception:  # noqa
        pass
    cur = []
    for p in glob.glob("/sys/devices/system/cpu/cpu[0-9]*/cpufreq/scaling_cur_freq"):
        v = _read(p)
        if v and v.isdigit():
            cur.append(int(v) / 1000.0)
    mx = _read(_CPUFREQ + "cpuinfo_max_freq")
    if cur and mx and mx.isdigit():
        return sum(cur) / len(cur), int(mx) / 1000.0
    return None, None


def _on_battery():
    try:
        import psutil
        b = psutil.sensors_battery()
        if b is not None and b.power_plugged is not None:
            return not b.power_plugged
    except Exception:  # noqa
        pass
    for p in glob.glob("/sys/class/power_supply/*/online"):
        t = _read(os.path.join(os.path.dirname(p), "type"))
        if t == "Mains":
            return _read(p) == "0"
    return None


def snapshot():
    s = {"platform": sys.platform, "on_battery": _on_battery()}
    cur, mx = _freq_mhz()
    s["mhz"], s["max_mhz"] = cur, mx
    if sys.platform.startswith("linux"):
        s["driver"] = _read(_CPUFREQ + "scaling_driver")
        s["governor"] = _read(_CPUFREQ + "scaling_governor")
        s["epp"] = _read(_CPUFREQ + "energy_performance_preference")
        nt = _read("/sys/devices/system/cpu/intel_pstate/no_turbo")
        s["turbo_off"] = (nt == "1") if nt in ("0", "1") else None
        if s["turbo_off"] is None:
            b = _read("/sys/devices/system/cpu/cpufreq/boost")
            s["turbo_off"] = (b == "0") if b in ("0", "1") else None
        mp = _read("/sys/devices/system/cpu/intel_pstate/max_perf_pct")
        s["max_perf_pct"] = int(mp) if mp and mp.isdigit() else None
        out = _run(["powerprofilesctl", "get"], 2).strip()
        s["profile"] = out or None
    elif sys.platform == "win32":
        out = _run(["powercfg", "/getactivescheme"], 4)
        m = re.search(r"\(([^)]+)\)\s*$", out.strip())
        s["profile"] = m.group(1) if m else None
    elif sys.platform == "darwin":
        out = _run(["pmset", "-g"], 3)
        m = re.search(r"lowpowermode\s+(\d)", out)
        s["low_power"] = (m.group(1) == "1") if m else None
    return s


def advice(s=None, busy=False):
    """Plain sentences, most important first; an empty list when nothing is in the way.
    busy=True: the processor is working now, so a low clock speed means something (idle, it is low anyway)."""
    s = s or snapshot()
    out = []
    if s.get("on_battery"):
        out.append("The computer is running on battery, and most laptops slow the processor down then. "
                   "Plug it in for a long conversion.")
    prof = (s.get("profile") or "").lower()
    if prof in ("power-saver", "power saver", "best power efficiency") or s.get("low_power"):
        how = {"linux": " (Linux: `powerprofilesctl set performance`, or Power Management in the system settings)",
               "win32": " (Windows: Settings > System > Power, choose Best performance)",
               "darwin": " (Mac: System Settings > Battery, turn Low Power Mode off)"}
        out.append("The power mode is set to saving power, which holds the processor back."
                   + next((v for k, v in how.items() if s["platform"].startswith(k)), ""))
    gov, epp, drv = s.get("governor"), s.get("epp"), s.get("driver")
    if gov == "powersave" and drv not in (None, "intel_pstate", "amd-pstate", "amd-pstate-epp"):
        out.append("The processor's governor is 'powersave', which keeps the clock low. "
                   "Set it to 'performance' or 'ondemand'.")
    if epp in ("power", "balance_power"):
        out.append(f"The processor is told to favour saving power ('{epp}'), so it ramps up slowly. "
                   "A 'balanced' or 'performance' power profile fixes that.")
    if s.get("turbo_off"):
        out.append("Turbo boost is switched off, so the processor can't go above its base clock "
                   "(often half its top speed).")
    if s.get("max_perf_pct") is not None and s["max_perf_pct"] < 100:
        out.append(f"The processor is capped at {s['max_perf_pct']} % of its performance.")
    cur, mx = s.get("mhz"), s.get("max_mhz")
    if busy and cur and mx and mx > 0 and cur < 0.45 * mx:
        out.append(f"The processor is running at {cur / 1000:.1f} of {mx / 1000:.1f} GHz; that is "
                   "idle speed, or a power limit or heat is holding it back.")
    return out


def clock_text():
    cur, mx = _freq_mhz()
    if not cur or not mx:
        return ""
    return f"CPU {cur / 1000:.1f} of {mx / 1000:.1f} GHz"
