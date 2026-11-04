# Windows engine 0.3.0: what the release ships and how it is proven

This note records the decisions of the grilling session of 4 November 2026 with the maintainer, the facts they rest on, and what the four specs and their tickets take from it. It does not replace the specs. The words it uses are defined in `GLOSSARY.md`. The decisions that close a door are ADRs 0005, 0006 and 0007; this note holds the scope around them.

## What 0.3 is

A pilot on Windows downloads AIDM UI, opens it, and the setup installs and starts an engine on that computer, CPU or CUDA. No Docker, no WSL, no terminal, nothing installed by hand. The path is the one a Mac pilot has today.

It is one coordinated release in the order engine 0.3.0, aidm-api 2.8.0, aidm-ui 0.3.0 (ADR 0007). A fourth repository, `AIDroneMap/aidm-windows-deps`, is created for it and releases before the engine does (ADR 0005).

Issue #15 is reopened for Windows only. Native engines for Linux and macOS stay in it, postponed.

## What exists today

Verified on 4 November 2026: engine master at 469b327c, aidm-api master at fbe684f (release 2.7.0), aidm-ui main at 6c9ab7e (release 0.2.1).

The engine:

- `README.md` line 107 says there are no native Windows or macOS builds. `AGENTS.md` line 33 says the inherited Windows files were removed in 0.1.0 except `configure.py`, which is not to be edited.
- `configure.py` is Windows-only and downloads a vcpkg export, the VC++ runtime installer, a Python embeddable package and Inno Setup from a release of a reference project (lines 27, 159, 171, 186). It expects `vcpkg-requirements.txt` and `innosetup.iss`, which do not exist. `run.bat`, `win32env.bat`, `vcpkg.json` and the Windows workflows were deleted in 0.1.0 (`44cafea6`, `d6814c0c`).
- `SuperBuild/CMakeLists.txt` keeps its `WIN32` branches: the vcpkg toolchain (line 18), the Python home, the GDAL wheel location and the copy of the vcpkg and CUDA runtime DLLs into `install/bin` (lines 96 to 119), and PoissonRecon through its Visual Studio project (line 184). No tool is left out on Windows; `External-Entwine.cmake` only drops one compiler flag there.
- `External-OpenMVS.cmake` line 45 downloads a stub `nvcuda.dll` without a hash. `External-ExifTool.cmake` line 11 downloads ExifTool from the same reference release. `requirements.txt` line 17 takes a GDAL wheel from it. `External-Obj2Tiles.cmake` downloads a prebuilt Windows zip from a reference project's release.
- OpenCV 4.12.0 and Ceres 2.2.0 are built by the SuperBuild itself. `External-OpenSfM.cmake` line 8 looks for OpenCV under `x64/vc17/lib` while `External-OpenCV.cmake` installs it under `lib`. That is a defect the first Windows build will meet.
- `modules/gpu.py` lines 14 to 31 load the system `nvcuda.dll` on Windows and log `CUDA drivers detected` or `No CUDA drivers detected`. `modules/system.py` lines 56 to 59 and `modules/config.py` lines 158 to 161 have their own Windows branches. `run.py` checks the CPU at start.
- Every compiled Python pin in `requirements.txt` has a `cp312` wheel for `win_amd64` on PyPI, except GDAL (source only) and the PDAL bindings, which the SuperBuild builds. `onnxruntime==1.22.1` has one.
- The release workflow builds three Linux images on `ubuntu-24.04` runners, tests the GPU image on Modal, compares golden outputs and publishes a draft release with no assets. `scripts/smoke-test.sh` runs the engine in a container.

The API:

