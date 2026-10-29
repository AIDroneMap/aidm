# Eigen mirror for engine builds

Issue: https://github.com/AIDroneMap/aidm/issues/45

## Problem

GitLab refused Eigen clones during image builds on 28 October 2026. A build
that misses the Docker cache must work while gitlab.com is unreachable.

The SuperBuild uses Eigen in three places. OpenMVS and OpenPointClass use
commit `7176ae16238ded7fb5ed30a7f5215825b3abd134`. Draco 383 has an Eigen
submodule at `3147391d946bb4b6c68edd901f2add6ac1f31f8c`. mvs-texturing
downloads the 3.3.2 archive, whose tag resolves to
`477d1e81928a8514d2cca6a6e1e7e478eecd8634`.

The four failed job logs linked in the issue name `elibs/eigen`, rather
than the previously reported `external_libs/eigen`. It is the archive
above, not a first-level submodule.

## Approach

Keep upstream branches and tags in the public, read-only
[AIDM Eigen mirror](https://github.com/AIDroneMap/aidm-eigen-mirror).
Keep mirror maintenance notes on a separate default branch. Do not change
upstream source trees or dependency pins. Refresh only when a pin changes.

Set the mirror URL once in `SuperBuild/cmake/EigenMirror.cmake`. Use it for
the direct clone and for Draco's clone-local `submodule.third_party/eigen.url`.
A clone-local `url.*.insteadOf` does not propagate to submodule clones.
The explicit submodule URL controls the clone that Git starts.

Use CMake's `CMAKE_PROJECT_Texturing_INCLUDE` hook to intercept only
mvs-texturing's `ext_eigen` ExternalProject. Replace its archive download
with a mirror clone of the same 3.3.2 commit. Check the original URL and
checksum before replacing them so a future upstream pin change fails
clearly. Keep the source directory and all build steps. No third-party
source patches or Dockerfile changes are needed. Native configure.sh
builds use the same SuperBuild settings.

## Validation

Run unit tests for the real SuperBuild configuration and a local submodule
clone fixture. The fixture blocks network protocols, reproduces the failed
clone-local URL rewrite and proves the explicit submodule URL works.
Check the mvs-texturing hook's pin guard and unchanged source directory.

Use temporary branch CI for no-cache CPU and GPU image builds on
GitHub-hosted amd64 runners. Map gitlab.com to loopback in each build.
Record Git transport traces and fail on GitLab requests. Run the full
`scripts/smoke-test.sh` on the pinned dataset with both images. The GPU
image runs on the CPU for this fetch-path proof. Run the standard unit
check too. No local image builds or full engine runs.

Audit fetch hosts in the build inputs and their pinned dependencies.
Record non-GitHub hosts in the issue. Keep release.yml, normal PR checks,
all pins, dataset settings and engine code unchanged. Remove temporary
validation workflow changes before hand-off.
