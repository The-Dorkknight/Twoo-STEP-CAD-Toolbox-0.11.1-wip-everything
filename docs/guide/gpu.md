**WORK IN PROGRESS: MANY FEATURES MAY BE BROKEN** (0.11.1-wip)

# Graphics cards

*Short version:* a graphics card makes **one step** faster, in **one preset**. Nothing here is needed for a good
result. If you want to try it, the **Graphics card** box on the Convert tab (always visible, not hidden in Advanced)
shows every card the computer lists, what each needs, and a button that does it.

## What the card speeds up (and what it does not)
- **Uses the card:** the *shape scoring* of the **v2 fitter**: trying hundreds of plane / cylinder / sphere guesses against a sample of
  the mesh. The **Fine** preset (Standard, then v2, keep the better) and the **Second pass** / **v2 fitter** engines run it.
- **Does not use the card:** Classic, Staged, Ladder, Quad patches, and so Quick and Standard. Building faces, sewing and writing STEP is
  OpenCASCADE, which only runs on the processor. Jetwash, SCAD → STEP, Hardpoints and Image → STEP do not use it either.
- **First real test (M5 MacBook, 2026-10-29): failed, then fixed.** The scoring asked Apple's GPU for float64, which Metal does not have; that bug was present in every earlier version, so no Mac had ever used its GPU. It is fixed and the test now exercises the real scoring, but it has **not been re-run on the M5 yet**.
- **Measured speed-up on a real card: none yet.** The card's maths was checked against NumPy on PyTorch's CPU build (same answers to 1e-15),
  never timed on a real Apple, Nvidia or AMD card. Press **Test it**: it times a block of scoring on the card against the processor on *your*
  machine and says the ratio. That number is the only honest one.

## Why the whole job is not many times faster
The card only does the v2 fitter's shape scoring. Everything else is OpenCascade on the processor, and in *Fine* on the Extreme profile the first pass and v2 run side by side, so the job lasts as long as the slower one. A 2x on a real Mac is what a big but not total share looks like. Since the next release the sample is uploaded once per round and read back once (fewer waits), which helps on every card; the gain is unmeasured on real hardware.

## What each kind of card needs
| Card | Support | What the installer does |
|---|---|---|
| **Apple Silicon** (M1 … M5) | Yes, through Metal | Installs PyTorch's normal macOS wheel (about 80 MB). |
| **Nvidia, Turing or newer** (RTX 20xx, GTX 16xx, RTX 30/40/50) | Yes, through CUDA | Normal wheel (about 2.5 GB) if the Nvidia driver is 580 or newer; the CUDA 12.6 build if the driver is older (528 or newer). A Blackwell card (RTX 50xx) needs driver 580+. |
| **Nvidia, Pascal / Maxwell / Volta** (GTX 750 … GTX 10xx, Titan V) | Yes, **only with the CUDA 12.6 build** | Installs `torch>=2.6,<2.15` from PyTorch's CUDA 12.6 index. See below. |
| **Nvidia, Kepler or older** (GTX 6xx / 7xx) | No | Says why. |
| **AMD**, built-in (Ryzen "Radeon Graphics") or older separate cards | No | Says why. PyTorch's AMD (ROCm) builds support only some separate Radeon cards and the newest Ryzen AI chips; this installer does not set ROCm up. |
| **Intel** graphics | No | Needs a different PyTorch build (XPU) that this installer does not set up. |

### Why a GTX 1060 was "seen but not selectable"
Before 0.11.1 the tick-box stayed greyed out until a GPU pack was installed, and the pack was the plain `pip install torch`. Since PyTorch 2.8
the plain build no longer contains code for Pascal cards (GTX 10-series): PyTorch can report "CUDA available" and then fail every
operation with *"no kernel image is available for execution on the device"*. The only current build with Pascal code is the **CUDA 12.6**
build, and PyTorch has said **2.14 is the last release to ship it** (2.15 drops Maxwell, Pascal and Volta; building from source is the only
way after that). So the installer now picks that build for those cards and pins `torch<2.15`, and a real test decides whether the card is
"Ready", not the word "available". When 2.14 stops being installable the Pascal route ends; the box will say so.

### Why the Radeon next to it stays idle
A Ryzen 5 laptop's built-in Radeon graphics is not in the list of chips PyTorch's AMD builds support. The Nvidia card is the one that can be used.

## What you see
Each card is a row with a coloured tag:
- **Ready**: the GPU pack is installed and a real test on this card passed.
- **Needs support installed**: *Install support* downloads the right PyTorch build into the toolbox's own Python (nothing system-wide), tests the card, and says the result.
- **Installed support does not work for it**: a PyTorch that cannot use this card is installed (for example the wrong build on a GTX 1060). *Reinstall the right build* removes it and installs the right one.
- **Not usable**: nothing this program can do; the row says why.

**Test it** runs the real check again and times it; **Look again** re-reads the cards (after a driver install). The tick-box
*Use the graphics card where it works* is never greyed out: it is an on/off preference that applies once a card is Ready. The **Potato**
profile never uses the card. Command line: `tostep gpu` (list), `tostep gpu --install`, `tostep gpu --test`, and `--no-gpu` on a conversion.

## If a card is "seen" but a conversion still ran without it
The worker runs the same real test before using the card; if it fails, the conversion silently uses the processor and **always finishes**.
The job log says "no GPU" for that reason. A card problem can slow a conversion; it cannot make one fail.

## Sources (checked October 2026)
- PyTorch 2.12 release notes: CUDA 13.0 is the default PyPI build; CUDA 12.6 stays for Pascal and Volta; CUDA 12.8 deprecated. <https://pytorch.org/blog/pytorch-2-12-release-blog/>
- Notice: CUDA 12.6 wheels end after PyTorch 2.14 (drops Maxwell / Pascal / Volta). <https://dev-discuss.pytorch.org/t/notice-cuda-12-6-wheels-will-no-longer-be-published-from-pytorch-2-15-drops-maxwell-pascal-volta/3432>
- A real report of the Pascal failure with CUDA 12.8 wheels and the cu126 fix. <https://github.com/jhj0517/Whisper-WebUI/issues/653>
- AMD's list of Ryzen / Radeon chips supported by PyTorch on Windows (Ryzen AI 300/400 series only; older Ryzen 5 graphics not listed). <https://rocm.docs.amd.com/projects/radeon-ryzen/en/latest/docs/compatibility/compatibilityryz/windows/windows_compatibility.html>

*Not verified here:* the installer's commands were never run against PyTorch's download server from the machine this was built on (no
access), and no real graphics card was available. That is why *Test it* exists, and why LTS gate 3 says a person must try it.
