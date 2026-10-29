# Vectorise weighted depth-patch sampling

Issue: https://github.com/AIDroneMap/aidm/issues/53

## Problem

The CPU depth estimator samples a 5 by 5 patch through a projective
transform. Its weighted NCC loop mixes coordinate updates, border checks,
bilinear reads and three ordered sums. Diagnose the pinned source with
GCC's vectorisation report and inspect the published CPU binary before
changing it. Record the compiler, CPU, image digest and engine commit.
These are diagnostics, not performance measurements.

## Approach

Keep the existing homography calculation and its incremental coordinate
updates. Store the patch coordinates, project them in a separate loop and
check every sample before reading the image. Checking only four corners
would miss a projective pole inside a patch.

Gather the four neighboring intensities into separate arrays, then
vectorise independent bilinear interpolation with OpenMP SIMD, which the
existing OpenMVS build already enables. Keep the weighted sums in their original
order. Do not enable reassociation or global fast-math. This is stricter
than the issue's proposed local reassociation and avoids a new score
tolerance. The compiler report must confirm packed sampling instructions
at the existing CPU target before accepting this approach.

Install an AIDM-owned header through the OpenMVS build recipe. Apply a
guarded source edit to call it only for weighted CPU NCC. Leave the other
NCC modes, CUDA kernels, dependency revision, CPU targets, command-line
options and output paths unchanged. Fail the build if the expected source
anchors change.

## Tests

- Compare the new sampler and ordered sums bit for bit with a synthetic
  scalar reference. Cover projective transforms, image borders, projective
  poles, non-finite coordinates, padded rows and low-texture patches.
- Run the synthetic C++ tests as part of the dependency build on both CPU
  architectures and the GPU image's host compiler. Test builds with
  sanitizers on the disposable diagnostic machine as well.
- Keep source-integration checks under `tests/`. Run them with CMake when
  build inputs are available. The published unit image does not carry new
  build inputs or CMake, so these checks also run in the dependency build.
- Use the full release dry run, which builds all three images and processes
  the pinned dataset. The smoke test checks output presence and
  georeferencing, not numerical equivalence. Compare densification from a
  shared stored reconstruction separately; do not treat two independent
  reconstructions as an equality test.

## Acceptance

Record the original compiler diagnostics and published disassembly on the
issue. Show packed instructions for the changed sampling loop and keep
synthetic weighted results identical. Explain any numerical difference
found by the stored-reconstruction comparison. Require passing unit and
full CI checks and an independent review. File the benchmark request only
after approval. No coding-session timing claim belongs in the change.
