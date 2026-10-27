# Changelog

AIDM uses [Semantic Versioning](https://semver.org). Each release names the ODX version it is based on.

## Unreleased

### Changed

- Both images are built for modern CPUs. Every C++ tool, dependencies included, is compiled for x86-64-v3 (AVX2, FMA, BMI2) on x86-64 and for ARMv8.2-A on arm64, instead of Nehalem (2008, no AVX) and ARMv8.0-A. This reverses "The CPU image is built for a generic CPU" from 0.1.0. See "Hardware we build for" in `README.md` and [ADR 0001](docs/adr/0001-build-for-modern-cpus.md).
- The minimum CPU is now Intel Haswell (2013) or AMD Zen (2017) on x86-64, and ARMv8.2-A on arm64 (AWS Graviton 2, Ampere Altra, Apple M1, Raspberry Pi 5). Older CPUs, including the Raspberry Pi 4, are not supported.
- The new target does not make the engine faster. On an AMD Ryzen 9 9900X, against 0.1.0 built from the same commit, a full run takes the same time: 124.6 s against 125.1 s on Brighton Beach (18 images) and 302.3 s against 305.3 s on aidm-vineyard-small (73 images). Most stages change by less than 4%. The openmvs stage is 0.2% slower on aidm-vineyard-small and 6% to 8% slower on Brighton Beach (about 2 s of 29 s).
- `configure.sh install` replaces gcc and g++ with the wrappers in `docker/` when `CPU_TARGET_INSTALL=YES` is set. The old name, `PORTABLE_INSTALL`, no longer does anything.
- `--sky-removal` needs less memory, and its sky masks may differ from 0.1.0 by a few pixels on the mask edge. The sky filter runs in float32 and frees the full image as soon as it can, so a 20 megapixel image needs 0.75 GiB instead of 1.8 GiB. It also runs only as many images at once as fit in half the free memory. On Brighton Beach with 24 threads, the dataset stage peaks at 4.2 GiB instead of 10.4 GiB. Its masks there are byte-identical, but in a synthetic worst case 5 pixels out of 54 million flip, all on the mask edge.
- The build pins dem2points to commit `4527122` and Abseil to the `20260107.1` release, instead of following branches. Both are the code the branches pointed to, so the built code does not change. The Ceres version check now asks for 2.2.0, the version the build downloads, and two build settings that nothing read (`BUILD_opencv_gpu`, `GPU_INSTALL`) are gone.
- Static tiles for orthophotos and DEMs are generated in parallel, with up to `--max-concurrency` workers. On Brighton Beach, orthophoto tiles take 0.55 s instead of 2.73 s and DSM tiles 1.08 s instead of 2.23 s.
- The DEM merge of a split dataset blends blocks in parallel, with up to `--max-concurrency` workers. On Brighton Beach with `--split 9`, the DSM merge takes 0.69 s instead of 1.07 s. Together with parallel tiles, the merge stage takes 11.0 s instead of 16.3 s.
- Frame extraction finds the SRT or GPX row for each frame by bisection instead of scanning every row. A 55-minute clip with a 100,000-row SRT extracts in 20.5 s instead of 62 s.
- Reading image metadata looks for the XMP packet in the first 1 MiB of each file and reads the rest only when the packet is not there. Loading the 18 Brighton Beach images reads 20.5 MB instead of 66.5 MB.
- With `--video-limit`, only the frames that are kept get their final JPEG encode. A 5-minute 4K clip extracts in 107 s instead of 132 s at the default limit of 500, and in 96 s instead of 131 s at a limit of 100.
- GPS to UTM conversion and `geo.txt` reprojection build one coordinate transformer per run instead of one per image or line. Stage times do not change measurably.
- Image builds no longer recompile the C++ dependencies when only Python code, tests or docs change. The release workflow keeps the layers in a registry build cache, `ghcr.io/aidronemap/aidm-buildcache`.
- The smoke tests and the Modal GPU test process [aidm-vineyard-small](https://huggingface.co/datasets/AIDroneMap/aidm-vineyard-small) v1 instead of Brighton Beach: 73 images of 20 megapixels instead of 18, pinned by commit and checked by SHA-256 on every run. They also check that the orthophoto is georeferenced where the images were taken. With a full build of the C++ dependencies, a release dry run takes 75 to 105 minutes instead of 65. The GPU test costs about USD 0.21 instead of 0.07.

### Added

- The engine checks the CPU at start. On a CPU below the minimum it stops with one line, for example `AIDM needs a CPU with AVX2 and FMA (2013 or later). This CPU does not have them.`, instead of crashing later with an illegal instruction.

### Fixed

- JPEG XL images get their EXIF metadata read again: make, model, GPS position, orientation and capture time. ODMExifRead, the EXIF library the engine uses since ODX dropped upstream ExifRead, does not know the JPEG XL container and returned no tags, so JPEG XL datasets lost their GPS positions (#12). The engine now finds the Exif box of the container itself and hands its TIFF block to the library.
- The arm64 image now has OpenCV's NEON_DOTPROD and NEON_FP16 kernels, which OpenCV runs on CPUs that support them. The compiler wrappers dropped the `-march=armv8.2-a+dotprod` and `-march=armv8.2-a+fp16` flags OpenCV builds them with, so the arm64 build had no dispatched kernels at all (#10). The smoke test on the GitHub arm64 runner shows no measurable change: opensfm took 51.8 s and 51.5 s before the fix and 51.5 s after.
- The release workflow accepts the manifest digest as proof that the pushed image is the tested one. Docker 29 on the GitHub runners uses the containerd image store, which reports that digest instead of the config digest, so every GPU image check failed from 26 October 2026.
- The cut and feathered orthophotos of split-merge submodels (`odm_orthophoto_cut.tif`, `odm_orthophoto_feathered.tif`) are computed in windows of about 4 megapixels instead of whole bands. On a 181 megapixel orthophoto the engine process peaks at 1.5 GiB instead of 4.1 GiB in the orthophoto stage, and the files are byte-identical.
- Orthophoto merge in split-merge runs stops reading the remaining submodels in its first pass once a block is fully covered. The check compared the covered pixel count with the block width instead of the block area, so it almost never stopped early. The merged pixels do not change.
- With `--bg-removal`, a failure to make a mask for one image logs "Cannot generate mask for <image>" again. Before, it raised a `NameError` and stopped the dataset stage.
- When the point classification model cannot be downloaded, the warning names the model URL. Before, a `NameError` hid the cause behind a generic "Cannot classify using OpenPointClass" warning.
- The GPU image no longer starts `PYTHONPATH` with an empty entry, which made Python import from the current directory.
- When PoissonRecon fails and `odm_meshing` retries with half the threads, the warning names the thread count that failed. It used to log twice the new count, so a failure with 7 threads read "failed with 6 threads" and one with 3 threads read "failed with 2 threads".

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
