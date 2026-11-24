# Windows Profile 7 compositor

The application launches an isolated Python process for two-layer composition. The
process explicitly loads the official x64 AVX2 FelBaker 1.0.0 and FFMS2 5.0 plug-ins
into VapourSynth R73. The installer carries the unmodified upstream binaries and the
license files below. Archive hashes and the exact extraction recipe are in
`tools/fetch_fel.py` in the LumaFrame source release.

| Component | License file included in the installer | Corresponding source and build instructions |
| --- | --- | --- |
| FelBaker 1.0.0, including upstream libdovi | `fel-FelBaker-LICENSE.txt` (GPL v3, Chire, derived from erazortt's DoViBaker), `libdovi-LICENSE.txt` | [FelBaker source and build workflow](https://github.com/bbeny123/felbaker/tree/1.0.0), [libdovi revision used by the publisher](https://github.com/quietvoid/dovi_tool/tree/82384bc) |
| FFMS2 5.0 | `fel-ffms2-GPLv3.txt` | [5.0 source and compilation scripts](https://github.com/FFMS/ffms2/tree/5.0), [upstream binary license statement](https://github.com/FFMS/ffms2/blob/5.0/COPYING) |
| VapourSynth R73 | `fel-vapoursynth-LGPL21.txt` | [VapourSynth R73 complete source](https://github.com/vapoursynth/vapoursynth/tree/R73), [binary distribution](https://github.com/vapoursynth/vapoursynth/releases/tag/R73) |
| CPython 3.12.10 Windows embeddable distribution | `bin/fel/LICENSE.txt` | [Python release and sources](https://www.python.org/downloads/release/python-31210/) |

The redistributed VapourSynth archive includes the Microsoft C++ runtime. The
original release notes identify the minimum processor as AVX2 for this FelBaker
binary. Upstream test footage is available separately from the FelBaker source
repository and is not included in the software.
