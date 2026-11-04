# Windows engine 0.3.0: what the release ships and how it is proven

Issue: https://github.com/AIDroneMap/aidm/issues/113

Written from the design note `docs/design/windows-engine-0.3.0.md`, ADR 0005, ADR 0006 and ADR 0007, and the words in `GLOSSARY.md`. The note records the decisions and the facts; this spec says what has to be true on `master` for v0.3.0 to be tagged, and how each part is proven. The tickets are cut from the "Implementation decisions" and "Testing decisions" sections, one pull request each. The deps repository's spec (AIDroneMap/aidm-windows-deps#1) comes first; the task API's and the interface's specs take the archive layout and the manifest keys from the ADRs.

## Problem statement

A pilot on Windows cannot run AIDM without Docker or WSL. The interface refuses every Windows machine, because no native package exists for it. The engine repository still carries the Windows branches of its SuperBuild and a `configure.py` that nobody may edit, both pointing at a reference project's release for their dependencies, with no workflow that builds them and no test that runs them. A Windows machine with an NVIDIA card gets nothing from it either.

For the maintainer, every line of that inherited path is a claim that cannot be checked: it downloads binaries from a release outside the project, one of them a stub driver fetched without a hash.

## Solution

The engine publishes two engine archives per release, `windows-x64` and `windows-x64-cuda`, built on hosted Windows runners from a pinned deps export (ADR 0005), with every tool compiled for x86-64-v3 and no file from a reference project's release. The CPU archive holds no CUDA file; the CUDA archive bundles the CUDA 12.9.1 runtime and needs only a driver at or above the floor (ADR 0006). Each archive is the `engine/` half of the native package the task API's tag run assembles and signs (ADR 0007).

The release workflow gains two jobs, gated by a dispatch input until they are green on master. The CPU job runs a Windows smoke test that drives the package through the task API the way the interface does, on the CI dataset on dry runs and on the full dataset on tag runs. The CUDA job builds the archive and checks that it starts and reports no CUDA driver on a runner without one. The maintainer proves the CUDA archive on a Windows machine with an NVIDIA card. `configure.py` becomes the Windows build and package script of AIDM, and `run.bat` and `win32env.bat` are written again for the package.

## User stories

1. As a pilot on Windows 11, I want the engine installed and started by AIDM UI with nothing typed and nothing else installed, so that the setup is the whole setup.
2. As a pilot with an NVIDIA card and a current driver, I want the CUDA variant to use the card for matching and densification, so that the hardware I bought is used.
3. As a pilot without an NVIDIA card, I want the CPU variant to carry no CUDA file and to start without looking for one, so that the package is smaller and never fails on a missing driver.
4. As a pilot with an old driver, I want to be told the floor and get the CPU variant meanwhile, so that updating the driver is my choice, not a blocker.
5. As a pilot, I want the same output files and folders on Windows as on Linux, so that the tools I open them with do not care where the job ran.
6. As a pilot, I want the engine's log to say in plain words whether CUDA drivers were detected, so that I can tell what my machine did.
7. As a pilot, I want the engine to write its cache and downloaded model weights under my own local application data, so that the package folder stays what the interface installed.
8. As a pilot who removes the package, I want nothing left behind outside the package folder and that local application data folder, so that an uninstall is a delete.
9. As a maintainer, I want every Windows dependency to come from a release of the project pinned by tag and hash, so that no release of AIDM downloads from a reference project's release.
10. As a maintainer, I want the first deps pin to be a pull request I approve, so that a new dependency enters by my decision.
11. As a maintainer, I want both Windows jobs in the release workflow, gated by an input until they are green, so that the Linux release is never held by Windows work in progress.
12. As a maintainer, I want the jobs to run on hosted Windows runners with no runner of our own, so that the release needs no machine I keep.
13. As a maintainer, I want the probe's dry run to tell me which tools fail on MSVC before anyone fixes one, so that the tickets match the failures and not a guess.
14. As a maintainer, I want the CPU archive tested through the task API on the runner, so that what the interface does is what the test did.
15. As a maintainer, I want the CUDA archive to pass a start check on the runner and a hand run on the machine with the card, with the GPU log lines on the ticket, so that the variant I publish is proven where it can be.
16. As a maintainer, I want a dry run to upload the archives as workflow artifacts, so that I can download one for the hand run without a release.
17. As a maintainer, I want the tag run to attach the two engine archives with their hashes to the draft release, so that the task API's tag run can download them by hash.
18. As a maintainer, I want the Windows jobs out of the nightly in this line, so that the nightly's merge rules and cache stay as they are.
19. As a maintainer, I want the changelog to credit the inherited Windows build path once and to record that the code was written again, so that the origin is stated and nothing is copied.
20. As a maintainer, I want `README.md` and `AGENTS.md` to say there is a Windows build only once a workflow publishes one, so that the rules state what exists.
21. As a builder, I want `configure.py` to build, package and clean with the deps release and nothing else, so that the Windows build is one script with pinned inputs.
22. As a builder, I want the package layout to be a function I can test on Linux against a fake tree, so that a layout mistake is caught by a unit test and not by a six-hour job.
23. As a builder, I want every rule about `SuperBuild/` files to fail the build itself, so that it holds on the branch under test and not on the last release's copy.
24. As a builder, I want the Windows smoke test's parsing and verdicts under unit tests on recorded inputs, so that a change to the test is proven before it costs a runner.
25. As a builder, I want each ticket to name its depth, who starts its dry run and whether the Windows job is gated, so that I never start a run that proves nothing.
26. As a builder, I want tools that compile first time to collapse into one ticket, so that the ticket list matches the work.
27. As the task API's builder, I want the archive to be one top folder with `engine/` inside, `run.bat` at its root and `win32env.bat` next to it, so that the API's Windows helpers find what they call.
28. As the interface's builder, I want the archive to be a `tar.gz` that the system `tar` of Windows 11 unpacks, so that the unpacker does not change.
29. As the independent reviewer, I want each pull request to carry the evidence its depth requires and the gate's state, so that I can approve from the record.
30. As a reader of the public repository, I want no private host, account, machine, model or tool named, so that the public record holds only what is public.

