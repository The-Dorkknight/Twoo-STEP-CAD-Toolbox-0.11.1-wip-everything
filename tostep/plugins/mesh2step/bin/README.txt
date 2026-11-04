Programs the app runs, one folder per system (<system>-<machine>):

  linux-x86_64/quadriflow   QuadriFlow, the quad remesher of the experimental Quad patches engine
                            (tostep/plugins/mesh2step/quadfit.py), for 64-bit Intel/AMD Linux with glibc 2.38 or newer
                            (Ubuntu 24.04, Linux Mint 22 and later). On an older Linux it cannot start;
                            the engine then says so and uses Instant Meshes or Classic instead, and
                            tools/build_quadriflow.py builds one that fits.

For other systems build it with tools/build_quadriflow.py (needs CMake and a C++ compiler), or
install Instant Meshes instead (pip install pynanoinstantmeshes); without either, the Quad patches
engine hands parts to Classic.

How linux-x86_64/quadriflow was built (21 October 2026, Ubuntu 24.04, g++ 13):
  QuadriFlow  https://github.com/hjwdzh/QuadriFlow  commit 810b7a0967c35b0dc85b4464e3835e26a756c967
  Eigen       https://github.com/libigl/eigen       commit 1f05f51517ec4fd91eed711e0f89e97a7c028c0e
  Boost       boost-1.86.0 (headers only), SHA-256 a4d99d032ab74c9c5e76eddcecc4489134282245fffa7e079c5804b92b45f51d
  cmake -DCMAKE_BUILD_TYPE=Release -DBUILD_FREE_LICENSE=ON -DCMAKE_CXX_FLAGS="-include cstdint"
        -DCMAKE_EXE_LINKER_FLAGS="-static-libstdc++ -static-libgcc"   (as tools/build_quadriflow.py does)
  SHA-256 of the program: 56ab17779070069d61aa2ab4ff0c0e970531d4b91b608adf62e730b2e77a09f5
The program is unchanged QuadriFlow (no source edits). Its licences follow; the source of every
part is at the addresses above (Eigen's MPL2 source: https://github.com/libigl/eigen).

============================================================================= QuadriFlow (BSD-3)
Copyright (c) 2018 Jingwei Huang, Yichao Zhou, Matthias Niessner,
Jonathan Shewchuk and Leonidas Guibas. All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

3. Neither the name of the copyright holder nor the names of its contributors
   may be used to endorse or promote products derived from this software
   without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

You are under no obligation whatsoever to provide any bug fixes, patches, or
upgrades to the features, functionality or performance of the source code
("Enhancements") to anyone; however, if you choose to make your Enhancements
available either publicly, or directly to the authors of this software, without
imposing a separate written license agreement for such Enhancements, then you
hereby grant the following license: a non-exclusive, royalty-free perpetual
license to install, use, modify, prepare derivative works, incorporate into
other computer software, distribute, and sublicense such enhancements or
derivative works thereof, in binary and source code form.

============================================================================= parts built into it
Eigen (Mozilla Public License 2.0; built with EIGEN_MPL2_ONLY, so only its MPL2 parts):
  https://www.mozilla.org/en-US/MPL/2.0/ ; source as above.
LEMON 1.3.1 (Boost Software License 1.0), Copyright (C) 2003-2012 Egervary Jeno Kombinatorikus
  Optimalizalasi Kutatocsoport (EGRES).
MapleCOMSPS_LRB / MiniSat (MIT), Copyright (c) 2003-2006 Niklas Een, Niklas Sorensson;
  (c) 2007-2010 Niklas Sorensson; (c) 2015 Chanseok Oh. Permission is hereby granted, free of
  charge, to any person obtaining a copy of this software and associated documentation files (the
  "Software"), to deal in the Software without restriction, including without limitation the
  rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the
  Software, and to permit persons to whom the Software is furnished to do so, subject to the
  following conditions: The above copyright notice and this permission notice shall be included
  in all copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS",
  WITHOUT WARRANTY OF ANY KIND.
pcg32 (Apache License 2.0), Melissa O'Neill / Wenzel Jakob: http://www.apache.org/licenses/LICENSE-2.0
Intel parallel stable sort (BSD-3), Copyright (C) 2014 Intel Corporation.
Boost graph headers (Boost Software License 1.0): https://www.boost.org/LICENSE_1_0.txt
The GCC C++ runtime (libstdc++, libgcc) is linked in statically under the GCC Runtime Library
Exception; the C library (glibc) is the system's own, linked dynamically.
