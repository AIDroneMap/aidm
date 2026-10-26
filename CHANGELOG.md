# Changelog

AIDM uses [Semantic Versioning](https://semver.org). Each release names the ODX version it is based on.

## Unreleased

### Changed

- Both images are built for modern CPUs. Every C++ tool, dependencies included, is compiled for x86-64-v3 (AVX2, FMA, BMI2) on x86-64 and for ARMv8.2-A on arm64, instead of Nehalem (2008, no AVX) and ARMv8.0-A. This reverses "The CPU image is built for a generic CPU" from 0.1.0. See "Hardware we build for" in `README.md` and [ADR 0001](docs/adr/0001-build-for-modern-cpus.md).
- The minimum CPU is now Intel Haswell (2013) or AMD Zen (2017) on x86-64, and ARMv8.2-A on arm64 (AWS Graviton 2, Ampere Altra, Apple M1, Raspberry Pi 5). Older CPUs, including the Raspberry Pi 4, are not supported.
- OpenSfM, OpenMVS, mvs-texturing, FPCFilter, RenderDEM and odm_orthophoto are built with link-time optimisation.
- `configure.sh install` replaces gcc and g++ with the wrappers in `docker/` when `CPU_TARGET_INSTALL=YES` is set. The old name, `PORTABLE_INSTALL`, no longer does anything.

### Added

- The engine checks the CPU at start. On a CPU below the minimum it stops with one line, for example `AIDM needs a CPU with AVX2 and FMA (2013 or later). This CPU does not have them.`, instead of crashing later with an illegal instruction.

## 0.1.0

The first AIDM release, based on the ODX 3.8.4 engine. Processing is unchanged, so results match ODX 3.8.4, and this release makes no performance or accuracy claims. What is new is how the engine is built, tested and shipped.

### Highlights

- **Every image is tested before it is published.** Each release image processes a real survey of 18 drone images from start to finish, and is published only if the orthophoto, elevation model, point cloud and textured model all come out. This covers the CPU image on x86-64 and arm64, and the GPU image on a real NVIDIA GPU as well as on a machine without one.
- **What you pull is what was tested.** The release workflow checks that each published image is the exact image that passed, and the release notes list the digests.
- **One GPU image for any machine.** The GPU image uses CUDA when an NVIDIA GPU is present and falls back to the CPU when it is not. Before, it needed an NVIDIA GPU to start at all.
- **You always know what you run.** Logs, `--version`, the PDF report and GeoTIFF metadata show `AIDM 0.1.0 (ODX 3.8.4)`, and AIDM API reports both versions in `GET /info`.

### Changed

- The engine calls itself AIDM. The log banner, `--version`, `--help`, the PDF report and the Software tag of GeoTIFF outputs show `AIDM 0.1.0 (ODX 3.8.4)`.
- `VERSION` holds the AIDM version, `0.1.0`. The ODX version it is based on is in the new `UPSTREAM_VERSION` file.
- `log.json` reports `"engine": "AIDM"` and `0.1.0` in `version` and `odmVersion`. A new field, `upstreamVersion`, holds `ODX 3.8.4`.
- A new `ENGINE` file holds `aidm`. AIDM API reports it as the engine name in `GET /info`.
- `benchmark.txt` and the EXIF Software tag of frames extracted from video say AIDM.
- The CPU image is built for a generic CPU, as the GPU image already was. It runs on machines older than the one that built it.
- Both images carry OCI labels for source, version, licence, title and description.
- The GPU image also runs on machines without a GPU, on the CPU. It ships the CUDA stub driver library as a fallback for OpenMVS; the real driver replaces it when the container runs with `--gpus`.
- Every release run tests the GPU image on a real NVIDIA GPU on Modal and checks that the engine uses it. The images are only tagged when this test passes.

### Removed

- The Windows and macOS build files inherited from ODX, which were not maintained: `*.bat`, `innosetup.iss`, `configure_macos.sh`, `portable.Dockerfile`, `vcpkg.json` and `vcpkg-requirements.txt`. The CPU `Dockerfile` now builds what `portable.Dockerfile` built.

### Fixed

- The build downloads Ceres Solver from its GitHub release first, and falls back to ceres-solver.org. The site timed out on the build runners and stopped every image build.

### Not changed

- Command-line options, `settings.yaml` keys and defaults, output files and folders, stage names and Python modules are the same as in ODX 3.8.4.

### Known limitations

- No performance or accuracy changes compared to ODX 3.8.4 yet.
- Images are for Linux only. The CPU image runs on x86-64 and arm64, the GPU image on x86-64 only.
- `configure.py`, the Windows configure script from ODX, is still in the repository. It no longer works, because the vcpkg files it reads are gone.