## Implementation decisions

### 1. The deps pin

One file under the SuperBuild folder names the deps release by tag and the SHA-256 of each asset the build downloads: the export zip, the GDAL wheel, the VC++ runtime zip, the Python embeddable package and the ExifTool zip. `configure.py` downloads each asset from that release, checks its hash and stops on a mismatch. The SuperBuild on Windows takes its toolchain file from the unpacked export and the triplet name `x64-windows-avx2` from the pin file. The pin is a build input; the first pin is approved by the maintainer. The four URLs that point at a reference project's release, in `configure.py`, `requirements.txt`, the ExifTool recipe and the OpenMVS recipe, go away with the tickets that touch each.

### 2. Compiler flags and the two variants

On Windows every external project of the SuperBuild is configured with `/arch:AVX2` in its C and C++ flags, the Windows form of ADR 0001. There is no second build for older CPUs.

`configure.py build` takes a variant, `cpu` or `cuda`. The CPU variant configures OpenMVS without CUDA and ships no stub driver: the download of the dummy `nvcuda.dll` is removed. The CUDA variant configures OpenMVS with CUDA from the toolkit at the path the CUDA installer sets, with the same maximum-compatibility setting the Linux GPU image uses, and copies into the install folder the CUDA runtime DLLs the built tools import. After the install step the build lists the DLLs in its install folder and fails a CPU build that contains any CUDA runtime DLL, and fails a CUDA build that lacks the ones its tools import.

### 3. GDAL, PROJ and PDAL

The SuperBuild takes GDAL's headers and import library from the export, not from the wheel. The GDAL Python bindings are the wheel of the deps release, installed by `configure.py` from the checked download; the Windows-only GDAL line of `requirements.txt` is removed, and the Linux and macOS lines stay. The GDAL and PROJ data folders of the export are copied into the install folder's `share`, and the DLLs of the export into its `bin`, as the SuperBuild already does for the DLLs. The PDAL Python bindings the SuperBuild builds are installed into the same site-packages as every other wheel.

### 4. Python in the package

