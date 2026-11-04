# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""Build QuadriFlow (the quad remesher of the experimental Quad patches engine) from its source.

    python3 tools/build_quadriflow.py            (in the Twoo STEP CAD Toolbox folder)

Only needed on a computer for which tostep/plugins/mesh2step/bin/ has no QuadriFlow that runs (it ships for 64-bit
Intel / AMD Linux with glibc 2.38 or newer: Ubuntu 24.04, Linux Mint 22 and later). Without it the Quad patches engine uses Instant Meshes instead, if that is installed
(`pip install pynanoinstantmeshes`), and otherwise hands the part to Classic.

Needs CMake 3.1+ and a C++14 compiler (Linux: `sudo apt install cmake g++`; macOS: Xcode's command
line tools and `brew install cmake`; Windows: Visual Studio's C++ workload and CMake). Downloads,
from GitHub only, exactly these versions:

  QuadriFlow  github.com/hjwdzh/QuadriFlow      commit 810b7a0967c35b0dc85b4464e3835e26a756c967  (BSD-3)
  Eigen       github.com/libigl/eigen (mirror)  commit 1f05f51517ec4fd91eed711e0f89e97a7c028c0e  (MPL2 parts only)
  Boost       github.com/boostorg/boost releases boost-1.86.0-b2-nodocs.tar.xz, SHA-256 checked (headers only)
(by git when it is installed, otherwise as GitHub's zip archives)

and builds with -DBUILD_FREE_LICENSE=ON (Eigen restricted to its MPL2 code). The program is put
in tostep/plugins/mesh2step/bin/<system>-<machine>/ (for example tostep/plugins/mesh2step/bin/linux-x86_64/quadriflow), where the app looks
for it. The build folder is a temporary one and is removed afterwards. Nothing else changes.
"""
import hashlib
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
QF = ("https://github.com/hjwdzh/QuadriFlow", "810b7a0967c35b0dc85b4464e3835e26a756c967")
EIGEN = ("https://github.com/libigl/eigen", "1f05f51517ec4fd91eed711e0f89e97a7c028c0e")
BOOST = ("https://github.com/boostorg/boost/releases/download/boost-1.86.0/boost-1.86.0-b2-nodocs.tar.xz",
         "a4d99d032ab74c9c5e76eddcecc4489134282245fffa7e079c5804b92b45f51d")


def platform_dir():
    return "%s-%s" % ("windows" if sys.platform.startswith("win") else sys.platform.rstrip("0123456789"),
                      platform.machine().lower().replace("amd64", "x86_64"))


def fetch(url, sha, dst):
    print("Downloading", url)
    with urllib.request.urlopen(url, timeout=300) as r, open(dst, "wb") as fh:
        shutil.copyfileobj(r, fh)
    if sha:
        got = hashlib.sha256(open(dst, "rb").read()).hexdigest()
        if got != sha:
            raise SystemExit("Checksum does not match for %s (got %s). Nothing was built." % (url, got))


def get_source(repo, commit, into):
    """One commit of a GitHub repository, by git when it is installed (only that commit is
    fetched), otherwise as GitHub's zip archive of that commit."""
    dst = os.path.join(into, repo.rsplit("/", 1)[1])
    if shutil.which("git"):
        print("Fetching", repo, commit[:12])
        os.makedirs(dst)
        for cmd in (["git", "init", "-q"], ["git", "remote", "add", "origin", repo],
                    ["git", "fetch", "-q", "--depth", "1", "origin", commit], ["git", "checkout", "-q", "FETCH_HEAD"]):
            subprocess.run(cmd, cwd=dst, check=True)
        return dst
    z = dst + ".zip"
    fetch(repo + "/archive/" + commit + ".zip", None, z)
    with zipfile.ZipFile(z) as zf:
        zf.extractall(into)
        top = zf.namelist()[0].split("/")[0]
    return os.path.join(into, top)


def main():
    if not shutil.which("cmake"):
        print("CMake was not found. Install CMake and a C++ compiler first (see the top of this file).")
        return 1
    tmp = tempfile.mkdtemp(prefix="e2s_qf_build_")
    try:
        src = get_source(*QF, tmp)
        eigen = get_source(*EIGEN, tmp)
        tb = os.path.join(tmp, "boost.tar.xz")
        fetch(*BOOST, tb)
        print("Unpacking the Boost headers…")
        with tarfile.open(tb) as t:
            members = [m for m in t.getmembers() if "/boost/" in m.name and m.isfile()]
            try:
                t.extractall(tmp, members=members, filter="data")   # (Python 3.12+: no links out of the folder)
            except TypeError:
                t.extractall(tmp, members=members)
        boost = os.path.join(tmp, "boost-1.86.0")
        build = os.path.join(tmp, "build")
        os.makedirs(build)
        win = sys.platform.startswith("win")
        cfg = ["cmake", src, "-DCMAKE_BUILD_TYPE=Release", "-DBUILD_FREE_LICENSE=ON",
               "-DEIGEN_INCLUDE_DIR=" + eigen, "-DBoost_INCLUDE_DIR=" + boost]
        if not win:
            cfg.append("-DCMAKE_CXX_FLAGS=-include cstdint")            # newer compilers need it said
            if sys.platform.startswith("linux"):
                # the GCC runtime built in (its licence exception allows it), the C library not
                cfg.append("-DCMAKE_EXE_LINKER_FLAGS=-static-libstdc++ -static-libgcc")
        subprocess.run(cfg, cwd=build, check=True)
        subprocess.run(["cmake", "--build", build, "--config", "Release", "-j", "2"], check=True)
        exe = "quadriflow.exe" if win else "quadriflow"
        built = next((os.path.join(d, exe) for d, _, fs in os.walk(build) if exe in fs), None)
        if not built:
            print("The build finished but no %s was made." % exe)
            return 1
        out = os.path.join(ROOT, "tostep", "plugins", "mesh2step", "bin", platform_dir())
        os.makedirs(out, exist_ok=True)
        shutil.copy2(built, os.path.join(out, exe))
        print("Done:", os.path.join(out, exe))
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
