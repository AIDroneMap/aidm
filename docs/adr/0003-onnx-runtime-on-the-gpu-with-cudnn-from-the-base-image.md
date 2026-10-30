# ADR 0003: ONNX Runtime on the GPU, with cuDNN from the base image

Status: accepted, October 2026

## Context

Sky removal (`modules/skyremoval/skyfilter.py`) and background removal (`modules/bgfilter.py`) run ONNX models. Both already pick `CUDAExecutionProvider` when ONNX Runtime offers it (`bgfilter.py` line 16, `skyfilter.py` line 23). But `requirements.txt` pins the CPU wheel `onnxruntime==1.22.1` for both images, so the GPU image runs both filters on the CPU.

The GPU wheel `onnxruntime-gpu` has no 1.22.1 release on PyPI; 1.22.0 exists. It is built against CUDA 12 and cuDNN 9, bundles neither, and its x86_64 cp312 wheel is about 270 MiB (PyPI).

The runtime stage of the GPU image is `nvidia/cuda:12.9.1-runtime-ubuntu24.04`, which has no cuDNN. The `12.9.1-cudnn-runtime-ubuntu24.04` tag of the same image family is about 700 MB larger compressed on Docker Hub (3.15 GB against 2.44 GB). The published `0.1.0-gpu` image is 5.25 GB compressed in the registry (15 layers).

A reference project keeps ONNX on the CPU in its GPU image.

## Decision

The GPU image installs `onnxruntime-gpu==1.22.0` in place of the CPU wheel. The wheel comes from a GPU-only requirements file that `gpu.Dockerfile` passes to `configure.sh`. `requirements.txt` and the CPU image do not change.

cuDNN comes from the base image. The runtime stage moves to `12.9.1-cudnn-runtime-ubuntu24.04`, pinned by digest, same image family. The pip extras (`nvidia-cudnn-cu12`) are not used, so every native library in the image comes from one place and one pin.

Both filters log the provider they chose when they create their session, so a CPU fallback is visible in every log.

The Modal test proves the path with a second engine run on the same downloaded set, `--bg-removal --end-with dataset`, and asserts the CUDA provider line. `--sky-removal` cannot serve, because it skips nadir images (`stages/dataset.py` line 213) and the pinned vineyard sets are nadir. `--bg-removal` cannot go into the options of the main run, because it masks every image and changes every output.

## Consequences

- The GPU image grows by about the base tag difference plus the wheel. The pull request records the size from the dry run and stops to ask if growth passes 2 GB.
- The models are still downloaded at run time from their current URL. An issue records the move to an AIDroneMap host after a licence check.
- The arm64 GPU wheel does not exist for 1.22. This does not matter, because the GPU image is amd64 only.