- Its release publishes a Windows zip with `aidm-api.exe` built by Bun, a bundled 7-Zip, `helpers\odm_python.bat` (which calls `win32env.bat` in the engine folder) and its web files. Its release workflow already runs the executable on a hosted Windows runner in test mode.
- On Windows it starts the engine through `run.bat` (`libs/odmRunner.js`), kills a task through `taskkill /pid /T /F` by way of `tree-kill`, and skips `postprocess.sh`. `--socket` is refused on Windows. `app.listen(port)` binds every interface; the UDP progress receiver binds port 6367. `--host` does not exist.
- Its tag run signs the release manifest in the `release` environment and the manifest lists container variants only.

The interface:

- `server/packages/native-package.ts` downloads one archive with resume, checks its SHA-256, lists it with `tar -tzf`, unpacks it with `tar -xzf`, renames the folder into place, writes a config file with `token`, `port`, `parallelQueueProcessing` and `odx_path`, and starts `aidm-api` detached. Its stop sends `SIGTERM` to the process group and, on Windows, to the pid only. Its ownership check reads `/proc` or `ps`.
- `server/machine/check.ts` line 25 fails every Windows machine. `manifest-service.ts` line 330 answers `machine.windows_not_yet`. `shared/api-types.ts` line 296 knows `windows-x64` and no CUDA id for Windows. `server/manifest/schema.ts` ignores fields and variant ids it does not know, so an older interface reads a newer manifest.
- The CPU check reads AVX2 and FMA; the GPU check applies to Linux x64 only and reads `nvidia-smi -L`.
- The Windows executable is built on Linux with the `bun-windows-x64-baseline` target, with a console window and without a signature.
- `test-support/native-archive.ts` builds a fake native package and `fake-native-engine.ts` stands in for the engine.

The hosted `windows-2025` runner has Visual Studio 2022, CMake, Ninja, vcpkg, Python 3.12.10 and 7-Zip, and no CUDA toolkit. A job may run six hours. A release asset may not pass 2 GiB.

## Decisions

### 1. Two variants, a driver floor, no CUDA in the CPU package (ADR 0006)

`windows-x64` has no CUDA file. `windows-x64-cuda` bundles the CUDA 12.9.1 runtime and sets `minimumDriverVersion` to `576.57`. The interface decides: card and driver at or above the floor, CUDA; anything else, CPU with the reason on screen. The CUDA package has no `--no-gpu` fallback of its own.

Both variants use the CPU wheel of ONNX Runtime. GPU ONNX Runtime and cuDNN on Windows are a later ticket.

Windows 11 on x86-64 with AVX2 and FMA, nothing below. The interface checks the CPU through `IsProcessorFeaturePresent` from `kernel32` before any download.

### 2. Prebuilt dependencies in their own repository (ADR 0005)

`AIDroneMap/aidm-windows-deps`, public, with a `vcpkg.json` in manifest mode, a pinned baseline, overrides, a triplet with `/arch:AVX2`, GDAL 3.8.4 as a port, and one workflow that exports. A release holds the export zip, a GDAL wheel built from the port, ExifTool and the Python embeddable package re-hosted, `SHA256SUMS` and the licence list. The VC++ runtime DLLs are copied from the redistributable folder of the build runner's Visual Studio.

The list of ports and overrides starts from the one the reference project published for its 3.8.4 line, credited in that repository's changelog. Version differences against the Linux images (PROJ, Boost, libtiff) are recorded there.

The engine pins one release by tag and SHA-256. The first pin is a new dependency: the maintainer approves it.

### 3. One archive, assembled and signed by the API's tag run (ADR 0007)

`tar.gz`, one top folder, the API's Windows files at the root, the engine under `engine/`. `run.bat` and `win32env.bat` are written again for AIDM. The engine's tag run publishes engine-only archives; the API's tag run wraps them, publishes the native packages and signs the manifest. The interface's unpacker does not change.

### 4. Compiler flags

`/arch:AVX2` across the SuperBuild on MSVC and in the vcpkg triplet. It is ADR 0001 on Windows. There is no second Windows build for older CPUs.

### 5. Where the Windows jobs run