The package carries the Python 3.12.10 embeddable package from the deps release, unpacked into `python/`, with its `._pth` file edited to import `site` and to list `Lib\site-packages`. The wheels of `requirements.txt` are installed into that site-packages with the runner's Python of the same version and architecture; no virtual environment is created. Every compiled pin of `requirements.txt` has a `cp312` wheel for Windows on PyPI except GDAL and PDAL, covered above. `requirements.txt` gains Windows markers only where a pin differs by platform; today none does besides GDAL. The ONNX Runtime wheel is the CPU one in both variants (ADR 0006).

### 5. `run.bat` and `win32env.bat`

Both are written for AIDM. `win32env.bat` switches the console to UTF-8, sets the engine base folder from its own location, puts the package's `python/` first on `PATH` and the SuperBuild's `bin` after it, points `GDAL_DATA` and `PROJ_LIB` into the install folder's `share`, sets `PDAL_DRIVER_PATH` to the SuperBuild's `bin`, sets the variable GDAL's Python package reads on Windows to search `PATH` for its DLLs, and puts the bytecode cache under the user's local application data in a folder named for the project. It runs nothing outside the package. `run.bat` calls it and starts `run.py` with `python -X utf8` and the arguments it received. The task API's Windows helper calls `win32env.bat` and then `python`, which is why `python/` is first on `PATH`.

The engine's model-weights folder on Windows moves from the program data folder of the reference project to the same local application data folder, in a code change with a unit test.

### 6. ExifTool and Obj2Tiles

On Windows the ExifTool recipe downloads the zip of the deps release, checked by hash, and installs the executable under the name the engine calls. Obj2Tiles is built on Windows from its pinned source tag with the .NET SDK the hosted runner carries, instead of downloading the prebuilt binary from a reference project's release; Linux keeps its download until its own issue. The maintainer approved this build step in the grilling.

### 7. The package step and the archive

`configure.py` has three actions: `build`, `package` and `clean`. The installer, the runtime installer and the signing step of the inherited script are gone. `package` takes the variant and lays out one top folder `aidm-<version>-windows-x64` or `-windows-x64-cuda`, containing `engine/` with: `run.bat`, `win32env.bat`, `run.py`, `VERSION`, `ENGINE`, `settings.yaml`, the Python package and stage folders of the engine, the SuperBuild's install folder, `python/`, and `licenses/` with the engine's licence, the licence list of the deps release and the licence file of each SuperBuild source. It copies the VC++ runtime DLLs of the deps release into the SuperBuild's `bin`. It then writes `aidm-<version>-windows-x64.tar.gz` or the CUDA name with the runner's `tar`, and a `.sha256` file next to it. The layout is a function of an install tree and a variant, importable on any platform, and `configure.py` calls it.

The archive is the engine half of ADR 0007. Nothing of the task API is in it.

### 8. The two jobs in the release workflow

`release.yml` gains `windows-x64` and `windows-x64-cuda`, on `windows-2025`, after `check`, with the longest timeout the hosted runner allows until the probe gives a real one. Until the gate is removed, each runs only when a dispatch sets the input `windows` to true, and each has `continue-on-error`, so the Linux jobs stay the verdict of a dry run while the Windows job's log is read. Steps: checkout, Python of the runner, download and check the deps assets by the pin, for the CUDA job the pinned network installer of CUDA 12.9.1 run silently with the compiler and runtime subpackages and checked by hash, `configure.py build`, `configure.py package`, the smoke test or the start check, upload of the archive and its hash as a workflow artifact. The dataset follows the mode: CI dataset on a dry run, full dataset on a tag run, as the Linux jobs do. No golden comparison on Windows (ADR 0005).

Once the gate is removed, the jobs run on every dry run and tag run and never in the nightly, and the `release` job downloads the two artifacts and attaches the archives and their hashes to the draft release, with a table of them in the notes. The task API's tag run downloads them from there by hash.

The registry build cache is not involved: the Windows jobs build from source every time, with the export as their only prebuilt input.

### 9. The Windows smoke test

A script under `scripts/`, run with the runner's Python and `pyodm` at the version `requirements.txt` pins. It reads the dataset pins, the default options and the output list from `smoke-test.conf`, downloads and checks the dataset, downloads the last released Windows zip of the task API by a URL and hash pinned in the same configuration file, unpacks it, writes the API's config with a token, a port, a concurrency of one and the package's `engine/` as the engine path, and starts the API executable. It submits the images with the default options, waits for the task, downloads the results, checks that every output of the list exists and is not empty, and runs the georeferencing check on the orthophoto. It stops the API and fails on any step with the step's name.

