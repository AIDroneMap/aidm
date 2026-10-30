# ADR 0002: Every GPU path is proven by the release test

Status: accepted, October 2026

## Context

The GPU image is tested on a real GPU on Modal by `scripts/gpu-smoke-modal.py`. Today that script asserts only that `nvidia-smi` was detected and that every `DensifyPointCloud` call ran with `--cuda-device -1`.

The engine has a second GPU path that nothing asserts. The OpenSfM fork AIDM pins builds its CUDA Hamming matcher by default: `OPENSFM_USE_CUDA` is ON, and CUDA is found at configure time in the GPU builder image. `modules/osfm.py` lines 293 to 297 select the matcher when a GPU is present and the dataset has 32 or more images. The Modal log of release dry run 40973807769 shows "Computing pair matching on GPU (CUDA, 1 device(s))" and "Batched Hamming matching 255 pairs ... on GPU".

When CUDA is missing, OpenSfM falls back to FLANN with a warning: "GPU matching requested but CUDA is not available. Falling back to CPU FLANN." A GPU image whose matcher silently stopped using the GPU would pass the release today. The same holds for any GPU path added later.

## Decision

The Modal GPU test asserts every GPU path the engine has. It reads the log lines that prove the GPU was used and the absence of the fallback lines. A silent fall back to the CPU fails the release.

A new GPU path lands with its assertion. The changelog counts a stage as "on the GPU" only once the Modal test asserts it.

Where the options of the main run do not reach a path, the test makes a separate, smaller engine run that does (see ADR 0003 for the ONNX case). It does not change the options of the main run, so the outputs the release test checks stay the same.

## Consequences

- The Modal job grows by one assertion per path and, for ONNX, by a second short engine run.
- The CI dataset of 32 images sits exactly on the matcher threshold of `modules/osfm.py`. The dataset pins are not changed to save time (`AGENTS.md`).
- Ceres with CUDA and OpenCV with CUDA are not enabled in 0.2.0, because nothing in the engine would call them. OpenSfM's bundle adjuster uses `SPARSE_SCHUR` on SuiteSparse (`bundle_adjuster.cc` lines 73 and 1184), and Ceres 2.2.0 with CUDA accelerates only its dense solvers and CGNR. No AIDM code calls OpenCV's CUDA modules.
- Both are recorded as "no consumer, not enabled". A flag flip with no consumer would prove nothing.