Both variants build on hosted `windows-2025` runners. The CUDA job installs the compiler and runtime libraries of CUDA 12.9.1 from the pinned network installer; it has no GPU. The first engine ticket is a probe: it adds the gated job, fetches the deps export and runs the SuperBuild, and its dry run is read, not fixed. If the SuperBuild does not fit the job limit next to the export, the way out is a larger export (OpenCV and Ceres first). A larger hosted runner is the maintainer's call. A self-hosted runner is the last resort and not in this line.

### 6. Where the Windows jobs appear

`windows-x64` and `windows-x64-cuda` are jobs of `release.yml`, on dry runs and tag runs, gated by a `workflow_dispatch` input `windows` until they are green on master. The last engine ticket removes the gate. The nightly does not run them in 0.3. The CPU job runs the Windows smoke test (CI dataset on a dry run, full dataset on a tag run). The CUDA job checks that the package starts and reports `No CUDA drivers detected`, which is what a runner without a GPU must say.

Every change to `release.yml` is CI code: its own pull request, dry run started by the maintainer.

### 7. The Windows smoke test

`scripts/` gains a Windows smoke test that drives the package the way the interface does: start `aidm-api.exe --config` on the runner, submit the dataset through `pyodm`, wait, check that the outputs exist and that the orthophoto is georeferenced where the images were taken. On the engine's own runs it uses the last released API zip, downloaded by hash, for the test only. The Linux smoke test does not change. No Windows golden hashes in 0.3: outputs may differ between the platforms (ADR 0005), and a diagnostic has to come first.

### 8. How each variant is proven

The CPU variant: the Windows smoke test on the hosted runner, and the maintainer's end-to-end run on a clean Windows 11 without a GPU, through the interface.

The CUDA variant: the maintainer's run on a Windows machine with an NVIDIA card, on the full dataset, with the log lines `CUDA drivers detected`, `Using GPU for image matching`, `Computing pair matching on GPU` and `DensifyPointCloud` with `--cuda-device -1`. These are the Windows counterparts of the proofs of GPU use that the Modal test asserts on Linux (ADR 0002). No private GPU runner in 0.3.

### 9. What the API changes

A `--host` option and `host` config key, applied to the HTTP listener and the UDP progress receiver. The interface writes `host: "127.0.0.1"` on every platform. Loopback TCP with the token; no socket and no named pipe on Windows. The API writes its task list to disk on every change, not only at shutdown, so a package killed with `taskkill` loses nothing. Cancel stays `tree-kill`.

### 10. What the interface changes

The new variant id, the optional manifest key, the Windows machine checks (platform, CPU, memory, disk, card and driver), the lift of `machine.windows_not_yet` once a signed manifest carries a Windows variant, a stop through `taskkill /PID <pid> /T /F`, an ownership check that reads the process command line on Windows, the engine started detached with its console hidden, and the `host` key in the config file. The wizard keeps its single `engine` step. The firewall sentence appears only on the LAN switch in Preferences. Update and rollback stay the versioned folders.

The interface keeps its console window in 0.3. A windowed executable is a later ticket.

### 11. Signing

The maintainer applies to SignPath Foundation for the interface executable. Signed if approved in time, else unsigned with a SmartScreen step in the release notes. The engine archive is not signed; the manifest signature covers its hash. The clean-machine test confirms what the pilot sees.

### 12. Versions and changelog

Engine 0.3.0, API 2.8.0, interface 0.3.0. `minimumUiVersion` does not change. The engine's changelog credits the inherited Windows build path of the reference project when the Windows job lands. `README.md` and `AGENTS.md` state what the workflow does at the time of each pull request: the sentence that there is no native Windows build goes when the ungated job publishes one, and the `configure.py` line changes in the pull request that rewrites it.

## What each repository owes

### aidm-windows-deps

