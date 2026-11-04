# ADR 0007: The native package is one archive assembled by the API release

Status: accepted, November 2026

## Context

The interface installs a native package by downloading one archive, checking its SHA-256 against the signed manifest, unpacking it with the system `tar`, and starting the executable `aidm-api` at the root of the package with the engine at `engine/` under it (`aidm-ui`, `server/packages/native-package.ts`, `EXECUTABLE_NAME` and `ENGINE_PATH_IN_PACKAGE`). The manifest is a release asset of `aidm-api`, signed by that repository's tag run (`aidm-ui` ADR 0003).

The API already publishes a Windows zip with `aidm-api.exe`, a bundled 7-Zip and the helper `helpers\odm_python.bat`, which calls `win32env.bat` in the engine folder. It starts the engine through `run.bat` on Windows (`libs/odmRunner.js`). The engine has neither file: `run.bat`, `win32env.bat` and the rest of the inherited Windows files were deleted in 0.1.0.

Three repositories publish on their own tags. If the engine's tag run produced the complete package, it would have to embed an API version whose tag run has not signed a manifest yet, and the API's tag run would then have to republish the engine's asset. That is a loop.

## Decision

The native package for Windows is one `tar.gz` archive with one top folder. Its root holds the files of the API's Windows zip. Its `engine/` folder holds the engine: `run.bat`, `win32env.bat`, `run.py`, `VERSION`, `ENGINE`, `settings.yaml`, `modules/`, `stages/`, the built tools under `SuperBuild/install/`, a `python/` folder with the embeddable Python and the installed wheels, and a `licenses/` folder.

`run.bat` and `win32env.bat` are written again for AIDM. `win32env.bat` sets the path and the GDAL, PROJ and PDAL environment to the folders inside the package and runs nothing outside it. `run.bat` calls it and starts `run.py` with `python -X utf8`.

The engine's tag run builds the `engine/` folder for each variant and publishes it as an engine-only archive, a release asset of the engine named `aidm-<version>-windows-x64.tar.gz` and `aidm-<version>-windows-x64-cuda.tar.gz`, with SHA-256 sums. It needs no secret beyond the release token every tag run has.

The API's tag run downloads the two engine archives by version and checks their hashes, places its Windows files at the root, writes the native packages as `tar.gz`, publishes them as its own release assets and signs the manifest that names them. The release order of one coordinated release is therefore engine, then API with its manifest, then interface.

An archive that would pass the size limit of a release asset moves to the project's object storage, with the same URL and hash fields in the manifest. The first dry run gives the sizes.

## Consequences

- The interface's unpacker does not change. The system `tar` on Windows 11 reads `tar.gz`.
- The engine does not know the API version and the API does not build the engine. Each tag run tests what it builds: the engine's Windows job tests the engine archive through the last released API, the API's tag run tests the assembled package.
- `configure.py` is rewritten to build, package and clean, and loses the installer, the runtime installer and the signing step of the inherited version. The `AGENTS.md` line that says not to edit it changes in that pull request.
- The engine's Windows smoke test needs a released API to drive the package. The first engine release in this line is tested with the API release before it, which has no Windows engine of its own but can start one.
- Update and rollback in the interface stay what they are for native packages: one versioned folder per installed package.
