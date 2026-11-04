# ADR 0006: Two Windows variants and a driver floor

Status: accepted, November 2026

## Context

On Linux the engine ships as two images, CPU and CUDA, and the interface picks one from what the machine has (`aidm-ui`, `server/machine/gpu.ts`). The CUDA image carries the CUDA runtime; the machine supplies the driver.

The inherited Windows build path had one build. It linked OpenMVS against CUDA and shipped a stub `nvcuda.dll` (`SuperBuild/cmake/External-OpenMVS.cmake` lines 39 to 46) so that machines without an NVIDIA driver could load the executable. The stub is downloaded without a hash from a reference project's release. `modules/gpu.py` lines 14 to 31 load the system's `nvcuda.dll` on Windows and log whether CUDA drivers were found.

A CUDA runtime built with CUDA 12.9.1 needs a driver of at least 576.57 on Windows, from NVIDIA's release notes for CUDA 12.9 Update 1. A machine with an older driver loads the runtime and fails on the first kernel.

The engine's pull request #89 moved ONNX Runtime to the GPU on Linux with cuDNN from the base image. No Windows base image exists; cuDNN on Windows would be a second redistribution decision.

## Decision

Windows ships as two native packages, called variants, with the ids `windows-x64` (CPU) and `windows-x64-cuda` (CUDA). The CPU variant contains no CUDA file: OpenMVS is built without CUDA and no stub driver is shipped. The CUDA variant bundles the CUDA 12.9.1 runtime libraries its tools link against and needs only the NVIDIA driver on the machine.

A native variant in the release manifest may carry an optional key `minimumDriverVersion`. The CUDA variant sets it to `576.57`. The interface reads the installed driver version from `nvidia-smi.exe` and offers the CUDA variant only when a driver at or above the floor is present. With no NVIDIA card, no driver or an older driver, it installs the CPU variant and shows the reason. The CUDA package has no CPU fallback of its own.

Both Windows variants use the CPU wheel of ONNX Runtime, `onnxruntime==1.22.1` from `requirements.txt`. ONNX Runtime on the GPU on Windows is a later decision.

Both variants target Windows 11 on x86-64 with AVX2 and FMA, the same minimum as ADR 0001. The interface checks the CPU before any download. Windows on ARM and Windows 10 are not targets.

## Consequences

- Two archives to build, test and publish per release instead of one. The CPU variant is the one most pilots get and the one the hosted runner can run; the CUDA variant is proven by hand on a Windows machine with an NVIDIA card.
- A pilot with an NVIDIA card and an old driver gets the CPU variant, with a message, until the driver is updated. Updating the driver and installing again gives the CUDA variant.
- The manifest schema of the interface gains one optional key. Older interfaces ignore it and never see a Windows variant, because the Windows variants arrive with the interface release that reads it.
- The stub driver download and its hashless URL go away.
- Sky removal and background removal run on the CPU on Windows in this line, as they do in the Linux CPU image.
