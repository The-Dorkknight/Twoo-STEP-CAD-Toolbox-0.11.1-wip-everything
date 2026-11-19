<!-- Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight) -->
<!-- Licensed under the PolyForm Noncommercial License 1.0.0. No warranty. -->

# Intel laptop graphics: can the toolbox use them? (research note, 11 November 2026)

**Asked because** a Linux Mint laptop with **Intel UHD Graphics 620** (8th-gen Core i5) showed *"Not usable. Intel
graphics need a different PyTorch build (XPU) that this installer does not set up"*, and most hobbyists have a
7th–12th gen Core i5 laptop.

## 1. Can PyTorch use those graphics at all?

**No.** PyTorch's Intel build (XPU) lists, for laptops and desktops, only Intel **Arc** cards (A- and B-series) and
**Core Ultra** chips with built-in Arc graphics (Meteor Lake, Arrow Lake-H, Lunar Lake, Panther Lake). On Intel's own
support forum, asked about UHD Graphics, Intel answered that Raptor Lake "iGPUs and older are not and will not be
supported". So UHD 620, UHD 630, Iris Plus and Iris Xe (6th to 14th gen Core) are out, whatever the installer does.

The other route would be OpenCL, which those chips do run on Linux (Intel's compute runtime, the "legacy" branch for
Gen8–Gen11, `intel-opencl-icd` on Ubuntu/Mint), with a hand-written scoring kernel instead of PyTorch. That needs a
one-time `sudo apt install` by the user and a second GPU code path to keep working. Section 2 decides whether it is
worth it.

## 2. Would it help?

The card only does one thing in this program: the v2 fitter's **shape scoring** (plane, cylinder and sphere guesses
tested against a sample of triangles). Everything else is OpenCascade on the processor. Measured with the card off
(`scripts/intel_gpu/score_share.py`, cProfile, 2-core Linux machine, engine `v2`, automatic tolerance):

| Mesh | Triangles | Whole v2 run | Shape scoring | Share |
|---|---|---|---|---|
| Ridged knob (`tests/data/knob_plain.stl`) | 2,958 | 9.4 s | 0.4 s | 5 % |
| Coarse blob | 5,120 | 41.4 s | 0.6 s | 1 % |
| Blob cut flat | 15,338 | 23.8 s | 2.2 s | 9 % |
| Organic test body | 81,920 | 46.9 s | 1.1 s | 2 % |
| Cat-like figure | 64,950 | 665.9 s | 1.1 s | **0.2 %** |

Even a card that scored instantly would save 0.2 % to 9 % of these runs. A UHD 620 is far from instant (about 0.4
TFLOPS on paper, sharing memory with the processor), so the real saving would be smaller still. **Not worth building:**
a second GPU code path and a `sudo` driver step for a few percent at best.

## 3. What this changes

- The message for Intel graphics now says plainly that UHD / Iris graphics cannot be used and why little is lost; Arc
  graphics get "supported by PyTorch's Intel build, not set up yet".
- **The 2× reported on the M5 does not fit these numbers.** Version 1.2 of the paper read it as "scoring is at least
  half the run". With scoring at 0.2–9 %, the 2× must have come from something else (the two runs probably differed
  in profile, preset, input or a warm cache). Paper version 1.4 says so under D16. To settle it: run the cat twice on
  the Mac, card on and off, same profile and preset, and compare the logs' timings.
- **What would help a 7th–12th gen laptop:** the time is OpenCascade building and checking faces (the face fits, the
  plate filler, sewing). The Potato profile's triangle ceiling (0.11.2) already targets that; profiling those steps is
  the next place to look.

## Sources
- PyTorch, *Getting Started on Intel GPU* (docs.pytorch.org, notes/get_start_xpu, version 2.15): supported hardware.
- Intel Community, *Intel UHD Graphics support for PyTorch* (community.intel.com, thread 1632158): "not and will not be supported".