- The repository, public, with `AGENTS.md`, a changelog and the AGPL notice of the project.
- `vcpkg.json`, the triplet, the export workflow, the GDAL wheel build, the re-hosted ExifTool and Python embeddable package, `SHA256SUMS`, the licence list.
- A first release, tagged by the maintainer, that the engine pins.

### aidm (the engine)

- A pin of the deps release in one place, with its hash.
- The SuperBuild on MSVC for every tool, with `/arch:AVX2`, and with CUDA on or off by variant.
- `configure.py` rewritten to build, package and clean. No installer, no runtime installer, no signing.
- `run.bat` and `win32env.bat`, written for AIDM, and a `python/` folder with the embeddable Python and the wheels.
- The two gated jobs in `release.yml`, the Windows smoke test in `scripts/`, the engine-only archives as release assets of the tag run, workflow artifacts on dry runs for the maintainer's hand test.
- Changelog, `README.md` and `AGENTS.md` lines as each lands.

### aidm-api

- `--host` and the `host` key, for HTTP and UDP.
- The task list written on every change.
- The tag-run step that downloads the two engine archives by hash, adds the Windows files at the root, publishes the native packages and lists them in the signed manifest with `minimumDriverVersion` on the CUDA variant.
- Release 2.8.0.

### aidm-ui

- `windows-x64-cuda` in the variant ids and `minimumDriverVersion` in the manifest schema.
- The Windows machine checks and the variant choice with its reason.
- The native package on Windows: `host` in the config, hidden console, `taskkill` stop, command-line ownership check.
- The firewall sentence on the LAN switch, the release-notes SmartScreen step, the signing step if SignPath approves.
- Release 0.3.0, after the API's.

## Tickets, depth and runs

The depth of a ticket follows the `AGENTS.md` of its repository, which the reviewer applies. In the engine, every file under `SuperBuild/`, `requirements.txt` and `release.yml` is a build input, so its ticket is `full`. In the API, configuration, the engine path in `libs/` and `.github/` are `full`. In the interface, `server/manifest/`, the install path, the accepted keys and `.github/` are `full`, with the maintainer's end-to-end record. The deps repository's workflow proves itself: `workflow`.

### aidm-windows-deps

| Ticket | Depth | Runs | Needs |
| --- | --- | --- | --- |
| Repository, `AGENTS.md`, changelog, licence | unit | none | the maintainer creates the repository |
| `vcpkg.json`, triplet, export workflow | workflow | its own run on a hosted Windows runner | |
| GDAL wheel from the port | workflow | its own run | |
| Re-hosted ExifTool and Python embeddable package, `SHA256SUMS`, licence list | workflow | its own run | |
| First release | none | the tag | the maintainer tags; the engine's first pin waits on it |

### aidm (the engine)

| Ticket | Depth | Dry runs | Who starts the dry run | Needs |
| --- | --- | --- | --- | --- |
| Probe: deps pin, gated `windows-x64` job, SuperBuild on the runner, result read not fixed | full | 1 | maintainer (CI code) | the deps release, the pin approved |
| One ticket per tool that fails on MSVC (candidates: OpenSfM with pybind, OpenMVS, PDAL and its bindings, MvsTexturing, PoissonRecon, OpenPointClass, FPCFilter, RenderDEM, Entwine, Untwine, dem2mesh, dem2points, odm_orthophoto, FastRasterFilter, LAStools, draco). Tools that compile first time collapse into one ticket | full | 1, at most 2 | the builder (SuperBuild only) | |
| Python layer: Windows markers in `requirements.txt`, the embeddable package, the wheels | full | 1, at most 2 | the builder | |
| `run.bat`, `win32env.bat`, the package step in `configure.py`, `AGENTS.md` line 33 | full | 1 | the builder | |
| Windows smoke test in `scripts/`, run by the CPU job | full | 1 | maintainer (CI code) | the last released API zip |
| `windows-x64-cuda` job with the CUDA installer | full | 1 | maintainer (CI code) | |
| Engine-only archives as tag-run assets, dry-run artifacts | full | 1 | maintainer (CI code) | |
| Remove the gate | full | 1 | maintainer (CI code) | the maintainer's two hand records |
| Changelog, `README.md` platform line | unit | none | nobody | |
| Release pull request (`VERSION`, labels, `CHANGELOG.md`) | full | 1 | maintainer (CI code), then the tag | |