Its second mode, `start`, is the CUDA job's check on a runner without a GPU: the API answers its information route with the engine version from the package, and the engine's GPU detection, run through the package's Python, reports no CUDA driver. The CUDA job never runs the pipeline on the runner.

The Linux smoke test does not change.

### 10. The gate's removal and the rule files

A pull request that touches `.github/` changes nothing else and lands alone, so every workflow change in this spec is its own pull request, ordered after the pull request that gives it something to run. The gate's removal is one such pull request: it removes the input gate and `continue-on-error` and makes the `release` job attach the archives. A documentation pull request follows it: `configure.py` joins the build inputs in `AGENTS.md` and in the nightly merge script's list, the `README.md` sentence that says there is no native Windows build gives way to the two archives and the minimum machine, and the changelog entry credits the inherited Windows build path of the reference project and says the Windows files were written again. `AGENTS.md`'s line about `configure.py` changes in the first pull request that edits the script, which is not a workflow pull request.

### 11. Versions and the release

Engine 0.3.0. The release pull request bumps `VERSION` and its copies, renames `## Unreleased` to `## 0.3.0` with the date, and its dry run rehearses the tag run with the Windows jobs in it. The tag is pushed when the taggable minimum of the tracking issue holds. The task API's and the interface's releases follow in that order.

## Testing decisions

A good test checks what a consumer sees: the archive, its layout, what the package does when the task API starts it, and the log lines the engine writes. It does not check how a tool was compiled. Two seams, and one rule.

The high seam is the Windows smoke test of decision 9. Its parsing and verdicts (configuration keys, the output list, the task states it waits on, the georeferencing verdict, the start-mode lines) get unit tests on recorded inputs in `tests/`, as the Modal test has. The script itself runs only on the hosted Windows runner.

The low seam is the package layout function of decision 7, tested on Linux against a fake install tree: the files that must exist, the `._pth` edit, no CUDA runtime DLL in a CPU layout and the required ones in a CUDA layout, the `licenses/` folder, the archive name per variant.

The rule: anything that must hold about files under `SuperBuild/` fails the build itself, with a message that names the file, because the unit tests in CI run inside the last published image and see that release's copy of those files.

Every ticket that touches `SuperBuild/`, `requirements.txt`, `configure.py` or `release.yml` is `full`, with at most two dry runs per pull request, one per head commit, and the three-runs limit shared with the nightly. A pull request that changes `release.yml` or anything under `scripts/` is CI code: the maintainer starts its dry run and the review loop hands it to the maintainer. A pull request that touches `.github/` changes nothing else and lands alone, so each workflow change below is its own row, ordered after the row that gives it something to run. While the gate holds, every dry run that should exercise a Windows job is started with the input `windows` set to true. Tools that compile first time collapse into one ticket; a tool that fails gets its own.

