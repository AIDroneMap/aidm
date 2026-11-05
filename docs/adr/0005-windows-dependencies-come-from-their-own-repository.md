# ADR 0005: Windows dependencies come from their own repository

Status: accepted, November 2026

## Context

The engine's C++ tools depend on a set of libraries (Eigen, SuiteSparse, Boost, PROJ, GDAL and others). On Linux the Dockerfiles build or install them. On Windows the inherited build path downloaded them prebuilt: `configure.py` line 27 fetches a vcpkg export from a release of a reference project's `windows-deps` repository, and `requirements.txt` line 17 takes a GDAL wheel from the same release. `SuperBuild/cmake/External-ExifTool.cmake` line 11 and `External-OpenMVS.cmake` line 45 take two more files from it.

The project's rule is that no release downloads anything from a release asset of a reference project. The hosted Windows runner gives one job six hours. Building the vcpkg set from source inside every engine run would repeat, on every dry run, work that does not change between engine commits, before the SuperBuild starts.

The same reference project kept its `vcpkg.json` in the engine repository and built the export in a separate workflow by hand. AIDM removed that file and that workflow in 0.1.0 (`44cafea6`, `d6814c0c`).

## Decision

A public repository, `AIDroneMap/aidm-windows-deps`, owns the prebuilt Windows dependencies. It holds a `vcpkg.json` in manifest mode with a pinned `builtin-baseline` and an `overrides` list, a custom triplet that compiles with `/arch:AVX2` (ADR 0001 on Windows), and one workflow that runs `vcpkg install` and `vcpkg export --zip` on a hosted Windows runner.

Each release of that repository publishes: the vcpkg export zip, a GDAL wheel built from the GDAL port of the same export, the Windows ExifTool executable re-hosted, the Python 3.12.10 embeddable package re-hosted, a `SHA256SUMS` file and a licence list. The dependency list starts from the one the reference project published for its 3.8.4 line, credited in the changelog of the new repository, with GDAL added as a vcpkg port pinned to 3.8.4, the version the Linux images carry.

The engine pins one release of that repository by tag and SHA-256, in one place, and its Windows build job downloads the export from there. Changing the pin is a pull request in the engine that changes a build input. The first pin is approved by the maintainer, since it adds a dependency.

The deps repository is rebuilt by hand or on its own tag, never on a schedule. It has no binary cache: the export zip is the cache.

## Consequences

- The engine's Windows job compiles only the engine's own tools. The time of a vcpkg build is paid once per deps release.
- No Windows build downloads from a reference project's release. The four URLs named in Context go away with the rewrite of `configure.py`.
- Versions on Windows and on Linux differ where vcpkg's pins differ (PROJ, Boost, libtiff among them). The deps repository records each difference. Outputs may differ between the platforms, so Windows gets no golden hashes until a diagnostic has established which outputs are stable there.
- The export is one more thing to keep in step with the SuperBuild pins. A SuperBuild pin that needs a newer library needs a deps release first.
- If the export turns out not to fit the hosted runner's time together with the SuperBuild, the way out is to move more libraries into the export (OpenCV and Ceres first), not to shorten the SuperBuild.

## Amendment, 2026-11-05

The deps release also publishes a zip of the VC++ runtime DLLs, as required by the [engine spec](../specs/0113-windows-engine-0.3.0.md) and the [deps spec](https://github.com/AIDroneMap/aidm-windows-deps/blob/main/docs/specs/0001-first-deps-release.md). The engine copies these DLLs into the package so that it needs no separate runtime installation.

The deps repository has two workflows, not one. `release.yml` builds and exports the dependencies. `tests.yml` runs the release check script's unit tests on every push to a branch and every pull request, as the deps spec requires. The release workflow remains unscheduled.