### aidm-api

| Ticket | Depth | Runs | Needs |
| --- | --- | --- | --- |
| `--host` and the `host` key, HTTP and UDP, with command-line tests | full | the maintainer's dry run; the Windows test-mode job | |
| Task list written on change | full | the maintainer's dry run | |
| Tag-run step: engine archives by hash, root files, native packages, manifest entries with `minimumDriverVersion` | full | the maintainer's dry run | the engine's first archives, the signing key |
| Release 2.8.0 | full | the tag | engine 0.3.0 released |

### aidm-ui

| Ticket | Depth | Record | Needs |
| --- | --- | --- | --- |
| `windows-x64-cuda` id, `minimumDriverVersion` in the schema, parser tests | full | end-to-end on the clean Windows 11 | the maintainer's record |
| Windows machine checks and the variant choice, with the fake native package | full | end-to-end on both machines | the maintainer's records |
| Native package on Windows: `host`, hidden console, `taskkill`, ownership | full | end-to-end on the clean Windows 11 | the maintainer's record |
| Firewall sentence, SmartScreen step, translation strings | unit | `ci.yml` | |
| Signing step | full | a signed release | SignPath approval |
| Release 0.3.0 | full | the two records on the released package | API 2.8.0 released |

Tickets built against the fake native package are built and unit-tested before the first engine archive exists; their end-to-end record waits for the first native package from the API's tag run. The spec may fold the three `full` interface tickets into fewer pull requests so that the maintainer records fewer runs. That is the spec's call.

Runner minutes: a Windows job on a hosted runner is billed at a higher rate per minute than a Linux job, by GitHub's pricing page. No duration is claimed here. The probe's run gives the first one.

## Order of the specs

1. `aidm-windows-deps`. Nothing in the engine can run before its first release.
2. The engine.
3. The API and the interface, in parallel. They need only the layout of ADR 0007 and the keys of ADR 0006, both fixed today.

Each spec waits for the maintainer's call.

## What the maintainer does

- Creates the public repository `AIDroneMap/aidm-windows-deps` and tags its first release.
- Approves the first deps pin in the engine, since it adds a dependency.
- Holds the manifest signing key in the API's `release` environment.
- Starts the dry run of every pull request that changes `release.yml` in any of the three repositories.
- Runs the CUDA variant on the Windows machine with an NVIDIA card and the CPU variant on a clean Windows 11, through the interface, and posts both records on their tickets.
- Applies to SignPath Foundation and, if approved, adds the signing step to the interface's release workflow.
- Decides a larger hosted runner if the probe shows the standard one does not fit.
- Pushes the three tags in the order engine, API, interface.

## Out of 0.3

- Native engines for Linux and macOS: still #15, postponed.
- Windows on ARM and Windows 10 (ADR 0006).
- The Microsoft Store, and any engine installer that runs outside AIDM UI.
- Split-merge across machines.
- Any speed or memory claim. The benchmark process measures after the tag.
- A windowed interface executable without a console.
- ONNX Runtime on the GPU and cuDNN on Windows (ADR 0006).
- Windows golden hashes (ADR 0005). A diagnostic on the stability of Windows outputs comes first.
- Windows jobs in the nightly.
- A private GPU runner.
- Signing of the engine archive.
- Signing of the API executable.
- Any change taken from the reference projects. The inherited Windows build path is credited in the changelog, the code is written again.

## Terms

`GLOSSARY.md` defines the words this note uses: native package, variant, deps export, engine archive, driver floor, dry run, tag run, CI code, proof of GPU use.