| Ticket | Depth | Dry runs | Who starts the dry run | Needs |
| --- | --- | --- | --- | --- |
| Probe, part one: the deps pin file, `configure.py` downloads by hash, the toolchain and triplet from the export, `/arch:AVX2`, the `AGENTS.md` line about `configure.py`. Its dry run shows the Linux images unchanged; nothing runs it on Windows yet | full | 1 | the builder | the deps release; the pin approved |
| Probe, part two: the gated `windows-x64` job with `continue-on-error` that runs `configure.py build`, workflow only. Its dry run with the input set is read, not fixed | full | 1 | maintainer (CI code) | part one merged |
| One ticket per tool that fails on MSVC, in the order the probe's log shows them; tools that compile first time collapse into one | full | 1, at most 2 | the builder | the probe's log |
| Obj2Tiles from its pinned source with the .NET SDK | full | 1 | the builder | |
| GDAL, PROJ and PDAL from the export: headers and import library, the wheel installed by `configure.py`, the `share` folders, the Windows GDAL line removed from `requirements.txt` | full | 1, at most 2 | the builder | |
| Python in the package: the embeddable package, the `._pth` edit, the wheels into its site-packages | full | 1, at most 2 | the builder | |
| `run.bat`, `win32env.bat`, the model folder change with its unit test, the package layout function with its unit tests, `configure.py package` and `clean` | full | 1 | the builder | |
| The package step in the job and the archive as a dry-run artifact, workflow only | full | 1 | maintainer (CI code) | the package step merged |
| The Windows smoke test with its unit tests and the API zip pin in `smoke-test.conf` | full | 1 | maintainer (CI code: `scripts/`) | the last released API zip |
| The CPU job runs the smoke test, workflow only | full | 1 | maintainer (CI code) | the smoke test merged |
| The CPU variant without CUDA, the CUDA variant's build, the build-time DLL checks, the start mode of the smoke test | full | 1, at most 2 | maintainer (CI code: `scripts/`) | |
| The `windows-x64-cuda` job with the CUDA installer and the start check, workflow only | full | 1, at most 2 | maintainer (CI code) | the CUDA variant merged |
| The maintainer's hand run of the CUDA archive on the full dataset, record on the ticket | none | 0 | nobody | the Windows machine with an NVIDIA card |
| Gate removal: jobs on every dry run and tag run, archives attached by the `release` job, workflow only | full | 1 | maintainer (CI code) | the hand run's record; the CPU job green |
| Rule files after the gate: `configure.py` in the build inputs of `AGENTS.md` and of the nightly merge list, the `README.md` platform sentence, the changelog credit | unit (`AGENTS.md`, `README.md`, changelog) or full if the nightly merge list changes in the same pull request | 0 or 1 | maintainer if `scripts/` changes | the gate removal merged |
| Release pull request (`VERSION` and copies, `CHANGELOG.md`) | full | 1 | maintainer (CI code), then the tag | everything above |

Prior art: `tests/test_gpu_smoke_modal.py` and `tests/test_golden_compare.py` for a CI script's verdicts on recorded inputs; `tests/test_check_georeferencing.py` for the georeferencing check; `tests/test_eigen_mirror.py` for a rule about SuperBuild files, with the lesson that such a rule belongs in the build.

The hand run: the maintainer downloads the CUDA archive from a dry run's artifact, unpacks it on the Windows machine with an NVIDIA card, runs `run.bat` on the full dataset and posts the lines `CUDA drivers detected`, `Using GPU for image matching`, `Computing pair matching on GPU` and the densification command with `--cuda-device -1`, with the driver version, on the ticket. No number of any kind goes with it.

## Out of scope

- Windows on ARM, Windows 10, a build for CPUs below x86-64-v3 (ADR 0001, ADR 0006).
- ONNX Runtime on the GPU and cuDNN on Windows (ADR 0006).
- Windows golden hashes and the diagnostic that would precede them (ADR 0005).
- Windows jobs in the nightly, a private GPU runner, signing of the engine archive.
- The task API's Windows files and the assembly of the native package (the API's spec), and every interface change (the interface's spec).
- Obj2Tiles on Linux: it stays a download from a reference project's release, in an issue of its own.
- The ffmpeg download at run time for video input: it comes from a release that is not a reference project's and is unchanged on every platform.
- Any speed, memory or size claim. The probe's run gives the first durations and sizes; nobody writes them in this repository.
- Split-merge across machines, and every change to the pipeline's outputs.

## Further notes

- The probe is two pull requests. The first changes `configure.py`, the pin and the SuperBuild and is proven only in that the Linux images do not change. The second adds the gated job and merges with a red Windows job: the job is gated and `continue-on-error`, so the dry run's verdict is the Linux jobs'. Its log is the input of the per-tool tickets.
- The OpenCV path the OpenSfM recipe expects on Windows differs from the folder the OpenCV recipe installs to. The probe will show it; it is one of the per-tool tickets.
- The CUDA network installer is pinned by URL and hash in the workflow. It installs the compiler and the runtime; the job has no GPU and never runs a kernel.
- The package's `python/` first on `PATH` is what the task API's Windows helper relies on. A change there is a change to the API's contract and needs its spec.
- The pin of the task API's Windows zip in `smoke-test.conf` moves to the newest API release in a pull request of its own, when the API's 2.8.0 is out; the engine's test needs only an API that can start a Windows engine.
- Three nightlies failed before this spec on a Python import unrelated to Windows; they are not part of this work.
